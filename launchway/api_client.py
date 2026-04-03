"""
HTTP client for the Launchway Railway backend.

All user data (auth, profile, application history, credits) goes through
this client - no direct database access in the CLI.

Default backend: https://jobapplicationagent-production.up.railway.app
Override via env var: LAUNCHWAY_BACKEND_URL
"""

import logging
import os
import time
from typing import Any, Dict, List, Optional, Tuple

import requests
from requests import Response, Session
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logger = logging.getLogger(__name__)

_DEFAULT_BACKEND = "https://jobapplicationagent-production.up.railway.app"


def _get_base_url() -> str:
    return os.getenv("LAUNCHWAY_BACKEND_URL", _DEFAULT_BACKEND).rstrip("/")


class LaunchwayAPIError(Exception):
    """Raised when the backend returns an error response."""
    def __init__(self, message: str, status_code: int = 0, email_not_verified: bool = False, beta_not_approved: bool = False):
        super().__init__(message)
        self.status_code = status_code
        self.email_not_verified = email_not_verified
        self.beta_not_approved = beta_not_approved


class LaunchwayClient:
    """
    Thin HTTP wrapper around the Launchway REST API.

    Usage:
        client = LaunchwayClient()
        result = client.login("user@example.com", "password")
        client.token = result["token"]
        profile = client.get_profile()
    """

    def __init__(self, base_url: str = None, token: str = None, timeout: int = 30):
        self.base_url = base_url or _get_base_url()
        self.token    = token
        self.timeout  = timeout
        self._session = Session()
        self._session.headers.update({"Content-Type": "application/json"})
        self._configure_retries()

    def _configure_retries(self) -> None:
        """
        Configure conservative transport retries for transient network/server issues.
        POST is excluded to avoid accidental duplicate write operations.
        """
        retry_cfg = Retry(
            total=3,
            connect=3,
            read=3,
            status=3,
            backoff_factor=0.5,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=frozenset({"GET", "PUT", "DELETE", "HEAD", "OPTIONS"}),
            raise_on_status=False,
            respect_retry_after_header=True,
        )
        adapter = HTTPAdapter(max_retries=retry_cfg)
        self._session.mount("https://", adapter)
        self._session.mount("http://", adapter)

    # ── internal ────────────────────────────────────────────────────────────

    def _auth_headers(self) -> Dict[str, str]:
        if self.token:
            return {"Authorization": f"Bearer {self.token}"}
        return {}

    def _url(self, path: str) -> str:
        return f"{self.base_url}{path}"

    def _get(self, path: str, params: dict = None) -> Dict[str, Any]:
        try:
            resp = self._session.get(
                self._url(path),
                headers=self._auth_headers(),
                params=params,
                timeout=self.timeout,
            )
            return self._handle(resp)
        except requests.RequestException as e:
            raise LaunchwayAPIError(f"Network error: {e}")

    def _post(self, path: str, json: dict = None) -> Dict[str, Any]:
        try:
            resp = self._session.post(
                self._url(path),
                headers=self._auth_headers(),
                json=json or {},
                timeout=self.timeout,
            )
            return self._handle(resp)
        except requests.RequestException as e:
            raise LaunchwayAPIError(f"Network error: {e}")

    def _put(self, path: str, json: dict = None) -> Dict[str, Any]:
        try:
            resp = self._session.put(
                self._url(path),
                headers=self._auth_headers(),
                json=json or {},
                timeout=self.timeout,
            )
            return self._handle(resp)
        except requests.RequestException as e:
            raise LaunchwayAPIError(f"Network error: {e}")

    @staticmethod
    def _handle(resp: Response) -> Dict[str, Any]:
        try:
            data = resp.json()
        except Exception:
            data = {}
        if not resp.ok:
            msg = data.get("error") or data.get("message") or f"HTTP {resp.status_code}"
            raise LaunchwayAPIError(
                msg,
                status_code=resp.status_code,
                email_not_verified=bool(data.get("email_not_verified")),
                beta_not_approved=bool(data.get("beta_not_approved")),
            )
        return data

    # ── auth ────────────────────────────────────────────────────────────────

    def login(self, email: str, password: str) -> Dict[str, Any]:
        """
        Authenticate and get a JWT token.

        Returns dict with keys: token, user {id, email, first_name, last_name, ...}
        Raises LaunchwayAPIError with beta_not_approved=True if the account is
        awaiting beta approval (backend returns HTTP 200 with success=False).
        """
        data = self._post("/api/auth/login", {"email": email, "password": password})
        if data.get("beta_not_approved"):
            raise LaunchwayAPIError(
                data.get("error", "Beta access required. Please request access at the Launchway website."),
                beta_not_approved=True,
            )
        if data.get("token"):
            self.token = data["token"]
        return data

    def register(
        self,
        email: str,
        password: str,
        first_name: str,
        last_name: str,
        beta_request_reason: Optional[str] = None,
        survey_consent: Optional[bool] = None,
    ) -> Dict[str, Any]:
        """
        Register a new account.

        Returns dict with keys: success, message, user {...}
        Note: email verification may be required before login.
        """
        # Beta signup policy: registration is only valid when users explicitly
        # consent to the weekly survey and provide a meaningful reason.
        reason = (beta_request_reason or "").strip()
        if len(reason) < 20:
            raise LaunchwayAPIError("Please provide at least 20 characters for your beta access reason.")
        if survey_consent is not True:
            raise LaunchwayAPIError("Survey consent is required to register for beta access.")

        payload: Dict[str, Any] = {
            "email":      email,
            "password":   password,
            "first_name": first_name,
            "last_name":  last_name,
            "beta_request_reason": reason,
            "survey_consent": True,
        }
        return self._post("/api/auth/signup", payload)

    def resend_verification_email(self, email: str) -> Dict[str, Any]:
        """Request a new verification email for the given address."""
        return self._post("/api/auth/resend-verification", {"email": email})

    def process_resume_url(self, resume_url: str) -> Dict[str, Any]:
        """
        Tell the server to extract the Google Doc at resume_url, run it through
        the LLM, and save the resulting profile fields.

        Returns dict with keys: success, profile_data, message
        """
        return self._post("/api/process-resume", {"resume_url": resume_url})

    def get_google_oauth_status(self) -> Dict[str, Any]:
        """Check whether the current user has Google OAuth connected."""
        return self._get("/api/oauth/status")

    def get_google_oauth_url(self) -> str:
        """Get the Google OAuth authorization URL to open in a browser."""
        data = self._get("/api/oauth/authorize")
        return data.get("authorization_url", "")

    def verify_token(self) -> Dict[str, Any]:
        """Verify the current token and refresh user info."""
        return self._get("/api/auth/verify")

    # ── profile ─────────────────────────────────────────────────────────────

    def get_profile(self) -> Dict[str, Any]:
        """
        Fetch the current user's profile.

        Returns dict: { resumeData: {...}, resume_url, resume_source_type, ... }
        The `resumeData` sub-dict is passed directly to agent form-filling.
        """
        data = self._get("/api/profile")
        return data.get("resumeData", {})

    def update_profile(self, profile_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Save the entire profile dict back to the server.
        The caller is responsible for merging changes before calling this.
        """
        return self._post("/api/profile", profile_data)

    # ── credits ─────────────────────────────────────────────────────────────

    def get_credits(self) -> Dict[str, Any]:
        """Return the user's rate limit / credit usage."""
        data = self._get("/api/credits")
        return data.get("credits", {})

    def consume_credit(self, service: str) -> Dict[str, Any]:
        """
        Consume one credit for a given service (called after a local task completes).

        service: 'resume_tailoring' | 'job_applications' | 'job_search'

        Returns dict with keys: success, remaining, limit, reset_time.
        Raises LaunchwayAPIError with status_code=429 when daily limit is reached.
        """
        return self._post("/api/credits/consume", {"service": service})

    def check_credit_available(self, service: str) -> Tuple[bool, Dict[str, Any]]:
        """
        Check whether at least one credit is available for the given service
        WITHOUT consuming it.

        Returns (available: bool, credit_info: dict).
        credit_info keys: remaining, limit, reset_time.
        Fails closed on network errors for cost-bearing operations.
        """
        try:
            credits = self.get_credits()
        except LaunchwayAPIError:
            return False, {
                "remaining": 0,
                "limit": "unknown",
                "reset_time": None,
                "error": "credit_check_unavailable"
            }

        svc = credits.get(service, {})
        daily = svc.get("daily", {})
        remaining = daily.get("remaining", "unlimited")
        if remaining == "unlimited":
            return True, daily
        try:
            return int(remaining) > 0, daily
        except (TypeError, ValueError):
            return False, {
                **daily,
                "error": "invalid_credit_state"
            }

    # ── applications ────────────────────────────────────────────────────────

    def get_applications(self, limit: int = 200) -> list:
        """Return list of recorded job applications (most recent first)."""
        data = self._get("/api/cli/applications", params={"limit": limit})
        return data.get("applications", [])

    def get_applications_summary(self, limit: int = 200) -> Dict[str, Any]:
        """Return applications with pagination/total metadata."""
        return self._get("/api/cli/applications", params={"limit": limit})

    def record_application(self, job_url: str, company: str = "Unknown",
                           title: str = "Unknown Position") -> Dict[str, Any]:
        """Record a completed job application."""
        return self._post("/api/cli/applications", {
            "job_url": job_url,
            "company": company,
            "title":   title,
            "status":  "completed",
        })

    def get_applied_job_urls(self) -> set:
        """Return a set of job URLs previously applied to (for deduplication)."""
        try:
            data = self._get("/api/cli/applications", params={"urls_only": "true"})
            return set(data.get("urls", []))
        except LaunchwayAPIError as e:
            logger.error(f"Failed to fetch applied job URLs: {e}")
            return set()

    def save_user_field_overrides(self, overrides: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Batch-upsert human-captured field overrides to the server.
        Called by UserPatternRecorder when there is no local DB connection.

        Each override dict must contain at minimum:
            field_label_normalized, field_label_raw, field_value_cached,
            field_category, source, was_ai_attempted, confidence_score
        Optional: site_domain, profile_field
        """
        if not overrides:
            return {"saved": 0, "skipped": 0}
        try:
            return self._post("/api/cli/user-field-overrides", {"overrides": overrides})
        except LaunchwayAPIError as e:
            logger.error(f"Failed to save user field overrides: {e}")
            return {"saved": 0, "skipped": 0, "error": str(e)}

    def save_field_label_patterns(self, patterns: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Batch-upsert global field-label patterns learned by local agents.

        Each pattern dict should include:
            field_label_normalized, field_label_raw, profile_field,
            field_category, success (optional)
        """
        if not patterns:
            return {"saved": 0, "skipped": 0}
        try:
            return self._post("/api/cli/field-label-patterns", {"patterns": patterns})
        except LaunchwayAPIError as e:
            logger.error(f"Failed to save field label patterns: {e}")
            return {"saved": 0, "skipped": 0, "error": str(e)}

    # ── account ─────────────────────────────────────────────────────────────

    def change_password(self, current_password: str, new_password: str) -> Dict[str, Any]:
        return self._post("/api/account/change-password", {
            "current_password": current_password,
            "new_password":     new_password,
        })

    def update_email(self, new_email: str) -> Dict[str, Any]:
        return self._put("/api/account/email", {"email": new_email})

    def get_account_info(self) -> Dict[str, Any]:
        """Return full account info including application count."""
        return self._get("/api/account/info")

    def extract_resume_keywords(self, resume_text: str = None) -> Dict[str, Any]:
        """
        Ask the server to extract structured keywords from the user's resume
        and store them in the profile.

        If `resume_text` is supplied it will be used directly; otherwise the
        server fetches the resume from the URL stored in the user's profile.

        Returns the extracted keyword dict on success.
        Raises LaunchwayAPIError on failure.
        """
        body: Dict[str, Any] = {}
        if resume_text:
            body["resume_text"] = resume_text
        data = self._post("/api/profile/keywords/extract", body)
        return data.get("resume_keywords", {})

    def download_resume_pdf(self, output_path: str, resume_url: str = None) -> bool:
        """
        Ask the server to export the user's Google Doc resume as a PDF and save
        the bytes to *output_path*.

        The server uses the user's stored OAuth credentials, so private Google
        Docs work without the document needing to be publicly shared.

        Args:
            output_path: Local file path where the PDF should be written.
            resume_url:  Override the resume URL stored in the user's profile.

        Returns:
            True on success, False on any failure.
        """
        try:
            params = {}
            if resume_url:
                params["url"] = resume_url

            resp = self._session.get(
                self._url("/api/resume/pdf"),
                headers=self._auth_headers(),
                params=params,
                timeout=60,  # PDF export can take a moment
            )

            if resp.status_code == 403:
                # Server told us Google account is not connected
                try:
                    err = resp.json().get("error", "")
                except Exception:
                    err = ""
                print(
                    "[ERROR] Resume PDF download failed - Google account is not connected on the server.\n"
                    "  → Open the app → Profile → Resume → click 'Connect Google Account'"
                )
                return False

            if not resp.ok:
                try:
                    err = resp.json().get("error", f"HTTP {resp.status_code}")
                except Exception:
                    err = f"HTTP {resp.status_code}"
                print(f"[ERROR] Resume PDF download failed: {err}")
                return False

            content_type = resp.headers.get("Content-Type", "")
            if "application/pdf" not in content_type:
                print(f"[ERROR] Server returned unexpected content type for resume PDF: {content_type}")
                return False

            import os as _os
            _os.makedirs(_os.path.dirname(output_path), exist_ok=True)
            with open(output_path, "wb") as fh:
                fh.write(resp.content)
            return True

        except Exception as e:
            print(f"[ERROR] Could not download resume PDF from server: {e}")
            return False

    def get_ai_key_settings(self) -> Dict[str, Any]:
        """Fetch the user's AI Engine configuration (masked key, modes)."""
        return self._get("/api/settings/ai-keys")

    def save_ai_key_settings(
        self,
        primary_mode: str,
        secondary_mode: Optional[str] = None,
        custom_api_key: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Save the user's AI Engine configuration.

        primary_mode  : 'launchway' | 'custom'
        secondary_mode: 'launchway' | 'custom' | None
        custom_api_key: plain-text Gemini key (required when any mode == 'custom')
        """
        body: Dict[str, Any] = {
            "primary_mode": primary_mode,
            "secondary_mode": secondary_mode,
        }
        if custom_api_key:
            body["custom_api_key"] = custom_api_key
        return self._post("/api/settings/ai-keys", body)

    # ── agent runtime key ───────────────────────────────────────────────────

    def get_agent_key(self) -> dict:
        """
        Fetch the agent runtime bundle from the server.
        Returns a dict with at minimum:
          { "key": "<fernet key>", "gemini_key": "..." }
        """
        return self._get("/api/cli/agent-key")

    # ── job search (server-side) ─────────────────────────────────────────────

    def search_jobs(
        self,
        keywords: str = None,
        location: str = None,
        remote: bool = False,
        easy_apply: bool = False,
        hours_old: int = None,
        min_relevance_score: int = 30,
    ) -> Dict[str, Any]:
        """
        Run a server-side job search using the user's profile.

        Returns dict with keys: jobs (list), total_found, sources, average_score, ...
        """
        body: Dict[str, Any] = {"min_relevance_score": min_relevance_score}
        if keywords:
            body["keywords"] = keywords
        if location:
            body["location"] = location
        if remote:
            body["remote"] = remote
        if easy_apply:
            body["easy_apply"] = easy_apply
        if hours_old is not None:
            body["hours_old"] = hours_old
        return self._post("/api/search-jobs", body)

    # ── resume tailoring (server-side async) ────────────────────────────────

    def submit_tailoring_job(
        self,
        job_description: str,
        job_title: str = "Position",
        company: str = "Company",
        resume_url: str = None,
        replace_projects_on_tailor: bool = False,
        skip_profile_gate: bool = False,
    ) -> Dict[str, Any]:
        """
        Submit an async resume-tailoring job to the server queue.

        Returns dict with keys: success, job_id, message, queue_position.
        Use get_job_status(job_id) to poll for completion.
        """
        body: Dict[str, Any] = {
            "job_description": job_description,
            "job_title": job_title,
            "company_name": company,
            "replace_projects_on_tailor": bool(replace_projects_on_tailor),
            "skip_profile_gate": bool(skip_profile_gate),
        }
        if resume_url:
            body["resume_url"] = resume_url
        return self._post("/api/tailor-resume", body)

    # ── job application (server-side async) ─────────────────────────────────

    def submit_apply_job(
        self,
        job_url: str,
        tailor_resume: bool = False,
        replace_projects_on_tailor: bool = False,
    ) -> Dict[str, Any]:
        """
        Submit a job application to the server queue.

        Returns dict with keys: success, job_id, message.
        Use get_job_status(job_id) to poll for completion.
        """
        return self._post("/api/cli/apply", {
            "job_url": job_url,
            "tailor_resume": tailor_resume,
            "replace_projects_on_tailor": bool(replace_projects_on_tailor),
        })

    # ── job queue status / management ───────────────────────────────────────

    def get_job_status(self, job_id: str) -> Dict[str, Any]:
        """
        Fetch the current status of a queued job.

        Returned dict typically has keys:
          status  : "QUEUED" | "RUNNING" | "COMPLETED" | "FAILED" | "CANCELLED"
          result  : dict with handler output (when COMPLETED)
          error   : str or None
          job_id  : str
        """
        return self._get(f"/api/jobs/{job_id}/status")

    def cancel_job(self, job_id: str) -> Dict[str, Any]:
        """Cancel a queued or running job."""
        return self._post(f"/api/jobs/{job_id}/cancel")

    def get_user_jobs(self) -> List[Dict[str, Any]]:
        """Return a list of recent jobs submitted by the current user."""
        data = self._get("/api/user/jobs")
        return data.get("jobs", [])

    def poll_job(
        self,
        job_id: str,
        timeout: int = 600,
        interval: int = 5,
    ) -> Dict[str, Any]:
        """
        Poll a job until it reaches a terminal state (COMPLETED / FAILED / CANCELLED).

        Returns the final status dict.
        Raises LaunchwayAPIError if the timeout is exceeded.
        """
        terminal = {"COMPLETED", "FAILED", "CANCELLED", "TIMEOUT"}
        deadline = time.monotonic() + timeout
        while True:
            status = self.get_job_status(job_id)
            state = (status.get("status") or "").upper()
            if state in terminal:
                return status
            if time.monotonic() >= deadline:
                raise LaunchwayAPIError(
                    f"Job {job_id} did not finish within {timeout}s (last state: {state})"
                )
            time.sleep(interval)

    def get_profile_strength(self) -> Dict[str, Any]:
        """Return profile-strength payload exposed by /api/profile."""
        data = self._get("/api/profile")
        return data.get("profile_strength", {})
