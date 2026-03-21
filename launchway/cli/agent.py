"""
CLIJobAgent - main class combining all feature mixins.
Entry point: `launchway.cli.agent:main`

All user data operations (auth, profile, applications, credits) are routed
through LaunchwayClient to the Railway backend.  No direct database access.
"""

import asyncio
import logging
import os
import sys
from typing import Any, Dict, Optional

# When running from source (dev mode), add the repo root so Agents/ is importable.
# When installed as a package, agent_bootstrap.py handles sys.path injection
# by decrypting encrypted_agents/ to a temp dir at runtime.
_PACKAGE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _PACKAGE_DIR not in sys.path:
    sys.path.insert(0, _PACKAGE_DIR)

from launchway.api_client           import LaunchwayClient
from launchway.cli.utils            import Colors, PrintMixin
from launchway.cli.mixins.auth          import AuthMixin
from launchway.cli.mixins.profile       import ProfileMixin
from launchway.cli.mixins.tailoring     import TailoringMixin
from launchway.cli.mixins.job_search    import JobSearchMixin
from launchway.cli.mixins.apply         import ApplyMixin
from launchway.cli.mixins.continuous    import ContinuousApplyMixin
from launchway.cli.mixins.browser_setup import BrowserSetupMixin
from launchway.cli.mixins.history       import HistoryMixin
from launchway.cli.mixins.settings      import SettingsMixin

logger = logging.getLogger(__name__)


class CLIJobAgent(
    PrintMixin,
    AuthMixin,
    ProfileMixin,
    TailoringMixin,
    JobSearchMixin,
    ApplyMixin,
    ContinuousApplyMixin,
    BrowserSetupMixin,
    HistoryMixin,
    SettingsMixin,
):
    """Terminal-based Job Application Agent."""

    def __init__(self):
        # HTTP client for the Railway backend - single source of truth for all data
        self.api: LaunchwayClient = LaunchwayClient()

        # current_user is a plain dict returned by the login API:
        #   { id, email, first_name, last_name, created_at, ... }
        # Access as self.current_user['id'], self.current_user['email'], etc.
        self.current_user: Optional[Dict[str, Any]]    = None
        self.current_profile: Optional[Dict[str, Any]] = None
        self.running: bool = True

        # Session-scoped Mimikree credentials (cleared on logout)
        self._session_mimikree_email: Optional[str]    = None
        self._session_mimikree_password: Optional[str] = None

        # Flags for incomplete-application handling
        self._should_open_incomplete: bool  = False
        self._incomplete_report_file: Optional[str] = None

        # Set to True once agent_bootstrap has decrypted agents into sys.path
        self._agents_bootstrapped: bool = False

    # ------------------------------------------------------------------
    # Agent bootstrap
    # ------------------------------------------------------------------

    def _ensure_agents_bootstrapped(self) -> bool:
        """
        Decrypt the encrypted Agents package into a session temp dir and inject
        it into sys.path.  Must be called (and return True) before any mixin
        method that does `from Agents.xxx import yyy`.

        Safe to call multiple times - the heavy work only runs once per process.
        Returns True when agents are ready, False on any failure.
        """
        if self._agents_bootstrapped:
            return True

        if not self.current_user:
            self.print_error("You must be logged in to use this feature.")
            return False

        # Fast path: already bootstrapped by a previous call in this process
        from launchway.agent_bootstrap import (
            is_bootstrapped,
            bootstrap_agents,
        )
        if is_bootstrapped():
            self._agents_bootstrapped = True
            return True

        self.print_info("🔐 Loading automation engine (one moment)...")
        try:
            ok = bootstrap_agents(self.api)
            if ok:
                self._agents_bootstrapped = True
                self.print_success("✓ Automation engine ready")
                return True
            else:
                self.print_error(
                    "Failed to load automation engine.\n"
                    "  • Make sure you are connected to the internet.\n"
                    "  • Try logging out and back in (re-fetches the key).\n"
                    "  • If this persists, report it at: https://www.launchway.app/report-bug"
                )
                return False
        except Exception as e:
            self.print_error(f"Bootstrap error: {e}")
            logger.error(f"Agent bootstrap error: {e}", exc_info=True)
            return False

    # ------------------------------------------------------------------
    # Main menu
    # ------------------------------------------------------------------

    def show_main_menu(self):
        while self.running and self.current_user:
            self.clear_screen()
            self.print_header("LAUNCHWAY - JOB APPLICATION AGENT")

            u = self.current_user
            print(f"  Logged in as: {Colors.OKGREEN}{u['first_name']} {u['last_name']}{Colors.ENDC}")
            print(f"  Email:        {Colors.OKCYAN}{u['email']}{Colors.ENDC}\n")

            print(f"  {Colors.BOLD}1.{Colors.ENDC}  Profile Management")
            print(f"  {Colors.BOLD}2.{Colors.ENDC}  Resume Tailoring")
            print(f"  {Colors.BOLD}3.{Colors.ENDC}  Search Jobs")
            print(f"  {Colors.BOLD}4.{Colors.ENDC}  Assisted Auto-Apply (Batch, up to 10)")
            print(f"  {Colors.BOLD}5.{Colors.ENDC}  View Application History")
            print(f"  {Colors.BOLD}6.{Colors.ENDC}  Fully Autonomous Auto-Apply - Continuous")
            print(f"  {Colors.BOLD}7.{Colors.ENDC}  Browser Profile Setup (One-Time Setup)")
            print(f"  {Colors.BOLD}8.{Colors.ENDC}  Settings")
            print(f"  {Colors.BOLD}9.{Colors.ENDC}  Logout")
            print(f"  {Colors.BOLD}10.{Colors.ENDC} Exit\n")

            choice = self.get_input("Select option (1-10): ").strip()

            if   choice == '1':  self.profile_menu()
            elif choice == '2':  self.resume_tailoring_menu()
            elif choice == '3':  self.job_search_menu()
            elif choice == '4':  asyncio.run(self.auto_apply_menu())
            elif choice == '5':  self.view_application_history()
            elif choice == '6':  asyncio.run(self.continuous_auto_apply_menu())
            elif choice == '7':  asyncio.run(self.browser_profile_setup_menu())
            elif choice == '8':  self.settings_menu()
            elif choice == '9':
                self.logout()
                break
            elif choice == '10':
                self.running = False
                break
            else:
                self.print_error("Invalid option")
                self.pause()

    # ------------------------------------------------------------------
    # Application loop
    # ------------------------------------------------------------------

    def run(self):
        try:
            self.show_auth_menu()
            if not self.running:
                self.clear_screen()
                self.print_success("Thank you for using Launchway!")
                print(f"{Colors.OKCYAN}Goodbye!{Colors.ENDC}\n")
        except KeyboardInterrupt:
            self.clear_screen()
            self.print_warning("\nApplication interrupted by user")
            print(f"{Colors.OKCYAN}Goodbye!{Colors.ENDC}\n")
        except Exception as e:
            self.print_error(f"An error occurred: {str(e)}")
            logger.error(f"Application error: {e}", exc_info=True)


class _AgentProgressHandler(logging.Handler):
    """
    Translates verbose agent log messages into clean, user-friendly status lines.
    Only high-level progress milestones are shown on the console; everything else
    stays in the log file.
    """

    # Loggers we monitor for progress messages
    _MONITORED = {
        "Agents.job_application_agent",
        "Agents.multi_source_job_discovery_agent",
        "components.router.state_machine",
        "components.executors.generic_form_filler_v2_enhanced",
        "components.executors.field_interactor_v2",
        "components.executors.account_creation_handler",
    }

    # (substring_to_match, friendly_message)
    # friendly_message=None → use the cleaned original log text
    # Order matters - first match wins
    _RULES = [
        # ── State machine transitions ─────────────────────────────────────
        (">>> State: AI_GUIDED_NAVIGATION",     "  Navigating through pages..."),
        (">>> State: ANALYZE_AND_FILL_FORM",    "  Analyzing form fields..."),
        # ── Application start ──────────────────────────────────────────────
        ("Detected application start page",    "  Found application start page"),
        ("Clicking manual application",        "  Selecting 'Apply Manually'..."),
        ("Clicking autofill",                  "  Selecting autofill option..."),
        # ── Form filling ───────────────────────────────────────────────────
        ("Starting enhanced form filling",     "  Filling application form..."),
        ("Attempting resume upload",           "  Uploading resume..."),
        ("resume upload field found on",       None),      # skip - too noisy
        ("No resumme upload field",            None),      # skip (typo in agent)
        ("Form filling completed",             None),      # show cleaned original
        ("Form filled successfully",           None),      # show cleaned original
        # ── Account creation ───────────────────────────────────────────────
        ("Generating new credentials",         "  Creating account credentials..."),
        ("Found existing credentials",         "  Using saved credentials for this site"),
        ("Filling password field",             "  Filling password..."),
        ("Filling confirm password",           "  Confirming password..."),
        # ── Navigation buttons ─────────────────────────────────────────────
        ("Clicking next",                      "  Clicking Next button..."),
        ("Clicking submit",                    "  Clicking Submit button..."),
        # ── State machine outcomes ─────────────────────────────────────────
        ("Stagnation detected",                "  Page appears stuck - attempting recovery..."),
        ("Loop detected",                      "  Loop detected - stopping this application"),
        ("human_intervention",                 "  Browser kept open for your review"),
        ("State machine finished",             None),      # show cleaned original
        # ── Job search ─────────────────────────────────────────────────────
        ("Searching JobSpy",                   "  Searching jobs across job boards..."),
        ("Searching JSearch",                  "  Searching JSearch..."),
        ("Found",                              None),      # e.g. "Found 7 jobs" - show as-is
        ("Ranked",                             None),      # e.g. "Ranked 7 jobs with min_score=..."
    ]

    def __init__(self):
        super().__init__()
        self._last_displayed = None   # simple deduplication

    def emit(self, record: logging.LogRecord):
        if record.name not in self._MONITORED:
            return
        if record.levelno < logging.INFO:
            return

        raw = record.getMessage()

        for search, display in self._RULES:
            if search.lower() in raw.lower():
                if display is None:
                    # Clean up the original message: strip emoji-heavy prefixes,
                    # newlines, and truncate long lines.
                    text = raw.strip().split("\n")[0]
                    # Strip leading special chars / emoji (keep first 90 chars)
                    text = text.lstrip("🔍📝✅❌⚠️🤖➡️🎯🚀📄🔒🏁💬📤💾🔑")
                    text = "  " + text.strip()[:90]
                else:
                    text = display

                # Deduplicate consecutive identical messages
                if text == self._last_displayed:
                    return
                self._last_displayed = text

                from launchway.cli.utils import Colors
                print(f"{Colors.OKCYAN}{text}{Colors.ENDC}", flush=True)
                return


def _configure_logging():
    """
    Send all logs to ~/.launchway/logs/launchway.log (full debug detail).
    Console shows only: clean agent progress via _AgentProgressHandler, plus
    ERROR+ from the launchway package itself.
    """
    from pathlib import Path
    log_dir = Path.home() / ".launchway" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "launchway.log"

    # Configure loguru first so 3rd-party/agent modules do not spam stderr.
    try:
        from loguru import logger as loguru_logger
        loguru_logger.remove()
        loguru_logger.add(
            str(log_file),
            level="DEBUG",
            enqueue=False,
            backtrace=False,
            diagnose=False,
        )
    except Exception:
        # loguru is optional in some environments
        pass

    # ── File handler: everything ──────────────────────────────────────────
    file_handler = logging.FileHandler(log_file, encoding="utf-8", mode="a")
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(name)s:%(lineno)d - %(message)s"
    ))

    # ── Console handler: launchway errors only ────────────────────────────
    error_handler = logging.StreamHandler(sys.stderr)
    error_handler.setLevel(logging.ERROR)
    error_handler.addFilter(lambda r: r.name.startswith("launchway"))

    # ── Progress handler: friendly agent status lines ─────────────────────
    progress_handler = _AgentProgressHandler()
    progress_handler.setLevel(logging.INFO)

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    root.handlers.clear()
    root.addHandler(file_handler)
    root.addHandler(error_handler)
    root.addHandler(progress_handler)

    # Suppress known chatty third-party loggers even in the log file
    for noisy in ("httpx", "httpcore", "urllib3", "googleapiclient.discovery_cache"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def main():
    """Package entry point - invoked by `launchway` command."""
    # Handle --version / -V before any heavyweight setup
    if len(sys.argv) > 1 and sys.argv[1] in ('--version', '-V'):
        try:
            from importlib.metadata import version
            ver = version('launchway')
        except Exception:
            ver = '0.1.0'
        print(f"Launchway v{ver}")
        sys.exit(0)

    # 1. Configure logging - all agent noise goes to file, terminal stays clean
    _configure_logging()

    # 2. Load .env (from ~/.launchway/.env or cwd/.env)
    from launchway.config import ensure_env_loaded, run_first_time_setup
    ensure_env_loaded()

    # 3. First-run setup wizard (AI Engine choice)
    run_first_time_setup()

    # 4. Ensure Playwright browser binaries are installed
    from launchway.postinstall import ensure_browsers
    ensure_browsers()

    cli = CLIJobAgent()
    cli.run()


if __name__ == "__main__":
    main()
