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
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# ── Production env defaults (applied every bootstrap) ───────────────────────
# These are safe defaults that should always be set for production use.
# They can be overridden by the user's ~/.launchway/.env or system env vars.

_PRODUCTION_DEFAULTS = {
    "MIMIKREE_BASE_URL": "https://www.mimikree.com",
}


def _apply_env_defaults():
    """
    Set production defaults and restore server-provided keys on every bootstrap.
    Called on both cache-hit and cache-miss runs so env vars are always present.
    """
    # Static production defaults (e.g. Mimikree URL)
    for key, default_value in _PRODUCTION_DEFAULTS.items():
        if not os.getenv(key):
            os.environ[key] = default_value

    # Gemini key — user's own key takes priority; fall back to the server-provided
    # key that was cached the last time a full bundle fetch was performed.
    if not os.getenv("GOOGLE_API_KEY"):
        cached = _load_cached_gemini_key()
        if cached:
            os.environ["GOOGLE_API_KEY"] = cached
            logger.debug("Restored GOOGLE_API_KEY from local cache")


# ── Constants ────────────────────────────────────────────────────────────────

_KEY_CACHE_PATH     = Path.home() / ".launchway" / ".rkey"
_GEMINI_KEY_CACHE   = Path.home() / ".launchway" / ".gemini_key"
_KEY_MAX_AGE_SEC    = 24 * 3600   # refresh key every 24 hours
_ENC_ROOT           = Path(__file__).parent / "encrypted_agents"

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
    try:
        import stat
        _KEY_CACHE_PATH.chmod(stat.S_IRUSR | stat.S_IWUSR)
    except Exception:
        pass


def _load_cached_gemini_key() -> Optional[str]:
    """Return the server-provided Gemini key cached from the last bundle fetch."""
    if not _GEMINI_KEY_CACHE.exists():
        return None
    val = _GEMINI_KEY_CACHE.read_text(encoding="utf-8").strip()
    return val if val else None


def _save_gemini_key(key: str) -> None:
    _GEMINI_KEY_CACHE.parent.mkdir(parents=True, exist_ok=True)
    _GEMINI_KEY_CACHE.write_text(key, encoding="utf-8")
    try:
        import stat
        _GEMINI_KEY_CACHE.chmod(stat.S_IRUSR | stat.S_IWUSR)
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

    # Always inject production env vars that are env-dependent (not stored in key cache)
    # We re-set them every bootstrap even on cache hit, because os.environ resets each process
    _apply_env_defaults()

    if not key_bytes:
        logger.debug("Key cache miss — fetching from server")
        try:
            bundle    = api_client.get_agent_key()   # returns dict with key + extras
            key_b64   = bundle if isinstance(bundle, str) else bundle.get("key", "")
            key_bytes = key_b64.encode() if isinstance(key_b64, str) else key_b64
            _save_key(key_bytes)
            logger.debug("Runtime key fetched and cached")

            # ── Inject service env vars from the bundle ──────────────────────
            # Only set each var if the user hasn't already configured their own.

            if isinstance(bundle, dict):
                # Gemini API key — needed by systematic_tailoring_complete.py
                gemini_key = bundle.get("gemini_key", "")
                if gemini_key:
                    _save_gemini_key(gemini_key)   # persist for future cache-hit runs
                    if not os.getenv("GOOGLE_API_KEY"):
                        os.environ["GOOGLE_API_KEY"] = gemini_key
                        logger.debug("Set GOOGLE_API_KEY from server bundle (Launchway AI)")

                # Mimikree production URL — overrides localhost default in agent code
                mimikree_url = bundle.get("mimikree_url", "")
                if mimikree_url and not os.getenv("MIMIKREE_BASE_URL"):
                    os.environ["MIMIKREE_BASE_URL"] = mimikree_url
                    logger.debug(f"Set MIMIKREE_BASE_URL={mimikree_url}")

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

    # ── 4. Decrypt support files (credentials.json, etc.) ───────────────────
    # These go to tmp_dir/ (the "project_root" the agents expect)

    support_root = Path(__file__).parent / "encrypted_support"
    if support_root.exists():
        for enc_file in support_root.glob("*.enc"):
            # e.g. credentials.json.enc -> tmp_dir/credentials.json
            out_name = enc_file.stem          # strips last suffix (.enc)
            out_file = tmp_dir / out_name
            try:
                out_file.write_bytes(f.decrypt(enc_file.read_bytes()))
                logger.debug(f"Decrypted support file: {out_name}")
            except Exception as e:
                logger.warning(f"Could not decrypt support file {enc_file.name}: {e}")

    # ── 5. Seed token.json from persistent store ──────────────────────────────
    # The agent reads/writes token.json at tmp_dir/token.json.
    # We copy the persisted token from ~/.launchway/token.json so the user
    # does not have to re-authenticate via browser on every launch.

    _TOKEN_STORE = Path.home() / ".launchway" / "token.json"
    _tmp_token   = tmp_dir / "token.json"

    if _TOKEN_STORE.exists() and not _tmp_token.exists():
        try:
            shutil.copy2(_TOKEN_STORE, _tmp_token)
            logger.debug("Copied token.json from ~/.launchway/")
        except Exception as e:
            logger.warning(f"Could not copy token.json: {e}")

    # ── 6. Write stubs for repo-root utilities that agents import ────────────

    _LOGGING_CONFIG_STUB = '''\
import logging

def setup_file_logging(log_level=logging.INFO, console_logging=False, **kwargs):
    """Stub: file logging is handled by the CLI entry point."""
    pass
'''
    stub = tmp_dir / "logging_config.py"
    if not stub.exists():
        stub.write_text(_LOGGING_CONFIG_STUB, encoding="utf-8")

    # ── 7. Inject into sys.path ───────────────────────────────────────────────
    # Add BOTH tmp_dir and tmp_dir/Agents so that:
    #   - "from Agents.xxx import yyy"  (called by CLI mixins) works via tmp_dir
    #   - "from xxx import yyy"          (inter-agent imports)  works via tmp_dir/Agents

    tmp_str       = str(tmp_dir)
    tmp_agents    = str(tmp_dir / "Agents")
    for p in (tmp_agents, tmp_str):   # agents dir first so intra-agent imports win
        if p not in sys.path:
            sys.path.insert(0, p)

    # ── 8. Persist token.json back to ~/.launchway/ on exit ──────────────────
    # The agent writes a new/refreshed token.json to tmp_dir after OAuth.
    # We save it back so the next session doesn't need a browser login.

    def _persist_token(tmp_path: str):
        try:
            src = Path(tmp_path) / "token.json"
            if src.exists():
                dst = Path.home() / ".launchway" / "token.json"
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
                logger.debug("Persisted token.json to ~/.launchway/")
        except Exception as e:
            logger.warning(f"Could not persist token.json: {e}")

    atexit.register(_persist_token, tmp_str)

    # ── 9. Schedule temp dir cleanup on exit ─────────────────────────────────

    atexit.register(shutil.rmtree, tmp_dir, ignore_errors=True)

    _tmp_dir        = tmp_str
    _bootstrap_done = True
    logger.info("Agent bootstrap complete")
    return True


def is_bootstrapped() -> bool:
    return _bootstrap_done
