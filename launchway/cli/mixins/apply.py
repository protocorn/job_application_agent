"""Batch / single auto-apply mixin - runs agents locally after bootstrap."""

import asyncio
import json
import logging
import os
import re
from datetime import datetime

from launchway.api_client import LaunchwayAPIError
from launchway.cli.utils import Colors, format_credits

logger = logging.getLogger(__name__)


class ApplyMixin:

    @staticmethod
    def _profile_value_filled(value) -> bool:
        """Return True when a profile field has meaningful user-provided content."""
        if value is None:
            return False
        if isinstance(value, str):
            return bool(value.strip())
        if isinstance(value, (list, tuple, set, dict)):
            return len(value) > 0
        return bool(value)

    def _has_resume_uploaded(self, profile: dict) -> bool:
        """Detect whether the user has any usable resume source configured."""
        if not profile:
            return False
        resume_url = profile.get("resume_url")
        resume_text = profile.get("resume_text")
        source_type = profile.get("resume_source_type", "")
        if self._profile_value_filled(resume_url):
            return True
        return self._profile_value_filled(resume_text) and source_type in ("pdf", "docx")

    def _profile_completion_percent(self, profile: dict) -> int:
        """
        Estimate profile completeness from core fields used by auto-apply.
        This is intentionally simple and conservative.
        """
        if not profile:
            return 0

        checks = [
            self._profile_value_filled(profile.get("first name")),
            self._profile_value_filled(profile.get("last name")),
            self._profile_value_filled(profile.get("email")),
            self._profile_value_filled(profile.get("phone")),
            self._profile_value_filled(profile.get("city")),
            self._profile_value_filled(profile.get("state")),
            self._profile_value_filled(profile.get("country")),
            self._profile_value_filled(profile.get("linkedin")),
            self._profile_value_filled(profile.get("summary")),
            self._profile_value_filled(profile.get("skills")),
            self._profile_value_filled(profile.get("education")),
            self._profile_value_filled(
                profile.get("work experience") or profile.get("work_experience")
            ),
            self._profile_value_filled(profile.get("projects")),
            self._profile_value_filled(profile.get("preferred location")),
            self._profile_value_filled(profile.get("visa status")),
        ]
        return int((sum(1 for ok in checks if ok) / len(checks)) * 100)

    def _show_auto_apply_profile_warning_if_needed(self) -> None:
        """
        Show readiness warning only when profile is <50% complete AND resume missing.
        """
        profile = self.current_profile or {}
        completion_pct = self._profile_completion_percent(profile)
        has_resume = self._has_resume_uploaded(profile)
        if completion_pct >= 50 or has_resume:
            return

        print()
        self.print_warning("⚠ Auto-apply readiness warning")
        self.print_warning("Your profile is not filled to a sufficient level (<50%).")
        self.print_warning("No resume is uploaded (Google Doc URL or PDF/DOCX).")
        self.print_info("Update your profile before running auto-apply for better results.\n")

    @staticmethod
    def _is_unknown_job_value(value: str) -> bool:
        v = (value or "").strip().lower()
        return v in {"", "unknown", "unknown company", "unknown position", "n/a", "na"}

    def _extract_job_metadata_with_llm(
        self,
        job_url: str,
        company: str,
        title: str,
        description: str = "",
    ) -> tuple[str, str]:
        """
        Best-effort metadata enrichment for application history.
        Uses Gemini through the existing key-manager pipeline when current values are unknown.
        """
        if not (self._is_unknown_job_value(company) or self._is_unknown_job_value(title)):
            return company, title

        try:
            from Agents.agent_profile_service import AgentProfileService
            key_manager = AgentProfileService.get_gemini_key_manager(str(self.current_user['id']))
            if key_manager is None or not getattr(key_manager, "is_configured", False):
                return company, title

            snippet = (description or "").strip()
            if len(snippet) > 4000:
                snippet = snippet[:4000]

            prompt = f"""Extract the job title and company name from this job context.
Return STRICT JSON only with keys: "title", "company".
If unknown, return "Unknown Position" or "Unknown Company".

URL:
{job_url}

Job description snippet:
{snippet if snippet else "(not available)"}
"""
            resp = key_manager.generate_content("gemini-2.0-flash-lite", prompt)
            text = getattr(resp, 'text', None) or (
                resp.candidates[0].content.parts[0].text
                if hasattr(resp, 'candidates') and resp.candidates else ""
            )
            raw = (text or "").strip()
            if not raw:
                return company, title

            raw = re.sub(r'^```(?:json)?\s*', '', raw, flags=re.IGNORECASE)
            raw = re.sub(r'\s*```$', '', raw).strip()
            data = json.loads(raw)

            inferred_title = str(data.get("title") or "").strip()
            inferred_company = str(data.get("company") or "").strip()

            final_title = title
            final_company = company
            if self._is_unknown_job_value(final_title) and inferred_title:
                final_title = inferred_title
            if self._is_unknown_job_value(final_company) and inferred_company:
                final_company = inferred_company
            return final_company, final_title
        except Exception as e:
            logger.debug(f"Job metadata LLM extraction skipped: {e}")
            return company, title

    async def auto_apply_menu(self):
        self.clear_screen()
        self.print_header("ASSISTED AUTO-APPLY (BATCH)")

        if not self._ensure_agents_bootstrapped():
            self.pause()
            return

        self._show_auto_apply_profile_warning_if_needed()

        if not self._ensure_resume_ready_for_auto_apply():
            self.pause()
            return

        self.print_info("Automatically fill and submit job applications in a controlled batch.")
        self.print_info("Warning: the agent can still make mistakes and may submit an application.")
        self.print_info("Credits: 1 credit is consumed per successful application.\n")

        num_jobs_str = self.get_input("How many jobs do you want to apply to? (1-10, default: 1): ").strip()
        if not num_jobs_str:
            num_jobs = 1
        else:
            try:
                num_jobs = int(num_jobs_str)
                if num_jobs < 1:
                    self.print_error("Number of jobs must be at least 1.")
                    self.pause()
                    return
                if num_jobs > 10:
                    self.print_warning("Maximum 10 jobs allowed. Setting to 10.")
                    num_jobs = 10
            except ValueError:
                self.print_error("Invalid number.")
                self.pause()
                return

        job_urls = []
        self.print_info(f"\nPlease enter {num_jobs} job application URL(s):")
        for i in range(num_jobs):
            url = self.get_input(f"  Job #{i+1} URL: ").strip()
            if not url:
                self.print_warning(f"Skipping empty URL for job #{i+1}")
                continue
            if not url.startswith(('http://', 'https://')):
                self.print_warning(f"Invalid URL for job #{i+1} (must start with http:// or https://)")
                continue
            job_urls.append(url)

        if not job_urls:
            self.print_error("No valid job URLs provided.")
            self.pause()
            return

        self.print_success(f"\n✓ Collected {len(job_urls)} valid job URL(s)")

        tailor_option = self.get_input("Tailor resume: (a)ll, (n)one, or (i)ndividual? (a/n/i, default: n): ").strip().lower()

        tailor_settings = []
        if tailor_option == 'a':
            tailor_settings = [True] * len(job_urls)
            self.print_info("✓ Will tailor resume for all jobs")
        elif tailor_option == 'i':
            for i, url in enumerate(job_urls):
                tailor = self.get_input_yn(f"  Tailor for job #{i+1}? (y/n, default: n): ", default='n')
                tailor_settings.append(tailor)
        else:
            tailor_settings = [False] * len(job_urls)
            self.print_info("✓ Will not tailor resume for any jobs")

        if any(tailor_settings):
            self.ensure_mimikree_connected_for_tailoring()

        # ── Credit check ────────────────────────────────────────────────────
        try:
            available, daily = self.api.check_credit_available("job_applications")
            remaining = daily.get("remaining")
            limit = daily.get("limit")
            credit_str = format_credits(remaining, limit, daily.get("reset_time"))
            if daily.get("error") == "credit_check_unavailable":
                self.print_error("Could not verify credits (backend unavailable).")
                self.print_info("Blocking cost-bearing automation to prevent untracked usage.")
                self.pause()
                return
            if not available:
                self.print_error(f"Daily job application limit reached ({credit_str}).")
                self.print_info("Limits reset at midnight UTC. Check launchway.app/manage-credits")
                self.pause()
                return
            if remaining != "unlimited":
                rem_int = int(remaining)
                if rem_int < len(job_urls):
                    self.print_warning(
                        f"You only have {rem_int} credit(s) left but requested {len(job_urls)}. "
                        f"Only the first {rem_int} job(s) will be attempted."
                    )
                    job_urls = job_urls[:rem_int]
                    tailor_settings = tailor_settings[:rem_int]
            self.print_info(f"Job Application credits: {credit_str}")
        except LaunchwayAPIError:
            self.print_error("Could not verify credits. Please retry in a moment.")
            self.pause()
            return

        await self.auto_apply_batch(job_urls, tailor_settings)

    async def auto_apply_batch(self, job_urls: list, tailor_settings: list):
        from Agents.job_application_agent import RefactoredJobAgent, _get_or_create_playwright

        total_jobs       = len(job_urls)
        successful_apps  = []
        failed_apps      = []
        billing_pending_apps = []
        detailed_results = []

        self.print_info(f"\n{'='*60}")
        self.print_info(f"BATCH APPLICATION MODE - {total_jobs} job(s)")
        self.print_info(f"{'='*60}\n")
        self.print_warning("Do not close browser windows manually during the process")

        try:
            playwright = await _get_or_create_playwright()
            for idx, job_url in enumerate(job_urls, start=1):
                tailor     = tailor_settings[idx - 1]
                job_result = {
                    'number':        idx,
                    'job_url':       job_url,
                    'job_title':     f'Job #{idx}',
                    'company':       'Unknown',
                    'timestamp':     datetime.now().isoformat(),
                    'success':       False,
                    'submitted':     False,
                    'fields_filled': 0,
                    'field_details': [],
                    'error':         None,
                    'tailored':      tailor,
                    'billing_pending': False,
                }

                self.print_header(f"JOB {idx}/{total_jobs}")
                self.print_info(f"URL: {job_url}")
                self.print_info(f"Tailor Resume: {'Yes' if tailor else 'No'}")

                try:
                    pre_fetched_desc = None
                    if tailor:
                        self.print_info("Pre-fetching job description for tailoring...")
                        try:
                            pre_fetched_desc = await asyncio.to_thread(
                                self._fetch_job_description_from_url, job_url
                            )
                            if pre_fetched_desc:
                                self.print_success(f"Description fetched ({len(pre_fetched_desc)} chars)")
                            else:
                                self.print_info("Will extract description from page during navigation")
                        except Exception as _pf_err:
                            logger.debug(f"Pre-fetch description error: {_pf_err}")

                    agent = RefactoredJobAgent(
                        playwright=playwright,
                        headless=False,
                        keep_open=True,
                        debug=True,
                        user_id=str(self.current_user['id']),
                        tailor_resume=tailor,
                        mimikree_email=self._session_mimikree_email if tailor else None,
                        mimikree_password=self._session_mimikree_password if tailor else None,
                        job_url=job_url,
                        use_persistent_profile=True,
                        pre_fetched_description=pre_fetched_desc,
                        profile_data=self.current_profile,
                    )
                    await agent.process_link(job_url)

                    if tailor:
                        profile_ctx = None
                        if hasattr(agent, 'state_machine') and agent.state_machine:
                            if hasattr(agent.state_machine, 'app_state'):
                                profile_ctx = agent.state_machine.app_state.context.get('profile', {})
                        if profile_ctx and profile_ctx.get('tailoring_metrics'):
                            self._display_tailored_resume_download(
                                profile_ctx['tailoring_metrics'],
                                job_result.get('company', 'Unknown'),
                            )

                    human_needed = getattr(agent, 'keep_browser_open_for_human', False)

                    if hasattr(agent, 'action_recorder') and agent.action_recorder:
                        for action in agent.action_recorder.actions:
                            if action.type in ['fill_field', 'enhanced_field_fill', 'select_option'] and action.success:
                                job_result['fields_filled'] += 1
                                job_result['field_details'].append({
                                    'label': action.field_label or 'Unknown',
                                    'value': action.value or '',
                                    'type':  action.field_type or 'unknown',
                                })
                        submit_actions = [
                            a for a in agent.action_recorder.actions
                            if 'submit' in a.type.lower() or
                            (a.type == 'click' and 'submit' in (a.element_text or '').lower())
                        ]
                        if submit_actions and not human_needed:
                            job_result['submitted'] = True

                    if human_needed:
                        job_result['submitted'] = False
                        job_result['error']     = 'Human intervention required'

                    if job_result['fields_filled'] > 0 and job_result['submitted']:
                        job_result['success'] = True
                        resolved_company, resolved_title = self._extract_job_metadata_with_llm(
                            job_url=job_url,
                            company=job_result.get('company', 'Unknown Company'),
                            title=job_result.get('job_title', f'Job #{idx}'),
                            description=pre_fetched_desc or "",
                        )
                        self.record_application(job_url, company=resolved_company, title=resolved_title)
                        successful_apps.append({'number': idx, 'url': job_url, 'tailored': tailor})
                        self.print_success(f"Job #{idx} submitted! ({job_result['fields_filled']} fields filled)")
                        # Consume one application credit and show updated balance
                        try:
                            cr = self.api.consume_credit("job_applications")
                            self.print_info(
                                f"Job Application credits: "
                                f"{format_credits(cr.get('remaining'), cr.get('limit'), cr.get('reset_time'))}"
                            )
                        except LaunchwayAPIError:
                            job_result['billing_pending'] = True
                            job_result['error'] = "Credit debit failed after submission; billing reconciliation pending"
                            billing_pending_apps.append({'number': idx, 'url': job_url})
                            self.print_warning(
                                "Credit debit failed after submission. Marked billing_pending and stopping batch."
                            )
                            detailed_results.append(job_result)
                            break
                    else:
                        job_result['success']   = False
                        job_result['submitted'] = False
                        if not job_result.get('error'):
                            job_result['error'] = f"Incomplete ({job_result['fields_filled']} fields filled, not submitted)"
                        failed_apps.append({'number': idx, 'url': job_url, 'error': job_result['error']})
                        self.print_warning(f"Job #{idx} incomplete ({job_result['fields_filled']} fields filled)")

                except Exception as e:
                    error_str       = str(e)
                    job_result['error'] = error_str
                    self.print_error(f"Job #{idx} failed: {error_str[:100]}")
                    logger.error(f"Job #{idx} auto apply error: {e}", exc_info=True)
                    failed_apps.append({'number': idx, 'url': job_url, 'error': error_str})

                detailed_results.append(job_result)
                if idx < total_jobs:
                    self.print_info("\n" + "="*60 + "\n")

        except Exception as e:
            self.print_error(f"Batch process error: {str(e)}")
            logger.error(f"Batch application error: {e}", exc_info=True)

        # Save local report
        report_filename = f"batch_progress_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        try:
            report = {
                'report_type':  'batch_report',
                'generated_at': datetime.now().isoformat(),
                'total_jobs':   total_jobs,
                'successful':   len(successful_apps),
                'failed':       len(failed_apps),
                'applications': detailed_results,
            }
            with open(report_filename, 'w') as f:
                json.dump(report, f, indent=2)
            self.print_info(f"Progress report saved: {report_filename}")
        except Exception as e:
            logger.error(f"Failed to save batch report: {e}")

        self.print_header("BATCH APPLICATION SUMMARY")
        self.print_info(f"Total jobs processed:     {total_jobs}")
        self.print_success(f"Successful applications: {len(successful_apps)}")
        if successful_apps:
            for app in successful_apps:
                self.print_info(f"  Job #{app['number']}: {app['url'][:60]}...")
        if failed_apps:
            self.print_error(f"\nFailed applications: {len(failed_apps)}")
            for app in failed_apps:
                self.print_error(f"  Job #{app['number']}: {str(app.get('error',''))[:100]}")
        if billing_pending_apps:
            self.print_warning(f"\nSubmitted with billing pending: {len(billing_pending_apps)}")
            for app in billing_pending_apps:
                self.print_warning(f"  Job #{app['number']}: {app['url'][:100]}")

        incomplete_count = len([j for j in detailed_results if not j['submitted']])
        if incomplete_count > 0:
            self.print_warning(f"\n{incomplete_count} application(s) were not fully submitted.")
            self.print_info("The browser window is already open - complete and submit manually.")
        else:
            self.print_info("\nAll browser windows are still open for your review.")

        self.pause()

    async def auto_apply_single(self, job_url: str):
        if not self._ensure_agents_bootstrapped():
            self.pause()
            return

        if not self._ensure_resume_ready_for_auto_apply():
            self.pause()
            return

        from Agents.job_application_agent import RefactoredJobAgent

        tailor   = self.get_input_yn("Tailor resume for this job? (y/n, default: n): ", default='n')

        # Block expensive single-run apply when credits cannot be verified.
        available, daily = self.api.check_credit_available("job_applications")
        if daily.get("error") == "credit_check_unavailable":
            self.print_error("Could not verify credits (backend unavailable).")
            self.print_info("Blocking automation to prevent untracked usage.")
            self.pause()
            return
        if not available:
            credit_str = format_credits(daily.get("remaining"), daily.get("limit"), daily.get("reset_time"))
            self.print_error(f"Daily job application limit reached ({credit_str}).")
            self.pause()
            return

        if tailor:
            self.ensure_mimikree_connected_for_tailoring()

        try:
            self.print_info("\nStarting automated job application...")
            self.print_warning("You may need to complete CAPTCHA or final submission manually.")

            pre_fetched_desc = None
            if tailor:
                self.print_info("Pre-fetching job description for tailoring...")
                try:
                    pre_fetched_desc = await asyncio.to_thread(
                        self._fetch_job_description_from_url, job_url
                    )
                    if pre_fetched_desc:
                        self.print_success(f"Description fetched ({len(pre_fetched_desc)} chars)")
                except Exception as _e:
                    logger.debug(f"Pre-fetch error: {_e}")

            from playwright.async_api import async_playwright
            async with async_playwright() as playwright:
                agent = RefactoredJobAgent(
                    playwright=playwright,
                    headless=False,
                    keep_open=True,
                    debug=True,
                    user_id=str(self.current_user['id']),
                    tailor_resume=tailor,
                    mimikree_email=self._session_mimikree_email if tailor else None,
                    mimikree_password=self._session_mimikree_password if tailor else None,
                    job_url=job_url,
                    use_persistent_profile=True,
                    pre_fetched_description=pre_fetched_desc,
                    profile_data=self.current_profile,
                )
                await agent.process_link(job_url)

                if tailor:
                    profile_ctx = None
                    if hasattr(agent, 'state_machine') and agent.state_machine:
                        if hasattr(agent.state_machine, 'app_state'):
                            profile_ctx = agent.state_machine.app_state.context.get('profile', {})
                    if profile_ctx and profile_ctx.get('tailoring_metrics'):
                        self._display_tailored_resume_download(profile_ctx['tailoring_metrics'], 'Company')

            resolved_company, resolved_title = self._extract_job_metadata_with_llm(
                job_url=job_url,
                company='Unknown Company',
                title='Unknown Position',
                description=pre_fetched_desc or "",
            )
            self.record_application(job_url, company=resolved_company, title=resolved_title)
            billing_pending = False
            try:
                cr = self.api.consume_credit("job_applications")
                self.print_info(
                    f"Job Application credits: "
                    f"{format_credits(cr.get('remaining'), cr.get('limit'), cr.get('reset_time'))}"
                )
            except LaunchwayAPIError:
                billing_pending = True
                self.print_warning(
                    "Application was submitted, but credit debit failed. Marking as billing_pending."
                )
            self.print_success("Application process completed!")
            if billing_pending:
                self.print_warning("Status: submitted=true, billing_pending=true")
            self.print_info("Check the browser for final status.")
            self.pause()

        except Exception as e:
            self.print_error(f"Auto apply failed: {str(e)}")
            logger.error(f"Auto apply error: {e}", exc_info=True)
            self.pause()

    async def _open_incomplete_applications(self, report_filename: str):
        from Agents.persistent_browser_manager import PersistentBrowserManager
        import os

        try:
            self.print_info(f"\n📖 Reading progress report: {report_filename}")
            if not os.path.exists(report_filename):
                self.print_error(f"Progress report not found: {report_filename}")
                return

            with open(report_filename, 'r') as f:
                report_data = json.load(f)

            incomplete_apps = [
                job for job in report_data.get('applications', [])
                if not job.get('submitted', False)
            ]

            if not incomplete_apps:
                self.print_success("All applications were submitted successfully!")
                return

            self.print_info(f"\n✓ Found {len(incomplete_apps)} incomplete application(s)")
            for i, app in enumerate(incomplete_apps, 1):
                print(f"\n{i}. {app.get('job_title','Unknown')} at {app.get('company','Unknown')}")
                print(f"   URL: {str(app.get('job_url','N/A'))[:70]}...")

            if not self.get_input_yn(f"\nOpen all {len(incomplete_apps)} application(s) in browser? (y/n): ", default=None):
                self.print_info("Cancelled.")
                return

            manager      = PersistentBrowserManager()
            profile_path = manager.get_profile_path(str(self.current_user['id']))

            from playwright.async_api import async_playwright
            async with async_playwright() as playwright:
                context = await manager.launch_persistent_browser(
                    user_id=str(self.current_user['id']),
                    headless=False,
                )

                pages = []
                for i, app in enumerate(incomplete_apps, 1):
                    job_url = app.get('job_url')
                    if not job_url:
                        continue
                    try:
                        page = await context.new_page()
                        await page.goto(job_url, timeout=30000, wait_until='domcontentloaded')
                        pages.append(page)
                        self.print_success(f"  ✓ Tab {i}: {str(app.get('job_title','Unknown'))[:50]}")
                        await asyncio.sleep(1)
                    except Exception as e:
                        self.print_warning(f"  ⚠ Tab {i} failed: {str(e)}")

                self.print_success(f"✓ Opened {len(pages)} application(s) in browser tabs")
                self.print_info("\nComplete and submit each application, then press Enter here.")
                input()
                self.print_info("Keeping browser and tabs open for your review.")

        except Exception as e:
            self.print_error(f"Failed to open incomplete applications: {str(e)}")
            logger.error(f"Open incomplete applications error: {e}", exc_info=True)

    def record_application(self, job_url: str, company: str = "Unknown Company",
                           title: str = "Unknown Position", description: str = ""):
        """Record a completed job application via the backend API."""
        try:
            if self._is_unknown_job_value(company) or self._is_unknown_job_value(title):
                company, title = self._extract_job_metadata_with_llm(
                    job_url=job_url,
                    company=company,
                    title=title,
                    description=description,
                )
            self.api.record_application(job_url, company=company, title=title)
            logger.info(f"Application recorded: {job_url} | {company} | {title}")
        except LaunchwayAPIError as e:
            logger.error(f"Failed to record application via API: {e}")

    def get_applied_job_urls(self) -> set:
        """Return URLs of previously applied-to jobs (for deduplication)."""
        return self.api.get_applied_job_urls()
