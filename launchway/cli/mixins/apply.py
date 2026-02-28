"""Batch / single auto-apply mixin — application recording via the Launchway API."""

import asyncio
import json
import logging
import os
from datetime import datetime
from launchway.api_client import LaunchwayAPIError
from launchway.cli.utils import Colors

logger = logging.getLogger(__name__)

try:
    from Agents.job_application_agent import RefactoredJobAgent
    _JOB_APPLICATION_AVAILABLE = True
except Exception as e:
    logger.error(f"Job application agent not available: {e}", exc_info=True)
    _JOB_APPLICATION_AVAILABLE = False


class ApplyMixin:

    async def auto_apply_menu(self):
        self.clear_screen()
        self.print_header("AUTO APPLY TO JOB(S)")

        if not _JOB_APPLICATION_AVAILABLE:
            self.print_error("Auto-apply feature is not available.")
            self.print_info("Missing dependencies or configuration.")
            self.pause()
            return

        if not self._ensure_resume_ready_for_auto_apply():
            self.pause()
            return

        self.print_info("Automatically fill and submit job applications.")
        self.print_info("You can apply to multiple jobs (up to 10) in one batch.\n")

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

        headless      = self.get_input("\nRun in headless mode? (y/n, default: n): ").strip().lower() == 'y'
        tailor_option = self.get_input("Tailor resume: (a)ll, (n)one, or (i)ndividual? (a/n/i, default: n): ").strip().lower()

        tailor_settings = []
        if tailor_option == 'a':
            tailor_settings = [True] * len(job_urls)
            self.print_info("✓ Will tailor resume for all jobs")
        elif tailor_option == 'i':
            for i, url in enumerate(job_urls):
                tailor = self.get_input(f"  Tailor for job #{i+1}? (y/n, default: n): ").strip().lower() == 'y'
                tailor_settings.append(tailor)
        else:
            tailor_settings = [False] * len(job_urls)
            self.print_info("✓ Will not tailor resume for any jobs")

        if any(tailor_settings):
            mimikree_email, mimikree_password = self.ensure_mimikree_connected_for_tailoring()
            if not mimikree_email or not mimikree_password:
                self.pause()
                return

        await self.auto_apply_batch(job_urls, tailor_settings, headless)

    async def auto_apply_batch(self, job_urls: list, tailor_settings: list, headless: bool = False):
        total_jobs       = len(job_urls)
        successful_apps  = []
        failed_apps      = []
        detailed_results = []

        self.print_info(f"\n{'='*60}")
        self.print_info(f"BATCH APPLICATION MODE — {total_jobs} job(s)")
        self.print_info(f"{'='*60}\n")
        self.print_warning("Do not close browser windows manually during the process")

        try:
            from playwright.async_api import async_playwright
            async with async_playwright() as playwright:
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
                            headless=headless,
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
                            self.record_application(job_url)
                            successful_apps.append({'number': idx, 'url': job_url, 'tailored': tailor})
                            self.print_success(f"Job #{idx} submitted! ({job_result['fields_filled']} fields filled)")
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

        incomplete_count = len([j for j in detailed_results if not j['submitted']])
        if incomplete_count > 0:
            self.print_warning(f"\n{incomplete_count} application(s) were not fully submitted.")
            if self.get_input("Open incomplete applications for manual completion? (y/n): ").strip().lower() == 'y':
                await self._open_incomplete_applications(report_filename)
        else:
            self.print_warning("\nAll browser windows are still open for your review.")

        self.pause()

    async def auto_apply_single(self, job_url: str):
        if not _JOB_APPLICATION_AVAILABLE:
            self.print_error("Auto-apply feature is not available.")
            self.pause()
            return

        if not self._ensure_resume_ready_for_auto_apply():
            self.pause()
            return

        headless = self.get_input("Run in headless mode? (y/n, default: n): ").strip().lower() == 'y'
        tailor   = self.get_input("Tailor resume for this job? (y/n, default: n): ").strip().lower() == 'y'

        if tailor:
            mimikree_email, mimikree_password = self.ensure_mimikree_connected_for_tailoring()
            if not mimikree_email or not mimikree_password:
                self.pause()
                return

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
                    headless=headless,
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

            self.record_application(job_url)
            self.print_success("Application process completed!")
            self.print_info("Check the browser for final status.")
            self.pause()

        except Exception as e:
            self.print_error(f"Auto apply failed: {str(e)}")
            logger.error(f"Auto apply error: {e}", exc_info=True)
            self.pause()

    def record_application(self, job_url: str, company: str = "Unknown Company",
                           title: str = "Unknown Position"):
        """Record a completed job application via the backend API."""
        try:
            self.api.record_application(job_url, company=company, title=title)
            logger.info(f"Application recorded: {job_url}")
        except LaunchwayAPIError as e:
            logger.error(f"Failed to record application via API: {e}")

    def get_applied_job_urls(self) -> set:
        """Return URLs of previously applied-to jobs (for deduplication)."""
        return self.api.get_applied_job_urls()
