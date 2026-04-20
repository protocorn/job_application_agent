import logging
from datetime import datetime

from flask import Blueprint, jsonify, request

from auth import require_admin, require_auth
from backup_manager import backup_manager, run_full_backup
from database_optimizer import get_database_health
from job_queue import job_queue
from rate_limiter import get_rate_limit_status
from security_manager import get_security_status, security_manager


def create_monitoring_blueprint() -> Blueprint:
    """Create production monitoring and management routes."""
    monitoring_bp = Blueprint("monitoring", __name__)

    @monitoring_bp.route("/api/system/status", methods=["GET"])
    @require_auth
    def system_status():
        """Comprehensive system status endpoint (requires authentication)."""
        try:
            from health_monitor import get_system_status

            status = get_system_status()
            return jsonify(status), 200
        except Exception as exc:
            logging.error(f"Error getting system status: {exc}")
            return (
                jsonify({"error": str(exc), "message": "System status unavailable"}),
                500,
            )

    @monitoring_bp.route("/api/admin/system-status", methods=["GET"])
    @require_auth
    @require_admin
    def get_system_status_admin():
        """Get comprehensive system status for monitoring."""
        try:
            status = {
                "timestamp": datetime.utcnow().isoformat(),
                "rate_limits": get_rate_limit_status(),
                "job_queue": job_queue.get_queue_stats(),
                "database": get_database_health(),
                "security": get_security_status(),
                "backups": backup_manager.get_backup_status(),
            }
            return jsonify(status), 200
        except Exception as exc:
            logging.error(f"Error getting system status: {exc}")
            return jsonify({"error": "Failed to get system status"}), 500

    @monitoring_bp.route("/api/admin/job-queue/stats", methods=["GET"])
    @require_auth
    @require_admin
    def get_job_queue_stats():
        """Get detailed job queue statistics."""
        try:
            return jsonify(job_queue.get_queue_stats()), 200
        except Exception as exc:
            logging.error(f"Error getting job queue stats: {exc}")
            return jsonify({"error": "Failed to get job queue stats"}), 500

    @monitoring_bp.route("/api/jobs/<job_id>/status", methods=["GET"])
    @require_auth
    def get_job_status_api(job_id):
        """Get status of a specific job."""
        try:
            user_id = request.current_user["id"]

            status = job_queue.get_job_status(job_id)
            if not status:
                return jsonify({"error": "Job not found"}), 404

            user_jobs = job_queue.get_user_jobs(user_id)
            if not any(job["job_id"] == job_id for job in user_jobs):
                return jsonify({"error": "Access denied"}), 403

            return jsonify(status.to_dict()), 200

        except Exception as exc:
            logging.error(f"Error getting job status: {exc}")
            return jsonify({"error": str(exc)}), 500

    @monitoring_bp.route("/api/jobs/<job_id>/cancel", methods=["POST"])
    @require_auth
    def cancel_job_api(job_id):
        """Cancel a job."""
        try:
            user_id = request.current_user["id"]

            success = job_queue.cancel_job(job_id, user_id)
            if success:
                return (
                    jsonify({"success": True, "message": "Job cancelled successfully"}),
                    200,
                )
            return jsonify({"error": "Failed to cancel job or job not found"}), 400

        except Exception as exc:
            logging.error(f"Error cancelling job: {exc}")
            return jsonify({"error": str(exc)}), 500

    @monitoring_bp.route("/api/user/jobs", methods=["GET"])
    @require_auth
    def get_user_jobs_api():
        """Get all jobs for the current user."""
        try:
            user_id = request.current_user["id"]
            jobs = job_queue.get_user_jobs(user_id)
            return jsonify({"jobs": jobs}), 200

        except Exception as exc:
            logging.error(f"Error getting user jobs: {exc}")
            return jsonify({"error": str(exc)}), 500

    @monitoring_bp.route("/api/admin/backups", methods=["GET"])
    @require_auth
    @require_admin
    def list_backups_api():
        """List all available backups."""
        try:
            backup_type = request.args.get("type")
            backups = backup_manager.list_backups(backup_type)
            return jsonify({"backups": backups}), 200

        except Exception as exc:
            logging.error(f"Error listing backups: {exc}")
            return jsonify({"error": "Failed to list backups"}), 500

    @monitoring_bp.route("/api/admin/backups/create", methods=["POST"])
    @require_auth
    @require_admin
    def create_backup_api():
        """Create a new backup."""
        try:
            data = request.json or {}
            backup_type = data.get("type", "full")

            if backup_type == "full":
                result = run_full_backup()
            elif backup_type == "database":
                result = backup_manager.backup_database()
            elif backup_type == "files":
                result = backup_manager.backup_files()
            elif backup_type == "logs":
                result = backup_manager.backup_logs()
            else:
                return jsonify({"error": "Invalid backup type"}), 400

            return jsonify(result), (200 if result.get("success") else 500)

        except Exception as exc:
            logging.error(f"Error creating backup: {exc}")
            return jsonify({"error": "Failed to create backup"}), 500

    @monitoring_bp.route("/api/admin/backups/<backup_id>/restore", methods=["POST"])
    @require_auth
    @require_admin
    def restore_backup_api(backup_id):
        """Restore from a backup."""
        try:
            result = backup_manager.restore_database(backup_id)
            return jsonify(result), (200 if result.get("success") else 500)

        except Exception as exc:
            logging.error(f"Error restoring backup: {exc}")
            return jsonify({"error": "Failed to restore backup"}), 500

    @monitoring_bp.route("/api/admin/security/events", methods=["GET"])
    @require_auth
    @require_admin
    def get_security_events_api():
        """Get recent security events."""
        try:
            limit = request.args.get("limit", 50, type=int)
            events = security_manager.get_security_events(limit)
            return jsonify({"events": events}), 200

        except Exception as exc:
            logging.error(f"Error getting security events: {exc}")
            return jsonify({"error": "Failed to get security events"}), 500

    @monitoring_bp.route("/api/admin/security/audit", methods=["POST"])
    @require_auth
    @require_admin
    def run_security_audit_api():
        """Run security audit."""
        try:
            audit_results = security_manager.run_security_audit()
            return jsonify(audit_results), 200

        except Exception as exc:
            logging.error(f"Error running security audit: {exc}")
            return jsonify({"error": "Failed to run security audit"}), 500

    return monitoring_bp

