"""
Post-install helper: ensure Playwright browser binaries are present.

Called automatically on first `launchway` run if browsers are missing.
Can also be invoked manually:
  python -m launchway.postinstall
"""

import subprocess
import sys


REQUIRED_BROWSERS = ["chromium"]


def check_browsers_installed() -> bool:
    """Return True if all required Playwright browser binaries are present."""
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as pw:
            # Attempt to launch headlessly — will throw if binary is missing
            browser = pw.chromium.launch(headless=True)
            browser.close()
        return True
    except Exception:
        return False


def install_browsers(browsers: list[str] = REQUIRED_BROWSERS):
    """Download Playwright browser binaries."""
    print("[INFO] Installing Playwright browser binaries (first-time setup)...")
    print("       This only happens once and may take a few minutes.\n")

    for browser in browsers:
        print(f"  Installing {browser}...")
        result = subprocess.run(
            [sys.executable, "-m", "playwright", "install", browser],
            capture_output=False,
        )
        if result.returncode != 0:
            print(f"\n[ERROR] Failed to install {browser}.")
            print("  Please run manually:  python -m playwright install chromium")
            sys.exit(1)

    print("\n[OK] Playwright browser binaries installed successfully.\n")


def ensure_browsers():
    """Install Playwright browsers if they are not already present."""
    if not check_browsers_installed():
        install_browsers()


if __name__ == "__main__":
    install_browsers()
