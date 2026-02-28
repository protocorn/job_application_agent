"""Resume tailoring mixin — Mimikree credentials fetched via Launchway API."""

import logging
import os
from typing import Optional
from launchway.api_client import LaunchwayAPIError
from launchway.cli.utils import Colors

logger = logging.getLogger(__name__)

try:
    from Agents.resume_tailoring_agent import tailor_resume_and_return_url
    _TAILORING_AVAILABLE = True
except Exception as e:
    logger.warning(f"Resume tailoring not available: {e}")
    _TAILORING_AVAILABLE = False


class TailoringMixin:

    def _is_latex_resume_mode(self) -> bool:
        source_type = (self.current_profile or {}).get('resume_source_type', 'google_doc')
        return source_type == 'latex_zip'

    def _ensure_resume_ready_for_auto_apply(self) -> bool:
        if not self.current_profile:
            self.print_error("Profile not loaded. Please log in again.")
            return False

        if self._is_latex_resume_mode():
            self.print_error("LaTeX resume auto-apply is not yet available in this version.")
            self.print_info("Please set your resume source to Google Docs in Profile Management.")
            return False

        resume_url = self.current_profile.get('resume_url')
        if not resume_url:
            self.print_error("Please add a resume URL (Google Docs link) in Profile Management first.")
            return False
        return True

    def ensure_mimikree_connected_for_tailoring(self) -> tuple[Optional[str], Optional[str]]:
        """
        Ensure Mimikree credentials are available for local resume tailoring.
        Tries the API first; falls back to session-cached or manual entry.
        """
        if not self.current_user:
            self.print_error("You must be logged in before using resume tailoring.")
            return None, None

        # Session cache (avoids repeated API calls within one CLI session)
        if self._session_mimikree_email and self._session_mimikree_password:
            return self._session_mimikree_email, self._session_mimikree_password

        # Try fetching stored credentials from the backend
        try:
            email, password = self.api.get_mimikree_credentials()
            if email and password:
                self._session_mimikree_email    = email
                self._session_mimikree_password = password
                self.print_success(f"Mimikree connected ({email})")
                return email, password
        except LaunchwayAPIError as e:
            if e.status_code not in (404, 400):
                logger.warning(f"Could not fetch Mimikree credentials: {e}")

        # Not connected — offer to connect now
        self.print_warning("Mimikree is not connected.")
        self.print_info("You need to connect your Mimikree account before resume tailoring can start.")
        if self.get_input("Connect Mimikree now? (y/n): ").strip().lower() != 'y':
            self.print_warning("Tailoring cancelled — Mimikree is not connected.")
            return None, None

        while True:
            email    = self.get_input("Mimikree Email: ").strip()
            password = self.get_input("Mimikree Password: ", password=True)

            if not email or not password:
                self.print_error("Both email and password are required.")
                if self.get_input("Try again? (y/n): ").strip().lower() != 'y':
                    return None, None
                continue

            self.print_info("Connecting Mimikree account...")
            try:
                result = self.api.connect_mimikree(email, password)
                if result.get('success'):
                    self.print_success("Mimikree connected successfully.")
                    # Try to get stored (decrypted) credentials from API
                    try:
                        stored_email, stored_password = self.api.get_mimikree_credentials()
                        if stored_email and stored_password:
                            self._session_mimikree_email    = stored_email
                            self._session_mimikree_password = stored_password
                            return stored_email, stored_password
                    except LaunchwayAPIError:
                        pass
                    # Fallback: use what the user just entered
                    self._session_mimikree_email    = email
                    self._session_mimikree_password = password
                    return email, password
                else:
                    self.print_error(result.get('error', 'Failed to connect Mimikree.'))
            except LaunchwayAPIError as e:
                self.print_error(str(e))

            if self.get_input("Try again? (y/n): ").strip().lower() != 'y':
                return None, None

    def resume_tailoring_menu(self):
        self.clear_screen()
        self.print_header("RESUME TAILORING")

        if not _TAILORING_AVAILABLE:
            self.print_error("Resume tailoring feature is not available.")
            self.print_info("Missing dependencies or configuration.")
            self.pause()
            return

        # LaTeX mode is not available in production
        if self._is_latex_resume_mode():
            self.print_warning("LaTeX resume tailoring is not yet available in this version.")
            self.print_info("Please set your resume source to Google Docs in Profile Management.")
            self.pause()
            return

        mimikree_email, mimikree_password = self.ensure_mimikree_connected_for_tailoring()
        if not mimikree_email or not mimikree_password:
            self.pause()
            return

        resume_url = (self.current_profile or {}).get('resume_url')
        if not resume_url:
            self.print_error("No resume URL found in your profile.")
            self.print_info("Please add your Google Docs resume URL in Profile Management.")
            self.pause()
            return

        self.print_info("Resume tailoring creates a customized version of your resume for a specific job.")
        print(f"\n  Current resume: {resume_url[:60]}...")
        print("\n  Requirements:")
        print("    1. Resume URL in profile  ✓")
        print("    2. A job description")
        print("    3. Google OAuth connection (token.json)\n")

        if self.get_input("Proceed? (y/n): ").strip().lower() != 'y':
            return

        job_description = self.get_input("\nEnter job description: ").strip()
        if not job_description:
            self.print_error("Job description is required.")
            self.pause()
            return

        job_title = self.get_input("Job Title: ").strip() or "Position"
        company   = self.get_input("Company Name: ").strip() or "Company"

        try:
            self.print_info("\nStarting resume tailoring... This may take 1-2 minutes")
            user_full_name = f"{self.current_user.get('first_name','')}{self.current_user.get('last_name','')}"

            tailored_url = tailor_resume_and_return_url(
                original_resume_url=resume_url,
                job_description=job_description,
                job_title=job_title,
                company=company,
                credentials=None,
                mimikree_email=mimikree_email,
                mimikree_password=mimikree_password,
                user_full_name=user_full_name,
            )

            if tailored_url:
                self.print_success("\nResume tailored successfully!")
                self._display_tailored_resume_download(tailored_url, company)
            else:
                self.print_error("Resume tailoring failed — no URL returned.")

            self.pause()

        except Exception as e:
            self.print_error(f"Resume tailoring failed: {str(e)}")
            logger.error(f"Resume tailoring error: {e}", exc_info=True)
            self.pause()

    def _display_tailored_resume_download(self, tailoring_metrics, company: str):
        try:
            if isinstance(tailoring_metrics, str):
                print(f"\n{Colors.OKGREEN}{'='*60}{Colors.ENDC}")
                print(f"{Colors.BOLD}{Colors.OKGREEN}Resume Tailored Successfully!{Colors.ENDC}")
                print(f"\n  Google Doc: {Colors.OKCYAN}{tailoring_metrics}{Colors.ENDC}")
                print(f"{Colors.OKGREEN}{'='*60}{Colors.ENDC}\n")
                return

            pdf_path       = tailoring_metrics.get('pdf_path')
            google_doc_url = tailoring_metrics.get('url')

            print(f"\n{Colors.OKGREEN}{'='*60}{Colors.ENDC}")
            print(f"{Colors.BOLD}{Colors.OKGREEN}Resume Tailored Successfully!{Colors.ENDC}")
            print(f"{Colors.OKGREEN}{'='*60}{Colors.ENDC}")

            if pdf_path and os.path.exists(pdf_path):
                print(f"\n  PDF: {Colors.OKCYAN}{pdf_path}{Colors.ENDC}")
                if self.get_input("\n  Open now? (y/n, default: y): ").strip().lower() != 'n':
                    try:
                        import subprocess
                        subprocess.run(["start", pdf_path], shell=True)
                        self.print_success("  Resume opened in your default PDF viewer!")
                    except Exception:
                        self.print_info(f"  Open manually: {pdf_path}")

            if google_doc_url:
                print(f"\n  Google Doc: {Colors.OKCYAN}{google_doc_url}{Colors.ENDC}")

            match_stats = tailoring_metrics.get('match_stats', {})
            if match_stats:
                match_pct = match_stats.get('match_percentage', 0)
                added     = match_stats.get('added', 0)
                missing   = match_stats.get('missing', 0)
                print(f"\n  Match Rate: {Colors.OKGREEN}{match_pct:.1f}%{Colors.ENDC}")
                if added:   print(f"  Keywords Added:   {Colors.OKGREEN}{added}{Colors.ENDC}")
                if missing: print(f"  Keywords Missing: {Colors.WARNING}{missing}{Colors.ENDC}")

            print(f"{Colors.OKGREEN}{'='*60}{Colors.ENDC}\n")

        except Exception as e:
            logger.error(f"Failed to display tailored resume info: {e}")
            self.print_warning("Resume tailored but display error occurred.")
