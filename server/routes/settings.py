import logging

from flask import Blueprint, jsonify, request

from auth import require_auth
from profile_service import ProfileService
from security_manager import security_manager


def create_settings_blueprint() -> Blueprint:
    """Create settings-related routes."""
    settings_bp = Blueprint("settings", __name__)

    @settings_bp.route("/api/settings/ai-keys", methods=["GET"])
    @require_auth
    def get_ai_key_settings():
        """Return the user's current AI Engine configuration."""
        try:
            user_id = request.current_user["id"]
            result = ProfileService.get_complete_profile(user_id)
            if not result.get("success"):
                return jsonify({"error": "Profile not found"}), 404

            profile = result["profile"]
            primary_mode = profile.get("api_primary_mode") or None
            secondary_mode = profile.get("api_secondary_mode") or None

            has_custom_key = False
            masked_key = None
            try:
                from database_config import SessionLocal, UserProfile

                db = SessionLocal()
                db_profile = db.query(UserProfile).filter(UserProfile.user_id == user_id).first()
                if db_profile and db_profile.custom_gemini_api_key:
                    has_custom_key = True
                    decrypted = security_manager.decrypt_sensitive_data(
                        db_profile.custom_gemini_api_key
                    )
                    masked_key = "•" * (len(decrypted) - 4) + decrypted[-4:]
                db.close()
            except Exception as key_err:
                logging.warning(f"Could not check custom key: {key_err}")

            return (
                jsonify(
                    {
                        "success": True,
                        "api_primary_mode": primary_mode,
                        "api_secondary_mode": secondary_mode,
                        "has_custom_key": has_custom_key,
                        "masked_custom_key": masked_key,
                        "configured": primary_mode is not None,
                    }
                ),
                200,
            )

        except Exception as exc:
            logging.error(f"Error getting AI key settings: {exc}", exc_info=True)
            return jsonify({"error": "Failed to get AI key settings"}), 500

    @settings_bp.route("/api/settings/ai-keys", methods=["POST"])
    @require_auth
    def save_ai_key_settings():
        """Save the user's AI Engine configuration."""
        try:
            user_id = request.current_user["id"]
            body = request.json or {}

            primary_mode = body.get("primary_mode", "").strip()
            secondary_mode = body.get("secondary_mode") or None
            custom_api_key_plain = (body.get("custom_api_key") or "").strip()

            valid_modes = {"launchway", "custom"}
            if primary_mode not in valid_modes:
                return jsonify({"error": "primary_mode must be 'launchway' or 'custom'"}), 400
            if secondary_mode and secondary_mode not in valid_modes:
                return (
                    jsonify(
                        {"error": "secondary_mode must be 'launchway', 'custom', or null"}
                    ),
                    400,
                )
            if primary_mode == secondary_mode and primary_mode:
                return (
                    jsonify({"error": "primary_mode and secondary_mode cannot be the same"}),
                    400,
                )
            if "custom" in {primary_mode, secondary_mode} and not custom_api_key_plain:
                return (
                    jsonify(
                        {"error": "A Gemini API key is required when using 'custom' mode"}
                    ),
                    400,
                )

            encrypted_key = None
            if custom_api_key_plain:
                if not custom_api_key_plain.startswith("AIza"):
                    return (
                        jsonify(
                            {
                                "error": "That doesn't look like a valid Gemini API key (should start with 'AIza')"
                            }
                        ),
                        400,
                    )
                encrypted_key = security_manager.encrypt_sensitive_data(custom_api_key_plain)

            update_payload = {
                "api_primary_mode": primary_mode,
                "api_secondary_mode": secondary_mode,
            }
            if encrypted_key is not None:
                update_payload["custom_gemini_api_key"] = encrypted_key

            ProfileService.create_or_update_profile(user_id, update_payload)

            return (
                jsonify(
                    {
                        "success": True,
                        "message": "AI Engine settings saved.",
                        "api_primary_mode": primary_mode,
                        "api_secondary_mode": secondary_mode,
                        "has_custom_key": bool(custom_api_key_plain),
                    }
                ),
                200,
            )

        except Exception as exc:
            logging.error(f"Error saving AI key settings: {exc}", exc_info=True)
            return jsonify({"error": str(exc)}), 500

    return settings_bp

