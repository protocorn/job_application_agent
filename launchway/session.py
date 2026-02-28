"""
Local session persistence for the Launchway CLI.

After a successful login the JWT token and basic user info are saved to
~/.launchway/session.json so that subsequent `launchway` invocations can
restore the session automatically without asking for credentials again.

The token is verified against the backend on every startup — if it has
expired or been revoked the saved session is discarded and the user is
asked to log in again.
"""

import json
import logging
from pathlib import Path
from typing import Optional, Tuple

logger = logging.getLogger(__name__)

_SESSION_DIR  = Path.home() / ".launchway"
_SESSION_FILE = _SESSION_DIR / "session.json"


def save_session(token: str, user: dict) -> None:
    """Persist token + user dict to disk after a successful login."""
    try:
        _SESSION_DIR.mkdir(parents=True, exist_ok=True)
        _SESSION_FILE.write_text(
            json.dumps({"token": token, "user": user}, indent=2),
            encoding="utf-8",
        )
        logger.debug(f"Session saved for {user.get('email')}")
    except Exception as e:
        logger.warning(f"Could not save session: {e}")


def load_session() -> Tuple[Optional[str], Optional[dict]]:
    """Return (token, user) from disk, or (None, None) if nothing is saved."""
    try:
        if _SESSION_FILE.exists():
            data = json.loads(_SESSION_FILE.read_text(encoding="utf-8"))
            token = data.get("token")
            user  = data.get("user")
            if token and user:
                return token, user
    except Exception as e:
        logger.warning(f"Could not load session: {e}")
    return None, None


def clear_session() -> None:
    """Delete the saved session file (called on explicit logout)."""
    try:
        if _SESSION_FILE.exists():
            _SESSION_FILE.unlink()
            logger.debug("Session cleared.")
    except Exception as e:
        logger.warning(f"Could not clear session: {e}")
