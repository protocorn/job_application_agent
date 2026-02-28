"""Job search mixin."""

import asyncio
import json
import logging
import sys
from datetime import datetime
from launchway.cli.utils import Colors

logger = logging.getLogger(__name__)

try:
    from Agents.multi_source_job_discovery_agent import MultiSourceJobDiscoveryAgent
    _JOB_DISCOVERY_AVAILABLE = True
except Exception as e:
    logger.warning(f"Job discovery not available: {e}")
    _JOB_DISCOVERY_AVAILABLE = False


class JobSearchMixin:

    def _ensure_resume_keywords(self) -> bool:
        """
        Check whether the current profile has Gemini-extracted resume keywords.
        If not, offer to extract them now (improves job relevance matching).
        Returns True to proceed, False to abort (user opted out or extraction failed).
        """
        rk = (self.current_profile or {}).get("resume_keywords") or {}
        if rk and (rk.get("skills") or rk.get("domains")):
            return True  # already extracted — nothing to do

        print(
            f"\n{Colors.WARNING}Tip:{Colors.ENDC} Your profile doesn't have Gemini-extracted "
            "resume keywords yet.\n"
            "  Extracting them once improves job relevance matching for any profession."
        )
        ans = self.get_input("  Extract keywords from your resume now? (y/n, default: y): ").strip().lower()
        if ans == 'n':
            return True  # user skipped — still proceed with search

        # Check resume URL
        resume_url = (self.current_profile or {}).get("resume_url", "")
        if not resume_url:
            self.print_warning(
                "No resume URL saved in your profile. "
                "Add it under Profile > Resume URL first."
            )
            return True  # proceed without keywords

        sys.stdout.write(f"\n  {Colors.OKCYAN}Extracting keywords from your resume...{Colors.ENDC} ")
        sys.stdout.flush()
        try:
            keywords = self.api.extract_resume_keywords()
            self.current_profile["resume_keywords"] = keywords
            total = sum(
                len(keywords.get(k, []))
                for k in ("skills", "job_titles", "industries", "domains")
            )
            sys.stdout.write(f"{Colors.OKGREEN}done.{Colors.ENDC}\n")
            self.print_success(
                f"Extracted {total} keywords "
                f"({len(keywords.get('skills', []))} skills, "
                f"{len(keywords.get('domains', []))} domains)"
            )
        except Exception as e:
            sys.stdout.write(f"{Colors.WARNING}skipped.{Colors.ENDC}\n")
            self.print_warning(f"Could not extract keywords: {e}")
            logger.warning(f"Keyword extraction failed: {e}")

        return True

    def job_search_menu(self):
        self.clear_screen()
        self.print_header("JOB SEARCH")

        if not _JOB_DISCOVERY_AVAILABLE:
            self.print_error("Job search feature is not available.")
            self.print_info("Missing dependencies or configuration.")
            self.pause()
            return

        self._ensure_resume_keywords()

        self.print_info("Search for jobs across multiple sources (Indeed, LinkedIn, etc.)")
        print(f"\n{Colors.BOLD}Search Parameters:{Colors.ENDC}")

        keywords = self.get_input("Job Keywords (e.g., 'Software Engineer'): ").strip()
        if not keywords:
            self.print_error("Keywords are required.")
            self.pause()
            return

        location       = self.get_input("Location (optional): ").strip()
        remote         = self.get_input("Remote only? (y/n): ").strip().lower() == 'y'
        easy_apply     = self.get_input("Easy Apply only? (y/n, default: n): ").strip().lower() == 'y'
        hours_old_str  = self.get_input("Only jobs posted in last N hours? (optional): ").strip()
        hours_old      = None
        if hours_old_str:
            try:
                hours_old = max(1, int(hours_old_str))
            except ValueError:
                self.print_warning("Invalid hours value. Using no recency filter.")

        max_results_str = self.get_input("Max results (default 20): ").strip()
        try:
            max_results = int(max_results_str) if max_results_str else 20
        except ValueError:
            max_results = 20

        try:
            self.print_info("\nSearching for jobs... This may take a moment")
            agent = MultiSourceJobDiscoveryAgent(
                user_id=str(self.current_user['id']),
                profile_data=self.current_profile,
            )
            result = agent.search_all_sources(
                min_relevance_score=30,
                manual_keywords=keywords,
                manual_location=location or None,
                manual_remote=remote,
                manual_search_overrides={
                    "easy_apply": easy_apply,
                    "hours_old": hours_old,
                },
            )

            results = result.get('data', [])
            if not results:
                self.print_warning("No jobs found matching your criteria.")
                self.print_info(f"Sources searched: {result.get('sources', {})}")
                self.pause()
                return

            self.clear_screen()
            self.print_header(f"SEARCH RESULTS ({len(results)} jobs found)")
            self.print_info(f"Average relevance score: {result.get('average_score', 0):.1f}%")
            self.print_info(f"Sources: {result.get('sources', {})}\n")

            for i, job in enumerate(results[:max_results], 1):
                print(f"\n{Colors.BOLD}{i}. {job.get('title', 'Unknown Title')}{Colors.ENDC}")
                print(f"   Company:  {job.get('company', 'Unknown')}")
                print(f"   Location: {job.get('location', 'Not specified')}")
                if job.get('salary') and job.get('salary') != 'null':
                    print(f"   Salary: {job.get('salary')}")

                apply_links = job.get('apply_links', {})
                if apply_links:
                    primary = apply_links.get('primary', '')
                    if primary:
                        print(f"   {Colors.OKGREEN}Apply → {primary}{Colors.ENDC}")
                    indeed   = apply_links.get('indeed', '')
                    linkedin = apply_links.get('linkedin', '')
                    if indeed or linkedin:
                        parts = []
                        if indeed:   parts.append("Indeed")
                        if linkedin: parts.append("LinkedIn")
                        print(f"   {Colors.OKCYAN}Also on: {' | '.join(parts)}{Colors.ENDC}")
                else:
                    job_url = job.get('job_url') or job.get('url')
                    if job_url:
                        print(f"   {Colors.OKCYAN}Apply: {job_url}{Colors.ENDC}")

                print(f"   Source: {job.get('source', 'Unknown')}")
                if job.get('relevance_score'):
                    print(f"   Relevance: {job.get('relevance_score', 0):.1f}%")

            print("\n" + "=" * 60)
            action = self.get_input("\nOptions: [A]pply to job, [S]ave results, [Q]uit: ").strip().lower()

            if action == 'a':
                job_num = self.get_input("Enter job number to apply: ").strip()
                if job_num.isdigit() and 1 <= int(job_num) <= len(results):
                    selected    = results[int(job_num) - 1]
                    apply_links = selected.get('apply_links', {})
                    job_url = (
                        apply_links.get('primary') or apply_links.get('indeed') or apply_links.get('linkedin')
                        if apply_links else None
                    ) or selected.get('job_url') or selected.get('url')

                    if job_url:
                        self.print_info(f"Opening auto-apply for: {selected.get('title')}")
                        self.pause()
                        asyncio.run(self.auto_apply_single(job_url))
                    else:
                        self.print_error("No URL available for this job.")

            elif action == 's':
                filename = f"job_search_results_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
                with open(filename, 'w') as f:
                    json.dump(results, f, indent=2)
                self.print_success(f"Results saved to {filename}")

            self.pause()

        except Exception as e:
            self.print_error(f"Job search failed: {str(e)}")
            logger.error(f"Job search error: {e}", exc_info=True)
            self.pause()
