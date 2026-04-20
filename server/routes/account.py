import logging
from datetime import datetime

from flask import Blueprint, jsonify, request

from auth import AuthService, require_auth
from profile_service import ProfileService


def create_account_blueprint() -> Blueprint:
    """Create account management routes."""
    account_bp = Blueprint("account", __name__)

    @account_bp.route("/api/account/change-password", methods=["POST"])
    @require_auth
    def change_password():
        """Change user password (GDPR compliance)."""
        try:
            from database_config import SessionLocal, User

            data = request.get_json()
            current_password = data.get("current_password")
            new_password = data.get("new_password")

            if not current_password or not new_password:
                return (
                    jsonify(
                        {"error": "Current password and new password are required"}
                    ),
                    400,
                )

            if len(new_password) < 8:
                return (
                    jsonify(
                        {"error": "New password must be at least 8 characters long"}
                    ),
                    400,
                )

            user_id = request.current_user["id"]
            db = SessionLocal()

            try:
                user = db.query(User).filter(User.id == user_id).first()
                if not user:
                    return jsonify({"error": "User not found"}), 404

                if not AuthService.verify_password(current_password, user.password_hash):
                    return jsonify({"error": "Current password is incorrect"}), 401

                user.password_hash = AuthService.hash_password(new_password)
                db.commit()

                logging.info(f"Password changed successfully for user {user.email}")

                return (
                    jsonify({"success": True, "message": "Password changed successfully"}),
                    200,
                )

            finally:
                db.close()

        except Exception as exc:
            logging.error(f"Error changing password: {exc}")
            return jsonify({"error": "Failed to change password"}), 500

    @account_bp.route("/api/account/email", methods=["PUT"])
    @require_auth
    def update_email():
        """Update the authenticated user's email address (CLI endpoint)."""
        try:
            from database_config import SessionLocal, User

            data = request.get_json() or {}
            new_email = (data.get("email") or "").strip().lower()

            if not new_email or "@" not in new_email:
                return jsonify({"error": "A valid email address is required"}), 400

            user_id = request.current_user["id"]
            db = SessionLocal()
            try:
                existing = db.query(User).filter(User.email == new_email).first()
                if existing and str(existing.id) != str(user_id):
                    return jsonify({"error": "Email already in use"}), 409

                user = db.query(User).filter(User.id == user_id).first()
                if not user:
                    return jsonify({"error": "User not found"}), 404

                user.email = new_email
                db.commit()
                logging.info(f"Email updated for user {user_id}")
                return (
                    jsonify({"success": True, "message": "Email updated successfully"}),
                    200,
                )
            finally:
                db.close()
        except Exception as exc:
            logging.error(f"Error updating email: {exc}")
            return jsonify({"error": "Failed to update email"}), 500

    @account_bp.route("/api/account/request-email-change", methods=["POST", "OPTIONS"])
    @require_auth
    def request_email_change():
        """Send a verification link to a new email address to confirm an email change."""
        if request.method == "OPTIONS":
            return "", 204
        try:
            data = request.get_json() or {}
            new_email = (data.get("new_email") or "").strip().lower()
            if not new_email:
                return jsonify({"error": "new_email is required"}), 400

            user_id = request.current_user["id"]
            result = AuthService.request_email_change(user_id, new_email)
            if result["success"]:
                return jsonify(result), 200
            return jsonify(result), 400
        except Exception as exc:
            logging.error(f"Error in request_email_change: {exc}")
            return jsonify({"error": "Failed to initiate email change"}), 500

    @account_bp.route("/api/account/info", methods=["GET"])
    @require_auth
    def get_account_info():
        """Return basic account info + application count (used by CLI)."""
        try:
            from database_config import SessionLocal, User, JobApplication

            user_id = request.current_user["id"]
            db = SessionLocal()
            try:
                user = db.query(User).filter(User.id == user_id).first()
                if not user:
                    return jsonify({"error": "User not found"}), 404

                app_count = (
                    db.query(JobApplication)
                    .filter(JobApplication.user_id == user_id)
                    .count()
                )

                return (
                    jsonify(
                        {
                            "success": True,
                            "account": {
                                "user_id": str(user.id),
                                "email": user.email,
                                "pending_email": user.pending_email or None,
                                "first_name": user.first_name,
                                "last_name": user.last_name,
                                "created_at": (
                                    user.created_at.isoformat() if user.created_at else None
                                ),
                                "email_verified": user.email_verified,
                                "is_active": user.is_active,
                                "total_applications": app_count,
                            },
                        }
                    ),
                    200,
                )
            finally:
                db.close()
        except Exception as exc:
            logging.error(f"Error fetching account info: {exc}")
            return jsonify({"error": "Failed to fetch account info"}), 500

    @account_bp.route("/api/account/export-data", methods=["GET"])
    @require_auth
    def export_user_data():
        """Export all user data in JSON format (GDPR portability)."""
        try:
            from database_config import SessionLocal, User

            user_id = request.current_user["id"]
            user_email = request.current_user["email"]

            db = SessionLocal()

            try:
                user = db.query(User).filter(User.id == user_id).first()
                if not user:
                    return jsonify({"error": "User not found"}), 404

                profile_data = ProfileService.get_profile(user_id)

                user_data = {
                    "account_information": {
                        "user_id": user.id,
                        "email": user.email,
                        "first_name": user.first_name,
                        "last_name": user.last_name,
                        "created_at": (
                            user.created_at.isoformat() if user.created_at else None
                        ),
                        "email_verified": user.email_verified,
                        "beta_access_requested": user.beta_access_requested,
                        "beta_access_approved": user.beta_access_approved,
                        "beta_request_date": (
                            user.beta_request_date.isoformat()
                            if user.beta_request_date
                            else None
                        ),
                        "beta_approved_date": (
                            user.beta_approved_date.isoformat()
                            if user.beta_approved_date
                            else None
                        ),
                        "beta_request_reason": user.beta_request_reason,
                        "google_oauth_connected": bool(user.google_refresh_token),
                        "google_account_email": user.google_account_email,
                    },
                    "profile_data": (
                        profile_data.get("profile") if profile_data.get("success") else {}
                    ),
                    "profile_strength": (
                        profile_data.get("profile_strength")
                        if profile_data.get("success")
                        else None
                    ),
                    "export_metadata": {
                        "export_date": datetime.utcnow().isoformat(),
                        "export_format": "JSON",
                        "gdpr_compliance": (
                            "This data export complies with GDPR Article 20 "
                            "(Right to Data Portability)"
                        ),
                    },
                }

                logging.info(f"Data export completed for user {user_email}")

                return jsonify({"success": True, "data": user_data}), 200

            finally:
                db.close()

        except Exception as exc:
            logging.error(f"Error exporting user data: {exc}")
            return jsonify({"error": "Failed to export user data"}), 500

    @account_bp.route("/api/account/delete", methods=["DELETE"])
    @require_auth
    def delete_account():
        """Delete user account and associated data (GDPR erasure)."""
        try:
            from database_config import SessionLocal, User

            data = request.get_json()
            password = data.get("password")
            confirmation = data.get("confirmation")

            if not password:
                return (
                    jsonify({"error": "Password is required to delete account"}),
                    400,
                )

            if confirmation != "DELETE":
                return (
                    jsonify({"error": "Please type DELETE to confirm account deletion"}),
                    400,
                )

            user_id = request.current_user["id"]
            user_email = request.current_user["email"]

            db = SessionLocal()

            try:
                user = db.query(User).filter(User.id == user_id).first()
                if not user:
                    return jsonify({"error": "User not found"}), 404

                if not AuthService.verify_password(password, user.password_hash):
                    return jsonify({"error": "Incorrect password"}), 401

                try:
                    ProfileService.delete_profile(user_id)
                except Exception as profile_err:
                    logging.warning(
                        f"Error deleting profile for user {user_id}: {profile_err}"
                    )

                db.delete(user)
                db.commit()

                logging.info(f"Account deleted successfully for user {user_email}")

                return (
                    jsonify(
                        {
                            "success": True,
                            "message": (
                                "Account and all associated data have been permanently deleted"
                            ),
                        }
                    ),
                    200,
                )

            finally:
                db.close()

        except Exception as exc:
            logging.error(f"Error deleting account: {exc}")
            return jsonify({"error": "Failed to delete account"}), 500

    return account_bp

