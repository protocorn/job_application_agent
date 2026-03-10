"""
Authentication mixin: register, login, logout, and session restoration.

Key behaviours
--------------
* After a successful login the JWT token is saved to ~/.launchway/session.json.
  On the next run the CLI restores the session automatically — no password prompt.
* On logout the saved session is wiped.
* After a first-time login (empty profile) the user is shown a "complete your
  profile" prompt that links to the website or offers to fill via CLI.
"""

import logging
import os
from launchway.api_client import LaunchwayAPIError
from launchway.session    import save_session, clear_session

logger = logging.getLogger(__name__)

# The public website URL shown to users when they need to complete their profile.
# Override via the LAUNCHWAY_APP_URL environment variable.
_DEFAULT_APP_URL = "https://jobapplicationagent-production.up.railway.app"

def _app_url() -> str:
    return os.getenv("LAUNCHWAY_APP_URL", _DEFAULT_APP_URL)


def _is_profile_empty(profile: dict) -> bool:
    """Return True only when every field in the profile is empty/null."""
    if not profile:
        return True
    # Fields that are internal/system metadata and should not count as
    # user-supplied profile content when evaluating completeness.
    _SKIP_KEYS = {"id", "user_id", "created_at", "updated_at", "resume_keywords"}
    return not any(
        bool(v)
        for k, v in profile.items()
        if k not in _SKIP_KEYS
    )


class AuthMixin:

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def register_user(self):
        self.clear_screen()
        self.print_header("CREATE ACCOUNT")

        print("  Create your Launchway account to get started.\n")

        email = self.get_input("Email: ").strip()
        if not email or "@" not in email:
            self.print_error("Invalid email address.")
            self.pause()
            return

        first_name = self.get_input("First Name: ").strip()
        last_name  = self.get_input("Last Name: ").strip()
        password   = self.get_input("Password (min 8 chars): ", password=True)
        confirm    = self.get_input("Confirm Password: ", password=True)

        if password != confirm:
            self.print_error("Passwords do not match.")
            self.pause()
            return

        if len(password) < 8:
            self.print_error("Password must be at least 8 characters.")
            self.pause()
            return

        try:
            result = self.api.register(email, password, first_name, last_name)
        except LaunchwayAPIError as e:
            self.print_error(str(e))
            self.pause()
            return

        self.clear_screen()
        self.print_header("ACCOUNT CREATED")
        print(f"\n  {result.get('message', 'Registration successful!')}\n")
        print("  ┌──────────────────────────────────────────────────┐")
        print("  │  Next step: check your email to verify your       │")
        print("  │  account, then come back here and log in.         │")
        print("  │                                                   │")
        print("  │  Didn't receive the email? You can request a      │")
        print("  │  new one from the login screen.                   │")
        print("  └──────────────────────────────────────────────────┘\n")
        self.pause()

    # ------------------------------------------------------------------
    # Login
    # ------------------------------------------------------------------

    def login_user(self) -> bool:
        """Prompt for credentials, authenticate against the API, save session."""
        self.clear_screen()
        self.print_header("LOGIN")

        email    = self.get_input("Email: ").strip()
        password = self.get_input("Password: ", password=True)

        return self._do_login(email, password, show_welcome=True)

    def _do_login(self, email: str, password: str, show_welcome: bool = True) -> bool:
        """
        Shared login logic used by both the manual prompt and the
        restored-session path (where the token is already present).
        """
        try:
            result = self.api.login(email, password)
        except LaunchwayAPIError as e:
            if e.email_not_verified:
                self._handle_unverified_email(email)
            elif e.beta_not_approved:
                self._handle_beta_not_approved()
            else:
                self.print_error(str(e))
                self.pause()
            return False

        user  = result.get("user", {})
        token = result.get("token", "")

        if not user or not token:
            self.print_error("Login failed — unexpected response from server.")
            self.pause()
            return False

        # Persist session so next launch skips the login prompt
        save_session(token, user)

        self.current_user = user  # plain dict: {id, email, first_name, last_name, ...}

        # Fetch profile from the backend
        try:
            self.current_profile = self.api.get_profile()
        except LaunchwayAPIError as e:
            logger.warning(f"Could not load profile after login: {e}")
            self.current_profile = {}

        if show_welcome:
            self.print_success(f"Welcome back, {user.get('first_name', '')}!")
            self.pause()

        # Prompt to complete the profile if it is empty
        self._check_and_prompt_profile_setup()

        # Prompt to configure AI Engine if not done yet
        self._check_and_prompt_ai_engine_setup()

        return True

    def _handle_unverified_email(self, email: str) -> None:
        """Show a helpful prompt when a user tries to log in before verifying."""
        self.clear_screen()
        self.print_header("EMAIL VERIFICATION REQUIRED")
        print("\n  Your email address has not been verified yet.")
        print("  Please check your inbox for the verification link.\n")
        print("  ┌──────────────────────────────────────────────────┐")
        print("  │  1. Resend verification email                     │")
        print("  │  2. Back                                          │")
        print("  └──────────────────────────────────────────────────┘\n")
        choice = self.get_input("Select option (1-2): ").strip()
        if choice == "1":
            try:
                self.api.resend_verification_email(email)
                self.print_success("Verification email sent! Check your inbox and then log in.")
            except LaunchwayAPIError as e:
                self.print_error(f"Could not send email: {e}")
        self.pause()

    def _handle_beta_not_approved(self) -> None:
        """Show a clear message when a user's account is not yet beta-approved."""
        self.clear_screen()
        self.print_header("BETA ACCESS REQUIRED")
        print("\n  Launchway is currently in private beta.")
        print("  Your account has not been approved for access yet.\n")
        print("  ┌──────────────────────────────────────────────────┐")
        print("  │  To request beta access, visit:                  │")
        print(f"  │  {_app_url()}/beta-request")
        print("  │                                                   │")
        print("  │  You will receive an email once your request      │")
        print("  │  has been reviewed and approved.                  │")
        print("  └──────────────────────────────────────────────────┘\n")
        self.pause()

    # ------------------------------------------------------------------
    # Session restoration (called by show_auth_menu before prompting)
    # ------------------------------------------------------------------

    def try_restore_session(self) -> bool:
        """
        Try to restore a previously saved session without prompting the user.

        Returns True if the session is valid and the user is now logged in.
        """
        from launchway.session import load_session
        self._session_restore_reason = None
        token, user = load_session()
        if not token or not user:
            return False

        # Inject the saved token into the HTTP client
        self.api.token = token

        # Verify the token is still valid on the server
        try:
            verified = self.api.verify_token()
            live_user = verified.get("user", {})
            if not live_user:
                raise LaunchwayAPIError("No user in verify response")
        except LaunchwayAPIError as e:
            # Differentiate between an actually invalid token vs transient
            # connectivity/backend issues during startup verification.
            if e.status_code in (401, 403):
                clear_session()
                self.api.token = None
                self._session_restore_reason = "expired"
            else:
                # Keep saved session on disk so user can retry once network recovers.
                self.api.token = None
                self._session_restore_reason = "network"
            return False

        # Merge live user data (may have been updated on the website)
        self.current_user = {**user, **live_user}

        # Beta gate: if the saved session belongs to a non-approved user, block them
        if not live_user.get('beta_access_approved', False):
            clear_session()
            self.api.token = None
            self.current_user = None
            self._session_restore_reason = "beta_not_approved"
            return False

        # Fetch profile
        try:
            self.current_profile = self.api.get_profile()
        except LaunchwayAPIError as e:
            logger.warning(f"Could not load profile during session restore: {e}")
            self.current_profile = {}

        # Prompt for profile setup if empty (e.g. brand-new account)
        self._check_and_prompt_profile_setup()
        # Prompt to configure AI Engine if not done yet
        self._check_and_prompt_ai_engine_setup()
        return True

    # ------------------------------------------------------------------
    # Profile completeness prompt
    # ------------------------------------------------------------------

    def _check_and_prompt_profile_setup(self):
        """
        If the user's profile is empty, guide them to complete it — either
        through the website (recommended) or via the CLI menu.
        """
        if not _is_profile_empty(self.current_profile):
            return

        self.clear_screen()
        self.print_header("COMPLETE YOUR PROFILE")

        u = self.current_user or {}
        print(f"\n  Hi {u.get('first_name', 'there')}! Your profile is empty.\n")
        print("  Launchway needs your profile information to fill out job")
        print("  applications on your behalf (contact info, resume URL,")
        print("  work experience, skills, etc.).\n")
        print("  ┌───────────────────────────────────────────────────────┐")
        print("  │  OPTION 1  (recommended)                              │")
        print(f"  │  Visit the website and complete your profile there:   │")
        print(f"  │  {_app_url():<53}│")
        print("  │                                                       │")
        print("  │  OPTION 2                                             │")
        print("  │  Fill in your profile now from this terminal.         │")
        print("  └───────────────────────────────────────────────────────┘\n")

        choice = self.get_input("  Enter 1 for website, 2 for terminal (default: 1): ").strip()

        if choice == "2":
            self.profile_menu()
        else:
            print(f"\n  Open this URL in your browser:\n")
            print(f"  {_app_url()}\n")
            print("  Once your profile is complete you can start applying for jobs.")
            self.pause()

    # ------------------------------------------------------------------
    # AI Engine configuration guard
    # ------------------------------------------------------------------

    def _check_and_prompt_ai_engine_setup(self):
        """
        Called right after login / session restore.
        If the user has never configured their AI Engine, show a banner and
        immediately redirect them to the AI Engine setup screen.
        """
        # Fast path: check the already-loaded profile before making an extra API call
        if (self.current_profile or {}).get("api_primary_mode"):
            return

        try:
            data = self.api.get_ai_key_settings()
            if data.get("api_primary_mode"):
                # Sync into local profile cache so the fast path works next time
                if self.current_profile is not None:
                    self.current_profile["api_primary_mode"] = data["api_primary_mode"]
                return   # already configured — nothing to do
        except Exception:
            return   # can't check → don't block startup

        self.clear_screen()
        self.print_header("AI ENGINE SETUP REQUIRED")
        print("\n  ┌─────────────────────────────────────────────────────────┐")
        print("  │  ⚠️  You haven't configured your AI Engine yet.          │")
        print("  │                                                         │")
        print("  │  All AI-powered features (job search, auto-apply,       │")
        print("  │  keyword extraction) require a Gemini API key method.   │")
        print("  │                                                         │")
        print("  │  You can use Launchway's shared free keys or provide    │")
        print("  │  your own Gemini API key for a private quota.           │")
        print("  └─────────────────────────────────────────────────────────┘\n")

        if self.get_input_yn("  Set up AI Engine now? (y/n, default: y): ", default='y'):
            self.update_ai_engine()
        else:
            print("\n  ⚠️  AI features will be unavailable until you configure the AI Engine.")
            print("     You can do this any time via: Profile Management → AI Engine\n")
            self.pause()

    def _require_ai_engine(self) -> bool:
        """
        Gate for any menu action that needs Gemini.

        Returns True if the user is configured and can proceed.
        Returns False (after showing a banner) if not configured,
        giving the user a chance to set it up immediately.
        """
        try:
            data = self.api.get_ai_key_settings()
            if data.get("api_primary_mode"):
                return True   # configured — all good
        except Exception:
            return True   # can't verify → let it through; the server will guard

        # Not configured
        self.clear_screen()
        print("\n  ╔══════════════════════════════════════════════════════════╗")
        print("  ║  ⛔  AI ENGINE NOT CONFIGURED                            ║")
        print("  ║                                                          ║")
        print("  ║  This feature requires a Gemini API key method.         ║")
        print("  ║  Please set up your AI Engine before continuing.        ║")
        print("  ╚══════════════════════════════════════════════════════════╝\n")

        if self.get_input_yn("  Configure AI Engine now? (y/n, default: y): ", default='y'):
            self.update_ai_engine()
            # Re-check after setup
            try:
                data = self.api.get_ai_key_settings()
                return bool(data.get("api_primary_mode"))
            except Exception:
                return False
        return False

    # ------------------------------------------------------------------
    # Logout
    # ------------------------------------------------------------------

    def logout(self):
        clear_session()
        self.current_user    = None
        self.current_profile = None
        self._session_mimikree_email    = None
        self._session_mimikree_password = None
        self.api.token = None
        self.print_success("Logged out successfully!")
        self.pause()

    # ------------------------------------------------------------------
    # Auth menu (entry point for the CLI)
    # ------------------------------------------------------------------

    def show_auth_menu(self):
        """
        Called once at startup.  Tries to restore a saved session first;
        only shows the login/register prompt if that fails.
        """
        from launchway import __version__

        # ── Try silent session restore ──────────────────────────────────
        self.clear_screen()
        self.print_header("LAUNCHWAY — JOB APPLICATION AGENT")
        print(f"  v{__version__}\n")
        print("  Checking saved session...")

        if self.try_restore_session():
            u = self.current_user or {}
            print(f"  Signed in as {u.get('email', '')} (session restored)\n")
            self.pause()
            self.show_main_menu()
            return
        if getattr(self, "_session_restore_reason", None) == "expired":
            self.print_warning("Your saved session has expired. Please log in again.")
            self.pause()
        elif getattr(self, "_session_restore_reason", None) == "network":
            self.print_warning("Could not verify saved session due to a network/backend error.")
            self.print_info("Your session was kept. Please retry when connectivity is stable.")
            self.pause()
        elif getattr(self, "_session_restore_reason", None) == "beta_not_approved":
            self.print_warning("Beta access is required to use Launchway.")
            self.print_info(f"Visit {_app_url()}/beta-request to request access.")
            self.pause()

        # ── Interactive auth loop ───────────────────────────────────────
        while self.running:
            self.clear_screen()
            self.print_header("LAUNCHWAY — JOB APPLICATION AGENT")
            print(f"  v{__version__}\n")
            print("  Terminal-Based Autonomous Job Application Agent\n")
            print("  1. Login")
            print("  2. Create Account")
            print("  3. Exit\n")

            choice = self.get_input("Select option (1-3): ").strip()

            if choice == "1":
                if self.login_user():
                    self.show_main_menu()
            elif choice == "2":
                self.register_user()
            elif choice == "3":
                self.running = False
                break
            else:
                self.print_error("Invalid option")
                self.pause()
