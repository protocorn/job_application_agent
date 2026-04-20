import logging
import os

from flask import Blueprint, jsonify, request

from auth import require_auth


def create_beta_blueprint() -> Blueprint:
    """Create beta program and admin beta routes."""
    beta_bp = Blueprint("beta", __name__)

    @beta_bp.route("/api/beta/status", methods=["GET"])
    @require_auth
    def get_beta_status():
        """Get beta access status for current user."""
        try:
            from database_config import SessionLocal, User

            user_id = request.current_user["id"]

            db = SessionLocal()
            try:
                user = db.query(User).filter(User.id == user_id).first()

                if not user:
                    return jsonify({"error": "User not found"}), 404

                return (
                    jsonify(
                        {
                            "success": True,
                            "beta_access_requested": user.beta_access_requested or False,
                            "beta_access_approved": user.beta_access_approved or False,
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
                        }
                    ),
                    200,
                )

            finally:
                db.close()

        except Exception as exc:
            logging.error(f"Error in get beta status endpoint: {exc}")
            return jsonify({"error": "Failed to get beta access status"}), 500

    @beta_bp.route("/api/beta/request", methods=["POST"])
    @require_auth
    def submit_beta_request():
        """Allow an authenticated user to (re-)submit a beta request."""
        try:
            from database_config import SessionLocal, User
            from datetime import datetime

            data = request.get_json() or {}
            reason = (data.get("reason") or "").strip()
            survey_consent = bool(data.get("survey_consent"))

            if len(reason) < 20:
                return (
                    jsonify(
                        {
                            "error": "Please provide at least 20 characters for your reason.",
                            "error_code": "validation_failed",
                        }
                    ),
                    400,
                )
            if not survey_consent:
                return (
                    jsonify(
                        {
                            "error": "Please agree to the weekly survey.",
                            "error_code": "validation_failed",
                        }
                    ),
                    400,
                )

            user_id = request.current_user["id"]

            db = SessionLocal()
            try:
                user = db.query(User).filter(User.id == user_id).first()
                if not user:
                    return jsonify({"error": "User not found"}), 404

                if user.beta_access_approved:
                    return (
                        jsonify(
                            {
                                "error": "You already have beta access.",
                                "error_code": "already_approved",
                            }
                        ),
                        400,
                    )

                if user.beta_access_requested:
                    return (
                        jsonify(
                            {
                                "error": "You already have a pending beta request.",
                                "error_code": "already_pending",
                            }
                        ),
                        400,
                    )

                consent_note = "[Survey consent given]" if survey_consent else ""
                stored_reason = f"{reason}\n\n{consent_note}".strip()

                user.beta_access_requested = True
                user.beta_request_date = datetime.utcnow()
                user.beta_request_reason = stored_reason

                db.commit()

                logging.info(f"Beta re-request submitted by user {user_id}: {user.email}")

                return (
                    jsonify(
                        {
                            "success": True,
                            "message": "Beta access request submitted successfully.",
                        }
                    ),
                    200,
                )

            finally:
                db.close()

        except Exception as exc:
            logging.error(f"Error in submit beta request endpoint: {exc}")
            return jsonify({"error": "Failed to submit beta request"}), 500

    @beta_bp.route("/api/admin/beta/requests", methods=["GET"])
    @require_auth
    def get_beta_requests():
        """Get all pending beta access requests (admin only)."""
        try:
            from database_config import SessionLocal, User

            user_email = request.current_user["email"]

            admin_emails = os.getenv("ADMIN_EMAILS", "").split(",")
            if user_email not in admin_emails:
                return jsonify({"error": "Unauthorized - Admin access required"}), 403

            db = SessionLocal()
            try:
                pending_requests = (
                    db.query(User)
                    .filter(
                        User.beta_access_requested == True,  # noqa: E712
                        User.beta_access_approved == False,  # noqa: E712
                    )
                    .order_by(User.beta_request_date.desc())
                    .all()
                )

                approved_users = (
                    db.query(User)
                    .filter(User.beta_access_approved == True)  # noqa: E712
                    .order_by(User.beta_approved_date.desc())
                    .all()
                )

                pending_list = [
                    {
                        "id": user.id,
                        "email": user.email,
                        "first_name": user.first_name,
                        "last_name": user.last_name,
                        "reason": user.beta_request_reason,
                        "request_date": (
                            user.beta_request_date.isoformat()
                            if user.beta_request_date
                            else None
                        ),
                        "created_at": user.created_at.isoformat(),
                    }
                    for user in pending_requests
                ]

                approved_list = [
                    {
                        "id": user.id,
                        "email": user.email,
                        "first_name": user.first_name,
                        "last_name": user.last_name,
                        "approved_date": (
                            user.beta_approved_date.isoformat()
                            if user.beta_approved_date
                            else None
                        ),
                    }
                    for user in approved_users
                ]

                return (
                    jsonify(
                        {
                            "success": True,
                            "pending_requests": pending_list,
                            "approved_users": approved_list,
                            "stats": {
                                "pending_count": len(pending_list),
                                "approved_count": len(approved_list),
                            },
                        }
                    ),
                    200,
                )

            finally:
                db.close()

        except Exception as exc:
            logging.error(f"Error in get beta requests endpoint: {exc}")
            return jsonify({"error": "Failed to get beta requests"}), 500

    @beta_bp.route("/api/admin/beta/approve/<string:user_id>", methods=["POST"])
    @require_auth
    def approve_beta_access(user_id):
        """Approve beta access for a user (admin only)."""
        try:
            from database_config import SessionLocal, User
            from datetime import datetime
            from email_service import email_service
            from uuid import UUID

            user_email = request.current_user["email"]
            admin_emails = os.getenv("ADMIN_EMAILS", "").split(",")
            if user_email not in admin_emails:
                return jsonify({"error": "Unauthorized - Admin access required"}), 403

            try:
                user_uuid = UUID(user_id)
            except ValueError:
                return jsonify({"error": "Invalid user ID format"}), 400

            db = SessionLocal()
            try:
                user = db.query(User).filter(User.id == user_uuid).first()

                if not user:
                    return jsonify({"error": "User not found"}), 404

                if user.beta_access_approved:
                    return (
                        jsonify({"success": False, "error": "User already has beta access"}),
                        400,
                    )

                user.beta_access_approved = True
                user.beta_approved_date = datetime.utcnow()

                db.commit()

                try:
                    email_service.send_beta_approval_email(
                        to_email=user.email, first_name=user.first_name
                    )
                except Exception as exc:
                    logging.error(f"Failed to send beta approval email: {exc}")

                logging.info(f"Beta access approved for user {user_id}: {user.email}")

                return (
                    jsonify(
                        {"success": True, "message": f"Beta access approved for {user.email}"}
                    ),
                    200,
                )

            finally:
                db.close()

        except Exception as exc:
            logging.error(f"Error in approve beta access endpoint: {exc}")
            return jsonify({"error": "Failed to approve beta access"}), 500

    @beta_bp.route("/api/admin/beta/reject/<string:user_id>", methods=["POST"])
    @require_auth
    def reject_beta_access(user_id):
        """Reject beta access for a user (admin only)."""
        try:
            from database_config import SessionLocal, User
            from uuid import UUID

            user_email = request.current_user["email"]
            admin_emails = os.getenv("ADMIN_EMAILS", "").split(",")
            if user_email not in admin_emails:
                return jsonify({"error": "Unauthorized - Admin access required"}), 403

            try:
                user_uuid = UUID(user_id)
            except ValueError:
                return jsonify({"error": "Invalid user ID format"}), 400

            data = request.get_json()
            rejection_reason = data.get(
                "reason", "Your request does not meet our current beta criteria."
            )

            db = SessionLocal()
            try:
                user = db.query(User).filter(User.id == user_uuid).first()

                if not user:
                    return jsonify({"error": "User not found"}), 404

                user_email_addr = user.email
                user_first_name = user.first_name

                user.beta_access_requested = False
                user.beta_request_date = None
                user.beta_request_reason = None

                db.commit()

                logging.info(f"Beta access rejected for user {user_id}: {user_email_addr}")

                from server.email_service import email_service

                email_sent = email_service.send_beta_rejection_email(
                    to_email=user_email_addr,
                    first_name=user_first_name,
                    rejection_reason=rejection_reason,
                )

                if email_sent:
                    logging.info(f"Rejection email sent to {user_email_addr}")
                else:
                    logging.warning(f"Failed to send rejection email to {user_email_addr}")

                return (
                    jsonify(
                        {
                            "success": True,
                            "message": f"Beta access request rejected for {user_email_addr}",
                            "email_sent": email_sent,
                        }
                    ),
                    200,
                )

            finally:
                db.close()

        except Exception as exc:
            logging.error(f"Error in reject beta access endpoint: {exc}")
            return jsonify({"error": "Failed to reject beta access"}), 500

    return beta_bp

