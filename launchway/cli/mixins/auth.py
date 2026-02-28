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
    """Return True when the profile has no meaningful content."""
    if not profile:
        return True
    has_name       = bool(profile.get("first name") or profile.get("last_name"))
    has_resume     = bool(profile.get("resume_url"))
    has_contact    = bool(profile.get("email") or profile.get("phone"))
    return not (has_name or has_resume or has_contact)


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
        password   = self.get_input("Password (min 6 chars): ", password=True)
        confirm    = self.get_input("Confirm Password: ", password=True)

        if password != confirm:
            self.print_error("Passwords do not match.")
            self.pause()
            return

        if len(password) < 6:
            self.print_error("Password must be at least 6 characters.")
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

        return True

    # ------------------------------------------------------------------
    # Session restoration (called by show_auth_menu before prompting)
    # ------------------------------------------------------------------

    def try_restore_session(self) -> bool:
        """
        Try to restore a previously saved session without prompting the user.

        Returns True if the session is valid and the user is now logged in.
        """
        from launchway.session import load_session
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
        except LaunchwayAPIError:
            # Token expired or revoked — clear the stale session
            clear_session()
            self.api.token = None
            return False

        # Merge live user data (may have been updated on the website)
        self.current_user = {**user, **live_user}

        # Fetch profile
        try:
            self.current_profile = self.api.get_profile()
        except LaunchwayAPIError as e:
            logger.warning(f"Could not load profile during session restore: {e}")
            self.current_profile = {}

        # Prompt for profile setup if empty (e.g. brand-new account)
        self._check_and_prompt_profile_setup()
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
