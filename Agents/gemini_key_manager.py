"""
GeminiKeyManager - primary/secondary API key fallback with retry + cooldown.

Fallback algorithm:
  1. Try primary key.
  2. Quota/rate-limit error → try secondary (if configured).
  3. Secondary fails → retry primary once more.
  4. Still failing → 60-second cooldown, then final retry of primary.
  5. Still failing → raise GeminiQuotaExhaustedError with a user-facing message.

Usage (sync or async; both thin wrappers are provided):
    mgr = GeminiKeyManager(
        primary_mode="custom",
        secondary_mode="launchway",
        custom_api_key="AIza...",
        launchway_api_key=os.getenv("GOOGLE_API_KEY"),
    )
    response = mgr.generate_content("gemini-2.0-flash", "Hello world")
"""

import asyncio
import logging
import os
import time
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

# Exceptions that signal quota / rate-limit exhaustion
_QUOTA_SIGNALS = (
    "quota",
    "rate limit",
    "resource_exhausted",
    "resourceexhausted",
    "429",
    "rateerror",
    "too many requests",
)


class GeminiQuotaExhaustedError(RuntimeError):
    """Raised when all keys and retries are exhausted."""


class AiEngineNotConfiguredError(RuntimeError):
    """Raised when the user has never set up their AI Engine (primary_mode is NULL)."""


def _is_quota_error(exc: Exception) -> bool:
    msg = str(exc).lower()
    return any(sig in msg for sig in _QUOTA_SIGNALS)


class GeminiKeyManager:
    """
    Wraps google-genai calls with primary/secondary key fallback logic.

    Parameters
    ----------
    primary_mode : 'launchway' | 'custom'
    secondary_mode : 'launchway' | 'custom' | None
    custom_api_key : decrypted key string (required when either mode == 'custom')
    launchway_api_key : server-side shared key (GOOGLE_API_KEY env var)
    cooldown_seconds : how long to wait before the final retry (default 60)
    """

    def __init__(
        self,
        primary_mode: Optional[str] = None,
        secondary_mode: Optional[str] = None,
        custom_api_key: Optional[str] = None,
        launchway_api_key: Optional[str] = None,
        cooldown_seconds: int = 60,
    ):
        # None means the user has never configured their AI Engine
        self.primary_mode = primary_mode
        self.secondary_mode = secondary_mode
        self.custom_api_key = custom_api_key
        self.launchway_api_key = launchway_api_key or os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY")
        self.cooldown_seconds = cooldown_seconds

    @property
    def is_configured(self) -> bool:
        """True only when the user has explicitly chosen a primary mode."""
        return self.primary_mode is not None

    # ── key resolution ────────────────────────────────────────────────────────

    def _key_for_mode(self, mode: str) -> Optional[str]:
        if mode == "custom":
            return self.custom_api_key
        return self.launchway_api_key  # 'launchway'

    @property
    def _primary_key(self) -> Optional[str]:
        return self._key_for_mode(self.primary_mode)

    @property
    def _secondary_key(self) -> Optional[str]:
        if not self.secondary_mode:
            return None
        return self._key_for_mode(self.secondary_mode)

    # ── single-attempt call ───────────────────────────────────────────────────

    def _attempt(self, api_key: str, model: str, contents: Any, **kwargs) -> Any:
        """Make one synchronous Gemini call with the given key."""
        try:
            from google import genai as _genai
            client = _genai.Client(api_key=api_key)
            return client.models.generate_content(model=model, contents=contents, **kwargs)
        except Exception:
            # Also try legacy SDK path (google.generativeai) as a fallback
            import google.generativeai as _legacy_genai
            _legacy_genai.configure(api_key=api_key)
            model_obj = _legacy_genai.GenerativeModel(model)
            return model_obj.generate_content(contents, **kwargs)

    # ── public synchronous interface ─────────────────────────────────────────

    def generate_content(self, model: str, contents: Any, **kwargs) -> Any:
        """
        Call Gemini with automatic primary → secondary → cooldown → retry fallback.
        Raises GeminiQuotaExhaustedError if all attempts fail.
        """
        if not self.is_configured:
            raise AiEngineNotConfiguredError(
                "AI Engine is not set up yet. Please configure your primary API key method "
                "in Profile → AI Engine (web) or CLI Profile Management → AI Engine."
            )

        primary_key = self._primary_key
        secondary_key = self._secondary_key

        if not primary_key:
            raise GeminiQuotaExhaustedError(
                "No API key available. If you chose 'custom', make sure you've saved a valid Gemini key."
            )

        # Step 1 - primary
        try:
            logger.debug(f"[GeminiKeyManager] Trying primary ({self.primary_mode})")
            return self._attempt(primary_key, model, contents, **kwargs)
        except Exception as e1:
            if not _is_quota_error(e1):
                raise  # not a quota issue - propagate immediately
            logger.warning(f"[GeminiKeyManager] Primary quota hit: {e1}")

        # Step 2 - secondary (if configured)
        if secondary_key:
            try:
                logger.debug(f"[GeminiKeyManager] Trying secondary ({self.secondary_mode})")
                return self._attempt(secondary_key, model, contents, **kwargs)
            except Exception as e2:
                if not _is_quota_error(e2):
                    raise
                logger.warning(f"[GeminiKeyManager] Secondary quota hit: {e2}")

        # Step 3 - retry primary once immediately
        try:
            logger.debug("[GeminiKeyManager] Retrying primary immediately")
            return self._attempt(primary_key, model, contents, **kwargs)
        except Exception as e3:
            if not _is_quota_error(e3):
                raise
            logger.warning(f"[GeminiKeyManager] Primary retry failed: {e3}")

        # Step 4 - cooldown then final attempt
        logger.warning(
            f"[GeminiKeyManager] All keys exhausted. Cooling down for {self.cooldown_seconds}s …"
        )
        time.sleep(self.cooldown_seconds)

        try:
            logger.debug("[GeminiKeyManager] Final attempt after cooldown")
            return self._attempt(primary_key, model, contents, **kwargs)
        except Exception as e4:
            if not _is_quota_error(e4):
                raise
            raise GeminiQuotaExhaustedError(
                "Gemini API quota exhausted on all configured keys after a cooldown retry. "
                "Please wait a few minutes and try again, or add a secondary API key in your AI Engine settings."
            ) from e4

    # ── public async interface ────────────────────────────────────────────────

    async def generate_content_async(self, model: str, contents: Any, **kwargs) -> Any:
        """Async variant - runs the synchronous logic in a thread executor."""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None, lambda: self.generate_content(model, contents, **kwargs)
        )

    # ── convenience: build from profile dict ─────────────────────────────────

    @classmethod
    def from_profile(
        cls,
        profile: dict,
        launchway_api_key: Optional[str] = None,
        cooldown_seconds: int = 60,
    ) -> "GeminiKeyManager":
        """
        Construct a manager from a decoded profile dict (as returned by the API).
        The custom_gemini_api_key in the profile is already decrypted by the server
        before being included in the agent's profile payload.
        """
        return cls(
            primary_mode=profile.get("api_primary_mode") or None,   # None = not configured → raises AiEngineNotConfiguredError
            secondary_mode=profile.get("api_secondary_mode") or None,
            custom_api_key=profile.get("custom_gemini_api_key_decrypted") or None,
            launchway_api_key=launchway_api_key,
            cooldown_seconds=cooldown_seconds,
        )

    # ── human-readable status ─────────────────────────────────────────────────

    def describe(self) -> str:
        has_custom = bool(self.custom_api_key)
        has_launchway = bool(self.launchway_api_key)
        parts = [f"primary={self.primary_mode}"]
        if self.secondary_mode:
            parts.append(f"secondary={self.secondary_mode}")
        parts.append(f"custom_key={'set' if has_custom else 'not set'}")
        parts.append(f"launchway_key={'set' if has_launchway else 'not set'}")
        return f"GeminiKeyManager({', '.join(parts)})"
