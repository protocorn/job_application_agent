"""
Configuration management for Launchway CLI.

End-user config story:
  - No DATABASE_URL needed. All data goes through the Launchway Railway backend.
  - Users only need a Gemini API key for the local AI features (job ranking, etc.).
  - LAUNCHWAY_BACKEND_URL defaults to the production Railway deployment.

.env resolution order:
  1. CWD/.env
  2. ~/.launchway/.env   (user-specific, written by first-run wizard)
  3. repo root .env      (developer mode)
"""

import os
from pathlib import Path

_USER_CONFIG_DIR = Path.home() / ".launchway"
_USER_ENV_FILE   = _USER_CONFIG_DIR / ".env"

# Repo root = two levels up from this file (launchway/config.py -> launchway/ -> repo root)
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
        pass  # python-dotenv not installed — rely on system env vars


def _required_vars() -> list[dict]:
    """
    Configuration values requested from the user on first run.

    DATABASE_URL is intentionally excluded — all data access goes
    through the Launchway API, not a direct database connection.
    """
    return [
        {
            "key":         "GOOGLE_API_KEY",
            "description": "Gemini AI API key (from Google AI Studio — aistudio.google.com)",
            "example":     "AIzaSy...",
            "secret":      True,
        },
    ]


def run_first_time_setup():
    """
    Interactive wizard that collects required configuration and writes
    it to ~/.launchway/.env.  Skipped if all required vars are already set.
    """
    ensure_env_loaded()

    missing = [
        v for v in _required_vars()
        if not v.get("optional") and not os.getenv(v["key"])
    ]

    if not missing:
        return

    _USER_CONFIG_DIR.mkdir(parents=True, exist_ok=True)

    print("\n" + "=" * 60)
    print("  LAUNCHWAY — FIRST-TIME SETUP")
    print("=" * 60)
    print("\nA few configuration values are missing.")
    print(f"Your settings will be saved to: {_USER_ENV_FILE}\n")

    collected: dict[str, str] = {}

    import getpass

    for spec in missing:
        key         = spec["key"]
        description = spec["description"]
        example     = spec.get("example", "")

        print(f"\n{key}")
        print(f"  {description}")
        if example:
            print(f"  Example: {example}")

        while True:
            if spec.get("secret"):
                value = getpass.getpass("  Enter value: ").strip()
            else:
                value = input("  Enter value: ").strip()

            if value:
                break

            print("  [!] Value is required — please enter it.")

        collected[key] = value

    # Preserve existing keys; only append new ones
    existing_lines: list[str] = []
    if _USER_ENV_FILE.exists():
        existing_lines = _USER_ENV_FILE.read_text(encoding="utf-8").splitlines()

    existing_keys = set()
    for line in existing_lines:
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            existing_keys.add(line.split("=", 1)[0].strip())

    new_lines = list(existing_lines)
    if new_lines and new_lines[-1] != "":
        new_lines.append("")

    for key, value in collected.items():
        if key not in existing_keys:
            new_lines.append(f'{key}="{value}"')

    _USER_ENV_FILE.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
    print(f"\n[OK] Configuration saved to {_USER_ENV_FILE}")

    try:
        from dotenv import load_dotenv
        load_dotenv(_USER_ENV_FILE, override=True)
    except ImportError:
        pass

    print("\nSetup complete! Starting Launchway...\n")


def get_config() -> dict:
    """Return a snapshot of the active configuration (safe to log)."""
    ensure_env_loaded()
    return {
        "GOOGLE_API_KEY":        bool(os.getenv("GOOGLE_API_KEY")),
        "LAUNCHWAY_BACKEND_URL": os.getenv(
            "LAUNCHWAY_BACKEND_URL",
            "https://jobapplicationagent-production.up.railway.app",
        ),
        "ENV": os.getenv("ENV", "production"),
    }
