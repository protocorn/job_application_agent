"""
HTTP client for the Launchway Railway backend.

All user data (auth, profile, application history, credits) goes through
this client — no direct database access in the CLI.

Default backend: https://jobapplicationagent-production.up.railway.app
Override via env var: LAUNCHWAY_BACKEND_URL
"""

import logging
import os
import time
from typing import Any, Dict, List, Optional, Tuple

import requests
from requests import Response, Session

logger = logging.getLogger(__name__)

_DEFAULT_BACKEND = "https://jobapplicationagent-production.up.railway.app"


def _get_base_url() -> str:
    return os.getenv("LAUNCHWAY_BACKEND_URL", _DEFAULT_BACKEND).rstrip("/")


class LaunchwayAPIError(Exception):
    """Raised when the backend returns an error response."""
    def __init__(self, message: str, status_code: int = 0, email_not_verified: bool = False):
        super().__init__(message)
        self.status_code = status_code
        self.email_not_verified = email_not_verified


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
            )
        return data

    # ── auth ────────────────────────────────────────────────────────────────

    def login(self, email: str, password: str) -> Dict[str, Any]:
        """
        Authenticate and get a JWT token.

        Returns dict with keys: token, user {id, email, first_name, last_name, ...}
        """
        data = self._post("/api/auth/login", {"email": email, "password": password})
        if data.get("token"):
            self.token = data["token"]
        return data

    def register(self, email: str, password: str, first_name: str, last_name: str) -> Dict[str, Any]:
        """
        Register a new account.

        Returns dict with keys: success, message, user {...}
        Note: email verification may be required before login.
        """
        return self._post("/api/auth/signup", {
            "email":      email,
            "password":   password,
            "first_name": first_name,
            "last_name":  last_name,
        })

    def resend_verification_email(self, email: str) -> Dict[str, Any]:
        """Request a new verification email for the given address."""
        return self._post("/api/auth/resend-verification", {"email": email})

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
        Fails open (returns True) on network errors so tasks are never blocked.
        """
        try:
            credits = self.get_credits()
        except LaunchwayAPIError:
            return True, {}  # fail open

        svc = credits.get(service, {})
        daily = svc.get("daily", {})
        remaining = daily.get("remaining", "unlimited")
        if remaining == "unlimited":
            return True, daily
        try:
            return int(remaining) > 0, daily
        except (TypeError, ValueError):
            return True, daily

    # ── applications ────────────────────────────────────────────────────────

    def get_applications(self, limit: int = 50) -> list:
        """Return list of recorded job applications (most recent first)."""
        data = self._get("/api/cli/applications", params={"limit": limit})
        return data.get("applications", [])

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

    # ── mimikree ────────────────────────────────────────────────────────────

    def get_mimikree_status(self) -> Dict[str, Any]:
        return self._get("/api/mimikree/status")

    def connect_mimikree(self, email: str, password: str) -> Dict[str, Any]:
        return self._post("/api/mimikree/connect", {"email": email, "password": password})

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
          { "key": "<fernet key>", "gemini_key": "...", "mimikree_url": "..." }
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
        }
        if resume_url:
            body["resume_url"] = resume_url
        return self._post("/api/tailor-resume", body)

    # ── job application (server-side async) ─────────────────────────────────

    def submit_apply_job(
        self,
        job_url: str,
        tailor_resume: bool = False,
    ) -> Dict[str, Any]:
        """
        Submit a job application to the server queue.

        Returns dict with keys: success, job_id, message.
        Use get_job_status(job_id) to poll for completion.
        """
        return self._post("/api/cli/apply", {
            "job_url": job_url,
            "tailor_resume": tailor_resume,
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

    def get_mimikree_credentials(self) -> Tuple[Optional[str], Optional[str]]:
        """
        Fetch the decrypted Mimikree credentials stored for the current user.
        Used by the local resume tailoring agent.

        Returns (email, password) tuple, or (None, None) if not connected.
        """
        try:
            data = self._get("/api/mimikree/credentials")
            return data.get("email"), data.get("password")
        except LaunchwayAPIError as e:
            if e.status_code in (404, 400):
                return None, None
            raise
