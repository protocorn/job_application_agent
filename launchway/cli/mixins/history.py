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
            applications = self.api.get_applications(limit=50)
        except LaunchwayAPIError as e:
            self.print_error(f"Failed to fetch history: {e}")
            self.pause()
            return

        if not applications:
            self.print_info("No applications recorded yet.")
            self.pause()
            return

        print(f"\n  Total applications recorded: {Colors.BOLD}{len(applications)}{Colors.ENDC}\n")
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

        self.pause()
