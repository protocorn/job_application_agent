import logging
from typing import Any, Callable, Dict, Tuple

from flask import Blueprint, jsonify, request

from auth import require_auth
from google_oauth_service import GoogleOAuthService
from job_handlers import submit_job_with_validation
from job_queue import JobPriority, job_queue
from profile_service import ProfileService
from profile_strength import score_profile_strength
from rate_limiter import rate_limit, rate_limiter
from security_manager import validate_input


def create_jobs_blueprint(
    *,
    get_user_and_limit: Callable[[str, str], Tuple[Any, int]],
    invalidate_credits_cache: Callable[[str], None],
) -> Blueprint:
    """Create job search/credits/tailoring routes."""
    jobs_bp = Blueprint("jobs", __name__)

    @jobs_bp.route("/api/search-jobs", methods=["POST"])
    @require_auth
    @rate_limit("job_search_per_user_per_day")
    def search_jobs():
        """Search for jobs via multi-source discovery agent."""
        try:
            from Agents.multi_source_job_discovery_agent import MultiSourceJobDiscoveryAgent

            user_id = request.current_user["id"]
            invalidate_credits_cache(str(user_id))
            data = request.json or {}

            min_relevance_score = data.get("min_relevance_score", 30)
            keywords = data.get("keywords")
            location = data.get("location")
            remote = data.get("remote", False)
            easy_apply = data.get("easy_apply", False)
            hours_old = data.get("hours_old")

            job_discovery_agent = MultiSourceJobDiscoveryAgent(user_id=user_id)
            if not job_discovery_agent.profile_data:
                return jsonify({"error": "Profile data not found for this user"}), 400

            logging.info(
                f"Searching for jobs (keywords={keywords}, min_relevance={min_relevance_score})..."
            )

            if keywords:
                search_overrides = {"easy_apply": easy_apply}
                if hours_old:
                    search_overrides["hours_old"] = hours_old
                result = job_discovery_agent.search_all_sources(
                    min_relevance_score=min_relevance_score,
                    manual_keywords=keywords,
                    manual_location=location or None,
                    manual_remote=remote,
                    manual_search_overrides=search_overrides,
                )
                if "error" in result:
                    return jsonify({"error": result["error"]}), 500
                jobs_data = result.get("data", [])
                sources = result.get("sources", {})
                avg_score = result.get("average_score", 0)
                saved_count = 0
                updated_count = 0
            else:
                response = job_discovery_agent.search_and_save(
                    min_relevance_score=min_relevance_score
                )
                if "error" in response:
                    return jsonify({"error": response["error"]}), 500
                jobs_data = response.get("jobs", [])
                sources = response.get("sources", {})
                avg_score = response.get("average_score", 0)
                saved_count = response.get("saved_count", 0)
                updated_count = response.get("updated_count", 0)

            return (
                jsonify(
                    {
                        "jobs": jobs_data,
                        "total_found": len(jobs_data),
                        "sources": sources,
                        "average_score": avg_score,
                        "saved_count": saved_count,
                        "updated_count": updated_count,
                        "success": True,
                        "message": f"Jobs searched from {len(sources)} sources",
                        "error": None,
                    }
                ),
                200,
            )
        except Exception as exc:
            logging.error(f"Error searching for jobs: {str(exc)}")
            return jsonify({"error": str(exc)}), 500

    @jobs_bp.route("/api/credits/consume", methods=["POST"])
    @require_auth
    @rate_limit("api_requests_per_user_per_minute")
    def consume_credit():
        """Consume one credit unit for a given service."""
        try:
            user_id = request.current_user["id"]
            data = request.json or {}
            service = data.get("service")

            service_map = {
                "resume_tailoring": "resume_tailoring_per_user_per_day",
                "job_applications": "job_applications_per_user_per_day",
                "job_search": "job_search_per_user_per_day",
            }

            if service not in service_map:
                return (
                    jsonify({"error": f"Unknown service '{service}'. Valid: {list(service_map)}"}),
                    400,
                )

            limit_type = service_map[service]
            _, effective_daily_limit = get_user_and_limit(str(user_id), limit_type)

            allowed, info = rate_limiter.check_limit(
                limit_type,
                str(user_id),
                custom_limit=effective_daily_limit,
            )

            invalidate_credits_cache(str(user_id))

            if not allowed:
                return (
                    jsonify(
                        {
                            "success": False,
                            "error": "Daily limit reached",
                            "remaining": 0,
                            "limit": info.get("limit"),
                            "reset_time": info.get("reset_time"),
                        }
                    ),
                    429,
                )

            return (
                jsonify(
                    {
                        "success": True,
                        "remaining": info.get("remaining"),
                        "limit": info.get("limit"),
                        "reset_time": info.get("reset_time"),
                    }
                ),
                200,
            )

        except Exception as exc:
            logging.error(f"Error consuming credit: {exc}")
            return jsonify({"error": str(exc)}), 500

    @jobs_bp.route("/api/credits", methods=["GET"])
    @require_auth
    def get_user_credits():
        """Get user's credit information including usage and limits."""
        try:
            user_id = request.current_user["id"]
            user_email = request.current_user.get("email", "")
            is_admin = user_email in rate_limiter.ADMIN_EMAILS

            cache_key = f"credits_cache:{user_id}"
            cached_credits = None

            try:
                from rate_limiter import redis_client
                import json

                cached_data = redis_client.get(cache_key)
                if cached_data:
                    cached_credits = json.loads(cached_data)
            except Exception as cache_error:
                logging.debug(f"Cache fetch failed: {cache_error}")

            if cached_credits:
                return jsonify({"success": True, "credits": cached_credits, "cached": True}), 200

            user_obj, tailoring_effective_limit = get_user_and_limit(
                str(user_id), "resume_tailoring_per_user_per_day"
            )
            _, applications_effective_limit = get_user_and_limit(
                str(user_id), "job_applications_per_user_per_day"
            )
            search_effective_limit = int(
                rate_limiter.LIMITS["job_search_per_user_per_day"].requests
            )
            bonus_resume = int(getattr(user_obj, "bonus_resume_tailoring_max", 0) or 0)
            bonus_apply = int(getattr(user_obj, "bonus_job_applications_max", 0) or 0)

            daily_tailoring = rate_limiter.get_usage_stats(
                "resume_tailoring_per_user_per_day",
                str(user_id),
                custom_limit=tailoring_effective_limit,
            )
            daily_applications = rate_limiter.get_usage_stats(
                "job_applications_per_user_per_day",
                str(user_id),
                custom_limit=applications_effective_limit,
            )
            daily_search = rate_limiter.get_usage_stats(
                "job_search_per_user_per_day",
                str(user_id),
                custom_limit=search_effective_limit,
            )

            credits_info: Dict[str, Any] = {
                "is_admin": is_admin,
                "resume_tailoring": {
                    "daily": {
                        "limit": "unlimited"
                        if is_admin
                        else daily_tailoring.get("limit", tailoring_effective_limit),
                        "used": 0 if is_admin else daily_tailoring.get("used", 0),
                        "remaining": "unlimited"
                        if is_admin
                        else daily_tailoring.get("remaining", tailoring_effective_limit),
                        "reset_time": daily_tailoring.get("reset_time"),
                        "window_hours": 24,
                    }
                },
                "job_applications": {
                    "daily": {
                        "limit": "unlimited"
                        if is_admin
                        else daily_applications.get("limit", applications_effective_limit),
                        "used": 0 if is_admin else daily_applications.get("used", 0),
                        "remaining": "unlimited"
                        if is_admin
                        else daily_applications.get("remaining", applications_effective_limit),
                        "reset_time": daily_applications.get("reset_time"),
                        "window_hours": 24,
                    }
                },
                "job_search": {
                    "daily": {
                        "limit": "unlimited" if is_admin else daily_search.get("limit", 20),
                        "used": 0 if is_admin else daily_search.get("used", 0),
                        "remaining": "unlimited"
                        if is_admin
                        else daily_search.get("remaining", 20),
                        "reset_time": daily_search.get("reset_time"),
                        "window_hours": 24,
                    }
                },
                "bonuses": {
                    "resume_tailoring_bonus": 0 if is_admin else bonus_resume,
                    "job_applications_bonus": 0 if is_admin else bonus_apply,
                },
            }

            try:
                from rate_limiter import redis_client
                import json

                redis_client.setex(cache_key, 5, json.dumps(credits_info))
            except Exception as cache_error:
                logging.debug(f"Cache set failed: {cache_error}")

            return jsonify({"success": True, "credits": credits_info}), 200

        except Exception as exc:
            logging.error(f"Error getting user credits: {str(exc)}")
            return jsonify({"error": str(exc)}), 500

    @jobs_bp.route("/api/tailor-resume", methods=["POST"])
    @require_auth
    @rate_limit("api_requests_per_user_per_minute")
    @validate_input
    def tailor_resume():
        """Submit resume tailoring job to queue."""
        try:
            user_id = request.current_user["id"]
            invalidate_credits_cache(str(user_id))
            data = request.json

            job_description = data.get("job_description")
            resume_url = data.get("resume_url")
            if not job_description:
                return jsonify({"error": "Job description is required"}), 400

            profile_result = ProfileService.get_profile(user_id)
            profile_data = profile_result.get("profile") if profile_result.get("success") else {}
            profile_strength = profile_result.get("profile_strength") or score_profile_strength(
                profile_data
            )
            resume_source_type = (profile_data or {}).get("resume_source_type", "google_doc")
            skip_profile_gate = bool(data.get("skip_profile_gate", False))

            if not profile_strength.get("gating_passed", False) and not skip_profile_gate:
                return (
                    jsonify(
                        {
                            "error": "Profile strength is too low for reliable tailoring.",
                            "success": False,
                            "profile_strength": profile_strength,
                            "hints": profile_strength.get("hints", []),
                            "nudges": profile_strength.get("nudges", []),
                            "can_override": True,
                            "override_field": "skip_profile_gate",
                        }
                    ),
                    412,
                )

            credentials = None
            credentials_dict = None
            if resume_source_type != "latex_zip":
                if not resume_url:
                    return (
                        jsonify({"error": "Resume URL is required for Google Docs tailoring"}),
                        400,
                    )

                if not GoogleOAuthService.is_connected(user_id):
                    return (
                        jsonify(
                            {
                                "error": "Please connect your Google account first to tailor Google Docs resumes",
                                "needs_google_auth": True,
                            }
                        ),
                        403,
                    )

                credentials = GoogleOAuthService.get_credentials(user_id)
                if not credentials:
                    return (
                        jsonify(
                            {
                                "error": "Failed to retrieve Google credentials. Please reconnect your account.",
                                "needs_google_auth": True,
                            }
                        ),
                        403,
                    )

                credentials_dict = {
                    "token": credentials.token,
                    "refresh_token": credentials.refresh_token,
                    "token_uri": credentials.token_uri,
                    "client_id": credentials.client_id,
                    "client_secret": credentials.client_secret,
                    "scopes": list(credentials.scopes) if credentials.scopes else None,
                }
            else:
                resume_url = None

            from database_config import SessionLocal, User

            db = SessionLocal()
            try:
                user = db.query(User).filter(User.id == user_id).first()
                user_full_name = f"{user.first_name} {user.last_name}" if user else "Resume"
            finally:
                db.close()

            payload = {
                "resume_source_type": resume_source_type,
                "original_resume_url": resume_url,
                "job_description": job_description,
                "job_title": data.get("job_title", "Unknown Position"),
                "company": data.get("company_name", "Unknown Company"),
                "credentials": credentials_dict,
                "user_full_name": user_full_name,
                "latex_main_tex_path": (profile_data or {}).get("latex_main_tex_path"),
                "replace_projects_on_tailor": bool(data.get("replace_projects_on_tailor", False)),
                "skip_profile_gate": skip_profile_gate,
                "profile_strength": profile_strength,
            }

            result = submit_job_with_validation(
                user_id=user_id,
                job_type="resume_tailoring",
                payload=payload,
                priority=JobPriority.NORMAL,
            )

            if result["success"]:
                return (
                    jsonify(
                        {
                            "success": True,
                            "job_id": result["job_id"],
                            "message": "Resume tailoring job submitted successfully. You will be notified when complete.",
                            "queue_position": job_queue.get_queue_stats()["queue_size"],
                        }
                    ),
                    202,
                )
            return jsonify({"error": result["error"], "success": False}), 400

        except Exception as exc:
            logging.error(f"Error submitting resume tailoring job: {str(exc)}")
            return jsonify({"error": str(exc)}), 500

    return jobs_bp

