"""Continuous auto-apply mixin — 100% automation mode."""

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

from launchway.cli.utils import Colors

logger = logging.getLogger(__name__)

try:
    from Agents.job_application_agent import RefactoredJobAgent, _get_or_create_playwright
    _JOB_APPLICATION_AVAILABLE = True
except Exception as e:
    logger.error(f"Job application agent not available: {e}", exc_info=True)
    _JOB_APPLICATION_AVAILABLE = False

try:
    from Agents.multi_source_job_discovery_agent import MultiSourceJobDiscoveryAgent
    _JOB_DISCOVERY_AVAILABLE = True
except Exception as e:
    logger.warning(f"Job discovery not available: {e}")
    _JOB_DISCOVERY_AVAILABLE = False

try:
    from Agents.gemini_query_optimizer import GeminiQueryOptimizer
    _QUERY_OPTIMIZER_AVAILABLE = True
except Exception as e:
    logger.warning(f"Query optimizer not available: {e}")
    _QUERY_OPTIMIZER_AVAILABLE = False


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
            if self.get_input("\nStop automation? (y/n, default: n): ").strip().lower() == 'y':
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
                    'start_time':           automation_state['start_time'].isoformat(),
                    'duration_minutes':     round((datetime.now() - automation_state['start_time']).total_seconds() / 60, 2),
                    'status':               'completed' if final else 'in_progress',
                    'original_keywords':    automation_state.get('original_keywords', ''),
                    'optimized_keywords':   automation_state.get('optimized_keywords', ''),
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
            self.print_info("Would you like to continue these applications manually?")
            print("=" * 60)
            choice = self.get_input("\nOpen incomplete applications? (y/n): ").strip().lower()
            self._should_open_incomplete    = (choice == 'y')
            self._incomplete_report_file    = report_filename if choice == 'y' else None

    async def _apply_to_single_job_automated(
        self,
        job_url:         str,
        job_title:       str,
        company:         str,
        tailor_resume:   bool,
        headless:        bool,
        automation_state: Dict[str, Any],
        description:     str = '',
    ) -> Dict[str, Any]:
        start_time = time.time()

        job_result = {
            'job_url':       job_url,
            'job_title':     job_title,
            'company':       company,
            'timestamp':     datetime.now().isoformat(),
            'success':       False,
            'submitted':     False,
            'fields_filled': 0,
            'field_details': [],
            'error':         None,
            'rate_limit_error': False,
            'duration_seconds': 0,
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
                self.record_application(job_url)
                self.print_success(f"✓ Application submitted! ({job_result['fields_filled']} fields filled)")
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
            print("\n" + "=" * 60)
            for i, app in enumerate(incomplete_apps, 1):
                print(f"\n{i}. {app.get('job_title','Unknown')} at {app.get('company','Unknown')}")
                print(f"   URL:          {str(app.get('job_url','N/A'))[:70]}...")
                print(f"   Fields Filled: {app.get('fields_filled', 0)}")
                if app.get('error'):
                    print(f"   Error:        {app.get('error')}")
            print("=" * 60)

            if self.get_input(f"\nOpen all {len(incomplete_apps)} application(s) in browser? (y/n): ").strip().lower() != 'y':
                self.print_info("Cancelled.")
                return

            self.print_info("\n🚀 Opening persistent browser...")
            manager      = PersistentBrowserManager()
            profile_path = manager.get_profile_path(str(self.current_user['id']))

            self.print_info(f"  Profile path:   {profile_path}")
            self.print_info(f"  Profile exists: {profile_path.exists()}")

            if not profile_path.exists():
                self.print_warning("⚠️ Profile directory does not exist — run 'Browser Profile Setup' first.")

            context = await manager.launch_persistent_browser(
                user_id=str(self.current_user['id']),
                headless=False,
            )

            self.print_success("✓ Browser opened with persistent profile")
            self.print_warning("\n⚠️  NOTE: Fields will NOT be pre-filled (technical limitation).")

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

            print("\n" + "=" * 60)
            self.print_success(f"✓ Opened {len(pages)} application(s) in browser tabs")
            print("=" * 60)
            self.print_info("\nInstructions:")
            self.print_info("  1. Complete and submit each application")
            self.print_info("  2. Close the browser when done")
            self.print_info("  💡 Your login sessions are preserved in this browser!")

            print("\nBrowser is open. Press Enter here when you're done...")
            input()

            try:
                await context.close()
                if hasattr(context, '_playwright'):
                    await context._playwright.stop()
                PersistentBrowserManager.close_browser_for_user(str(self.current_user['id']))
                self.print_success("✓ Browser closed successfully")
            except Exception as e:
                logger.warning(f"Error closing browser: {e}")

        except Exception as e:
            self.print_error(f"Failed to open incomplete applications: {str(e)}")
            logger.error(f"Open incomplete applications error: {e}", exc_info=True)

    async def continuous_auto_apply_menu(self):
        self.clear_screen()
        self.print_header("🚀 100% AUTO JOB APPLY - CONTINUOUS MODE")

        if not _JOB_APPLICATION_AVAILABLE or not _JOB_DISCOVERY_AVAILABLE:
            self.print_error("Required features are not available.")
            self.pause()
            return

        if not self._ensure_resume_ready_for_auto_apply():
            self.pause()
            return

        self.print_warning("⚠️  WARNING: This mode runs continuously and AUTOMATICALLY SUBMITS applications!")
        self.print_info("\nThis mode will:")
        self.print_info("  • Continuously search for relevant jobs")
        self.print_info("  • Automatically tailor your resume for each job")
        self.print_info("  • Fill and SUBMIT applications automatically")
        self.print_info("  • Handle rate limits gracefully (pause & retry)")
        self.print_info("  • Rotate proxies to avoid IP bans")
        self.print_info("  • Generate detailed progress reports")
        self.print_info("\nYou can stop anytime by pressing Ctrl+C\n")

        keywords = self.get_input("Job Keywords (e.g., 'Software Engineer'): ").strip()
        if not keywords:
            self.print_error("Keywords are required.")
            self.pause()
            return

        location      = self.get_input("Location (optional, leave blank for any): ").strip()
        remote        = self.get_input("Remote only? (y/n, default: n): ").strip().lower() == 'y'
        easy_apply    = self.get_input("Easy Apply only? (y/n, default: n): ").strip().lower() == 'y'
        hours_old_str = self.get_input("Only jobs posted in last N hours? (optional): ").strip()
        hours_old     = None
        if hours_old_str:
            try:
                hours_old = max(1, int(hours_old_str))
            except ValueError:
                self.print_warning("Invalid hours value. Using no recency filter.")

        tailor_all = self.get_input("Tailor resume for each job? (y/n, default: y): ").strip().lower()
        tailor_all = tailor_all != 'n'

        if tailor_all:
            mimikree_email, mimikree_password = self.ensure_mimikree_connected_for_tailoring()
            if not mimikree_email or not mimikree_password:
                self.pause()
                return

        headless = self.get_input("Run in headless mode? (y/n, default: n): ").strip().lower() == 'y'

        use_proxies  = self.get_input("Use proxies to avoid IP bans? (y/n, default: n): ").strip().lower() == 'y'
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

        max_apps_str = self.get_input("Max applications per session (default: 50): ").strip()
        try:
            max_apps = int(max_apps_str) if max_apps_str else 50
        except ValueError:
            max_apps = 50

        print(f"\n{Colors.BOLD}Summary:{Colors.ENDC}")
        print(f"  Keywords:         {keywords}")
        print(f"  Location:         {location or 'Any'}")
        print(f"  Remote:           {'Yes' if remote else 'No'}")
        print(f"  Easy Apply:       {'Yes' if easy_apply else 'No'}")
        print(f"  Hours Old Filter: {hours_old or 'Any'}")
        print(f"  Tailor Resume:    {'Yes' if tailor_all else 'No'}")
        print(f"  Headless:         {'Yes' if headless else 'No'}")
        print(f"  Proxies:          {proxy_manager.get_stats()['active_proxies'] if proxy_manager else 'None (direct)'}")
        print(f"  Max Applications: {max_apps}")
        print(f"  Job Discovery:    Every 1 hour")

        confirm = self.get_input(
            f"\n{Colors.WARNING}Start continuous automation? (type 'START' to confirm): {Colors.ENDC}"
        ).strip()
        if confirm.upper() != 'START':
            self.print_info("Cancelled.")
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
            max_applications=max_apps,
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
        max_applications: int,
        proxy_manager=None,
    ):
        self.print_header("🚀 STARTING AUTOMATION ENGINE")

        if proxy_manager:
            stats = proxy_manager.get_stats()
            self.print_success(f"✓ Proxy rotation enabled: {stats['active_proxies']} proxies ready")

        optimized_keywords   = keywords
        query_variations     = [keywords]
        profile_dict         = None
        enriched_search_params = {
            "keywords":   keywords,
            "location":   location,
            "remote":     remote,
            "easy_apply": easy_apply,
            "hours_old":  hours_old,
        }

        if _QUERY_OPTIMIZER_AVAILABLE:
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
                    seen = set()
                    normalized = []
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
                        self.print_info(f"✓ Generated {len(query_variations)-1} alternative queries")

                # Enrich search parameters
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

        automation_state = {
            'start_time':              datetime.now(),
            'applications_submitted':  0,
            'applications_failed':     0,
            'jobs_discovered':         0,
            'jobs_processed':          0,
            'rate_limit_hits':         0,
            'running':                 True,
            'progress_log':            [],
            'original_keywords':       keywords,
            'optimized_keywords':      optimized_keywords,
            'query_variations':        query_variations,
            'enriched_search_params':  enriched_search_params,
            'current_query_index':     0,
            'last_job_discovery':      None,
            'job_discovery_interval_seconds': 3600,
        }

        report_filename = f"automation_progress_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        job_queue       = deque()
        processed_urls  = set()

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
        self.print_info(f"✓ Progress report: {report_filename}")
        self.print_info("✓ Press Ctrl+C to stop gracefully\n")

        try:
            while automation_state['running'] and automation_state['applications_submitted'] < max_applications:

                current_time  = datetime.now()
                should_search = automation_state['last_job_discovery'] is None

                if not should_search and automation_state['last_job_discovery']:
                    elapsed = (current_time - automation_state['last_job_discovery']).total_seconds()
                    if elapsed >= automation_state['job_discovery_interval_seconds']:
                        should_search = True

                if should_search:
                    self.print_info(f"\n{'='*60}")
                    self.print_info("🔍 DISCOVERING NEW JOBS...")
                    self.print_info(f"{'='*60}")

                    all_queries   = automation_state.get('query_variations', [optimized_keywords])
                    current_idx   = automation_state.get('current_query_index', 0)
                    if current_idx >= len(all_queries):
                        current_idx = 0
                    queries_to_try = all_queries[current_idx:]

                    try:
                        agent = MultiSourceJobDiscoveryAgent(
                            user_id=str(self.current_user['id']),
                            proxy_manager=proxy_manager,
                            profile_data=self.current_profile,
                        )
                        new_count              = 0
                        skipped_applied        = 0
                        skipped_dup            = 0
                        found_with_query_index = None

                        for idx, q in enumerate(queries_to_try, start=current_idx):
                            self.print_info(f"Using query #{idx}: '{q}'")
                            search_overrides = dict(enriched_search_params)
                            search_overrides["keywords"] = q
                            if not search_overrides.get("location") and location:
                                search_overrides["location"] = location

                            result   = agent.search_all_sources(
                                min_relevance_score=30,
                                manual_keywords=q,
                                manual_location=search_overrides.get("location") or None,
                                manual_remote=remote,
                                manual_search_overrides=search_overrides,
                            )
                            new_jobs = result.get('data', [])

                            if not new_jobs:
                                if idx < len(all_queries) - 1:
                                    self.print_warning("No jobs, trying next query variation...")
                                    continue
                                self.print_warning("No jobs found with any query variation.")
                                break

                            found_with_query_index = idx
                            for job in new_jobs:
                                job_url = self._extract_job_url(job)
                                if not job_url:
                                    continue
                                if job_url in previously_applied_urls:
                                    skipped_applied += 1
                                    continue
                                if job_url in processed_urls:
                                    skipped_dup += 1
                                    continue
                                job_queue.append({
                                    'url':             job_url,
                                    'title':           job.get('title', 'Unknown'),
                                    'company':         job.get('company', 'Unknown'),
                                    'description':     job.get('description', ''),
                                    'relevance_score': job.get('relevance_score', 0),
                                    'search_query':    q,
                                })
                                processed_urls.add(job_url)
                                new_count += 1

                            if new_count > 0:
                                break

                        automation_state['current_query_index'] = found_with_query_index or 0
                        automation_state['jobs_discovered']    += new_count
                        automation_state['last_job_discovery']  = datetime.now()

                        self.print_success(f"✓ Found {new_count} new jobs (Queue: {len(job_queue)})")
                        if skipped_applied: self.print_info(f"  ↳ Skipped {skipped_applied} already-applied jobs")
                        if skipped_dup:     self.print_info(f"  ↳ Skipped {skipped_dup} duplicate jobs")

                        next_search = automation_state['last_job_discovery'] + timedelta(
                            seconds=automation_state['job_discovery_interval_seconds']
                        )
                        self.print_info(f"📅 Next job search at: {next_search.strftime('%I:%M %p')}")

                    except Exception as e:
                        self.print_error(f"Job discovery error: {str(e)}")
                        logger.error(f"Job discovery error: {e}", exc_info=True)
                        if self._is_rate_limit_error(e):
                            automation_state['rate_limit_hits'] += 1
                            await self._handle_rate_limit(automation_state)
                        continue

                    await asyncio.sleep(2)

                if not job_queue:
                    if automation_state['last_job_discovery']:
                        next_search      = automation_state['last_job_discovery'] + timedelta(
                            seconds=automation_state['job_discovery_interval_seconds']
                        )
                        time_until_next  = (next_search - datetime.now()).total_seconds()
                        if time_until_next > 0:
                            minutes_until = int(time_until_next / 60)
                            self.print_info(f"📭 Queue empty. Next search in {minutes_until} min.")
                            await asyncio.sleep(min(60, time_until_next))
                        else:
                            await asyncio.sleep(2)
                    else:
                        await asyncio.sleep(5)
                    continue

                job = job_queue.popleft()
                automation_state['jobs_processed'] += 1

                self.print_header(f"JOB {automation_state['jobs_processed']} — {job['company']}")
                self.print_info(f"Title:     {job['title']}")
                self.print_info(f"URL:       {job['url'][:60]}...")
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

                self._save_progress_report(report_filename, automation_state, job_queue)

                if job_result.get('rate_limit_error'):
                    automation_state['rate_limit_hits'] += 1
                    await self._handle_rate_limit(automation_state)

                await asyncio.sleep(3)

            self.print_header("🎉 AUTOMATION COMPLETED")

        except KeyboardInterrupt:
            self.print_warning("\n\n⚠️  Automation stopped by user")
        except Exception as e:
            self.print_error(f"Automation engine error: {str(e)}")
            logger.error(f"Automation error: {e}", exc_info=True)
        finally:
            self._save_progress_report(report_filename, automation_state, job_queue, final=True)
            self._should_open_incomplete   = False
            self._incomplete_report_file   = None
            self._display_automation_summary(automation_state, report_filename)
            if self._should_open_incomplete and self._incomplete_report_file:
                await self._open_incomplete_applications(self._incomplete_report_file)
            self.pause()
