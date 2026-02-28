"""Account settings mixin — password change, email update, account info via API."""

import logging
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
            print(f"  1. View Account Info")
            print(f"  2. Change Password")
            print(f"  3. Update Email")
            print(f"  4. Back to Main Menu\n")

            choice = self.get_input("Select option (1-4): ").strip()

            if   choice == '1': self.view_account_info()
            elif choice == '2': self.change_password()
            elif choice == '3': self.update_email()
            elif choice == '4': break
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
