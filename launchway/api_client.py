"""
HTTP client for the Launchway Railway backend.

All user data (auth, profile, application history, credits) goes through
this client — no direct database access in the CLI.

Default backend: https://jobapplicationagent-production.up.railway.app
Override via env var: LAUNCHWAY_BACKEND_URL
"""

import logging
import os
from typing import Any, Dict, Optional, Tuple

import requests
from requests import Response, Session

logger = logging.getLogger(__name__)

_DEFAULT_BACKEND = "https://jobapplicationagent-production.up.railway.app"


def _get_base_url() -> str:
    return os.getenv("LAUNCHWAY_BACKEND_URL", _DEFAULT_BACKEND).rstrip("/")


class LaunchwayAPIError(Exception):
    """Raised when the backend returns an error response."""
    def __init__(self, message: str, status_code: int = 0):
        super().__init__(message)
        self.status_code = status_code


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
            raise LaunchwayAPIError(msg, status_code=resp.status_code)
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
