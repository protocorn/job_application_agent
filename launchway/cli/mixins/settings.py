"""Account settings mixin — password change, email update, account info via API."""

import logging
import os
from launchway.api_client import LaunchwayAPIError
from launchway.cli.utils import Colors

logger = logging.getLogger(__name__)


class SettingsMixin:

    def settings_menu(self):
        while True:
            self.clear_screen()
            self.print_header("ACCOUNT SETTINGS")
            user = self.current_user or {}
            print(f"  Logged in as: {user.get('email','')}\n")

            provider = os.getenv("AI_PROVIDER", "")
            has_key  = bool(os.getenv("GOOGLE_API_KEY"))
            if has_key:
                ai_status = f"Custom Gemini key ({Colors.OKGREEN}set{Colors.ENDC})"
            elif provider == "custom":
                ai_status = f"Custom Gemini key ({Colors.WARNING}key missing{Colors.ENDC})"
            elif provider == "launchway":
                ai_status = "Launchway AI"
            else:
                ai_status = f"{Colors.WARNING}Not configured{Colors.ENDC}"

            print(f"  1. View Account Info")
            print(f"  2. Change Password")
            print(f"  3. Update Email")
            print(f"  4. AI Engine  [{ai_status}]")
            print(f"  5. Back to Main Menu\n")

            choice = self.get_input("Select option (1-5): ").strip()

            if   choice == '1': self.view_account_info()
            elif choice == '2': self.change_password()
            elif choice == '3': self.update_email()
            elif choice == '4': self.ai_provider_settings()
            elif choice == '5': break
            else:
                self.print_error("Invalid option")
                self.pause()

    def view_account_info(self):
        self.clear_screen()
        self.print_header("ACCOUNT INFO")

        try:
            info = self.api.get_account_info()
            acct = info.get("account", {})
        except LaunchwayAPIError as e:
            # Fallback to locally cached user dict
            acct = self.current_user or {}
            logger.warning(f"Could not fetch account info from API: {e}")

        print(f"\n  {Colors.BOLD}Account Details:{Colors.ENDC}")
        print(f"    User ID:    {acct.get('user_id', acct.get('id', 'N/A'))}")
        print(f"    Name:       {acct.get('first_name','')} {acct.get('last_name','')}")
        print(f"    Email:      {acct.get('email','N/A')}")
        created = acct.get('created_at', '')
        if created and 'T' in created:
            created = created.split('T')[0]
        print(f"    Member Since: {created or 'N/A'}")
        print(f"    Email Verified: {'Yes' if acct.get('email_verified') else 'No'}")
        total = acct.get('total_applications', '—')
        print(f"    Total Applications: {total}")

        try:
            credits = self.api.get_credits()
            if credits:
                print(f"\n  {Colors.BOLD}Rate Limits:{Colors.ENDC}")
                tailoring = credits.get('resume_tailoring', {}).get('daily', {})
                apps      = credits.get('job_applications', {}).get('daily', {})
                print(f"    Resume Tailoring: {tailoring.get('used',0)}/{tailoring.get('limit','—')} today")
                print(f"    Applications:     {apps.get('used',0)}/{apps.get('limit','—')} today")
        except LaunchwayAPIError:
            pass

        self.pause()

    def change_password(self):
        self.clear_screen()
        self.print_header("CHANGE PASSWORD")

        current_password = self.get_input("Current Password: ", password=True)
        new_password     = self.get_input("New Password (min 8 chars): ", password=True)
        confirm          = self.get_input("Confirm New Password: ", password=True)

        if new_password != confirm:
            self.print_error("Passwords do not match.")
            self.pause()
            return

        if len(new_password) < 8:
            self.print_error("New password must be at least 8 characters.")
            self.pause()
            return

        try:
            result = self.api.change_password(current_password, new_password)
            self.print_success(result.get("message", "Password changed successfully!"))
        except LaunchwayAPIError as e:
            self.print_error(str(e))
        self.pause()

    def ai_provider_settings(self):
        self.clear_screen()
        self.print_header("AI ENGINE SETTINGS")

        provider = os.getenv("AI_PROVIDER", "")
        has_key  = bool(os.getenv("GOOGLE_API_KEY"))

        print(f"\n  AI powers job matching, form filling, and resume tailoring.\n")
        print(f"  Current setting:")
        if has_key:
            print(f"    {Colors.OKGREEN}Custom Gemini API key is active.{Colors.ENDC}")
        elif provider == "launchway":
            print(f"    {Colors.OKCYAN}Launchway AI is active (no API key needed).{Colors.ENDC}")
        else:
            print(f"    {Colors.WARNING}Not configured yet.{Colors.ENDC}")

        print(f"""
  Options:

    1. Use Launchway AI  (no API key needed)
       Best for most users. Works out of the box.

    2. Use my own Gemini API key
       Get a free key at: https://aistudio.google.com
       Your key is stored in ~/.launchway/.env

    3. Remove my Gemini key  (switch back to Launchway AI)

    4. Back
""")
        choice = self.get_input("Select option (1-4): ").strip()

        if choice == '1':
            self._save_ai_provider("launchway", api_key=None)
            self.print_success("Launchway AI selected. No API key required.")
            self.pause()

        elif choice == '2':
            import getpass
            print()
            print("  Enter your Gemini API key.")
            print("  Get one free at: https://aistudio.google.com")
            print("  (Press Enter to cancel.)\n")
            api_key = getpass.getpass("  Gemini API Key: ").strip()
            if not api_key:
                self.print_info("Cancelled.")
            else:
                self._save_ai_provider("custom", api_key=api_key)
                self.print_success("Custom Gemini key saved.")
                self.print_info(f"Config: ~/.launchway/.env")
            self.pause()

        elif choice == '3':
            self._save_ai_provider("launchway", api_key=None, remove_key=True)
            self.print_success("Gemini key removed. Switched to Launchway AI.")
            self.pause()

        elif choice == '4':
            return
        else:
            self.print_error("Invalid option.")
            self.pause()

    def _save_ai_provider(self, provider: str, api_key: str | None, remove_key: bool = False):
        """Write AI_PROVIDER (and optionally GOOGLE_API_KEY) to ~/.launchway/.env."""
        from pathlib import Path

        config_dir = Path.home() / ".launchway"
        env_file   = config_dir / ".env"
        config_dir.mkdir(parents=True, exist_ok=True)

        lines: list[str] = []
        if env_file.exists():
            lines = env_file.read_text(encoding="utf-8").splitlines()

        # Rewrite lines — remove old AI_PROVIDER and GOOGLE_API_KEY entries
        keys_to_drop = {"AI_PROVIDER"}
        if remove_key or api_key is not None:
            keys_to_drop.add("GOOGLE_API_KEY")

        filtered = [
            ln for ln in lines
            if not any(
                ln.strip().startswith(f"{k}=") or ln.strip().startswith(f'{k}="')
                for k in keys_to_drop
            )
        ]

        if filtered and filtered[-1] != "":
            filtered.append("")

        filtered.append(f'AI_PROVIDER="{provider}"')
        if api_key:
            filtered.append(f'GOOGLE_API_KEY="{api_key}"')

        env_file.write_text("\n".join(filtered) + "\n", encoding="utf-8")

        # Reload into current process
        os.environ["AI_PROVIDER"] = provider
        if api_key:
            os.environ["GOOGLE_API_KEY"] = api_key
        elif remove_key:
            os.environ.pop("GOOGLE_API_KEY", None)

        try:
            from dotenv import load_dotenv
            load_dotenv(env_file, override=True)
        except ImportError:
            pass

    def update_email(self):
        self.clear_screen()
        self.print_header("UPDATE EMAIL")

        user = self.current_user or {}
        print(f"  Current email: {user.get('email', 'N/A')}\n")

        new_email = self.get_input("New Email: ").strip()
        if not new_email or '@' not in new_email:
            self.print_error("Invalid email address.")
            self.pause()
            return

        try:
            result = self.api.update_email(new_email)
            self.print_success(result.get("message", "Email updated successfully!"))
            if self.current_user:
                self.current_user['email'] = new_email
        except LaunchwayAPIError as e:
            self.print_error(str(e))
        self.pause()
