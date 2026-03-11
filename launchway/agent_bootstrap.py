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
import hashlib
import importlib.abc
import importlib.util
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


def _set_gemini_env(key_value: str) -> None:
    """
    Set both supported Gemini env var names unless the user explicitly set one.
    Many agent components look for GEMINI_API_KEY while others use GOOGLE_API_KEY.
    """
    if not key_value:
        return
    if not os.getenv("GOOGLE_API_KEY"):
        os.environ["GOOGLE_API_KEY"] = key_value
    if not os.getenv("GEMINI_API_KEY"):
        os.environ["GEMINI_API_KEY"] = key_value


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
    if not os.getenv("GOOGLE_API_KEY") and not os.getenv("GEMINI_API_KEY"):
        cached = _load_cached_gemini_key()
        if cached:
            _set_gemini_env(cached)
            logger.debug("Restored Gemini API key env vars from local cache")


# ── Constants ────────────────────────────────────────────────────────────────

_KEY_CACHE_PATH     = Path.home() / ".launchway" / ".rkey"
_GEMINI_KEY_CACHE   = Path.home() / ".launchway" / ".gemini_key"
_KEY_MAX_AGE_SEC    = 24 * 3600   # refresh key every 24 hours
_ENC_ROOT           = Path(__file__).parent / "encrypted_agents"
_KEY_FINGERPRINT_FILE = _ENC_ROOT / "key_fingerprint.txt"

# Module-level state (set once per process)
_bootstrap_done: bool = False
_tmp_dir: Optional[str] = None
_bootstrap_diag: dict = {
    "source": "none",  # server|cache|none
    "loader_mode": "disk_decrypt",
    "bundle_gemini_key": "",
    "effective_google_api_key": "",
    "effective_gemini_api_key": "",
}

_import_finder = None


class _SyntheticPackageLoader(importlib.abc.Loader):
    """Creates an empty package module used as a namespace anchor."""

    def create_module(self, spec):
        return None

    def exec_module(self, module):
        return None


class _EncryptedModuleLoader(importlib.abc.Loader):
    """
    Decrypts a single encrypted module in memory and executes it.
    No plaintext Python source is written to disk.
    """

    def __init__(self, fullname: str, enc_file: Path, is_package: bool, fernet_obj, runtime_root: Path):
        self.fullname = fullname
        self.enc_file = enc_file
        self.is_package = is_package
        self.fernet_obj = fernet_obj
        self.runtime_root = runtime_root

    def create_module(self, spec):
        return None

    def exec_module(self, module):
        encrypted = self.enc_file.read_bytes()
        decrypted = self.fernet_obj.decrypt(encrypted)
        source = decrypted.decode("utf-8")

        if self.fullname.startswith("Agents."):
            rel_parts = self.fullname.split(".")[1:]
        else:
            rel_parts = self.fullname.split(".")

        if self.is_package:
            pseudo_file = self.runtime_root.joinpath(*rel_parts, "__init__.py")
        else:
            pseudo_file = self.runtime_root.joinpath(*rel_parts).with_suffix(".py")

        pseudo_file.parent.mkdir(parents=True, exist_ok=True)
        filename = str(pseudo_file)
        code = compile(source, filename, "exec")

        module.__file__ = filename
        module.__loader__ = self
        module.__package__ = self.fullname if self.is_package else self.fullname.rpartition(".")[0]
        if self.is_package:
            module.__path__ = [str(pseudo_file.parent)]

        exec(code, module.__dict__)

        # Best-effort cleanup of sensitive plaintext in local scope.
        del source
        del decrypted
        del encrypted


class _EncryptedAgentsFinder(importlib.abc.MetaPathFinder):
    """
    Resolves encrypted agent modules from launchway/encrypted_agents.
    Supports both:
      - Agents.xxx imports
      - top-level imports expected by legacy agent code (e.g. components.*)
    """

    def __init__(self, enc_root: Path, fernet_obj, runtime_root: Path):
        self.enc_root = enc_root
        self.fernet_obj = fernet_obj
        self.runtime_root = runtime_root
        self._pkg_loader = _SyntheticPackageLoader()

    def _resolve_under_root(self, module_path: str):
        rel = module_path.replace(".", "/")
        mod_file = self.enc_root / f"{rel}.enc"
        if mod_file.exists():
            return mod_file, False
        pkg_init = self.enc_root / rel / "__init__.enc"
        if pkg_init.exists():
            return pkg_init, True
        return None, False

    def find_spec(self, fullname, path=None, target=None):
        if fullname == "Agents":
            return importlib.util.spec_from_loader(fullname, self._pkg_loader, is_package=True)

        enc_file = None
        is_pkg = False

        if fullname.startswith("Agents."):
            stripped = fullname[len("Agents."):]
            if stripped:
                enc_file, is_pkg = self._resolve_under_root(stripped)
        else:
            # Legacy intra-agent imports often reference top-level modules/packages
            # that physically live under Agents/ in source.
            enc_file, is_pkg = self._resolve_under_root(fullname)

        if not enc_file:
            return None

        loader = _EncryptedModuleLoader(
            fullname=fullname,
            enc_file=enc_file,
            is_package=is_pkg,
            fernet_obj=self.fernet_obj,
            runtime_root=self.runtime_root,
        )
        return importlib.util.spec_from_loader(fullname, loader, is_package=is_pkg)


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


def _key_fingerprint(key_bytes: bytes) -> str:
    return hashlib.sha256(key_bytes).hexdigest()


def _validate_bundle_key(fernet_obj, key_bytes: bytes):
    """
    Validate that the runtime key matches this package's encrypted bundle.
    Returns (ok: bool, reason: str)
    """
    expected = ""
    if _KEY_FINGERPRINT_FILE.exists():
        expected = _KEY_FINGERPRINT_FILE.read_text(encoding="utf-8").strip()
        if expected:
            actual = _key_fingerprint(key_bytes)
            if actual != expected:
                return False, (
                    "Bundle key mismatch: runtime key does not match this release's "
                    "encrypted agent bundle."
                )

    # Backward compatibility: older bundles may not have fingerprint file.
    # In that case, attempt to decrypt one encrypted module now to fail early.
    probe = next(_ENC_ROOT.rglob("*.enc"), None)
    if not probe:
        return False, f"No encrypted module files found under {_ENC_ROOT}"
    try:
        fernet_obj.decrypt(probe.read_bytes())
    except Exception:
        if expected:
            return False, (
                "Bundle key mismatch: runtime key fingerprint matched, but decrypt "
                "probe still failed."
            )
        return False, (
            "Runtime key does not match encrypted bundle (legacy bundle without "
            "fingerprint metadata)."
        )
    return True, ""


# ── Main bootstrap function ──────────────────────────────────────────────────

def bootstrap_agents(api_client) -> bool:
    """
    Decrypt the encrypted Agents/ package into a temp dir and inject it
    into sys.path.  Safe to call multiple times — only runs once per process.

    Returns True on success, False on failure.
    """
    global _bootstrap_done, _tmp_dir, _import_finder

    if _bootstrap_done:
        return True

    try:
        from cryptography.fernet import Fernet, InvalidToken
    except ImportError:
        logger.error("cryptography package not installed. Run: pip install cryptography")
        return False

    # ── 1. Obtain decryption key ─────────────────────────────────────────────

    key_bytes = _load_cached_key()
    _bootstrap_diag["source"] = "cache" if key_bytes else "server"

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
                _bootstrap_diag["bundle_gemini_key"] = gemini_key
                if gemini_key:
                    _save_gemini_key(gemini_key)   # persist for future cache-hit runs
                    _set_gemini_env(gemini_key)
                    logger.debug("Set Gemini API key env vars from server bundle (Launchway AI)")

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

    # ── 3. Configure in-memory encrypted importer ────────────────────────────

    if not key_bytes:
        logger.error(
            "Runtime key missing/empty from server. "
            "Cannot decrypt encrypted agents."
        )
        return False

    try:
        f = Fernet(key_bytes)
    except Exception as e:
        logger.error(
            f"Invalid runtime key format from server: {e}. "
            "Expected Fernet base64 key."
        )
        return False

    key_ok, key_reason = _validate_bundle_key(f, key_bytes)
    if not key_ok:
        logger.error(key_reason)
        return False

    tmp_dir = Path(tempfile.mkdtemp(prefix="lw_agents_"))
    runtime_agents_root = tmp_dir / "Agents"
    runtime_agents_root.mkdir(parents=True, exist_ok=True)

    _import_finder = _EncryptedAgentsFinder(
        enc_root=_ENC_ROOT,
        fernet_obj=f,
        runtime_root=runtime_agents_root,
    )
    if _import_finder not in sys.meta_path:
        sys.meta_path.insert(0, _import_finder)

    _bootstrap_diag["loader_mode"] = "memory_decrypt_import"
    logger.debug("Encrypted agent importer installed (in-memory decrypt)")

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
    # These modules exist in the developer's repo but are not shipped.
    # Stubs let agent code import them without crashing.

    _STUBS = {
        "logging_config.py": '''\
import logging

def setup_file_logging(log_level=logging.INFO, console_logging=False, **kwargs):
    pass
''',
        # database_config is used by CompanyCredentialsService (account creation).
        # We provide a no-op stub so credentials are generated per-session but
        # not persisted to a local DB (the server tracks them instead).
        "database_config.py": '''\
import logging
logger = logging.getLogger(__name__)

class _NoopSession:
    def query(self, *a, **kw): return self
    def filter(self, *a, **kw): return self
    def filter_by(self, *a, **kw): return self
    def first(self): return None
    def all(self): return []
    def add(self, obj): pass
    def commit(self): pass
    def rollback(self): pass
    def close(self): pass
    def __enter__(self): return self
    def __exit__(self, *a): pass

def get_db():
    return _NoopSession()

def get_db_session():
    return _NoopSession()

SessionLocal = _NoopSession

class Base: pass

class User(Base):
    id = None; email = None; first_name = None; last_name = None

class JobApplication(Base):
    id = None; job_url = None; user_id = None; company = None; position = None

engine = None
''',
    }

    for filename, content in _STUBS.items():
        stub_path = tmp_dir / filename
        if not stub_path.exists():
            stub_path.write_text(content, encoding="utf-8")

    # ── 7. Inject tmp root into sys.path ─────────────────────────────────────
    # Keep tmp_dir importable for support modules and compatibility stubs.
    tmp_str       = str(tmp_dir)
    if tmp_str not in sys.path:
        sys.path.insert(0, tmp_str)

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

    # Capture final effective env values for diagnostics
    _bootstrap_diag["effective_google_api_key"] = os.getenv("GOOGLE_API_KEY", "")
    _bootstrap_diag["effective_gemini_api_key"] = os.getenv("GEMINI_API_KEY", "")

    _tmp_dir        = tmp_str
    _bootstrap_done = True
    logger.info("Agent bootstrap complete")
    return True


def is_bootstrapped() -> bool:
    return _bootstrap_done

def get_bootstrap_diagnostics() -> dict:
    """Return runtime diagnostics for key hydration troubleshooting."""
    return dict(_bootstrap_diag)

