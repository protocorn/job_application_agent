"""
Agent bootstrap — fetches the AES runtime key from the server, decrypts the
encrypted Agents/ blobs to a temporary directory, and injects that directory
into sys.path so that  `from Agents.xxx import yyy`  works normally.

Usage (inside a CLI mixin method, after the user is logged in):

    if not self._ensure_agents_bootstrapped():
        return

    from Agents.job_application_agent import RefactoredJobAgent
    ...

The key is cached locally for 24 hours so that most launches are offline-fast.
The decrypted source lives only in a per-session temp directory and is deleted
automatically when the process exits.
"""

import atexit
import logging
import shutil
import sys
import tempfile
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# ── Constants ────────────────────────────────────────────────────────────────

_KEY_CACHE_PATH  = Path.home() / ".launchway" / ".rkey"
_KEY_MAX_AGE_SEC = 24 * 3600   # refresh key every 24 hours
_ENC_ROOT        = Path(__file__).parent / "encrypted_agents"

# Module-level state (set once per process)
_bootstrap_done: bool = False
_tmp_dir: Optional[str] = None


# ── Key helpers ──────────────────────────────────────────────────────────────

def _load_cached_key() -> Optional[bytes]:
    """Return the cached Fernet key bytes if still fresh, else None."""
    if not _KEY_CACHE_PATH.exists():
        return None
    age = time.time() - _KEY_CACHE_PATH.stat().st_mtime
    if age > _KEY_MAX_AGE_SEC:
        return None
    raw = _KEY_CACHE_PATH.read_bytes()
    return raw if raw else None


def _save_key(key_bytes: bytes) -> None:
    _KEY_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    _KEY_CACHE_PATH.write_bytes(key_bytes)
    # Restrict read access to the current user only (best-effort on Windows)
    try:
        import stat
        _KEY_CACHE_PATH.chmod(stat.S_IRUSR | stat.S_IWUSR)
    except Exception:
        pass


# ── Main bootstrap function ──────────────────────────────────────────────────

def bootstrap_agents(api_client) -> bool:
    """
    Decrypt the encrypted Agents/ package into a temp dir and inject it
    into sys.path.  Safe to call multiple times — only runs once per process.

    Returns True on success, False on failure.
    """
    global _bootstrap_done, _tmp_dir

    if _bootstrap_done:
        return True

    try:
        from cryptography.fernet import Fernet, InvalidToken
    except ImportError:
        logger.error("cryptography package not installed. Run: pip install cryptography")
        return False

    # ── 1. Obtain decryption key ─────────────────────────────────────────────

    key_bytes = _load_cached_key()

    if not key_bytes:
        logger.debug("Key cache miss — fetching from server")
        try:
            key_b64 = api_client.get_agent_key()
            key_bytes = key_b64.encode() if isinstance(key_b64, str) else key_b64
            _save_key(key_bytes)
            logger.debug("Runtime key fetched and cached")
        except Exception as e:
            logger.error(f"Failed to fetch runtime key from server: {e}")
            return False

    # ── 2. Verify we have encrypted files ────────────────────────────────────

    if not _ENC_ROOT.exists() or not any(_ENC_ROOT.rglob("*.enc")):
        logger.error(f"Encrypted agents not found at {_ENC_ROOT}")
        return False

    # ── 3. Decrypt to a fresh temp directory ─────────────────────────────────

    f       = Fernet(key_bytes)
    tmp_dir = Path(tempfile.mkdtemp(prefix="lw_agents_"))

    try:
        enc_files = list(_ENC_ROOT.rglob("*.enc"))
        for enc_file in enc_files:
            rel      = enc_file.relative_to(_ENC_ROOT)
            out_file = tmp_dir / "Agents" / rel.with_suffix(".py")
            out_file.parent.mkdir(parents=True, exist_ok=True)
            out_file.write_bytes(f.decrypt(enc_file.read_bytes()))

        logger.debug(f"Decrypted {len(enc_files)} agent files to {tmp_dir}")

    except InvalidToken:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        # Stale cached key — delete cache and signal caller to retry
        _KEY_CACHE_PATH.unlink(missing_ok=True)
        logger.error("Decryption failed: cached key is invalid. "
                     "Deleted key cache — restart launchway to re-fetch.")
        return False
    except Exception as e:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        logger.error(f"Decryption error: {e}")
        return False

    # ── 4. Inject into sys.path ───────────────────────────────────────────────

    tmp_str = str(tmp_dir)
    if tmp_str not in sys.path:
        sys.path.insert(0, tmp_str)

    # ── 5. Schedule cleanup on exit ──────────────────────────────────────────

    atexit.register(shutil.rmtree, tmp_dir, ignore_errors=True)

    _tmp_dir        = tmp_str
    _bootstrap_done = True
    logger.info("Agent bootstrap complete")
    return True


def is_bootstrapped() -> bool:
    return _bootstrap_done
