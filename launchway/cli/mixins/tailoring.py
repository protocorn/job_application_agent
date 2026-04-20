"""Resume tailoring mixin - runs locally after agent bootstrap."""

import logging
import os
import inspect
import importlib.util
from pathlib import Path

from launchway.api_client import LaunchwayAPIError
from launchway.cli.utils import Colors, format_credits

logger = logging.getLogger(__name__)


class TailoringMixin:
    def _load_resume_tailoring_callable(self):
        """
        Prefer local source checkout for resume tailoring when available.
        This bypasses encrypted-bundle `Agents` imports during local development.
        """
        repo_root = Path(__file__).resolve().parents[3]
        local_agent_file = repo_root / "Agents" / "resume_tailoring_agent.py"
        if local_agent_file.exists():
            try:
                spec = importlib.util.spec_from_file_location(
                    "launchway_local_resume_tailoring_agent",
                    str(local_agent_file),
                )
                if spec and spec.loader:
                    module = importlib.util.module_from_spec(spec)
                    # Ensure sibling imports like `import systematic_tailoring_complete`
                    # resolve to local checkout first.
                    local_agents_dir = str(local_agent_file.parent)
                    if local_agents_dir not in os.sys.path:
                        os.sys.path.insert(0, local_agents_dir)
                    spec.loader.exec_module(module)
                    fn = getattr(module, "tailor_resume_and_return_url", None)
                    if callable(fn):
                        return fn
            except Exception as e:
                logger.warning(f"Falling back to bundled Agents module: {e}")

        from Agents.resume_tailoring_agent import tailor_resume_and_return_url
        return tailor_resume_and_return_url


    def _is_latex_resume_mode(self) -> bool:
        source_type = (self.current_profile or {}).get('resume_source_type', 'google_doc')
        return source_type == 'latex_zip'

    def _profile_strength_payload(self) -> dict:
        profile = self.current_profile or {}
        score = 0
        hints = []
        projects = profile.get("projects") if isinstance(profile.get("projects"), list) else []
        work_exp = profile.get("work experience") if isinstance(profile.get("work experience"), list) else []
        skills = profile.get("skills", {})
        summary = str(profile.get("summary", "") or "").strip()

        project_count = len([p for p in projects if isinstance(p, dict) and str(p.get("name", "")).strip()])
        work_count = len([w for w in work_exp if isinstance(w, dict) and str(w.get("title", "")).strip()])
        skill_count = 0
        if isinstance(skills, dict):
            for v in skills.values():
                if isinstance(v, list):
                    skill_count += len([x for x in v if str(x).strip()])
                elif str(v).strip():
                    skill_count += 1

        score += min(25, project_count * 8)
        score += min(20, work_count * 10)
        score += min(20, skill_count)
        score += 10 if summary else 0
        score += 5 if profile.get("resume_url") or profile.get("resume_text") else 0
        score = min(100, score)

        if project_count < 2:
            hints.append("Add at least 2 detailed projects in Profile > Projects.")
        if work_count < 1:
            hints.append("Add at least 1 work experience entry with measurable outcomes.")
        if skill_count < 10:
            hints.append("Expand skills with concrete tools/frameworks.")
        if not summary:
            hints.append("Add a short summary for stronger tailoring context.")

        return {"score": score, "minimum_score": 45, "hints": hints}

    def _confirm_profile_gate(self) -> bool:
        strength = self._profile_strength_payload()
        score = strength["score"]
        threshold = strength["minimum_score"]
        if score >= threshold:
            return True

        self.print_warning(f"Profile strength is low ({score}/{threshold}). Tailoring quality may be limited.")
        for hint in strength["hints"][:3]:
            self.print_info(f"  • {hint}")
        self.print_info("You can continue now, but results improve when your Launchway profile is stronger.")
        return self.get_input_yn("Continue anyway? (y/n, default: n): ", default="n")

    def _ask_replace_projects_on_tailor(self) -> bool:
        self.print_info(
            "Project swap option: replace low-relevance resume projects with relevant projects from your Launchway profile."
        )
        self.print_info(
            "Hint: Add projects in Profile > Projects so tailoring can swap in projects not present on your resume."
        )
        return self.get_input_yn("Enable project replacement for this tailoring run? (y/n, default: n): ", default='n')

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
            # If the resume source is Google Docs, require a live Google OAuth
            # connection before running auto-apply workflows.
            is_google_doc_resume = (
                source_type == 'google_doc'
                or ('docs.google.com' in str(resume_url).lower())
            )
            if is_google_doc_resume:
                try:
                    status = self.api.get_google_oauth_status()
                except LaunchwayAPIError as e:
                    self.print_error(f"Could not verify Google account connection: {e}")
                    self.print_info("Please retry when backend connectivity is stable.")
                    return False

                if not status.get("is_connected", False):
                    self.print_warning(
                        "Your resume is a Google Doc, but your Google account is not connected "
                        "or the token has expired."
                    )
                    self.print_info("You must reconnect Google before continuing.")
                    if hasattr(self, "_ensure_google_connected"):
                        connected = self._ensure_google_connected()
                        if not connected:
                            self.print_error(
                                "Google account connection is required to continue with "
                                "Google Docs resume auto-apply."
                            )
                            return False
                        # Re-check status after reconnect flow
                        try:
                            status = self.api.get_google_oauth_status()
                        except LaunchwayAPIError as e:
                            self.print_error(f"Could not re-verify Google account: {e}")
                            return False
                        if not status.get("is_connected", False):
                            self.print_error(
                                "Google account is still not connected. Please connect it "
                                "from Profile Management and try again."
                            )
                            return False
                    else:
                        self.print_error(
                            "Google connection helper is unavailable in this CLI context."
                        )
                        return False
            return True

        if resume_text and source_type in ('pdf', 'docx'):
            return True

        self.print_error(
            "No resume found. Please upload a PDF/DOCX or add a Google Docs URL "
            "in Profile Management first."
        )
        return False

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

        if not self._confirm_profile_gate():
            self.pause()
            return
        replace_projects_on_tailor = self._ask_replace_projects_on_tailor()

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
        print("  (Paste the full text - press Enter on a blank line twice when done)\n")
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
            tailor_resume_and_return_url = self._load_resume_tailoring_callable()

            self.print_info("\nStarting resume tailoring... This may take 1-2 minutes")
            user_full_name = (
                f"{self.current_user.get('first_name','')} "
                f"{self.current_user.get('last_name','')}".strip()
                or "Resume"
            )

            # Use the server's Google OAuth token so the tailoring agent can
            # access the user's resume doc without a separate local OAuth flow.
            google_credentials = None
            try:
                access_token = self.api.get_google_oauth_access_token()
                if access_token:
                    from google.oauth2.credentials import Credentials as GoogleCredentials
                    google_credentials = GoogleCredentials(token=access_token)
            except Exception as _cred_err:
                logger.debug(f"Could not fetch server Google token: {_cred_err}")

            tailor_kwargs = dict(
                original_resume_url=resume_url,
                job_description=job_description,
                job_title=job_title,
                company=company,
                credentials=google_credentials,
                user_full_name=user_full_name,
                user_id=self.current_user.get('id'),
                profile_projects=(self.current_profile or {}).get("projects", []),
            )
            # Compatibility guard: some older runtime copies may not expose
            # the newer project-swap keyword yet.
            try:
                sig = inspect.signature(tailor_resume_and_return_url)
                source_path = inspect.getsourcefile(tailor_resume_and_return_url) or "<unknown>"
                self.print_info(
                    f"Tailoring runtime: {tailor_resume_and_return_url.__module__} | {source_path}"
                )
                self.print_info(f"Tailoring signature: {sig}")
                accepts_var_kw = any(
                    p.kind == inspect.Parameter.VAR_KEYWORD
                    for p in sig.parameters.values()
                )
                if "replace_projects_on_tailor" in sig.parameters or accepts_var_kw:
                    tailor_kwargs["replace_projects_on_tailor"] = replace_projects_on_tailor
                else:
                    self.print_warning(
                        "Project swap was requested, but the loaded tailoring agent "
                        f"does not support it in this runtime. Signature: {sig}"
                    )
            except Exception:
                # Best-effort fallback - keep tailoring flow running.
                tailor_kwargs["replace_projects_on_tailor"] = replace_projects_on_tailor

            tailored_url = tailor_resume_and_return_url(**tailor_kwargs)

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
                self.print_error("Resume tailoring failed - no URL returned.")

            self.pause()

        except Exception as e:
            self.print_error(f"Resume tailoring failed: {str(e)}")
            logger.error(f"Resume tailoring error: {e}", exc_info=True)
            self.pause()

    async def _pre_tailor_for_job(
        self,
        idx: int,
        job_url: str,
        pre_fetched_desc: str | None,
        replace_projects: bool,
    ) -> dict:
        """
        Run resume tailoring BEFORE opening the browser for a job.

        Returns a dict with keys:
            skip       - bool: user chose to skip this job entirely
            pdf_path   - str | None: local PDF of the tailored resume
            resume_url - str | None: tailored Google Doc URL (or original if fallback)
            did_tailor - bool: whether tailoring actually ran
        """
        import asyncio
        import inspect

        result = {"skip": False, "pdf_path": None, "resume_url": None, "did_tailor": False}

        job_desc = pre_fetched_desc
        if not job_desc:
            self.print_warning("Could not auto-fetch the job description.")
            paste = self.get_input_yn(
                "  Paste description manually for tailoring? (y/n, default: n): ", default="n"
            )
            if paste:
                self.print_info("  Paste the job description — press Enter on a blank line twice when done:")
                lines = []
                while True:
                    line = input("    > ")
                    if line == "" and lines and lines[-1] == "":
                        break
                    lines.append(line)
                job_desc = "\n".join(lines).strip()
            if not job_desc:
                self.print_info("  No description available — skipping tailoring, using original resume.")
                return result

        resume_url_for_tailor = (self.current_profile or {}).get("resume_url", "")
        user_full_name = (
            f"{self.current_user.get('first_name', '')} "
            f"{self.current_user.get('last_name', '')}".strip()
            or "Resume"
        )
        # Best-effort metadata extraction so tailored files use real company/title
        # (avoids generic names like Company_1 and duplicate resume filenames).
        resolved_company = f"Company_{idx}"
        resolved_title = f"Job {idx}"
        try:
            if hasattr(self, "_extract_job_metadata_with_llm"):
                c, t = self._extract_job_metadata_with_llm(
                    job_url=job_url,
                    company="Unknown Company",
                    title=f"Job #{idx}",
                    description=job_desc or "",
                )
                unknown_values = {"", "unknown", "unknown company", "n/a", "na"}
                if str(c or "").strip().lower() not in unknown_values:
                    resolved_company = str(c).strip()
                if str(t or "").strip().lower() not in {"", "unknown", "unknown position", "n/a", "na"}:
                    resolved_title = str(t).strip()
        except Exception as _meta_err:
            logger.debug(f"Could not resolve job metadata for pre-tailor naming: {_meta_err}")

        self.print_info("  Tailoring resume… (this may take 1-2 minutes)")
        try:
            tailor_callable = self._load_resume_tailoring_callable()

            # Use the server's Google OAuth token so the tailoring agent can
            # access the user's existing resume doc without a local OAuth flow.
            google_credentials = None
            try:
                access_token = self.api.get_google_oauth_access_token()
                if access_token:
                    from google.oauth2.credentials import Credentials as GoogleCredentials
                    google_credentials = GoogleCredentials(token=access_token)
            except Exception as _cred_err:
                logger.debug(f"Could not fetch server Google token for tailoring: {_cred_err}")

            tailor_kwargs = dict(
                original_resume_url=resume_url_for_tailor,
                job_description=job_desc,
                job_title=resolved_title,
                company=resolved_company,
                credentials=google_credentials,
                user_full_name=user_full_name,
                user_id=str(self.current_user.get("id")),
                profile_projects=(self.current_profile or {}).get("projects", []),
            )
            try:
                sig = inspect.signature(tailor_callable)
                accepts_var_kw = any(
                    p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values()
                )
                if "replace_projects_on_tailor" in sig.parameters or accepts_var_kw:
                    tailor_kwargs["replace_projects_on_tailor"] = replace_projects
            except Exception:
                tailor_kwargs["replace_projects_on_tailor"] = replace_projects

            tailoring_result = await asyncio.to_thread(tailor_callable, **tailor_kwargs)
        except Exception as err:
            err_str = str(err)
            if "notFound" in err_str or "File not found" in err_str or "404" in err_str:
                self.print_error(
                    "  Tailoring failed: The app cannot access your Google Doc resume.\n"
                    "  This happens when the resume was not uploaded through the web app.\n"
                    "\n"
                    "  FIX: Go to the web app → Profile → re-upload your Google Doc resume\n"
                    "       by pasting its URL into the 'Google Doc Resume URL' field and\n"
                    "       clicking Save. This re-grants the app access to the file.\n"
                    "\n"
                    "  Continuing with original resume for now."
                )
            else:
                self.print_error(f"  Tailoring failed: {err}. Will use original resume.")
            return result

        if not tailoring_result:
            self.print_error("  Tailoring returned no result. Will use original resume.")
            return result

        result["did_tailor"] = True

        # ── Extract paths from result ─────────────────────────────────────────
        if isinstance(tailoring_result, dict):
            result["pdf_path"]   = tailoring_result.get("pdf_path")
            result["resume_url"] = tailoring_result.get("url")
        elif isinstance(tailoring_result, str):
            result["resume_url"] = tailoring_result

        # ── Show the tailored resume to the user ─────────────────────────────
        print(f"\n{Colors.OKGREEN}{'='*60}{Colors.ENDC}")
        print(f"{Colors.BOLD}{Colors.OKGREEN}  Resume Tailored — Job #{idx}{Colors.ENDC}")
        print(f"{Colors.OKGREEN}{'='*60}{Colors.ENDC}")

        if result["pdf_path"] and __import__("os").path.exists(result["pdf_path"]):
            print(f"\n  PDF: {Colors.OKCYAN}{result['pdf_path']}{Colors.ENDC}")
            if self.get_input_yn("  Open PDF to review? (y/n, default: y): ", default="y"):
                try:
                    import subprocess
                    subprocess.run(["start", result["pdf_path"]], shell=True)
                except Exception:
                    self.print_info(f"  Open manually: {result['pdf_path']}")

        if result["resume_url"]:
            print(f"\n  Google Doc: {Colors.OKCYAN}{result['resume_url']}{Colors.ENDC}")
            if self.get_input_yn("  Open Google Doc to review/edit? (y/n, default: n): ", default="n"):
                try:
                    import webbrowser
                    webbrowser.open(result["resume_url"])
                    self.get_input("  Press Enter after you finish reviewing/editing in Google Docs: ")
                except Exception:
                    self.print_info(f"  Open manually: {result['resume_url']}")

        if isinstance(tailoring_result, dict):
            match_stats = tailoring_result.get("match_stats", {})
            if match_stats:
                match_pct = match_stats.get("match_percentage", 0)
                added     = match_stats.get("added", 0)
                missing   = match_stats.get("missing", 0)
                print(f"\n  Match Rate:       {Colors.OKGREEN}{match_pct:.1f}%{Colors.ENDC}")
                if added:   print(f"  Keywords Added:   {Colors.OKGREEN}{added}{Colors.ENDC}")
                if missing: print(f"  Keywords Missing: {Colors.WARNING}{missing}{Colors.ENDC}")

        print(f"{Colors.OKGREEN}{'='*60}{Colors.ENDC}\n")

        # ── Ask for confirmation before opening the browser ───────────────────
        confirmed = self.get_input_yn(
            "  Proceed with this tailored resume? (y/n, default: y): ", default="y"
        )
        if not confirmed:
            choice = (
                self.get_input(
                    "  (s)kip this job entirely  or  (o)riginal resume — which? (s/o, default: s): "
                )
                .strip()
                .lower()
            )
            if choice == "o":
                # Fall back to original resume; don't set tailored paths
                result["pdf_path"]   = None
                result["resume_url"] = None
                result["did_tailor"] = False
            else:
                result["skip"] = True
        else:
            # IMPORTANT: If user edited the tailored Google Doc before confirming,
            # refresh PDF from the latest doc state so apply uses final content.
            if result.get("resume_url"):
                try:
                    import os
                    import re
                    import tempfile

                    def _safe(s: str) -> str:
                        token = re.sub(r"[^A-Za-z0-9]+", "_", (s or "").strip()).strip("_")
                        return token or "Resume"

                    fresh_name = f"{_safe(user_full_name)}_Resume_{_safe(resolved_company)}.pdf"
                    fresh_pdf_path = os.path.join(tempfile.gettempdir(), fresh_name)
                    if self.api.download_resume_pdf(fresh_pdf_path, resume_url=result["resume_url"]):
                        result["pdf_path"] = fresh_pdf_path
                        self.print_info(f"  Using latest edited resume PDF: {fresh_pdf_path}")
                except Exception as _fresh_pdf_err:
                    logger.debug(f"Could not refresh tailored PDF after review/edit: {_fresh_pdf_err}")

        return result

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
