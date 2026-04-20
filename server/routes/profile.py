import logging

from flask import Blueprint, jsonify, request

from auth import require_auth
from profile_service import ProfileService
from profile_strength import score_profile_strength


def create_profile_blueprint() -> Blueprint:
    """Create profile-related routes."""
    profile_bp = Blueprint("profile", __name__)

    @profile_bp.route("/api/profile", methods=["GET"])
    @require_auth
    def get_profile():
        """Get user profile data from PostgreSQL."""
        try:
            user_id = request.current_user["id"]
            result = ProfileService.get_complete_profile(user_id)

            if result["success"]:
                profile = dict(result.get("profile") or {})
                profile_strength = result.get("profile_strength") or score_profile_strength(
                    profile
                )
                source_type = profile.get("resume_source_type", "google_doc")

                # Normalize resume fields by source type to avoid leaking stale payloads.
                if source_type in ("pdf", "docx"):
                    profile["resume_url"] = ""
                else:
                    profile["resume_text"] = ""
                    profile["resume_filename"] = ""
                    profile["resume_file_base64"] = ""

                return (
                    jsonify(
                        {
                            "resumeData": profile,
                            "resume_url": profile.get("resume_url", ""),
                            "resume_source_type": source_type,
                            "profile_strength": profile_strength,
                            "success": True,
                            "message": "Profile fetched successfully",
                            "error": None,
                        }
                    ),
                    200,
                )

            return jsonify({"error": result["error"], "success": False}), 404

        except Exception as exc:
            logging.error(f"Error getting profile: {exc}")
            return jsonify({"error": "Failed to get profile"}), 500

    @profile_bp.route("/api/profile", methods=["POST"])
    @require_auth
    def save_profile():
        """Save user profile data to PostgreSQL."""
        try:
            user_id = request.current_user["id"]
            profile_data = request.json

            if not profile_data:
                return jsonify({"error": "No profile data provided"}), 400

            result = ProfileService.create_or_update_profile(user_id, profile_data)
            if result["success"]:
                return (
                    jsonify(
                        {
                            "success": True,
                            "message": "Profile saved successfully to database",
                        }
                    ),
                    200,
                )

            return jsonify({"error": result["error"], "success": False}), 500

        except Exception as exc:
            logging.error(f"Error saving profile: {exc}")
            return jsonify({"error": "Failed to save profile"}), 500

    return profile_bp

