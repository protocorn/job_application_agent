"""Continuous auto-apply mixin - fully autonomous automation mode."""

import asyncio
import json
import logging
import os
import re
import signal
import time
from collections import deque
from datetime import datetime, timedelta
from html.parser import HTMLParser
from typing import Any, Dict, Optional
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import requests

from launchway.api_client import LaunchwayAPIError
from launchway.cli.utils import Colors, format_credits

logger = logging.getLogger(__name__)


class ContinuousApplyMixin:

    def _sanitize_search_query(self, query: str, fallback_keywords: str = "") -> str:
        if not query:
            return fallback_keywords or ""

        cleaned = str(query).strip()
        cleaned = re.sub(r"\s+", " ", cleaned)
        return cleaned or (fallback_keywords or "")

    def _normalize_company_name(self, company: Any) -> str:
        company_name = str(company or "").strip()
        if company_name.lower() in {"nan", "none", "null", "n/a", "na"}:
            company_name = ""
        return company_name if company_name else "Unknown Company"

    def _canonicalize_job_url(self, url: str) -> str:
        raw = (url or "").strip()
        if not raw:
            return ""
        try:
            split = urlsplit(raw)
            scheme = (split.scheme or "https").lower()
            netloc = split.netloc.lower()
            path = split.path.rstrip("/")
            keep_query_keys = {
                "id",
                "jobid",
                "job_id",
                "pid",
                "jk",
                "gh_jid",
                "reqid",
                "requisitionid",
            }
            query_pairs = [
                (k.lower(), v)
                for k, v in parse_qsl(split.query, keep_blank_values=False)
                if k and k.lower() in keep_query_keys
            ]
            query = urlencode(sorted(query_pairs))
            return urlunsplit((scheme, netloc, path, query, ""))
        except Exception:
            return raw.rstrip("/").lower()

    def _extract_job_url(self, job: Dict[str, Any]) -> Optional[str]:
        apply_links = job.get('apply_links', {})
        if apply_links:
            return apply_links.get('primary') or apply_links.get('indeed') or apply_links.get('linkedin')
        return job.get('job_url') or job.get('url')

    def _fetch_job_description_from_url(self, url: str) -> Optional[str]:
        """Lightweight HTTP fetch of a job listing to extract description text."""
        try:
            headers = {
                'User-Agent': (
                    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                    'AppleWebKit/537.36 (KHTML, like Gecko) '
                    'Chrome/121.0.0.0 Safari/537.36'
                ),
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
                'Accept-Language': 'en-US,en;q=0.5',
            }
            response = requests.get(url, headers=headers, timeout=10, allow_redirects=True)
            if response.status_code != 200:
                return None

            class _TextExtractor(HTMLParser):
                def __init__(self):
                    super().__init__()
                    self._chunks: list = []
                    self._skip = False

                def handle_starttag(self, tag, attrs):
                    if tag in ('script', 'style', 'nav', 'header', 'footer', 'noscript'):
                        self._skip = True

                def handle_endtag(self, tag):
                    if tag in ('script', 'style', 'nav', 'header', 'footer', 'noscript'):
                        self._skip = False

                def handle_data(self, data):
                    if not self._skip:
                        stripped = data.strip()
                        if stripped:
                            self._chunks.append(stripped)

            extractor = _TextExtractor()
            extractor.feed(response.text)
            text = ' '.join(extractor._chunks)
            text = ' '.join(text.split())
            return text[:12000] if len(text) >= 200 else None

        except Exception as e:
            logger.debug(f"Pre-fetch description failed for {url}: {e}")
            return None

    def _is_rate_limit_error(self, error: Exception) -> bool:
        error_str = str(error).lower()
        return any(kw in error_str for kw in ['429', 'rate limit', 'resource_exhausted', 'quota', 'too many requests'])

    def _prewarm_runtime_models(self):
        """Warm heavy local models once so first application run is smoother."""
        try:
            from Agents.components.executors.semantic_field_mapper import SemanticFieldMapper
            mapper = SemanticFieldMapper()
            if mapper._ensure_initialized():
                self.print_info("✓ Semantic mapper pre-warmed")
        except Exception as e:
            logger.debug(f"Semantic mapper prewarm skipped: {e}")

    async def _handle_rate_limit(self, automation_state: Dict[str, Any]):
        self.print_warning("\n⚠️  RATE LIMIT DETECTED")
        self.print_info("Pausing for 60 seconds before retrying...")

        if automation_state['rate_limit_hits'] > 5:
            self.print_error("\n❌ Multiple rate limit hits detected. This may be a daily quota limit.")
            if self.get_input_yn("\nStop automation? (y/n, default: n): ", default='n'):
                automation_state['running'] = False
                return

        for i in range(60, 0, -5):
            self.print_info(f"  Waiting... {i} seconds remaining", end='\r')
            await asyncio.sleep(5)

        self.print_success("\n✓ Resuming automation")

    def _save_progress_report(self, filename: str, automation_state: Dict[str, Any],
                               job_queue: deque, final: bool = False):
        try:
            report = {
                'report_type':  'final_report' if final else 'progress_checkpoint',
                'generated_at': datetime.now().isoformat(),
                'session_info': {
                    'start_time':            automation_state['start_time'].isoformat(),
                    'duration_minutes':      round((datetime.now() - automation_state['start_time']).total_seconds() / 60, 2),
                    'status':                'completed' if final else 'in_progress',
                    'original_keywords':     automation_state.get('original_keywords', ''),
                    'optimized_keywords':    automation_state.get('optimized_keywords', ''),
                    'query_variations_used': automation_state.get('query_variations', []),
                },
                'statistics': {
                    'applications_submitted': automation_state['applications_submitted'],
                    'applications_failed':    automation_state['applications_failed'],
                    'jobs_discovered':        automation_state['jobs_discovered'],
                    'jobs_processed':         automation_state['jobs_processed'],
                    'rate_limit_hits':        automation_state['rate_limit_hits'],
                    'success_rate':           round(
                        (automation_state['applications_submitted'] / max(automation_state['jobs_processed'], 1)) * 100, 2
                    ),
                },
                'applications':    automation_state['progress_log'],
                'queue_remaining': len(job_queue),
            }
            with open(filename, 'w') as f:
                json.dump(report, f, indent=2)
            if final:
                logger.info(f"Final progress report saved: {filename}")
        except Exception as e:
            logger.error(f"Failed to save progress report: {e}", exc_info=True)

    def _display_automation_summary(self, automation_state: Dict[str, Any], report_filename: str):
        duration = (datetime.now() - automation_state['start_time']).total_seconds() / 60
        self.print_info(f"\nSession Duration:         {duration:.1f} minutes")
        self.print_info(f"Jobs Discovered:          {automation_state['jobs_discovered']}")
        self.print_info(f"Jobs Processed:           {automation_state['jobs_processed']}")
        self.print_success(f"Applications Submitted: {automation_state['applications_submitted']}")
        if automation_state['applications_failed'] > 0:
            self.print_error(f"Applications Failed: {automation_state['applications_failed']}")
        if automation_state['rate_limit_hits'] > 0:
            self.print_warning(f"Rate Limit Hits: {automation_state['rate_limit_hits']}")
        if automation_state['jobs_processed'] > 0:
            success_rate = (automation_state['applications_submitted'] / automation_state['jobs_processed']) * 100
            self.print_info(f"Success Rate: {success_rate:.1f}%")
        self.print_info(f"\n📄 Detailed Progress Report: {report_filename}")
        self.print_success("\n✓ All data has been saved")

        incomplete_count = automation_state['jobs_processed'] - automation_state['applications_submitted']
        if incomplete_count > 0:
            print("\n" + "=" * 60)
            self.print_warning(f"⚠️  {incomplete_count} application(s) were not fully submitted.")
            self._should_open_incomplete   = self.get_input_yn("\nOpen incomplete applications? (y/n): ", default=None)
            self._incomplete_report_file   = report_filename if self._should_open_incomplete else None

    async def _apply_to_single_job_automated(
        self,
        job_url:          str,
        job_title:        str,
        company:          str,
        tailor_resume:    bool,
        replace_projects_on_tailor: bool,
        headless:         bool,
        automation_state: Dict[str, Any],
        description:      str = '',
    ) -> Dict[str, Any]:
        from Agents.job_application_agent import RefactoredJobAgent, _get_or_create_playwright

        start_time = time.time()

        job_result = {
            'job_url':          job_url,
            'job_title':        job_title,
            'company':          company,
            'timestamp':        datetime.now().isoformat(),
            'success':          False,
            'submitted':        False,
            'fields_filled':    0,
            'field_details':    [],
            'error':            None,
            'rate_limit_error': False,
            'duration_seconds': 0,
            'billing_pending':  False,
            'agent_final_state': None,
            'human_intervention_required': False,
        }

        try:
            self.print_info("🤖 Starting automated application...")
            self.print_info(f"   Tailor Resume: {'✓ Enabled' if tailor_resume else '✗ Disabled'}")
            self._mark_job_tracking_status(
                job_url,
                "attempted_auto",
                company=company,
                title=job_title,
                evidence="continuous_start",
            )

            playwright = await _get_or_create_playwright()
            agent = RefactoredJobAgent(
                playwright=playwright,
                headless=headless,
                keep_open=False,
                debug=False,
                user_id=str(self.current_user['id']),
                tailor_resume=tailor_resume,
                replace_projects_on_tailor=replace_projects_on_tailor if tailor_resume else False,
                job_url=job_url,
                use_persistent_profile=True,
                pre_fetched_description=description or None,
                profile_data=self.current_profile,
                full_auto_mode=True,
            )
            await agent.process_link(job_url)

            app_state = None
            if hasattr(agent, 'state_machine') and agent.state_machine and hasattr(agent.state_machine, 'app_state'):
                app_state = agent.state_machine.app_state
                job_result['agent_final_state'] = app_state.context.get('final_state')
            if not job_result.get('agent_final_state'):
                job_result['agent_final_state'] = getattr(agent, 'last_state_machine_final_state', None)

            if tailor_resume:
                profile = None
                if hasattr(agent, 'state_machine') and agent.state_machine:
                    if hasattr(agent.state_machine, 'app_state'):
                        profile = agent.state_machine.app_state.context.get('profile', {})
                if profile and profile.get('tailoring_metrics'):
                    tailoring_metrics = profile['tailoring_metrics']
                    pdf_path       = tailoring_metrics.get('pdf_path')
                    google_doc_url = tailoring_metrics.get('url')
                    self.print_success(f"✨ Resume tailored for {company}")
                    if pdf_path and os.path.exists(pdf_path):
                        self.print_info(f"   📄 PDF: {pdf_path}")
                        job_result['tailored_resume_path'] = pdf_path
                    if google_doc_url:
                        self.print_info(f"   🔗 Google Doc: {google_doc_url}")
                        job_result['tailored_resume_url'] = google_doc_url
                    match_stats = tailoring_metrics.get('match_stats', {})
                    if match_stats:
                        match_pct = match_stats.get('match_percentage', 0)
                        added     = match_stats.get('added', 0)
                        self.print_info(f"   📊 Match Rate: {match_pct:.1f}% | Keywords Added: {added}")

            human_needed = getattr(agent, 'keep_browser_open_for_human', False)
            if app_state and app_state.context.get('final_state') == 'human_intervention':
                human_needed = True

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
                job_result['human_intervention_required'] = True
                self._mark_job_tracking_status(
                    job_url,
                    "human_takeover_open_tab",
                    company=company,
                    title=job_title,
                    evidence="continuous_handoff",
                )
                self._start_manual_submission_hook(
                    agent,
                    job_url=job_url,
                    company=company,
                    title=job_title,
                )
                if app_state and hasattr(app_state, 'context'):
                    reason = app_state.context.get('human_intervention_reason')
                    if reason:
                        job_result['error'] = f'Human intervention required: {reason}'
                if not job_result.get('error'):
                    job_result['error'] = 'Human intervention required'

            # Trust terminal state machine result when available.
            final_state = job_result.get('agent_final_state')
            if final_state == 'success':
                job_result['submitted'] = True
            elif final_state in {'fail', 'human_intervention'}:
                job_result['submitted'] = False

            if job_result['submitted']:
                job_result['success'] = True
                automation_state['applications_submitted'] += 1
                self.record_application(
                    job_url,
                    company=company,
                    title=job_title,
                    description=description or "",
                )
                self.print_success(f"✓ Application submitted! ({job_result['fields_filled']} fields filled)")
                # Consume one application credit and show updated balance
                try:
                    _cr = self.api.consume_credit("job_applications")
                    self.print_info(
                        f"Job Application credits: "
                        f"{format_credits(_cr.get('remaining'), _cr.get('limit'), _cr.get('reset_time'))}"
                    )
                except LaunchwayAPIError:
                    job_result['billing_pending'] = True
                    job_result['error'] = "Credit debit failed after submission; billing reconciliation pending"
                    self.print_warning(
                        "Credit debit failed after submission. Marked billing_pending and stopping automation."
                    )
                    automation_state['running'] = False
            else:
                job_result['success']   = False
                job_result['submitted'] = False
                automation_state['applications_failed'] += 1
                if not job_result.get('human_intervention_required'):
                    self._mark_job_tracking_status(
                        job_url,
                        "abandoned_or_timeout",
                        company=company,
                        title=job_title,
                        evidence="continuous_incomplete",
                    )
                if not job_result.get('error'):
                    status_suffix = f", state={job_result['agent_final_state']}" if job_result.get('agent_final_state') else ""
                    job_result['error'] = (
                        f"Incomplete ({job_result['fields_filled']} fields filled, not submitted{status_suffix})"
                    )
                self.print_warning(f"⚠ Application incomplete ({job_result['fields_filled']} fields filled)")

        except Exception as e:
            error_str = str(e)
            job_result['error'] = error_str
            automation_state['applications_failed'] += 1
            self._mark_job_tracking_status(
                job_url,
                "abandoned_or_timeout",
                company=company,
                title=job_title,
                evidence=f"continuous_exception:{error_str[:120]}",
            )
            if self._is_rate_limit_error(e):
                job_result['rate_limit_error'] = True
                self.print_error(f"✗ Rate limit hit: {error_str[:100]}")
            else:
                self.print_error(f"✗ Application failed: {error_str[:100]}")
            logger.error(f"Auto apply error: {e}", exc_info=True)

        job_result['duration_seconds'] = round(time.time() - start_time, 2)
        return job_result

    async def _open_incomplete_applications(self, report_filename: str):
        from Agents.persistent_browser_manager import PersistentBrowserManager

        try:
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
                print(f"   URL: {str(app.get('job_url','N/A'))[:70]}")

            if not self.get_input_yn(f"\nOpen all {len(incomplete_apps)} application(s) in browser? (y/n): ", default=None):
                self.print_info("Cancelled.")
                return

            manager = PersistentBrowserManager()
            context = await manager.launch_persistent_browser(
                user_id=str(self.current_user['id']),
                headless=False,
            )

            self.print_success("✓ Browser opened with persistent profile")

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

            try:
                await context.close()
                PersistentBrowserManager.close_browser_for_user(str(self.current_user['id']))
            except Exception as e:
                logger.warning(f"Error closing browser: {e}")

        except Exception as e:
            self.print_error(f"Failed to open incomplete applications: {str(e)}")
            logger.error(f"Open incomplete applications error: {e}", exc_info=True)

    async def continuous_auto_apply_menu(self):
        self.clear_screen()
        self.print_header("🚀 FULLY AUTONOMOUS AUTO-APPLY (CONTINUOUS)")

        if not self._ensure_agents_bootstrapped():
            self.pause()
            return

        self._show_auto_apply_profile_warning_if_needed()

        if not self._ensure_resume_ready_for_auto_apply():
            self.pause()
            return

        self.print_info("This mode runs continuously and can submit applications automatically.")
        self.print_info("Warning: the agent can make mistakes and may submit an application.")
        self.print_info("\nThis mode will:")
        self.print_info("  • Continuously search for relevant jobs")
        self.print_info("  • Automatically tailor your resume for each job")
        self.print_info("  • Fill and SUBMIT applications automatically")
        self.print_info("  • Keep every opened application tab for manual finish when needed")
        self.print_info("  • Handle rate limits gracefully (pause & retry)")
        self.print_info("  • Rotate proxies to avoid IP bans")
        self.print_info("  • Generate detailed progress reports")
        self.print_info("  • Consume 1 credit per successful application")
        self.print_info("\nYou can stop anytime by pressing Ctrl+C\n")

        keywords = self.get_input("Job Keywords (e.g., 'Software Engineer'): ").strip()
        if not keywords:
            self.print_error("Keywords are required.")
            self.pause()
            return

        location      = self.get_input("Location (optional, leave blank for any): ").strip()
        remote        = self.get_input_yn("Remote only? (y/n, default: n): ", default='n')
        easy_apply    = self.get_input_yn("Easy Apply only? (y/n, default: n): ", default='n')
        hours_old_str = self.get_input("Only jobs posted in last N hours? (optional): ").strip()
        hours_old     = None
        if hours_old_str:
            try:
                hours_old = max(1, int(hours_old_str))
            except ValueError:
                self.print_warning("Invalid hours value. Using no recency filter.")

        tailor_all = self.get_input_yn("Tailor resume for each job? (y/n, default: y): ", default='y')

        replace_projects_on_tailor_all = False
        if tailor_all:
            if not self._confirm_profile_gate():
                self.print_info("Profile gate declined. Continuous run will proceed without tailoring.")
                tailor_all = False
            else:
                self.print_info(
                    "Hint: Keep Profile > Projects updated to enable high-quality project swaps during tailoring."
                )
                replace_projects_on_tailor_all = self.get_input_yn(
                    "Enable project replacement for each tailored resume in this run? (y/n, default: n): ",
                    default='n'
                )

        headless = self.get_input_yn("Run in headless mode? (y/n, default: n): ", default='n')

        use_proxies   = self.get_input_yn("Use proxies to avoid IP bans? (y/n, default: n): ", default='n')
        proxy_manager = None

        if use_proxies:
            self.print_info("\nProxy Configuration:")
            self.print_info("  1. Environment variable: PROXY_LIST='proxy1:8080,proxy2:8080'")
            self.print_info("  2. File: proxies.txt (one per line)")
            self.print_info("  3. Manual entry now")

            proxy_choice = self.get_input("\nConfigure via (env/file/manual/skip): ").strip().lower()

            if proxy_choice == 'manual':
                proxies = []
                self.print_info("Enter proxies (format: host:port or user:pass@host:port), empty line to finish:")
                while True:
                    proxy = self.get_input(f"  Proxy #{len(proxies)+1}: ").strip()
                    if not proxy:
                        break
                    proxies.append(proxy)
                if proxies:
                    try:
                        from Agents.proxy_manager import ProxyManager
                        proxy_manager = ProxyManager(proxies, rotation_strategy="round_robin")
                        self.print_success(f"✓ {len(proxies)} proxies configured")
                    except Exception as e:
                        self.print_error(f"Failed to setup proxies: {e}")

            elif proxy_choice == 'file':
                proxy_file = self.get_input("Proxy file path (default: proxies.txt): ").strip() or "proxies.txt"
                if os.path.exists(proxy_file):
                    try:
                        from Agents.proxy_manager import ProxyManager
                        proxy_manager = ProxyManager.from_file(proxy_file)
                        self.print_success(f"✓ Proxies loaded from {proxy_file}")
                    except Exception as e:
                        self.print_error(f"Failed to load proxies: {e}")
                else:
                    self.print_error(f"File not found: {proxy_file}")

            elif proxy_choice == 'env':
                try:
                    from Agents.proxy_manager import ProxyManager
                    proxy_manager = ProxyManager.from_env()
                    if proxy_manager.proxies:
                        self.print_success(f"✓ {len(proxy_manager.proxies)} proxies loaded from environment")
                    else:
                        self.print_warning("No proxies found in environment")
                except Exception as e:
                    self.print_error(f"Failed to setup proxies: {e}")

        goal_str = self.get_input("Round goal - jobs to apply per round (max 10, default: 5): ").strip()
        try:
            session_goal = min(10, max(1, int(goal_str))) if goal_str else 5
        except ValueError:
            session_goal = 5

        cooldown_str = self.get_input("Cooldown between rounds in minutes (default: 60): ").strip()
        try:
            cooldown_minutes = max(1, int(cooldown_str)) if cooldown_str else 60
        except ValueError:
            cooldown_minutes = 60

        print(f"\n{Colors.BOLD}Summary:{Colors.ENDC}")
        print(f"  Keywords:         {keywords}")
        print(f"  Location:         {location or 'Any'}")
        print(f"  Remote:           {'Yes' if remote else 'No'}")
        print(f"  Easy Apply:       {'Yes' if easy_apply else 'No'}")
        print(f"  Hours Old Filter: {hours_old or 'Any'}")
        print(f"  Tailor Resume:    {'Yes' if tailor_all else 'No'}")
        print(f"  Headless:         {'Yes' if headless else 'No'}")
        print(f"  Proxies:          {proxy_manager.get_stats()['active_proxies'] if proxy_manager else 'None (direct)'}")
        print(f"  Round Goal:       {session_goal} jobs per round")
        print(f"  Cooldown:         {cooldown_minutes} min after each completed round")

        confirm = self.get_input(
            f"\n{Colors.WARNING}Start continuous automation? (type 'START' to confirm): {Colors.ENDC}"
        ).strip()
        if confirm.upper() != 'START':
            self.print_info("Cancelled.")
            self.pause()
            return

        # ── Credit check before launching ────────────────────────────────────
        try:
            available, daily = self.api.check_credit_available("job_applications")
            credit_str = format_credits(daily.get("remaining"), daily.get("limit"), daily.get("reset_time"))
            if daily.get("error") == "credit_check_unavailable":
                self.print_error("Could not verify credits (backend unavailable).")
                self.print_info("Blocking continuous mode to prevent untracked usage.")
                self.pause()
                return
            if not available:
                self.print_error(f"Daily job application limit reached ({credit_str}).")
                self.print_info("Limits reset at midnight UTC. Check launchway.app/manage-credits")
                self.pause()
                return
            self.print_info(f"Job Application credits at start: {credit_str}")
        except LaunchwayAPIError:
            self.print_error("Could not verify credits. Please retry in a moment.")
            self.pause()
            return

        await self.run_continuous_automation(
            keywords=keywords,
            location=location,
            remote=remote,
            easy_apply=easy_apply,
            hours_old=hours_old,
            tailor_resume=tailor_all,
            replace_projects_on_tailor=replace_projects_on_tailor_all,
            headless=headless,
            session_goal=session_goal,
            cooldown_minutes=cooldown_minutes,
            proxy_manager=proxy_manager,
        )

    async def run_continuous_automation(
        self,
        keywords:         str,
        location:         str,
        remote:           bool,
        easy_apply:       bool,
        hours_old:        Optional[int],
        tailor_resume:    bool,
        replace_projects_on_tailor: bool,
        headless:         bool,
        session_goal:     int  = 5,
        cooldown_minutes: int  = 60,
        proxy_manager=None,
    ):
        from Agents.multi_source_job_discovery_agent import MultiSourceJobDiscoveryAgent
        from Agents.gemini_query_optimizer import GeminiQueryOptimizer

        self.print_header("🚀 STARTING AUTOMATION ENGINE")
        max_rounds = max(1, int(os.getenv("LAUNCHWAY_CONTINUOUS_MAX_ROUNDS", "12")))
        max_runtime_hours = max(1.0, float(os.getenv("LAUNCHWAY_CONTINUOUS_MAX_RUNTIME_HOURS", "8")))
        max_submissions = max(1, int(os.getenv("LAUNCHWAY_CONTINUOUS_MAX_SUBMISSIONS", "50")))

        if proxy_manager:
            stats = proxy_manager.get_stats()
            self.print_success(f"✓ Proxy rotation enabled: {stats['active_proxies']} proxies ready")

        # ── Query optimisation ───────────────────────────────────────────────
        optimized_keywords     = keywords
        query_variations       = [keywords]
        profile_dict           = None
        query_optimizer        = None
        enriched_search_params = {
            "keywords":   keywords,
            "location":   location,
            "remote":     remote,
            "easy_apply": easy_apply,
            "hours_old":  hours_old,
        }

        try:
            optimizer = GeminiQueryOptimizer()
            query_optimizer = optimizer
            if self.current_profile:
                profile_dict = (
                    {k: v for k, v in self.current_profile.__dict__.items() if not k.startswith('_')}
                    if hasattr(self.current_profile, '__dict__')
                    else self.current_profile
                )
            opt_result = optimizer.optimize_search_query(keywords, location, profile_dict)
            if opt_result and opt_result.get('success'):
                raw_primary    = opt_result['primary_query']
                raw_variations = [raw_primary] + opt_result.get('variations', [])
                seen, normalized = set(), []
                for q in raw_variations:
                    n = self._sanitize_search_query(q, keywords)
                    if n and n.lower() not in seen:
                        normalized.append(n)
                        seen.add(n.lower())
                query_variations   = normalized or [keywords]
                optimized_keywords = query_variations[0]
                method = opt_result.get('method', 'unknown')
                prefix = "🤖 AI-optimized" if method == 'gemini_ai' else "✓ Rule-based optimized"
                self.print_success(f"{prefix} query: '{keywords}' → '{optimized_keywords}'")
                if len(query_variations) > 1:
                    self.print_info(f"✓ Generated {len(query_variations) - 1} alternative queries")

                param_result = optimizer.enrich_jobspy_parameters(
                    user_keywords=optimized_keywords,
                    location=location,
                    profile_data=profile_dict,
                    remote=remote,
                    user_easy_apply=easy_apply,
                    user_hours_old=hours_old,
                )
                ai_params = (param_result or {}).get("params", {})
                if ai_params:
                    enriched_search_params.update(ai_params)
                    enriched_search_params["easy_apply"] = easy_apply
                    if hours_old is not None:
                        enriched_search_params["hours_old"] = hours_old
                    self.print_success("🤖 AI-enriched search parameters enabled")

        except Exception as e:
            self.print_warning(f"⚠️  Query optimization error: {str(e)}")
            logger.error(f"Query optimization error: {e}", exc_info=True)

        # ── State ────────────────────────────────────────────────────────────
        automation_state = {
            'start_time':             datetime.now(),
            'applications_submitted': 0,
            'applications_failed':    0,
            'jobs_discovered':        0,
            'jobs_processed':         0,
            'rate_limit_hits':        0,
            'running':                True,
            'progress_log':           [],
            'original_keywords':      keywords,
            'optimized_keywords':     optimized_keywords,
            'query_variations':       query_variations,
            'enriched_search_params': enriched_search_params,
            'discovery_min_relevance': 30,
            'broaden_retry_used':     False,
            'broaden_removed_terms':  [],
            'round_number':           0,
        }

        report_filename         = f"automation_progress_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        job_queue               = deque()
        processed_urls          = set()
        overflow_queue          = deque()

        self.print_info("📋 Loading application history for deduplication...")
        previously_applied_urls = self.get_applied_job_urls()
        previously_applied_urls |= {
            self._canonicalize_job_url(u) for u in list(previously_applied_urls) if u
        }
        if previously_applied_urls:
            self.print_success(f"✓ Loaded {len(previously_applied_urls)} previously applied jobs")
        else:
            self.print_info("  → No previous applications found (fresh start)")

        def signal_handler(sig, frame):
            self.print_warning("\n\n⚠️  Stopping automation... (finishing current job)")
            automation_state['running'] = False

        try:
            signal.signal(signal.SIGINT, signal_handler)
        except Exception:
            pass

        self.print_success("✓ Automation engine initialized")
        self.print_info(f"✓ Goal: {session_goal} jobs per round, {cooldown_minutes} min cooldown")
        self.print_info(
            f"✓ Safety caps: {max_rounds} rounds, {max_submissions} submissions, {max_runtime_hours:.1f}h max runtime"
        )
        self.print_info(f"✓ Progress report: {report_filename}")
        self.print_info("✓ Press Ctrl+C to stop gracefully\n")
        self._prewarm_runtime_models()

        def _enqueue_jobs(new_jobs: list) -> tuple:
            added, skipped_applied, skipped_dup, skipped_no_url = 0, 0, 0, 0
            for job in new_jobs:
                url = self._extract_job_url(job)
                if not url:
                    skipped_no_url += 1
                    continue
                canonical_url = self._canonicalize_job_url(url)
                if canonical_url in previously_applied_urls or url in previously_applied_urls:
                    skipped_applied += 1
                    continue
                title = str(job.get('title', 'Unknown')).strip() or "Unknown"
                company = self._normalize_company_name(job.get('company', 'Unknown'))
                dedupe_key = canonical_url or f"{title.lower()}|{company.lower()}"
                if dedupe_key in processed_urls:
                    skipped_dup += 1
                    continue
                entry = {
                    'url':             url,
                    'canonical_url':   canonical_url,
                    'title':           title,
                    'company':         company,
                    'description':     job.get('description', ''),
                    'relevance_score': job.get('relevance_score', 0),
                }
                processed_urls.add(dedupe_key)
                needed = session_goal - len(job_queue)
                if needed > 0:
                    job_queue.append(entry)
                else:
                    overflow_queue.append(entry)
                added += 1
            return added, skipped_applied, skipped_dup, skipped_no_url

        def _apply_broader_retry_strategy() -> bool:
            """One-time broader search retry when strict discovery yields nothing."""
            if automation_state.get('broaden_retry_used'):
                return False

            base_keywords = self._sanitize_search_query(
                automation_state.get('original_keywords', '') or keywords,
                keywords
            ) or keywords
            current_queries = list(automation_state.get('query_variations') or [])
            seed_query = self._sanitize_search_query(
                current_queries[0] if current_queries else automation_state.get('optimized_keywords', ''),
                base_keywords
            )
            prior_removed_terms = list(automation_state.get('broaden_removed_terms') or [])

            def _drop_terms(query_text: str, terms: list) -> str:
                updated = str(query_text or "")
                for term in terms:
                    t = str(term or "").strip()
                    if not t:
                        continue
                    updated = re.sub(
                        rf"(?i)\b{re.escape(t)}\b",
                        " ",
                        updated,
                    )
                return self._sanitize_search_query(updated, "")

            refined_query = seed_query
            niche_terms_next = []
            if query_optimizer:
                try:
                    refinement = query_optimizer.refine_query_for_broader_retry(
                        current_query=seed_query,
                        location=location,
                        removed_terms_prior=prior_removed_terms,
                        profile_data=profile_dict,
                    )
                    refined_query = self._sanitize_search_query(
                        (refinement or {}).get("refined_query", seed_query),
                        seed_query
                    )
                    niche_terms_next = [
                        str(t).strip() for t in ((refinement or {}).get("niche_terms_to_remove_next") or [])
                        if str(t).strip()
                    ]
                except Exception as ge:
                    logger.warning(f"Broader retry refinement failed, using seed query: {ge}")

            # One-call strategy: use Gemini's refined query now, store niche terms
            # to drop if a future broader retry iteration is introduced.
            broader_candidates = [refined_query]
            if prior_removed_terms:
                dropped = _drop_terms(refined_query, prior_removed_terms)
                if dropped:
                    broader_candidates.append(dropped)
            broader_candidates.extend([base_keywords, f"{base_keywords} jobs"])

            seen = set()
            broadened_queries = []
            for q in broader_candidates:
                nq = self._sanitize_search_query(q, base_keywords)
                if not nq:
                    continue
                key = nq.lower()
                if key in seen:
                    continue
                seen.add(key)
                broadened_queries.append(nq)

            if niche_terms_next:
                existing = [str(t).strip() for t in prior_removed_terms if str(t).strip()]
                existing_lower = {t.lower() for t in existing}
                for t in niche_terms_next:
                    if t.lower() not in existing_lower:
                        existing.append(t)
                        existing_lower.add(t.lower())
                automation_state['broaden_removed_terms'] = existing[:12]

            automation_state['query_variations'] = broadened_queries[:4] or [base_keywords]

            overrides = dict(automation_state.get('enriched_search_params', {}))
            # Broaden temporal constraints for recall.
            overrides.pop("hours_old", None)
            # Let adapters pull more candidates during broad retry.
            overrides["results_wanted"] = max(30, int(overrides.get("results_wanted", 20) or 20))
            automation_state['enriched_search_params'] = overrides

            # Lower threshold slightly to allow more candidates into queue.
            automation_state['discovery_min_relevance'] = max(
                15, int(automation_state.get('discovery_min_relevance', 30)) - 10
            )
            automation_state['broaden_retry_used'] = True
            return True

        async def _fill_queue_to_goal() -> bool:
            all_queries = automation_state['query_variations']
            self.print_info(f"\n{'='*60}")
            self.print_info(f"🔍 ROUND {automation_state['round_number']} - DISCOVERING JOBS (goal: {session_goal})")
            self.print_info(f"{'='*60}")

            while overflow_queue and len(job_queue) < session_goal:
                job_queue.append(overflow_queue.popleft())
            if len(job_queue) >= session_goal:
                self.print_success(f"✓ Queue already at goal ({len(job_queue)}) from overflow")
                return True

            total_added = 0
            try:
                agent = MultiSourceJobDiscoveryAgent(
                    user_id=str(self.current_user['id']),
                    proxy_manager=proxy_manager,
                    profile_data=self.current_profile,
                )
                for idx, q in enumerate(all_queries):
                    if len(job_queue) >= session_goal:
                        break
                    self.print_info(f"  Query {idx + 1}/{len(all_queries)}: '{q}'")
                    search_overrides = dict(enriched_search_params)
                    search_overrides["keywords"] = q
                    if not search_overrides.get("location") and location:
                        search_overrides["location"] = location
                    try:
                        result   = agent.search_all_sources(
                            min_relevance_score=int(automation_state.get('discovery_min_relevance', 30)),
                            manual_keywords=q,
                            manual_location=search_overrides.get("location") or None,
                            manual_remote=remote,
                            manual_search_overrides=search_overrides,
                        )
                        new_jobs = result.get('data', [])
                        queue_before = len(job_queue)
                        added, s_app, s_dup, s_no_url = _enqueue_jobs(new_jobs)
                        total_added += added
                        automation_state['jobs_discovered'] += added
                        source_counts = result.get('sources', {}) or {}
                        raw_total = sum(int(v or 0) for v in source_counts.values())
                        after_dedup = int(result.get('total_before_filter', len(new_jobs)) or 0)
                        after_rank_and_applied = int(result.get('count', len(new_jobs)) or 0)
                        dropped_upstream = max(0, after_dedup - after_rank_and_applied)
                        queued_now = max(0, len(job_queue) - queue_before)
                        overflow_count = max(0, added - queued_now)
                        self.print_info(
                            f"    → {added} new  |  queue: {len(job_queue)}/{session_goal}"
                            + (
                                f"  ({s_app} prev-applied, {s_dup} dup skipped, {s_no_url} missing-url)"
                                if s_app or s_dup or s_no_url else ""
                            )
                        )
                        self.print_info(
                            "      debug: "
                            f"raw={raw_total}, unique_after_source_dedup={after_dedup}, "
                            f"after_rank+db_filter={after_rank_and_applied}, "
                            f"upstream_dropped={dropped_upstream}, added={added}, queued_now={queued_now}, overflow={overflow_count}"
                        )
                        if source_counts:
                            src_parts = ", ".join(f"{k}:{v}" for k, v in sorted(source_counts.items()))
                            self.print_info(f"      sources: {src_parts}")
                    except Exception as qe:
                        self.print_warning(f"    ⚠ Query error: {str(qe)[:80]}")
                        if self._is_rate_limit_error(qe):
                            automation_state['rate_limit_hits'] += 1
                            await self._handle_rate_limit(automation_state)

            except Exception as e:
                self.print_error(f"Discovery error: {str(e)[:100]}")
                logger.error(f"Job discovery error: {e}", exc_info=True)

            if len(job_queue) >= session_goal:
                self.print_success(f"✓ Goal reached - {len(job_queue)} jobs queued"
                                   + (f" (+{len(overflow_queue)} overflow for next round)" if overflow_queue else ""))
                return True

            self.print_warning(f"  Only {len(job_queue)}/{session_goal} jobs found after all queries.")
            return len(job_queue) > 0

        async def _run_round() -> int:
            round_submitted = 0
            round_attempted = 0
            round_goal      = min(session_goal, len(job_queue))
            while job_queue and round_submitted < session_goal and automation_state['running']:
                # Check credits before each job - stop gracefully when exhausted
                try:
                    _avail, _daily = self.api.check_credit_available("job_applications")
                    if _daily.get("error") == "credit_check_unavailable":
                        self.print_error("Credit check unavailable mid-run. Stopping automation.")
                        automation_state['running'] = False
                        break
                    if not _avail:
                        _cs = format_credits(
                            _daily.get("remaining"), _daily.get("limit"), _daily.get("reset_time")
                        )
                        self.print_warning(f"\nDaily job application limit reached ({_cs}).")
                        self.print_info("Stopping continuous mode. Limits reset at midnight UTC.")
                        automation_state['running'] = False
                        break
                except LaunchwayAPIError:
                    self.print_error("Credit check failed mid-run. Stopping automation.")
                    automation_state['running'] = False
                    break

                job = job_queue.popleft()
                round_attempted += 1
                automation_state['jobs_processed'] += 1

                self.print_header(
                    f"ROUND {automation_state['round_number']}  •  "
                    f"JOB {round_attempted}/{round_goal}  -  {job['company']}"
                )
                self.print_info(f"Title:     {job['title']}")
                self.print_info(f"URL:       {job['url'][:70]}...")
                self.print_info(f"Relevance: {job['relevance_score']:.1f}%")

                tailor_for_job = tailor_resume
                replace_projects_for_job = False
                if tailor_resume:
                    tailor_for_job = self.get_input_yn(
                        "Tailor resume for this job? (y/n, default: y): ",
                        default='y'
                    )
                    if tailor_for_job:
                        replace_projects_for_job = self.get_input_yn(
                            "Enable project replacement for this tailored resume? (y/n, default: n): ",
                            default='y' if replace_projects_on_tailor else 'n'
                        )

                job_result = await self._apply_to_single_job_automated(
                    job_url=job['url'],
                    job_title=job['title'],
                    company=job['company'],
                    description=job.get('description', ''),
                    tailor_resume=tailor_for_job,
                    replace_projects_on_tailor=replace_projects_for_job,
                    headless=headless,
                    automation_state=automation_state,
                )
                automation_state['progress_log'].append(job_result)

                if job_result.get('success') or job_result.get('submitted'):
                    previously_applied_urls.add(job['url'])
                    canonical = job.get('canonical_url') or self._canonicalize_job_url(job['url'])
                    if canonical:
                        previously_applied_urls.add(canonical)
                    round_submitted += 1

                self._save_progress_report(report_filename, automation_state, job_queue)

                if job_result.get('rate_limit_error'):
                    automation_state['rate_limit_hits'] += 1
                    await self._handle_rate_limit(automation_state)

                await asyncio.sleep(3)

            return round_submitted

        async def _cooldown():
            total_secs   = cooldown_minutes * 60
            wake_at      = datetime.now() + timedelta(seconds=total_secs)
            self.print_info(
                f"\n😴 Round complete - cooldown until {wake_at.strftime('%I:%M %p')} "
                f"({cooldown_minutes} min)"
            )
            while True:
                remaining = (wake_at - datetime.now()).total_seconds()
                if remaining <= 0 or not automation_state['running']:
                    break
                mins = int(remaining // 60)
                secs = int(remaining % 60)
                print(f"\r  ⏳ Cooldown: {mins:02d}:{secs:02d} remaining … (Ctrl+C to stop)", end='', flush=True)
                await asyncio.sleep(15)
            print()
            self.print_success("✓ Cooldown finished - starting next round")

        try:
            while automation_state['running']:
                elapsed_hours = (datetime.now() - automation_state['start_time']).total_seconds() / 3600
                if elapsed_hours >= max_runtime_hours:
                    self.print_warning(f"Max runtime reached ({max_runtime_hours:.1f}h). Stopping safely.")
                    automation_state['running'] = False
                    break
                if automation_state['round_number'] >= max_rounds:
                    self.print_warning(f"Max rounds reached ({max_rounds}). Stopping safely.")
                    automation_state['running'] = False
                    break
                if automation_state['applications_submitted'] >= max_submissions:
                    self.print_warning(f"Max submissions reached ({max_submissions}). Stopping safely.")
                    automation_state['running'] = False
                    break
                automation_state['round_number'] += 1

                goal_met = await _fill_queue_to_goal()

                if not job_queue:
                    self.print_warning("\n📭 No jobs found. Options:")
                    self.print_info("  1. Wait 5 minutes and retry automatically")
                    self.print_info("  2. Change search parameters (restart)")
                    self.print_info("  3. Stop")
                    self.print_info("  4. Retry now with broader search terms")
                    choice = self.get_input("Choice [1/2/3/4, default 1]: ").strip()
                    if choice == '2':
                        self.print_info("Returning to menu - re-run to change parameters.")
                        break
                    if choice == '3':
                        break
                    if choice == '4':
                        if _apply_broader_retry_strategy():
                            self.print_info("Retrying now with broader search terms...")
                            self.print_info(
                                f"  Broadened queries: {automation_state['query_variations']}"
                            )
                            if automation_state.get('broaden_removed_terms'):
                                self.print_info(
                                    f"  Niche terms earmarked for next retry: {automation_state['broaden_removed_terms']}"
                                )
                            self.print_info(
                                f"  Min relevance lowered to {automation_state['discovery_min_relevance']}"
                            )
                            continue
                        self.print_warning("Broader retry already used in this session; choose another option.")
                        continue
                    self.print_info("Retrying in 5 minutes...")
                    for _ in range(30):
                        if not automation_state['running']:
                            break
                        await asyncio.sleep(10)
                    continue

                if not goal_met:
                    self.print_warning(
                        f"⚠  Proceeding with {len(job_queue)} job(s) (goal was {session_goal})."
                    )

                round_submitted = await _run_round()

                self.print_success(
                    f"\n🏁 Round {automation_state['round_number']} done - "
                    f"{round_submitted} submitted | "
                    f"total: {automation_state['applications_submitted']}"
                )

                if not automation_state['running']:
                    break

                if round_submitted > 0:
                    await _cooldown()
                else:
                    retry_minutes = max(1, int(os.getenv("LAUNCHWAY_CONTINUOUS_ZERO_SUBMIT_RETRY_MINUTES", "5")))
                    wake_at = datetime.now() + timedelta(minutes=retry_minutes)
                    self.print_warning(
                        f"\n⚠ No submissions in round {automation_state['round_number']} - "
                        f"retrying discovery at {wake_at.strftime('%I:%M %p')} ({retry_minutes} min)"
                    )
                    for _ in range(retry_minutes * 4):
                        if not automation_state['running']:
                            break
                        await asyncio.sleep(15)

            self.print_header("🎉 AUTOMATION COMPLETED")

        except KeyboardInterrupt:
            self.print_warning("\n\n⚠️  Automation stopped by user")
        except Exception as e:
            self.print_error(f"Automation engine error: {str(e)}")
            logger.error(f"Automation error: {e}", exc_info=True)
        finally:
            self._save_progress_report(report_filename, automation_state, job_queue, final=True)
            self._should_open_incomplete  = False
            self._incomplete_report_file  = None
            self._display_automation_summary(automation_state, report_filename)
            if self._should_open_incomplete and self._incomplete_report_file:
                await self._open_incomplete_applications(self._incomplete_report_file)
            self.pause()
