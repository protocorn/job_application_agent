"""Resume tailoring mixin — runs locally after agent bootstrap."""

import logging
import os
import webbrowser
import json
from pathlib import Path
from typing import Optional, Tuple

from launchway.api_client import LaunchwayAPIError
from launchway.cli.utils import Colors, format_credits

MIMIKREE_SIGNUP_URL = "https://www.mimikree.com/signup"

logger = logging.getLogger(__name__)


class TailoringMixin:

    def _prefs_path(self) -> Path:
        prefs_dir = Path.home() / ".launchway"
        prefs_dir.mkdir(parents=True, exist_ok=True)
        return prefs_dir / "preferences.json"

    def _skip_mimikree_prompt(self) -> bool:
        user_id = str((self.current_user or {}).get("id", "anonymous"))
        try:
            prefs_file = self._prefs_path()
            if not prefs_file.exists():
                return False
            prefs = json.loads(prefs_file.read_text(encoding="utf-8"))
            return bool(prefs.get("mimikree_prompt_opt_out", {}).get(user_id, False))
        except Exception:
            return False

    def _set_skip_mimikree_prompt(self, value: bool):
        user_id = str((self.current_user or {}).get("id", "anonymous"))
        try:
            prefs_file = self._prefs_path()
            prefs = {}
            if prefs_file.exists():
                prefs = json.loads(prefs_file.read_text(encoding="utf-8"))
            opt_map = prefs.get("mimikree_prompt_opt_out", {})
            opt_map[user_id] = bool(value)
            prefs["mimikree_prompt_opt_out"] = opt_map
            prefs_file.write_text(json.dumps(prefs, indent=2), encoding="utf-8")
        except Exception as e:
            logger.warning(f"Could not save Mimikree prompt preference: {e}")

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

        resume_url  = self.current_profile.get('resume_url')
        resume_text = self.current_profile.get('resume_text')
        source_type = self.current_profile.get('resume_source_type', '')

        if resume_url:
            return True

        if resume_text and source_type in ('pdf', 'docx'):
            return True

        self.print_error(
            "No resume found. Please upload a PDF/DOCX or add a Google Docs URL "
            "in Profile Management first."
        )
        return False

    def ensure_mimikree_connected_for_tailoring(self) -> Tuple[Optional[str], Optional[str]]:
        """
        Ensure Mimikree credentials are available for local resume tailoring.
        Tries the API first; if not connected, offers: open signup, enter login, or skip.
        Never forces connection — returns (None, None) if user skips; callers may continue without Mimikree.
        """
        if not self.current_user:
            self.print_error("You must be logged in before using resume tailoring.")
            return None, None

        if self._session_mimikree_email and self._session_mimikree_password:
            return self._session_mimikree_email, self._session_mimikree_password

        try:
            email, password = self.api.get_mimikree_credentials()
            if email and password:
                self._session_mimikree_email    = email
                self._session_mimikree_password = password
                self._set_skip_mimikree_prompt(False)
                self.print_success(f"Mimikree connected ({email}). Tailoring will use Mimikree for better factual accuracy.")
                return email, password
        except LaunchwayAPIError as e:
            if e.status_code not in (404, 400):
                logger.warning(f"Could not fetch Mimikree credentials: {e}")

        if self._skip_mimikree_prompt():
            self.print_info("Mimikree is not connected (prompt disabled). Tailoring will continue with reduced accuracy.")
            return None, None

        self.print_info("Mimikree is not connected. Tailoring can still run, but quality may be lower.")
        while True:
            print(f"\n  {Colors.BOLD}Mimikree options:{Colors.ENDC}")
            print("    1) Open signup page (create account) — https://www.mimikree.com/signup")
            print("    2) Enter login details to connect")
            print("    3) Continue without Mimikree (reduced accuracy)")
            print("    4) Never show this prompt again")
            choice = self.get_input("\n  Choose [1/2/3/4]: ").strip().lower()

            if choice == '1':
                try:
                    webbrowser.open(MIMIKREE_SIGNUP_URL)
                    self.print_info(f"Opened {MIMIKREE_SIGNUP_URL}")
                    self.print_info("After creating an account, choose 2 here to connect.")
                except Exception as e:
                    self.print_warning(f"Could not open browser: {e}")
                    self.print_info(f"Visit {MIMIKREE_SIGNUP_URL} to sign up.")
                continue

            if choice == '2':
                email    = self.get_input("  Mimikree Email: ").strip()
                password = self.get_input("  Mimikree Password: ", password=True)

                if not email or not password:
                    self.print_error("Both email and password are required.")
                    if not self.get_input_yn("  Try again? (y/n): ", default=None):
                        return None, None
                    continue

                self.print_info("Connecting Mimikree account...")
                try:
                    result = self.api.connect_mimikree(email, password)
                    if result.get('success'):
                        self.print_success("Mimikree connected successfully.")
                        self._set_skip_mimikree_prompt(False)
                        try:
                            stored_email, stored_password = self.api.get_mimikree_credentials()
                            if stored_email and stored_password:
                                self._session_mimikree_email    = stored_email
                                self._session_mimikree_password = stored_password
                                return stored_email, stored_password
                        except LaunchwayAPIError:
                            pass
                        self._session_mimikree_email    = email
                        self._session_mimikree_password = password
                        return email, password
                    self.print_error(result.get('error', 'Failed to connect Mimikree.'))
                except LaunchwayAPIError as e:
                    self.print_error(str(e))

                if not self.get_input_yn("  Try again? (y/n): ", default=None):
                    return None, None
                continue

            if choice == '3':
                self.print_info("Continuing without Mimikree. Tailoring may be less accurate or less factual.")
                return None, None

            if choice == '4':
                self._set_skip_mimikree_prompt(True)
                self.print_info("Got it — we will stop asking about Mimikree for this account.")
                self.print_info("Tailoring will continue without Mimikree and may be less accurate.")
                return None, None

            self.print_warning("Please enter 1, 2, 3, or 4.")

    def resume_tailoring_menu(self):
        self.clear_screen()
        self.print_header("RESUME TAILORING")

        if not self._ensure_agents_bootstrapped():
            self.pause()
            return

        if self._is_latex_resume_mode():
            self.print_warning("LaTeX resume tailoring is not yet available in this version.")
            self.print_info("Please set your resume source to Google Docs in Profile Management.")
            self.pause()
            return

        mimikree_email, mimikree_password = self.ensure_mimikree_connected_for_tailoring()

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
        print("    3. Google account connected in Launchway profile\n")
        self.print_info("Credits: Resume tailoring consumes 1 credit after successful completion.")

        # ── Credit check ────────────────────────────────────────────────────
        try:
            available, daily = self.api.check_credit_available("resume_tailoring")
            credit_str = format_credits(daily.get("remaining"), daily.get("limit"), daily.get("reset_time"))
            if daily.get("error") == "credit_check_unavailable":
                self.print_error("Could not verify credits (backend unavailable).")
                self.print_info("Blocking tailoring to prevent untracked LLM usage.")
                self.pause()
                return
            if not available:
                self.print_error(f"Daily resume tailoring limit reached ({credit_str}).")
                self.print_info("Limits reset at midnight UTC. Check launchway.app/manage-credits")
                self.pause()
                return
            self.print_info(f"Resume Tailoring credits: {credit_str}")
        except LaunchwayAPIError:
            self.print_error("Could not verify credits. Please retry in a moment.")
            self.pause()
            return

        if not self.get_input_yn("Proceed? (y/n): ", default=None):
            return

        print("\nEnter job description:")
        print("  (Paste the full text — press Enter on a blank line twice when done)\n")
        jd_lines = []
        while True:
            line = input("  > ")
            if line == "" and jd_lines and jd_lines[-1] == "":
                break
            jd_lines.append(line)
        job_description = "\n".join(jd_lines).strip()
        if not job_description:
            self.print_error("Job description is required.")
            self.pause()
            return

        job_title = self.get_input("\nJob Title: ").strip() or "Position"
        company   = self.get_input("Company Name: ").strip() or "Company"

        try:
            from Agents.resume_tailoring_agent import tailor_resume_and_return_url

            self.print_info("\nStarting resume tailoring... This may take 1-2 minutes")
            user_full_name = (
                f"{self.current_user.get('first_name','')} "
                f"{self.current_user.get('last_name','')}".strip()
                or "Resume"
            )

            tailored_url = tailor_resume_and_return_url(
                original_resume_url=resume_url,
                job_description=job_description,
                job_title=job_title,
                company=company,
                credentials=None,
                mimikree_email=mimikree_email,
                mimikree_password=mimikree_password,
                user_full_name=user_full_name,
                user_id=self.current_user.get('id'),
            )

            if tailored_url:
                self.print_success("\nResume tailored successfully!")
                self._display_tailored_resume_download(tailored_url, company)
                # Consume one tailoring credit and show updated balance
                try:
                    cr = self.api.consume_credit("resume_tailoring")
                    self.print_info(
                        f"Resume Tailoring credits: "
                        f"{format_credits(cr.get('remaining'), cr.get('limit'), cr.get('reset_time'))}"
                    )
                except LaunchwayAPIError as _ce:
                    self.print_error("Credit debit failed; tailored output will not be kept in this run.")
                    raise
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
                if self.get_input_yn("\n  Open now? (y/n, default: y): ", default='y'):
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
