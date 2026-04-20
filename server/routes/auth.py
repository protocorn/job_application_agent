import logging
from typing import Any, Callable, Dict

from flask import Blueprint, jsonify, redirect, request

from auth import AuthService, require_auth
from security_manager import security_manager


def create_auth_blueprint(
    *, build_frontend_redirect: Callable[[str, Dict[str, Any]], str]
) -> Blueprint:
    """Create authentication-related routes."""
    auth_bp = Blueprint("auth", __name__)

    @auth_bp.route("/api/auth/signup", methods=["POST", "OPTIONS"])
    def signup():
        """User registration endpoint."""
        if request.method == "OPTIONS":
            return "", 204

        try:
            data = request.json
            if not data:
                return jsonify({"error": "No data provided"}), 400

            required_fields = ["email", "password", "first_name", "last_name"]
            for field in required_fields:
                if not data.get(field):
                    pretty = field.replace("_", " ").title()
                    return (
                        jsonify(
                            {
                                "success": False,
                                "error": f"{pretty} is required",
                                "error_code": "validation_failed",
                                "field_errors": {field: f"{pretty} is required."},
                            }
                        ),
                        400,
                    )

            email = data["email"].strip().lower()
            password = data["password"]
            first_name = data["first_name"].strip()
            last_name = data["last_name"].strip()

            field_errors = {}
            if "@" not in email:
                field_errors["email"] = "Please provide a valid email address."
            if len(password) < 8:
                field_errors["password"] = (
                    "Password must be at least 8 characters long."
                )
            if not first_name:
                field_errors["first_name"] = "First name is required."
            if not last_name:
                field_errors["last_name"] = "Last name is required."

            beta_request_reason = (data.get("beta_request_reason") or "").strip()
            survey_consent = bool(data.get("survey_consent"))
            if not beta_request_reason:
                field_errors["beta_request_reason"] = (
                    "Please tell us why you want beta access."
                )
            elif len(beta_request_reason) < 20:
                field_errors["beta_request_reason"] = "Please provide at least 20 characters."
            if not survey_consent:
                field_errors["survey_consent"] = "Please agree to the weekly survey."

            if field_errors:
                return (
                    jsonify(
                        {
                            "success": False,
                            "error": "Please fix the highlighted fields.",
                            "field_errors": field_errors,
                            "error_code": "validation_failed",
                        }
                    ),
                    400,
                )

            result = AuthService.register_user(
                email,
                password,
                first_name,
                last_name,
                beta_request_reason=beta_request_reason,
                survey_consent=survey_consent,
            )

            if result["success"]:
                return jsonify(result), 201
            status_code = 409 if result.get("error_code") == "email_already_exists" else 400
            return jsonify(result), status_code

        except Exception as exc:
            logging.error(f"Error in signup endpoint: {exc}")
            return jsonify({"error": "Registration failed. Please try again."}), 500

    @auth_bp.route("/api/auth/login", methods=["POST", "OPTIONS"])
    def login():
        """User login endpoint with IP-based rate limiting."""
        if request.method == "OPTIONS":
            return "", 204

        try:
            data = request.json
            if not data:
                return jsonify({"error": "No data provided"}), 400

            email = data.get("email", "").strip().lower()
            password = data.get("password", "")

            if not email or not password:
                return jsonify({"error": "Email and password are required"}), 400

            client_ip = request.remote_addr

            ip_allowed, ip_remaining, ip_reason = security_manager.check_ip_login_attempts(
                client_ip
            )
            if not ip_allowed:
                logging.warning(f"Login attempt blocked for IP {client_ip}: {ip_reason}")
                return jsonify({"success": False, "error": ip_reason}), 429

            account_allowed, account_remaining = security_manager.check_login_attempts(email)
            if not account_allowed:
                return (
                    jsonify(
                        {
                            "success": False,
                            "error": (
                                "Account temporarily locked due to too many failed login attempts. "
                                "Please try again in 15 minutes."
                            ),
                            "remaining_attempts": 0,
                        }
                    ),
                    429,
                )

            result = AuthService.authenticate_user(email, password)
            credentials_ok = result["success"] or result.get("beta_not_approved", False)

            security_manager.record_login_attempt(
                identifier=email,
                success=credentials_ok,
                user_id=result.get("user", {}).get("id") if credentials_ok else None,
                ip_address=client_ip,
            )

            if result["success"] or result.get("beta_not_approved"):
                return jsonify(result), 200

            return (
                jsonify(
                    {
                        **result,
                        "remaining_attempts": account_remaining - 1,
                        "ip_remaining_attempts": ip_remaining - 1,
                    }
                ),
                401,
            )
        except Exception as exc:
            logging.error(f"Error in login endpoint: {exc}")
            return jsonify({"error": "Login failed. Please try again."}), 500

    @auth_bp.route("/api/auth/verify", methods=["GET"])
    @require_auth
    def verify_token():
        """Verify JWT token and return user info."""
        try:
            return (
                jsonify(
                    {
                        "success": True,
                        "user": request.current_user,
                        "message": "Token is valid",
                    }
                ),
                200,
            )
        except Exception as exc:
            logging.error(f"Error in verify token endpoint: {exc}")
            return jsonify({"error": "Token verification failed"}), 500

    @auth_bp.route("/api/auth/logout", methods=["POST"])
    @require_auth
    def logout():
        """Logout user (client-side token removal)."""
        try:
            return (
                jsonify({"success": True, "message": "Logged out successfully"}),
                200,
            )
        except Exception as exc:
            logging.error(f"Error in logout endpoint: {exc}")
            return jsonify({"error": "Logout failed"}), 500

    @auth_bp.route("/api/auth/verify-email", methods=["GET"])
    def verify_email():
        """Verify user email with verification token."""
        try:
            should_redirect = (request.args.get("redirect", "") or "").strip().lower() in (
                "1",
                "true",
                "yes",
            )
            token = request.args.get("token")
            if not token:
                if should_redirect:
                    return redirect(
                        build_frontend_redirect(
                            "/login",
                            {"verified": "0", "message": "Verification token is required"},
                        )
                    )
                return jsonify({"error": "Verification token is required"}), 400

            result = AuthService.verify_email(token)
            if should_redirect:
                if result.get("success"):
                    return redirect(
                        build_frontend_redirect(
                            "/login",
                            {
                                "verified": "1",
                                "message": result.get(
                                    "message", "Email verified successfully"
                                ),
                            },
                        )
                    )
                return redirect(
                    build_frontend_redirect(
                        "/login",
                        {
                            "verified": "0",
                            "message": result.get("error", "Email verification failed"),
                        },
                    )
                )

            if result["success"]:
                return jsonify(result), 200
            return jsonify(result), 400
        except Exception as exc:
            logging.error(f"Error in verify email endpoint: {exc}")
            return jsonify({"error": "Email verification failed"}), 500

    @auth_bp.route("/api/auth/resend-verification", methods=["POST", "OPTIONS"])
    def resend_verification():
        """Resend verification email to user."""
        if request.method == "OPTIONS":
            return "", 204

        try:
            data = request.json
            if not data or not data.get("email"):
                return jsonify({"error": "Email address is required"}), 400

            email = data["email"].strip().lower()
            if "@" not in email:
                return jsonify({"error": "Please provide a valid email address"}), 400

            result = AuthService.resend_verification_email(email)
            if result["success"]:
                return jsonify(result), 200
            return jsonify(result), 400
        except Exception as exc:
            logging.error(f"Error in resend verification endpoint: {exc}")
            return jsonify({"error": "Failed to resend verification email"}), 500

    @auth_bp.route("/api/auth/verify-email-change", methods=["GET"])
    def verify_email_change():
        """Confirm an email change using a verification token."""
        try:
            should_redirect = (request.args.get("redirect", "") or "").strip().lower() in (
                "1",
                "true",
                "yes",
            )
            token = request.args.get("token")
            if not token:
                if should_redirect:
                    return redirect(
                        build_frontend_redirect(
                            "/login",
                            {
                                "email_change_verified": "0",
                                "message": "Verification token is required",
                            },
                        )
                    )
                return jsonify({"error": "Verification token is required"}), 400

            result = AuthService.verify_email_change(token)
            if should_redirect:
                if result.get("success"):
                    return redirect(
                        build_frontend_redirect(
                            "/login",
                            {
                                "email_change_verified": "1",
                                "message": result.get(
                                    "message", "Email updated successfully"
                                ),
                            },
                        )
                    )
                return redirect(
                    build_frontend_redirect(
                        "/login",
                        {
                            "email_change_verified": "0",
                            "message": result.get(
                                "error", "Email change verification failed"
                            ),
                        },
                    )
                )

            if result["success"]:
                return jsonify(result), 200
            return jsonify(result), 400
        except Exception as exc:
            logging.error(f"Error in verify_email_change: {exc}")
            return jsonify({"error": "Email change verification failed"}), 500

    return auth_bp

