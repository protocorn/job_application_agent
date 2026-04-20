import html
import json
import logging
import os
import secrets
import time
from typing import Any, Dict, Sequence

from flask import Blueprint, jsonify, request
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from auth import require_auth
from google_oauth_service import GoogleOAuthService


OAUTH_STATE_TTL_SECONDS = int(os.getenv("OAUTH_STATE_TTL_SECONDS", "600"))
_oauth_serializer = URLSafeTimedSerializer(
    os.getenv("JWT_SECRET_KEY", "dev-fallback-key-change-in-production")
)
_CONSUMED_OAUTH_NONCES: Dict[str, float] = {}


def create_frontend_redirect_builder(
    *, allowed_origins: Sequence[str], flask_env: str
):
    """Build and return a frontend URL redirect helper."""

    def _get_default_frontend_origin() -> str:
        explicit_origin = (os.getenv("FRONTEND_URL") or "").strip()
        if explicit_origin.startswith("http"):
            return explicit_origin

        http_origins = [
            origin
            for origin in allowed_origins
            if isinstance(origin, str) and origin.startswith("http")
        ]
        non_localhost = [
            origin
            for origin in http_origins
            if "localhost" not in origin and "127.0.0.1" not in origin
        ]

        if flask_env != "development":
            if non_localhost:
                return non_localhost[0]
            if http_origins:
                return http_origins[0]
            return "https://www.launchway.app/"

        if http_origins:
            return http_origins[0]
        return "http://localhost:3000"

    def _build_frontend_redirect(path: str, params: Dict[str, Any]) -> str:
        from urllib.parse import urlencode

        base_origin = (_get_default_frontend_origin() or "http://localhost:3000").rstrip(
            "/"
        )
        query = urlencode({k: v for k, v in (params or {}).items() if v is not None})
        if query:
            return f"{base_origin}{path}?{query}"
        return f"{base_origin}{path}"

    return _build_frontend_redirect


def create_oauth_blueprint(
    *, allowed_origins: Sequence[str], flask_env: str
) -> Blueprint:
    """Create OAuth routes."""
    oauth_bp = Blueprint("oauth", __name__)
    build_frontend_redirect = create_frontend_redirect_builder(
        allowed_origins=allowed_origins,
        flask_env=flask_env,
    )

    def _create_oauth_state(user_id: str, origin: str) -> str:
        nonce = secrets.token_urlsafe(8)
        return _oauth_serializer.dumps(
            {"user_id": str(user_id), "origin": origin, "nonce": nonce}
        )

    def _consume_oauth_state(state_token: str) -> Dict[str, Any]:
        try:
            data = _oauth_serializer.loads(state_token, max_age=OAUTH_STATE_TTL_SECONDS)
        except (SignatureExpired, BadSignature):
            return {}

        nonce = data.get("nonce", "")
        if nonce in _CONSUMED_OAUTH_NONCES:
            return {}
        _CONSUMED_OAUTH_NONCES[nonce] = time.time()

        cutoff = time.time() - OAUTH_STATE_TTL_SECONDS * 2
        stale = [k for k, v in _CONSUMED_OAUTH_NONCES.items() if v < cutoff]
        for key in stale:
            _CONSUMED_OAUTH_NONCES.pop(key, None)

        return data

    @oauth_bp.route("/api/oauth/authorize", methods=["GET"])
    @require_auth
    def oauth_authorize():
        """Get Google OAuth authorization URL."""
        try:
            user_id = request.current_user["id"]
            request_origin = request.headers.get("Origin", "")
            trusted_origin = (
                request_origin
                if request_origin in allowed_origins
                else build_frontend_redirect("", {}).rstrip("/")
            )
            state_token = _create_oauth_state(user_id=user_id, origin=trusted_origin)
            auth_url = GoogleOAuthService.get_authorization_url(user_id, state_token)
            return jsonify({"success": True, "authorization_url": auth_url}), 200
        except Exception as exc:
            logging.error(f"Error generating OAuth URL: {exc}")
            return jsonify({"error": "Failed to generate OAuth authorization URL"}), 500

    @oauth_bp.route("/api/oauth/callback", methods=["GET"])
    def oauth_callback():
        """Handle Google OAuth callback."""
        try:
            state_token = request.args.get("state")
            state_payload = _consume_oauth_state(state_token or "")
            callback_origin = (
                state_payload.get("origin")
                or build_frontend_redirect("", {}).rstrip("/")
            )

            def _popup_html(
                success: bool, message: str, email: str = "", error_message: str = ""
            ) -> str:
                safe_message = html.escape(message or "")
                safe_email = html.escape(email or "")
                payload = {
                    "type": "GOOGLE_AUTH_SUCCESS" if success else "GOOGLE_AUTH_ERROR",
                    "email": email or "",
                    "error": error_message or message or "",
                }
                payload_json = json.dumps(payload)
                target_origin_json = json.dumps(callback_origin)
                status_color = "#2e7d32" if success else "#d32f2f"
                status_title = (
                    "Authorization Successful!" if success else "Authorization Failed"
                )
                return f"""
                    <html>
                        <head>
                            <style>
                                body {{ font-family: Arial, sans-serif; text-align: center; padding: 50px; }}
                                .status {{ color: {status_color}; }}
                                .countdown {{ font-size: 14px; color: #666; margin-top: 10px; }}
                            </style>
                        </head>
                        <body>
                            <h2 class="status">{'✓' if success else '✗'} {status_title}</h2>
                            <p>{safe_message}</p>
                            {"<p>Email: " + safe_email + "</p>" if safe_email else ""}
                            <p class="countdown">This window will close automatically in <span id="countdown">3</span> seconds...</p>
                            <script>
                                const payload = {payload_json};
                                const targetOrigin = {target_origin_json};
                                if (window.opener && targetOrigin) {{
                                    window.opener.postMessage(payload, targetOrigin);
                                }}

                                let countdown = 2;
                                const countdownElement = document.getElementById('countdown');
                                const interval = setInterval(() => {{
                                    countdown--;
                                    if (countdownElement) countdownElement.textContent = countdown;
                                    if (countdown <= 0) {{
                                        clearInterval(interval);
                                        window.close();
                                    }}
                                }}, 1000);
                            </script>
                        </body>
                    </html>
                """

            if not state_payload:
                logging.warning("OAuth callback rejected due to invalid/expired state")
                return _popup_html(
                    success=False,
                    message="Invalid or expired OAuth session. Please try connecting again.",
                    error_message="Invalid or expired OAuth session.",
                )

            error = request.args.get("error")
            if error:
                error_description = request.args.get(
                    "error_description", "Authorization denied"
                )
                logging.warning(f"OAuth error from Google: {error} - {error_description}")
                return _popup_html(
                    success=False,
                    message="Google denied authorization. Please try connecting again.",
                    error_message=error_description,
                )

            code = request.args.get("code")
            if not code:
                return jsonify({"error": "Missing code parameter"}), 400

            user_id = state_payload["user_id"]
            result = GoogleOAuthService.handle_oauth_callback(code, user_id)

            if result["success"]:
                return _popup_html(
                    success=True,
                    message="Your Google account has been connected successfully.",
                    email=result.get("google_email", ""),
                )
            return _popup_html(
                success=False,
                message="Google account connection failed. Please try again.",
                error_message=result.get("error", "Unknown error occurred"),
            )
        except Exception as exc:
            logging.error(f"Error in OAuth callback: {exc}")
            return jsonify({"error": "OAuth callback failed"}), 500

    @oauth_bp.route("/api/oauth/status", methods=["GET"])
    @require_auth
    def oauth_status():
        """Check if user has a valid, working Google connection."""
        try:
            user_id = request.current_user["id"]

            if not GoogleOAuthService.is_connected(user_id):
                return (
                    jsonify(
                        {
                            "success": True,
                            "is_connected": False,
                            "token_expired": False,
                            "google_email": None,
                        }
                    ),
                    200,
                )

            google_email = GoogleOAuthService.get_google_email(user_id)
            credentials = GoogleOAuthService.get_credentials(user_id)

            if credentials is None:
                logging.info(
                    f"Google token validation failed for user {user_id} - marking as expired"
                )
                return (
                    jsonify(
                        {
                            "success": True,
                            "is_connected": False,
                            "token_expired": True,
                            "google_email": google_email,
                        }
                    ),
                    200,
                )

            return (
                jsonify(
                    {
                        "success": True,
                        "is_connected": True,
                        "token_expired": False,
                        "google_email": google_email,
                    }
                ),
                200,
            )
        except Exception as exc:
            logging.error(f"Error checking OAuth status: {exc}")
            return jsonify({"error": "Failed to check OAuth status"}), 500

    @oauth_bp.route("/api/oauth/disconnect", methods=["POST"])
    @require_auth
    def oauth_disconnect():
        """Disconnect Google account."""
        try:
            user_id = request.current_user["id"]
            result = GoogleOAuthService.disconnect_google_account(user_id)
            if result["success"]:
                return jsonify(result), 200
            return jsonify(result), 400
        except Exception as exc:
            logging.error(f"Error disconnecting Google account: {exc}")
            return jsonify({"error": "Failed to disconnect Google account"}), 500

    @oauth_bp.route("/api/oauth/access-token", methods=["GET"])
    @require_auth
    def oauth_access_token():
        """Return the current Google OAuth access token."""
        try:
            user_id = request.current_user["id"]
            access_token = GoogleOAuthService.get_access_token(user_id)
            if not access_token:
                return jsonify({"error": "Google account not connected"}), 403
            return jsonify({"access_token": access_token}), 200
        except Exception as exc:
            logging.error(f"Error fetching access token: {exc}")
            return jsonify({"error": "Failed to retrieve access token"}), 500

    @oauth_bp.route("/api/oauth/picker-config", methods=["GET"])
    def oauth_picker_config():
        """Return frontend-safe OAuth picker config."""
        return (
            jsonify(
                {
                    "client_id": os.getenv("GOOGLE_CLIENT_ID", ""),
                    "api_key": os.getenv("GOOGLE_PICKER_API_KEY", ""),
                }
            ),
            200,
        )

    return oauth_bp

