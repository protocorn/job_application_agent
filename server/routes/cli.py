import hashlib
import logging
import os
import re

from flask import Blueprint, jsonify, request

from auth import require_auth
from job_handlers import submit_job_with_validation
from job_queue import JobPriority
from rate_limiter import rate_limit


def create_cli_blueprint() -> Blueprint:
    """Create CLI-facing routes."""
    cli_bp = Blueprint("cli", __name__)

    @cli_bp.route("/api/cli/applications", methods=["GET"])
    @require_auth
    def cli_get_applications():
        """Return the user's application history (used by CLI)."""
        try:
            from database_config import JobApplication, SessionLocal

            user_id = request.current_user["id"]
            limit = min(int(request.args.get("limit", 50)), 200)
            urls_only = request.args.get("urls_only", "false").lower() == "true"
            db = SessionLocal()
            try:
                total_count = (
                    db.query(JobApplication).filter(JobApplication.user_id == user_id).count()
                )
                query = (
                    db.query(JobApplication)
                    .filter(JobApplication.user_id == user_id)
                    .order_by(JobApplication.created_at.desc())
                    .limit(limit)
                )
                apps = query.all()

                if urls_only:
                    return (
                        jsonify(
                            {
                                "success": True,
                                "total_count": total_count,
                                "urls": [
                                    a.job_url
                                    for a in apps
                                    if a.job_url
                                    and a.status in ("completed", "in_progress", "queued")
                                ],
                            }
                        ),
                        200,
                    )

                return (
                    jsonify(
                        {
                            "success": True,
                            "total_count": total_count,
                            "returned_count": len(apps),
                            "limit": limit,
                            "applications": [
                                {
                                    "id": str(a.id),
                                    "job_title": a.job_title,
                                    "company": a.company_name,
                                    "job_url": a.job_url,
                                    "status": a.status,
                                    "applied_at": (
                                        a.applied_at.isoformat() if a.applied_at else None
                                    ),
                                    "created_at": (
                                        a.created_at.isoformat() if a.created_at else None
                                    ),
                                }
                                for a in apps
                            ],
                        }
                    ),
                    200,
                )
            finally:
                db.close()
        except Exception as exc:
            logging.error(f"Error fetching CLI applications: {exc}")
            return jsonify({"error": "Failed to fetch applications"}), 500

    @cli_bp.route("/api/cli/applications", methods=["POST"])
    @require_auth
    def cli_record_application():
        """Record a completed job application from the CLI."""
        try:
            from datetime import datetime

            from database_config import JobApplication, SessionLocal

            user_id = request.current_user["id"]
            data = request.get_json() or {}

            job_url = (data.get("job_url") or "").strip()
            if not job_url:
                return jsonify({"error": "job_url is required"}), 400

            db = SessionLocal()
            try:
                application = JobApplication(
                    user_id=user_id,
                    job_id=f"cli_{datetime.utcnow().timestamp()}",
                    company_name=data.get("company", "Unknown Company"),
                    job_title=data.get("title", "Unknown Position"),
                    job_url=job_url,
                    status=data.get("status", "completed"),
                    applied_at=datetime.utcnow(),
                )
                db.add(application)
                db.commit()
                return jsonify({"success": True, "message": "Application recorded"}), 201
            finally:
                db.close()
        except Exception as exc:
            logging.error(f"Error recording CLI application: {exc}")
            return jsonify({"error": "Failed to record application"}), 500

    @cli_bp.route("/api/cli/user-field-overrides", methods=["POST"])
    @require_auth
    def cli_save_user_field_overrides():
        """
        Batch-upsert user field overrides captured by the HumanFillTracker.
        CLI only - the agent has no direct DB access in production.
        """
        try:
            from database_config import SessionLocal
            from sqlalchemy import text

            user_id = request.current_user["id"]
            data = request.get_json() or {}
            overrides = data.get("overrides", [])

            if not overrides:
                return jsonify({"saved": 0, "skipped": 0}), 200

            def _normalize_profile_field(value: str) -> str:
                pf = (value or "").strip().lower()
                pf = pf.replace("-", "_").replace(" ", "_").replace("/", "_")
                pf = re.sub(r"_+", "_", pf)
                pf = re.sub(r"\.\d+$", "", pf)
                aliases = {
                    "firstname": "first_name",
                    "first_name": "first_name",
                    "lastname": "last_name",
                    "last_name": "last_name",
                    "middlename": "middle_name",
                    "middle_name": "middle_name",
                    "preferredname": "preferred_name",
                    "preferred_name": "preferred_name",
                    "email_address": "email",
                    "e_mail": "email",
                    "phone_number": "phone",
                    "mobile_number": "mobile",
                    "cell_phone": "mobile",
                    "zip": "zip_code",
                    "zipcode": "zip_code",
                    "zip_code": "zip_code",
                    "postal": "postal_code",
                    "postalcode": "postal_code",
                    "post_code": "postal_code",
                    "linked_in": "linkedin",
                    "git_hub": "github",
                    "website_url": "website",
                    "portfolio_url": "portfolio",
                    "require_sponsorship": "sponsorship_required",
                    "sponsorship": "sponsorship_required",
                    "visa_sponsorship": "sponsorship_required",
                    "disability": "disability_status",
                    "disability_status": "disability_status",
                    "veteran": "veteran_status",
                    "veteran_status": "veteran_status",
                    "race_ethnicity": "race_ethnicity",
                    "authorized_to_work": "work_authorization",
                    "work_auth": "work_authorization",
                    "preferred_location": "preferred_locations",
                    "preferred_locations": "preferred_locations",
                    "relocate": "willing_to_relocate",
                    "willing_to_relocate": "willing_to_relocate",
                    "years_experience": "years_of_experience",
                    "yrs_experience": "years_of_experience",
                    "cover_letter_text": "cover_letter",
                }
                return aliases.get(pf, pf)

            def _infer_profile_field_from_label(label_norm: str) -> str:
                l = (label_norm or "").strip().lower()
                if not l:
                    return ""
                rules = [
                    ("first name", "first_name"),
                    ("last name", "last_name"),
                    ("full name", "full_name"),
                    ("middle name", "middle_name"),
                    ("preferred name", "preferred_name"),
                    ("email", "email"),
                    ("phone", "phone"),
                    ("mobile", "mobile"),
                    ("city", "city"),
                    ("state", "state"),
                    ("country", "country"),
                    ("zip", "zip_code"),
                    ("postal", "postal_code"),
                    ("linkedin", "linkedin"),
                    ("github", "github"),
                    ("portfolio", "portfolio"),
                    ("website", "website"),
                    ("veteran", "veteran_status"),
                    ("disability", "disability_status"),
                    ("gender", "gender"),
                    ("race", "race_ethnicity"),
                    ("ethnicity", "race_ethnicity"),
                    ("nationality", "nationality"),
                    ("visa", "visa_status"),
                    ("sponsorship", "sponsorship_required"),
                    ("work authorization", "work_authorization"),
                    ("authorized to work", "work_authorization"),
                    ("relocate", "willing_to_relocate"),
                    ("preferred location", "preferred_locations"),
                    ("location preference", "preferred_locations"),
                    ("years of experience", "years_of_experience"),
                    ("cover letter", "cover_letter"),
                ]
                for needle, mapped in rules:
                    if needle in l:
                        return mapped
                return ""

            valid_global_fields = {
                "first_name",
                "last_name",
                "full_name",
                "middle_name",
                "preferred_name",
                "email",
                "phone",
                "mobile",
                "address",
                "address_line_1",
                "address_line_2",
                "city",
                "state",
                "country",
                "zip_code",
                "postal_code",
                "linkedin",
                "github",
                "portfolio",
                "website",
                "twitter",
                "visa_status",
                "work_authorization",
                "sponsorship_required",
                "require_sponsorship",
                "veteran_status",
                "disability_status",
                "gender",
                "gender_identity",
                "race",
                "ethnicity",
                "race_ethnicity",
                "nationality",
                "pronouns",
                "willing_to_relocate",
                "remote_preference",
                "preferred_locations",
                "start_date",
                "notice_period",
                "salary_range",
                "highest_education",
                "degree",
                "major",
                "gpa",
                "years_of_experience",
                "current_title",
                "current_company",
                "cover_letter",
                "hear_about_us",
                "referral_source",
            }

            db = SessionLocal()
            saved = 0
            skipped = 0
            promoted_patterns = 0
            try:
                for entry in overrides:
                    raw_label = (entry.get("field_label_raw") or "").strip()
                    label_norm = (entry.get("field_label_normalized") or "").strip()
                    if not label_norm and raw_label:
                        label_norm = re.sub(
                            r"\s+",
                            " ",
                            re.sub(r"[^a-z0-9\s]", "", raw_label.lower()),
                        ).strip()
                    label_raw = (entry.get("field_label_raw") or "").strip()
                    value = (entry.get("field_value_cached") or "").strip()
                    category = entry.get("field_category", "text_input")
                    source = entry.get("source", "human_fill")
                    was_ai = bool(entry.get("was_ai_attempted", True))
                    confidence = float(entry.get("confidence_score", 0.95))
                    site_domain = (entry.get("site_domain") or "").strip().lower() or None
                    profile_fld = (entry.get("profile_field") or "").strip() or None
                    profile_fld = _normalize_profile_field(
                        profile_fld or _infer_profile_field_from_label(label_norm)
                    ) or None

                    if not label_norm or not value:
                        skipped += 1
                        continue

                    existing = db.execute(
                        text(
                            """
                    SELECT id, success_count, failure_count, occurrence_count
                    FROM user_field_overrides
                    WHERE user_id = :uid
                      AND LOWER(field_label_normalized) = LOWER(:label)
                      AND LOWER(COALESCE(site_domain, '')) = LOWER(COALESCE(:domain, ''))
                """
                        ),
                        {"uid": str(user_id), "label": label_norm, "domain": site_domain},
                    ).first()

                    if existing:
                        row_id = existing[0]
                        new_succ = (existing[1] or 0) + 1
                        new_fail = existing[2] or 0
                        new_occ = (existing[3] or 0) + 1
                        total = new_succ + new_fail
                        new_conf = (
                            round(min(0.99, (new_succ / total) + min(0.05, new_occ / 200)), 2)
                            if total > 0
                            else confidence
                        )
                        db.execute(
                            text(
                                """
                        UPDATE user_field_overrides
                        SET field_value_cached = :value,
                            field_category     = :cat,
                            profile_field      = COALESCE(:pf, profile_field),
                            source             = :source,
                            occurrence_count   = :occ,
                            success_count      = :succ,
                            failure_count      = :fail,
                            confidence_score   = :conf,
                            last_seen          = NOW()
                        WHERE id = :id
                    """
                            ),
                            {
                                "id": row_id,
                                "value": value,
                                "cat": category,
                                "pf": profile_fld,
                                "source": source,
                                "occ": new_occ,
                                "succ": new_succ,
                                "fail": new_fail,
                                "conf": new_conf,
                            },
                        )
                    else:
                        db.execute(
                            text(
                                """
                        INSERT INTO user_field_overrides
                        (user_id, field_label_normalized, field_label_raw,
                         profile_field, field_value_cached, field_category,
                         source, was_ai_attempted, confidence_score,
                         occurrence_count, success_count, failure_count,
                         site_domain, created_at, last_seen)
                        VALUES
                        (:uid, :label, :label_raw,
                         :pf, :value, :cat,
                         :source, :was_ai, :conf,
                         1, 1, 0,
                         :domain, NOW(), NOW())
                    """
                            ),
                            {
                                "uid": str(user_id),
                                "label": label_norm,
                                "label_raw": label_raw,
                                "pf": profile_fld,
                                "value": value,
                                "cat": category,
                                "source": source,
                                "was_ai": was_ai,
                                "conf": confidence,
                                "domain": site_domain,
                            },
                        )
                    saved += 1

                    if (
                        profile_fld
                        and profile_fld in valid_global_fields
                        and "[" not in profile_fld
                        and "(" not in profile_fld
                        and "." not in profile_fld
                    ):
                        p_existing = db.execute(
                            text(
                                """
                        SELECT id, success_count, failure_count, occurrence_count
                        FROM field_label_patterns
                        WHERE LOWER(field_label_normalized) = LOWER(:label)
                          AND LOWER(profile_field) = LOWER(:profile_field)
                    """
                            ),
                            {"label": label_norm, "profile_field": profile_fld},
                        ).first()
                        if p_existing:
                            p_row_id = p_existing[0]
                            p_succ = (p_existing[1] or 0) + 1
                            p_fail = p_existing[2] or 0
                            p_occ = (p_existing[3] or 0) + 1
                            p_total = p_succ + p_fail
                            p_conf = (
                                round(min(0.99, (p_succ / p_total) + min(0.1, p_occ / 100)), 2)
                                if p_total > 0
                                else 0.85
                            )
                            db.execute(
                                text(
                                    """
                            UPDATE field_label_patterns
                            SET occurrence_count = :occ,
                                success_count = :succ,
                                failure_count = :fail,
                                confidence_score = :conf,
                                last_seen = NOW()
                            WHERE id = :id
                        """
                                ),
                                {
                                    "id": p_row_id,
                                    "occ": p_occ,
                                    "succ": p_succ,
                                    "fail": p_fail,
                                    "conf": p_conf,
                                },
                            )
                        else:
                            db.execute(
                                text(
                                    """
                            INSERT INTO field_label_patterns
                            (field_label_normalized, field_label_raw, profile_field, field_category,
                             confidence_score, occurrence_count, success_count, failure_count,
                             created_by_user_id, source)
                            VALUES
                            (:label_norm, :label_raw, :profile_field, :category,
                             0.85, 1, 1, 0, :user_id, 'human_fill')
                        """
                                ),
                                {
                                    "label_norm": label_norm,
                                    "label_raw": label_raw or label_norm,
                                    "profile_field": profile_fld,
                                    "category": category,
                                    "user_id": str(user_id),
                                },
                            )
                        promoted_patterns += 1

                db.commit()
                logging.info(
                    f"CLI user-field-overrides: saved={saved} skipped={skipped} "
                    f"promoted_patterns={promoted_patterns} user={user_id}"
                )
                return (
                    jsonify(
                        {
                            "saved": saved,
                            "skipped": skipped,
                            "promoted_patterns": promoted_patterns,
                        }
                    ),
                    201,
                )

            except Exception as inner_e:
                db.rollback()
                raise inner_e
            finally:
                db.close()

        except Exception as exc:
            logging.error(f"Error saving user field overrides: {exc}")
            return jsonify({"error": "Failed to save overrides"}), 500

    @cli_bp.route("/api/cli/field-label-patterns", methods=["POST"])
    @require_auth
    def cli_save_field_label_patterns():
        """Batch-upsert global field label patterns learned by local agents."""
        try:
            from database_config import SessionLocal
            from sqlalchemy import text

            user_id = request.current_user["id"]
            data = request.get_json() or {}
            patterns = data.get("patterns", [])
            if not patterns:
                return jsonify({"saved": 0, "skipped": 0}), 200

            def _normalize_label(value: str) -> str:
                norm = (value or "").strip().lower()
                norm = re.sub(r"[^a-z0-9\s]", "", norm)
                norm = re.sub(r"\s+", " ", norm).strip()
                return norm

            def _normalize_profile_field(value: str) -> str:
                pf = (value or "").strip().lower()
                pf = pf.replace("-", "_").replace(" ", "_").replace("/", "_")
                pf = re.sub(r"_+", "_", pf)
                pf = re.sub(r"\.\d+$", "", pf)
                aliases = {
                    "firstname": "first_name",
                    "first_name": "first_name",
                    "lastname": "last_name",
                    "last_name": "last_name",
                    "middlename": "middle_name",
                    "middle_name": "middle_name",
                    "preferredname": "preferred_name",
                    "preferred_name": "preferred_name",
                    "email_address": "email",
                    "e_mail": "email",
                    "phone_number": "phone",
                    "mobile_number": "mobile",
                    "cell_phone": "mobile",
                    "zip": "zip_code",
                    "zipcode": "zip_code",
                    "zip_code": "zip_code",
                    "postal": "postal_code",
                    "postalcode": "postal_code",
                    "post_code": "postal_code",
                    "linked_in": "linkedin",
                    "git_hub": "github",
                    "website_url": "website",
                    "portfolio_url": "portfolio",
                    "require_sponsorship": "sponsorship_required",
                    "sponsorship": "sponsorship_required",
                    "visa_sponsorship": "sponsorship_required",
                    "disability": "disability_status",
                    "disability_status": "disability_status",
                    "veteran": "veteran_status",
                    "veteran_status": "veteran_status",
                    "race_ethnicity": "race_ethnicity",
                    "authorized_to_work": "work_authorization",
                    "work_auth": "work_authorization",
                    "preferred_location": "preferred_locations",
                    "preferred_locations": "preferred_locations",
                    "relocate": "willing_to_relocate",
                    "willing_to_relocate": "willing_to_relocate",
                    "years_experience": "years_of_experience",
                    "yrs_experience": "years_of_experience",
                    "current_job_title": "current_title",
                    "current_employer": "current_company",
                    "cover_letter_text": "cover_letter",
                    "hear_about": "hear_about_us",
                    "referral": "referral_source",
                }
                return aliases.get(pf, pf)

            valid_global_fields = {
                "first_name",
                "last_name",
                "full_name",
                "middle_name",
                "preferred_name",
                "email",
                "phone",
                "mobile",
                "address",
                "address_line_1",
                "address_line_2",
                "city",
                "state",
                "country",
                "zip_code",
                "postal_code",
                "linkedin",
                "github",
                "portfolio",
                "website",
                "twitter",
                "visa_status",
                "work_authorization",
                "sponsorship_required",
                "require_sponsorship",
                "veteran_status",
                "disability_status",
                "gender",
                "gender_identity",
                "race",
                "ethnicity",
                "race_ethnicity",
                "nationality",
                "pronouns",
                "willing_to_relocate",
                "remote_preference",
                "preferred_locations",
                "start_date",
                "notice_period",
                "salary_range",
                "highest_education",
                "degree",
                "major",
                "gpa",
                "years_of_experience",
                "current_title",
                "current_company",
                "cover_letter",
                "hear_about_us",
                "referral_source",
            }

            db = SessionLocal()
            saved = 0
            skipped = 0
            try:
                for entry in patterns:
                    label_raw = (entry.get("field_label_raw") or "").strip()
                    label_norm = _normalize_label(
                        entry.get("field_label_normalized") or label_raw
                    )
                    profile_field = _normalize_profile_field(entry.get("profile_field") or "")
                    field_category = (entry.get("field_category") or "text_input").strip()
                    success = bool(entry.get("success", True))

                    if not label_norm or not profile_field:
                        skipped += 1
                        continue
                    if (
                        "[" in profile_field
                        or "(" in profile_field
                        or "." in profile_field
                        or profile_field not in valid_global_fields
                    ):
                        skipped += 1
                        continue

                    existing = db.execute(
                        text(
                            """
                    SELECT id, success_count, failure_count, occurrence_count
                    FROM field_label_patterns
                    WHERE LOWER(field_label_normalized) = LOWER(:label)
                      AND LOWER(profile_field) = LOWER(:profile_field)
                """
                        ),
                        {"label": label_norm, "profile_field": profile_field},
                    ).first()

                    if existing:
                        row_id = existing[0]
                        new_succ = (existing[1] or 0) + (1 if success else 0)
                        new_fail = (existing[2] or 0) + (0 if success else 1)
                        new_occ = (existing[3] or 0) + 1
                        total = new_succ + new_fail
                        new_conf = (
                            round(min(0.99, (new_succ / total) + min(0.1, new_occ / 100)), 2)
                            if total > 0
                            else 0.0
                        )
                        db.execute(
                            text(
                                """
                        UPDATE field_label_patterns
                        SET occurrence_count = :occ,
                            success_count = :succ,
                            failure_count = :fail,
                            confidence_score = :conf,
                            last_seen = NOW()
                        WHERE id = :id
                    """
                            ),
                            {
                                "id": row_id,
                                "occ": new_occ,
                                "succ": new_succ,
                                "fail": new_fail,
                                "conf": new_conf,
                            },
                        )
                    else:
                        init_conf = 0.85 if success else 0.0
                        db.execute(
                            text(
                                """
                        INSERT INTO field_label_patterns
                        (field_label_normalized, field_label_raw, profile_field, field_category,
                         confidence_score, occurrence_count, success_count, failure_count,
                         created_by_user_id, source)
                        VALUES
                        (:label_norm, :label_raw, :profile_field, :category,
                         :confidence, 1, :success_count, :failure_count,
                         :user_id, 'gemini_ai')
                    """
                            ),
                            {
                                "label_norm": label_norm,
                                "label_raw": label_raw or label_norm,
                                "profile_field": profile_field,
                                "category": field_category,
                                "confidence": init_conf,
                                "success_count": 1 if success else 0,
                                "failure_count": 0 if success else 1,
                                "user_id": str(user_id),
                            },
                        )
                    saved += 1

                db.commit()
                logging.info(
                    f"CLI field-label-patterns: saved={saved} skipped={skipped} user={user_id}"
                )
                return jsonify({"saved": saved, "skipped": skipped}), 201

            except Exception:
                db.rollback()
                raise
            finally:
                db.close()
        except Exception as exc:
            logging.error(f"Error saving field label patterns: {exc}")
            return jsonify({"error": "Failed to save field label patterns"}), 500

    @cli_bp.route("/api/cli/agent-key", methods=["GET"])
    @require_auth
    def get_cli_agent_key():
        """
        Return runtime key for local agent decryption (authenticated CLI only).
        """
        runtime_key = (os.getenv("AGENT_RUNTIME_KEY") or "").strip()
        if not runtime_key:
            logging.error(
                "CLI agent key request failed: AGENT_RUNTIME_KEY is not configured"
            )
            return (
                jsonify({"error": "AGENT_RUNTIME_KEY is not configured on the server."}),
                500,
            )

        gemini_key = (os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY") or "").strip()
        shared_gemini_configured = bool(gemini_key)
        return (
            jsonify(
                {
                    "key": runtime_key,
                    "runtime_key_configured": True,
                    "gemini_key": gemini_key,
                    "shared_gemini_configured": shared_gemini_configured,
                }
            ),
            200,
        )

    @cli_bp.route("/api/cli/apply", methods=["POST"])
    @require_auth
    @rate_limit("api_requests_per_user_per_minute")
    def cli_submit_apply():
        """Submit a job application job to the server-side queue (CLI endpoint)."""
        try:
            import uuid as uuid_module

            from database_config import SessionLocal, UserProfile

            user_id = request.current_user["id"]
            data = request.get_json() or {}

            job_url = (data.get("job_url") or "").strip()
            if not job_url:
                return jsonify({"error": "job_url is required"}), 400

            normalized_job_url = job_url.rstrip("/").lower()
            dedupe_hash = hashlib.sha256(
                f"{user_id}:{normalized_job_url}".encode("utf-8")
            ).hexdigest()
            dedupe_key = f"cli_apply_dedupe:{dedupe_hash}"
            try:
                from rate_limiter import redis_client as _rc

                if not _rc.set(dedupe_key, "1", ex=120, nx=True):
                    return (
                        jsonify(
                            {
                                "error": (
                                    "Duplicate apply request detected. Please wait 2 minutes "
                                    "before retrying this URL."
                                )
                            }
                        ),
                        409,
                    )
            except Exception:
                pass

            tailor_resume_flag = data.get("tailor_resume", False)

            db = SessionLocal()
            try:
                user_uuid = uuid_module.UUID(str(user_id))
                profile = (
                    db.query(UserProfile).filter(UserProfile.user_id == user_uuid).first()
                )
                resume_url = (profile.resume_url or "").strip() if profile else ""
            finally:
                db.close()

            if not resume_url:
                return (
                    jsonify(
                        {"error": "No resume URL found in your profile. Please add one first."}
                    ),
                    400,
                )

            payload = {
                "job_url": job_url,
                "resume_url": resume_url,
                "use_tailored": tailor_resume_flag,
                "replace_projects_on_tailor": bool(
                    data.get("replace_projects_on_tailor", False)
                ),
            }

            result = submit_job_with_validation(
                user_id=user_id,
                job_type="job_application",
                payload=payload,
                priority=JobPriority.NORMAL,
            )

            if result["success"]:
                return (
                    jsonify(
                        {
                            "success": True,
                            "job_id": result["job_id"],
                            "message": "Job application submitted to queue.",
                        }
                    ),
                    202,
                )
            return jsonify({"error": result["error"], "success": False}), 400

        except Exception as exc:
            logging.error(f"Error submitting CLI apply job: {exc}")
            return jsonify({"error": str(exc)}), 500

    @cli_bp.route("/api/cli/ats-confidence", methods=["GET"])
    @require_auth
    def cli_get_ats_confidence():
        """
        Pre-run ATS compatibility check for a job URL.

        Returns the detected ATS platform, a baseline confidence tier derived
        from URL pattern matching, and — when the user has prior runs on the
        same domain — a personalised confidence score pulled from their stored
        human-fill corrections.

        Query params:
            url   (required) — the job application URL
        """
        try:
            from urllib.parse import urlsplit
            from database_config import SessionLocal
            from sqlalchemy import text

            url = request.args.get("url", "").strip()
            if not url:
                return jsonify({"success": False, "error": "url parameter required"}), 400

            user_id = request.current_user["id"]

            # ── 1. Detect ATS type from URL ───────────────────────────────
            try:
                netloc = urlsplit(url).netloc.lower()
            except Exception:
                netloc = ""

            # Ordered from most- to least-specific pattern.
            # tier: "supported" | "partial" | "unsupported"
            ATS_PATTERNS = [
                # Tier 1 — fully supported
                ("Greenhouse",        ["job-boards.greenhouse.io", "boards.greenhouse.io"],            "supported"),
                ("Lever",             ["jobs.lever.co"],                                               "supported"),
                ("Ashby",             ["jobs.ashbyhq.com"],                                            "supported"),
                # Tier 2 — partial support
                ("Workday",           ["myworkdayjobs.com", "wd1.myworkdayjobs.com",
                                       "wd3.myworkdayjobs.com", "wd5.myworkdayjobs.com"],              "partial"),
                ("SmartRecruiters",   ["careers.smartrecruiters.com", "smartrecruiters.com"],          "partial"),
                ("Rippling ATS",      ["ats.rippling.com"],                                            "partial"),
                ("BambooHR",          ["app.bamboohr.com"],                                            "partial"),
                ("JazzHR",            ["app.jazz.co"],                                                 "partial"),
                ("Recruitee",         ["jobs.recruitee.com"],                                          "partial"),
                ("Breezy HR",         ["breezy.hr"],                                                   "partial"),
                ("Teamtailor",        ["teamtailor.com"],                                              "partial"),
                ("Personio",          ["personio.de", "personio.com"],                                 "partial"),
                ("Pinpoint",          ["pinpointhq.com"],                                              "partial"),
                # Tier 3 — not well supported
                ("Taleo (Oracle)",    ["taleo.net"],                                                   "unsupported"),
                ("iCIMS",             ["icims.com", "careers.icims.com"],                              "unsupported"),
                ("SAP SuccessFactors",["successfactors.com", "successfactors.eu"],                     "unsupported"),
                ("Comeet",            ["comeet.com", "careers.comeet.com"],                            "unsupported"),
            ]

            ats_name = "Company / Custom Site"
            ats_tier = "unsupported"
            for name, patterns, tier in ATS_PATTERNS:
                if any(p in netloc for p in patterns):
                    ats_name = name
                    ats_tier = tier
                    break

            tier_confidence = {"supported": 0.85, "partial": 0.55, "unsupported": 0.25}
            baseline_confidence = tier_confidence[ats_tier]

            # Readable domain for DB lookup (last two parts of netloc)
            parts = netloc.split(".")
            short_domain = ".".join(parts[-2:]) if len(parts) >= 2 else netloc

            # ── 2. Personalised score from user_field_overrides ───────────
            personal_data = {
                "total_fields_learned": 0,
                "human_fills": 0,
                "human_corrections": 0,
                "avg_confidence": None,
                "total_apps_attempted": 0,
                "completed_apps": 0,
            }
            db = SessionLocal()
            try:
                fo_row = db.execute(text("""
                    SELECT
                        COUNT(*)                                                        AS total_fields,
                        ROUND(AVG(confidence_score)::numeric, 2)                       AS avg_conf,
                        SUM(CASE WHEN source = 'human_fill'       THEN 1 ELSE 0 END)  AS fills,
                        SUM(CASE WHEN source = 'human_correction' THEN 1 ELSE 0 END)  AS corrections
                    FROM user_field_overrides
                    WHERE user_id = :uid
                      AND (site_domain = :domain OR site_domain LIKE :domain_like)
                """), {
                    "uid": str(user_id),
                    "domain": short_domain,
                    "domain_like": f"%{short_domain}",
                }).fetchone()

                if fo_row and fo_row[0]:
                    personal_data["total_fields_learned"] = int(fo_row[0])
                    personal_data["avg_confidence"]       = float(fo_row[1]) if fo_row[1] else None
                    personal_data["human_fills"]          = int(fo_row[2] or 0)
                    personal_data["human_corrections"]    = int(fo_row[3] or 0)

                app_row = db.execute(text("""
                    SELECT
                        COUNT(*)                                                             AS total,
                        SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END)               AS completed
                    FROM job_applications
                    WHERE user_id = :uid
                      AND job_url LIKE :domain_pattern
                """), {
                    "uid": str(user_id),
                    "domain_pattern": f"%{short_domain}%",
                }).fetchone()

                if app_row:
                    personal_data["total_apps_attempted"] = int(app_row[0] or 0)
                    personal_data["completed_apps"]       = int(app_row[1] or 0)

            except Exception as db_err:
                logging.warning(f"ATS confidence DB query failed: {db_err}")
            finally:
                db.close()

            # ── 3. Blend baseline + personal history ─────────────────────
            # If the user has personal data, weight it heavily.
            n = personal_data["total_fields_learned"]
            if n > 0 and personal_data["avg_confidence"] is not None:
                personal_weight = min(n / 10.0, 1.0)   # saturates at 10+ fields
                final_confidence = round(
                    baseline_confidence * (1 - personal_weight)
                    + personal_data["avg_confidence"] * personal_weight,
                    2,
                )
            else:
                final_confidence = baseline_confidence

            return jsonify({
                "success":            True,
                "url":                url,
                "domain":             short_domain,
                "ats_name":           ats_name,
                "ats_tier":           ats_tier,
                "baseline_confidence": baseline_confidence,
                "final_confidence":   final_confidence,
                "personal":           personal_data,
            }), 200

        except Exception as exc:
            logging.error(f"Error in ats-confidence endpoint: {exc}")
            return jsonify({"success": False, "error": str(exc)}), 500

    return cli_bp

