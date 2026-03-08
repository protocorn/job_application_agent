"""
Configuration management for Launchway CLI.

AI Engine story:
  - "launchway"  (default): no API key needed; uses Launchway's built-in AI
  - "custom":               user supplies their own GOOGLE_API_KEY

.env resolution order:
  1. CWD/.env
  2. ~/.launchway/.env   (written by first-run wizard)
  3. repo root .env      (developer mode)
"""

import os
from pathlib import Path

_USER_CONFIG_DIR = Path.home() / ".launchway"
_USER_ENV_FILE   = _USER_CONFIG_DIR / ".env"

_REPO_ROOT = Path(__file__).resolve().parent.parent
_REPO_ENV  = _REPO_ROOT / ".env"


def _find_env_file() -> Path | None:
    candidates = [
        Path(os.getcwd()) / ".env",
        _USER_ENV_FILE,
        _REPO_ENV,
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def ensure_env_loaded():
    """Load environment variables from .env if not already loaded."""
    try:
        from dotenv import load_dotenv
        env_file = _find_env_file()
        if env_file:
            load_dotenv(env_file, override=False)
    except ImportError:
        pass


def _setup_already_done() -> bool:
    """
    True if first-time setup already ran.
    We treat AI_SETUP_DONE=1, AI_PROVIDER, or GOOGLE_API_KEY as done.
    """
    return bool(
        os.getenv("AI_SETUP_DONE") == "1"
        or os.getenv("AI_PROVIDER")
        or os.getenv("GOOGLE_API_KEY")
    )


def _append_to_user_env(entries: dict[str, str]):
    """Write key=value pairs to ~/.launchway/.env, preserving existing content."""
    _USER_CONFIG_DIR.mkdir(parents=True, exist_ok=True)

    existing_lines: list[str] = []
    if _USER_ENV_FILE.exists():
        existing_lines = _USER_ENV_FILE.read_text(encoding="utf-8").splitlines()

    existing_keys: set[str] = set()
    for line in existing_lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            existing_keys.add(stripped.split("=", 1)[0].strip())

    new_lines = list(existing_lines)
    if new_lines and new_lines[-1] != "":
        new_lines.append("")

    for key, value in entries.items():
        if key not in existing_keys:
            new_lines.append(f'{key}="{value}"')

    _USER_ENV_FILE.write_text("\n".join(new_lines) + "\n", encoding="utf-8")

    try:
        from dotenv import load_dotenv
        load_dotenv(_USER_ENV_FILE, override=True)
    except ImportError:
        pass


def run_first_time_setup():
    """
    Optional first-run wizard. Lets users choose their AI Engine mode (or skip).
    Skipped silently if a choice was already saved.
    """
    ensure_env_loaded()

    if _setup_already_done():
        return

    print("\n" + "=" * 60)
    print("  LAUNCHWAY — FIRST-TIME SETUP")
    print("=" * 60)
    print("""
Welcome to Launchway!

Launchway uses AI to match jobs to your profile and fill
application forms. Choose how you want to power AI features:

  1. Use Launchway AI  (recommended — no API key needed)
     Works out of the box. Best choice for most users.

  2. Use my own Gemini API key
     Get a free key at aistudio.google.com
     Good for power users or very heavy usage.

  3. Skip for now — decide later
     You can always change this from the Settings menu.
""")

    choice = input("  Your choice [1/2/3, default: 1]: ").strip() or "1"

    if choice == "2":
        # ── Custom Gemini key ────────────────────────────────────────────────
        import getpass
        print()
        print("  Enter your Gemini API key.")
        print("  Get one free at: https://aistudio.google.com")
        print("  (Press Enter to skip and use Launchway AI instead.)\n")

        api_key = getpass.getpass("  Gemini API Key: ").strip()

        if api_key:
            _append_to_user_env({"GOOGLE_API_KEY": api_key, "AI_PROVIDER": "custom", "AI_SETUP_DONE": "1"})
            print("\n  [OK] Custom Gemini key saved.")
            print(f"  Config file: {_USER_ENV_FILE}")
        else:
            # User pressed Enter — treat as "use Launchway AI"
            _append_to_user_env({"AI_PROVIDER": "launchway", "AI_SETUP_DONE": "1"})
            print("\n  No key entered — Launchway AI will be used.")

    elif choice == "3":
        # ── Skip entirely — do not write anything ────────────────────────────
        print()
        print("  [OK] Setup skipped.")
        print("  You can configure your AI Engine anytime from:")
        print("    launchway  →  Settings  →  AI Engine")
        # Mark as done so the wizard does not repeat on next launch,
        # but intentionally leave AI engine choice unset.
        _append_to_user_env({"AI_PROVIDER": "", "AI_SETUP_DONE": "1"})

    else:
        # ── Default: Launchway AI ────────────────────────────────────────────
        _append_to_user_env({"AI_PROVIDER": "launchway", "AI_SETUP_DONE": "1"})
        print()
        print("  [OK] Launchway AI selected — no API key needed.")

    print("\nSetup complete! Starting Launchway...\n")


def get_config() -> dict:
    """Return a snapshot of the active configuration (safe to log)."""
    ensure_env_loaded()
    provider = os.getenv("AI_PROVIDER", "")
    if not provider:
        provider = "custom" if os.getenv("GOOGLE_API_KEY") else "not_configured"
    return {
        "AI_PROVIDER":          provider,
        "GOOGLE_API_KEY":       bool(os.getenv("GOOGLE_API_KEY")),
        "LAUNCHWAY_BACKEND_URL": os.getenv(
            "LAUNCHWAY_BACKEND_URL",
            "https://jobapplicationagent-production.up.railway.app",
        ),
        "ENV": os.getenv("ENV", "production"),
    }
