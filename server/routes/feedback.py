import logging
from datetime import datetime
from typing import Callable

from flask import Blueprint, jsonify, request

from auth import require_admin, require_auth
from bug_bounty import (
    SEVERITY_REWARD_MAP,
    build_dedupe_key,
    get_reward_for_severity,
    normalize_severity,
    validate_bug_report_payload,
)


def create_feedback_blueprint(
    *, invalidate_credits_cache: Callable[[str], None]
) -> Blueprint:
    """Create feedback and admin moderation routes."""
    feedback_bp = Blueprint("feedback", __name__)

    @feedback_bp.route("/api/feedback/bug-report", methods=["POST"])
    @require_auth
    def submit_bug_report():
        """Submit a structured beta bug report for admin review."""
        try:
            from database_config import BugReport, SessionLocal

            user_id = request.current_user["id"]
            user_email = request.current_user.get("email", "")
            data = request.get_json() or {}

            is_valid, validation_error = validate_bug_report_payload(data)
            if not is_valid:
                return jsonify({"error": validation_error}), 400

            dedupe_key = build_dedupe_key(
                str(user_id),
                data.get("title", ""),
                data.get("steps_to_reproduce", ""),
                data.get("actual_behavior", ""),
            )

            db = SessionLocal()
            try:
                existing = (
                    db.query(BugReport)
                    .filter(BugReport.user_id == user_id, BugReport.dedupe_key == dedupe_key)
                    .first()
                )
                if existing:
                    return (
                        jsonify(
                            {
                                "success": True,
                                "message": "A similar report is already under review.",
                                "report_id": existing.id,
                                "status": existing.status,
                                "duplicate": True,
                            }
                        ),
                        200,
                    )

                severity = normalize_severity(data.get("severity"))
                report = BugReport(
                    user_id=user_id,
                    user_email=user_email,
                    title=(data.get("title") or "").strip(),
                    summary=(data.get("summary") or "").strip(),
                    steps_to_reproduce=(data.get("steps_to_reproduce") or "").strip(),
                    expected_behavior=(data.get("expected_behavior") or "").strip(),
                    actual_behavior=(data.get("actual_behavior") or "").strip(),
                    environment=(data.get("environment") or "").strip(),
                    attachments_or_logs=(data.get("attachments_or_logs") or "").strip()
                    or None,
                    suggested_fix=(data.get("suggested_fix") or "").strip() or None,
                    severity=severity,
                    status="pending",
                    dedupe_key=dedupe_key,
                )
                db.add(report)
                db.commit()
                db.refresh(report)

                return (
                    jsonify(
                        {
                            "success": True,
                            "message": "Bug report submitted successfully. We will review it shortly.",
                            "report_id": report.id,
                            "status": report.status,
                        }
                    ),
                    201,
                )
            finally:
                db.close()
        except Exception as exc:
            logging.error(f"Error submitting bug report: {exc}")
            return jsonify({"error": "Failed to submit bug report"}), 500

    @feedback_bp.route("/api/admin/feedback/bug-reports", methods=["GET"])
    @require_auth
    @require_admin
    def list_bug_reports():
        """List bug reports for admin moderation with optional status filter."""
        try:
            from database_config import BugReport, SessionLocal

            status_filter = (request.args.get("status") or "pending").strip().lower()
            severity_filter = (request.args.get("severity") or "").strip().lower()
            limit = min(max(int(request.args.get("limit", 100)), 1), 500)

            db = SessionLocal()
            try:
                query = db.query(BugReport)
                if status_filter and status_filter != "all":
                    query = query.filter(BugReport.status == status_filter)
                if severity_filter and severity_filter in SEVERITY_REWARD_MAP:
                    query = query.filter(BugReport.severity == severity_filter)

                reports = query.order_by(BugReport.submitted_at.desc()).limit(limit).all()
                return (
                    jsonify(
                        {
                            "success": True,
                            "reports": [
                                {
                                    "id": report.id,
                                    "user_id": str(report.user_id),
                                    "user_email": report.user_email,
                                    "title": report.title,
                                    "summary": report.summary,
                                    "steps_to_reproduce": report.steps_to_reproduce,
                                    "expected_behavior": report.expected_behavior,
                                    "actual_behavior": report.actual_behavior,
                                    "environment": report.environment,
                                    "attachments_or_logs": report.attachments_or_logs,
                                    "suggested_fix": report.suggested_fix,
                                    "severity": report.severity,
                                    "status": report.status,
                                    "admin_notes": report.admin_notes,
                                    "rejection_reason": report.rejection_reason,
                                    "reward_resume_bonus": report.reward_resume_bonus,
                                    "reward_job_apply_bonus": report.reward_job_apply_bonus,
                                    "cash_reward_amount": report.cash_reward_amount,
                                    "cash_reward_note": report.cash_reward_note,
                                    "submitted_at": (
                                        report.submitted_at.isoformat()
                                        if report.submitted_at
                                        else None
                                    ),
                                    "processed_at": (
                                        report.processed_at.isoformat()
                                        if report.processed_at
                                        else None
                                    ),
                                }
                                for report in reports
                            ],
                        }
                    ),
                    200,
                )
            finally:
                db.close()
        except Exception as exc:
            logging.error(f"Error listing bug reports: {exc}")
            return jsonify({"error": "Failed to fetch bug reports"}), 500

    @feedback_bp.route("/api/admin/feedback/bug-reports/<int:report_id>/approve", methods=["POST"])
    @require_auth
    @require_admin
    def approve_bug_report(report_id: int):
        """Approve a bug report and grant permanent per-user bonus limits."""
        try:
            import uuid as uuid_module

            from database_config import BugReport, SessionLocal, User
            from email_service import email_service

            data = request.get_json() or {}
            admin_notes = (data.get("admin_notes") or "").strip() or None
            selected_severity = normalize_severity(data.get("severity"))

            db = SessionLocal()
            try:
                report = db.query(BugReport).filter(BugReport.id == report_id).first()
                if not report:
                    return jsonify({"error": "Bug report not found"}), 404

                if report.status == "approved" and report.reward_applied_at:
                    return (
                        jsonify(
                            {
                                "success": True,
                                "message": "Bug report already approved. Reward was already applied.",
                                "already_processed": True,
                            }
                        ),
                        200,
                    )
                if report.status == "rejected":
                    return (
                        jsonify(
                            {
                                "error": "This bug report was already rejected and cannot be approved."
                            }
                        ),
                        409,
                    )

                report.severity = selected_severity
                reward = get_reward_for_severity(selected_severity)
                reward_resume = int(reward["resume_bonus"])
                reward_apply = int(reward["job_apply_bonus"])

                user = db.query(User).filter(User.id == report.user_id).first()
                if not user:
                    return jsonify({"error": "Report owner not found"}), 404

                user.bonus_resume_tailoring_max = int(user.bonus_resume_tailoring_max or 0) + reward_resume
                user.bonus_job_applications_max = int(user.bonus_job_applications_max or 0) + reward_apply

                report.status = "approved"
                report.admin_notes = admin_notes
                report.rejection_reason = None
                report.reward_resume_bonus = reward_resume
                report.reward_job_apply_bonus = reward_apply
                report.cash_reward_amount = data.get("cash_reward_amount")
                report.cash_reward_note = (data.get("cash_reward_note") or "").strip() or None
                report.reward_applied_at = datetime.utcnow()
                report.processed_at = datetime.utcnow()
                report.processed_by_admin_id = uuid_module.UUID(str(request.current_user["id"]))

                db.commit()

                invalidate_credits_cache(str(report.user_id))

                try:
                    email_service.send_bug_report_approved_email(
                        to_email=user.email,
                        first_name=user.first_name,
                        report_title=report.title,
                        severity=selected_severity,
                        reward_resume_bonus=reward_resume,
                        reward_job_apply_bonus=reward_apply,
                    )
                except Exception as email_error:
                    logging.error(f"Failed to send bug report approval email: {email_error}")

                return (
                    jsonify(
                        {
                            "success": True,
                            "message": "Bug report approved and reward applied.",
                            "reward": {
                                "severity": selected_severity,
                                "resume_bonus": reward_resume,
                                "job_applications_bonus": reward_apply,
                            },
                        }
                    ),
                    200,
                )
            finally:
                db.close()
        except Exception as exc:
            logging.error(f"Error approving bug report: {exc}")
            return jsonify({"error": "Failed to approve bug report"}), 500

    @feedback_bp.route("/api/admin/feedback/bug-reports/<int:report_id>/reject", methods=["POST"])
    @require_auth
    @require_admin
    def reject_bug_report(report_id: int):
        """Reject a bug report with explicit reason and notify the reporter."""
        try:
            import uuid as uuid_module

            from database_config import BugReport, SessionLocal, User
            from email_service import email_service

            data = request.get_json() or {}
            rejection_reason = (data.get("rejection_reason") or "").strip()
            admin_notes = (data.get("admin_notes") or "").strip() or None
            if not rejection_reason:
                return jsonify({"error": "rejection_reason is required"}), 400

            db = SessionLocal()
            try:
                report = db.query(BugReport).filter(BugReport.id == report_id).first()
                if not report:
                    return jsonify({"error": "Bug report not found"}), 404

                if report.status == "approved":
                    return jsonify({"error": "Approved report cannot be rejected."}), 409
                if report.status == "rejected":
                    return (
                        jsonify(
                            {
                                "success": True,
                                "message": "Bug report already rejected.",
                                "already_processed": True,
                            }
                        ),
                        200,
                    )

                report.status = "rejected"
                report.rejection_reason = rejection_reason
                report.admin_notes = admin_notes
                report.processed_at = datetime.utcnow()
                report.processed_by_admin_id = uuid_module.UUID(str(request.current_user["id"]))

                db.commit()

                user = db.query(User).filter(User.id == report.user_id).first()
                if user:
                    try:
                        email_service.send_bug_report_rejected_email(
                            to_email=user.email,
                            first_name=user.first_name,
                            report_title=report.title,
                            rejection_reason=rejection_reason,
                        )
                    except Exception as email_error:
                        logging.error(f"Failed to send bug report rejection email: {email_error}")

                return jsonify({"success": True, "message": "Bug report rejected."}), 200
            finally:
                db.close()
        except Exception as exc:
            logging.error(f"Error rejecting bug report: {exc}")
            return jsonify({"error": "Failed to reject bug report"}), 500

    return feedback_bp

