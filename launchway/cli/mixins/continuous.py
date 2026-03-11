"""Continuous auto-apply mixin — fully autonomous automation mode."""

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

        removal_patterns = [
            r"\bin any location\b",
            r"\bin remote locations?\b",
            r"\bin all locations?\b",
            r"\bjobs for\b",
        ]
        for pattern in removal_patterns:
            cleaned = re.sub(pattern, " ", cleaned, flags=re.IGNORECASE)

        cleaned = re.sub(r"\b(entry level|mid level|senior)\b", " ", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\s+", " ", cleaned).strip(" ,.-")

        tokens = cleaned.split()
        if len(tokens) > 8:
            cleaned = " ".join(tokens[:8])

        return cleaned or (fallback_keywords or "")

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
        }

        try:
            self.print_info("🤖 Starting automated application...")
            self.print_info(f"   Tailor Resume: {'✓ Enabled' if tailor_resume else '✗ Disabled'}")

            playwright = await _get_or_create_playwright()
            agent = RefactoredJobAgent(
                playwright=playwright,
                headless=headless,
                keep_open=False,
                debug=False,
                user_id=str(self.current_user['id']),
                tailor_resume=tailor_resume,
                mimikree_email=self._session_mimikree_email if tailor_resume else None,
                mimikree_password=self._session_mimikree_password if tailor_resume else None,
                job_url=job_url,
                use_persistent_profile=True,
                pre_fetched_description=description or None,
                profile_data=self.current_profile,
            )
            await agent.process_link(job_url)

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
                job_result['error'] = 'Human intervention required'

            if job_result['fields_filled'] > 0 and job_result['submitted']:
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
                if not job_result.get('error'):
                    job_result['error'] = f"Incomplete ({job_result['fields_filled']} fields filled, not submitted)"
                self.print_warning(f"⚠ Application incomplete ({job_result['fields_filled']} fields filled)")

        except Exception as e:
            error_str = str(e)
            job_result['error'] = error_str
            automation_state['applications_failed'] += 1
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

        if not self._ensure_resume_ready_for_auto_apply():
            self.pause()
            return

        self.print_info("This mode runs continuously and can submit applications automatically.")
        self.print_info("Warning: the agent can make mistakes and may submit an application.")
        self.print_info("\nThis mode will:")
        self.print_info("  • Continuously search for relevant jobs")
        self.print_info("  • Automatically tailor your resume for each job")
        self.print_info("  • Fill and SUBMIT applications automatically")
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

        if tailor_all:
            self.ensure_mimikree_connected_for_tailoring()

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

        goal_str = self.get_input("Round goal — jobs to apply per round (max 10, default: 5): ").strip()
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
        enriched_search_params = {
            "keywords":   keywords,
            "location":   location,
            "remote":     remote,
            "easy_apply": easy_apply,
            "hours_old":  hours_old,
        }

        try:
            optimizer = GeminiQueryOptimizer()
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
            'round_number':           0,
        }

        report_filename         = f"automation_progress_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        job_queue               = deque()
        processed_urls          = set()
        overflow_queue          = deque()

        self.print_info("📋 Loading application history for deduplication...")
        previously_applied_urls = self.get_applied_job_urls()
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

        def _enqueue_jobs(new_jobs: list) -> tuple:
            added, skipped_applied, skipped_dup = 0, 0, 0
            for job in new_jobs:
                url = self._extract_job_url(job)
                if not url:
                    continue
                if url in previously_applied_urls:
                    skipped_applied += 1
                    continue
                if url in processed_urls:
                    skipped_dup += 1
                    continue
                entry = {
                    'url':             url,
                    'title':           job.get('title', 'Unknown'),
                    'company':         job.get('company', 'Unknown'),
                    'description':     job.get('description', ''),
                    'relevance_score': job.get('relevance_score', 0),
                }
                processed_urls.add(url)
                needed = session_goal - len(job_queue)
                if needed > 0:
                    job_queue.append(entry)
                else:
                    overflow_queue.append(entry)
                added += 1
            return added, skipped_applied, skipped_dup

        async def _fill_queue_to_goal() -> bool:
            all_queries = automation_state['query_variations']
            self.print_info(f"\n{'='*60}")
            self.print_info(f"🔍 ROUND {automation_state['round_number']} — DISCOVERING JOBS (goal: {session_goal})")
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
                            min_relevance_score=30,
                            manual_keywords=q,
                            manual_location=search_overrides.get("location") or None,
                            manual_remote=remote,
                            manual_search_overrides=search_overrides,
                        )
                        new_jobs = result.get('data', [])
                        added, s_app, s_dup = _enqueue_jobs(new_jobs)
                        total_added += added
                        automation_state['jobs_discovered'] += added
                        self.print_info(
                            f"    → {added} new  |  queue: {len(job_queue)}/{session_goal}"
                            + (f"  ({s_app} prev-applied, {s_dup} dup skipped)" if s_app or s_dup else "")
                        )
                    except Exception as qe:
                        self.print_warning(f"    ⚠ Query error: {str(qe)[:80]}")
                        if self._is_rate_limit_error(qe):
                            automation_state['rate_limit_hits'] += 1
                            await self._handle_rate_limit(automation_state)

            except Exception as e:
                self.print_error(f"Discovery error: {str(e)[:100]}")
                logger.error(f"Job discovery error: {e}", exc_info=True)

            if len(job_queue) >= session_goal:
                self.print_success(f"✓ Goal reached — {len(job_queue)} jobs queued"
                                   + (f" (+{len(overflow_queue)} overflow for next round)" if overflow_queue else ""))
                return True

            self.print_warning(f"  Only {len(job_queue)}/{session_goal} jobs found after all queries.")
            return len(job_queue) > 0

        async def _run_round() -> int:
            round_submitted = 0
            round_goal      = min(session_goal, len(job_queue))
            while job_queue and round_submitted < session_goal and automation_state['running']:
                # Check credits before each job — stop gracefully when exhausted
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
                automation_state['jobs_processed'] += 1

                self.print_header(
                    f"ROUND {automation_state['round_number']}  •  "
                    f"JOB {round_submitted + 1}/{round_goal}  —  {job['company']}"
                )
                self.print_info(f"Title:     {job['title']}")
                self.print_info(f"URL:       {job['url'][:70]}...")
                self.print_info(f"Relevance: {job['relevance_score']:.1f}%")

                job_result = await self._apply_to_single_job_automated(
                    job_url=job['url'],
                    job_title=job['title'],
                    company=job['company'],
                    description=job.get('description', ''),
                    tailor_resume=tailor_resume,
                    headless=headless,
                    automation_state=automation_state,
                )
                automation_state['progress_log'].append(job_result)

                if job_result.get('success') or job_result.get('submitted'):
                    previously_applied_urls.add(job['url'])
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
                f"\n😴 Round complete — cooldown until {wake_at.strftime('%I:%M %p')} "
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
            self.print_success("✓ Cooldown finished — starting next round")

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
                    choice = self.get_input("Choice [1/2/3, default 1]: ").strip()
                    if choice == '2':
                        self.print_info("Returning to menu — re-run to change parameters.")
                        break
                    if choice == '3':
                        break
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
                    f"\n🏁 Round {automation_state['round_number']} done — "
                    f"{round_submitted} submitted | "
                    f"total: {automation_state['applications_submitted']}"
                )

                if not automation_state['running']:
                    break

                await _cooldown()

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
