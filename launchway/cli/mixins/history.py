"""Application history mixin — fetched via the Launchway API."""

import logging
from launchway.api_client import LaunchwayAPIError
from launchway.cli.utils import Colors

logger = logging.getLogger(__name__)


class HistoryMixin:

    def view_application_history(self):
        self.clear_screen()
        self.print_header("APPLICATION HISTORY")

        try:
            data = self.api.get_applications_summary(limit=200)
            applications = data.get("applications", [])
            total_count = int(data.get("total_count", len(applications)))
            returned_count = int(data.get("returned_count", len(applications)))
            limit = int(data.get("limit", 200))
        except LaunchwayAPIError as e:
            self.print_error(f"Failed to fetch history: {e}")
            self.pause()
            return

        if not applications:
            self.print_info("No applications recorded yet.")
            self.pause()
            return

        print(f"\n  Total applications recorded: {Colors.BOLD}{total_count}{Colors.ENDC}\n")
        if total_count > returned_count:
            self.print_warning(
                f"Showing latest {returned_count} records (server limit {limit})."
            )
        print(f"  {'#':<4} {'Company':<25} {'Position':<30} {'Status':<15} {'Date'}")
        print(f"  {'-'*85}")

        for i, app in enumerate(applications, 1):
            company     = (app.get('company') or 'N/A')[:24]
            title       = (app.get('job_title') or 'N/A')[:29]
            status      = app.get('status', 'N/A')
            applied_at  = app.get('applied_at', app.get('created_at', ''))
            if applied_at and 'T' in applied_at:
                applied_at = applied_at.split('T')[0]
            print(f"  {i:<4} {company:<25} {title:<30} {status:<15} {applied_at}")

        print("\n  Coming in the next major version:")
        print("  Automatic 7-day follow-up reminders and AI-drafted follow-up emails")
        print("  for hiring contacts (with user review before sending).")

        self.pause()
