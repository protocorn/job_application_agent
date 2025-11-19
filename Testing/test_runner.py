"""
Job Application Agent Test Runner
Wraps the agent with metrics collection for comprehensive testing
"""

import sys
import time
from pathlib import Path
from datetime import datetime

# Add parent directory to path to import the agent
sys.path.insert(0, str(Path(__file__).parent.parent))

from Testing.test_metrics_tracker import TestMetricsTracker


class JobApplicationAgentTester:
    """Wrapper for testing the job application agent with metrics collection"""

    def __init__(self):
        self.tracker = TestMetricsTracker()
        self.current_metrics = None
        self.start_time = None

    def start_test(self, job_url: str, job_board_type: str = ""):
        """
        Start a new test session

        Args:
            job_url: The job posting URL to test
            job_board_type: Type of job board (LinkedIn, Indeed, etc.)
        """
        print("\n" + "="*60)
        print(f"STARTING TEST: {job_url}")
        print("="*60 + "\n")

        self.start_time = time.time()
        self.current_metrics = self.tracker.create_test_template()
        self.current_metrics["Job URL"] = job_url
        self.current_metrics["Job Board/Site Type"] = job_board_type or self._detect_job_board_type(job_url)
        self.current_metrics["Date/Time of Test"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    def _detect_job_board_type(self, url: str) -> str:
        """Auto-detect job board type from URL"""
        url_lower = url.lower()

        if "linkedin.com" in url_lower:
            return "LinkedIn"
        elif "indeed.com" in url_lower:
            return "Indeed"
        elif "greenhouse.io" in url_lower:
            return "Greenhouse"
        elif "myworkdayjobs.com" in url_lower or "workday.com" in url_lower:
            return "Workday"
        elif "lever.co" in url_lower:
            return "Lever"
        elif "ashbyhq.com" in url_lower:
            return "Ashby"
        elif "smartrecruiters.com" in url_lower:
            return "SmartRecruiters"
        elif "icims.com" in url_lower:
            return "iCIMS"
        else:
            return "Company Website - Custom"

    def update_metric(self, key: str, value):
        """Update a specific metric"""
        if self.current_metrics is None:
            raise RuntimeError("No test session started. Call start_test() first.")

        if key in self.current_metrics:
            self.current_metrics[key] = value
            print(f"  ✓ Updated: {key} = {value}")
        else:
            print(f"  ⚠ Warning: Unknown metric key '{key}'")

    def update_metrics(self, metrics_dict: dict):
        """Update multiple metrics at once"""
        for key, value in metrics_dict.items():
            self.update_metric(key, value)

    def end_test(self, manual_review: bool = True):
        """
        End the test session and save metrics

        Args:
            manual_review: If True, prompt user for manual quality assessment
        """
        if self.current_metrics is None:
            print("No test session to end.")
            return

        # Calculate total time
        if self.start_time:
            total_time = time.time() - self.start_time
            self.current_metrics["Total Time Taken (seconds)"] = round(total_time, 2)

        # Determine form complexity based on field count
        total_fields = self.current_metrics.get("Total Form Fields Detected", 0)
        try:
            total_fields = int(total_fields)
            if total_fields < 10:
                form_type = "Simple (<10 fields)"
            elif total_fields <= 20:
                form_type = "Medium (10-20 fields)"
            else:
                form_type = "Complex (>20 fields)"
            self.current_metrics["Form Type"] = form_type
        except (ValueError, TypeError):
            pass

        # Manual review prompts
        if manual_review:
            print("\n" + "-"*60)
            print("MANUAL REVIEW")
            print("-"*60)

            # Company and job title if not set
            if not self.current_metrics.get("Company Name"):
                company = input("Company Name: ").strip()
                if company:
                    self.current_metrics["Company Name"] = company

            if not self.current_metrics.get("Job Title"):
                job_title = input("Job Title: ").strip()
                if job_title:
                    self.current_metrics["Job Title"] = job_title

            # Accuracy score
            print("\nAccuracy Score (1-10): How accurately were fields filled?")
            print("  1-3: Many errors, incorrect data")
            print("  4-6: Some errors, mostly correct")
            print("  7-9: Very accurate, minor issues")
            print("  10: Perfect, no errors")
            try:
                accuracy = input("Accuracy Score [1-10]: ").strip()
                if accuracy:
                    self.current_metrics["Accuracy Score (1-10)"] = int(accuracy)
            except ValueError:
                pass

            # Frustration check
            frustrating = input("\nWould this be frustrating for user? [Yes/No]: ").strip()
            if frustrating:
                self.current_metrics["Would This Be Frustrating for User?"] = frustrating

            # Additional notes
            notes = input("\nAny unique challenges or notes? (Press Enter to skip): ").strip()
            if notes:
                self.current_metrics["Unique Challenges"] = notes

        # Save to tracker
        print("\n" + "="*60)
        print("SAVING TEST RESULTS...")
        print("="*60)
        self.tracker.record_test_result(self.current_metrics)

        # Print summary
        self._print_test_summary()

        # Reset for next test
        self.current_metrics = None
        self.start_time = None

    def _print_test_summary(self):
        """Print summary of current test"""
        print("\n" + "-"*60)
        print("TEST COMPLETED")
        print("-"*60)
        print(f"Job: {self.current_metrics.get('Job Title', 'N/A')} at {self.current_metrics.get('Company Name', 'N/A')}")
        print(f"URL: {self.current_metrics.get('Job URL', 'N/A')}")
        print(f"Board Type: {self.current_metrics.get('Job Board/Site Type', 'N/A')}")
        print(f"Status: {self.current_metrics.get('Final Status', 'Unknown')}")
        print(f"Total Time: {self.current_metrics.get('Total Time Taken (seconds)', 0):.1f}s")
        print(f"Fields Filled: {self.current_metrics.get('Fields Filled/Total Available', '0/0')}")
        print(f"Form Type: {self.current_metrics.get('Form Type', 'Unknown')}")
        print("-"*60 + "\n")

    def abort_test(self, reason: str = ""):
        """Abort current test without saving"""
        print(f"\n⚠ Test aborted. Reason: {reason}")
        self.current_metrics = None
        self.start_time = None

    def show_summary(self):
        """Show summary of all tests"""
        self.tracker.print_summary()


# Helper function for quick testing
def quick_test(job_url: str, **metrics):
    """
    Quick test function for simple testing

    Args:
        job_url: URL of the job posting
        **metrics: Additional metrics to set

    Example:
        quick_test(
            "https://linkedin.com/jobs/123",
            company_name="Google",
            job_title="Software Engineer",
            apply_button_found=True,
            final_status="Success - Auto Submitted"
        )
    """
    tester = JobApplicationAgentTester()
    tester.start_test(job_url)

    # Map friendly names to metric keys
    friendly_map = {
        "company_name": "Company Name",
        "job_title": "Job Title",
        "apply_button_found": "Apply Button Found?",
        "time_to_find_button": "Time to Find Apply Button (seconds)",
        "redirected": "Redirected to External Site?",
        "login_required": "Login/Auth Required?",
        "captcha_encountered": "CAPTCHA Encountered?",
        "popup_detected": "Popup Detected?",
        "popup_resolved": "Popup Resolved?",
        "total_fields": "Total Form Fields Detected",
        "basic_info_filled": "Basic Info Filled?",
        "resume_uploaded": "Resume Upload Successful?",
        "work_experience_filled": "Work Experience Filled?",
        "education_filled": "Education Filled?",
        "final_status": "Final Status",
        "failure_point": "Failure Point",
        "error_messages": "Error Messages Encountered"
    }

    # Update metrics
    for key, value in metrics.items():
        metric_key = friendly_map.get(key, key)
        tester.update_metric(metric_key, value)

    tester.end_test(manual_review=True)


if __name__ == "__main__":
    # Example usage
    print("Job Application Agent Test Runner")
    print("="*60)
    print("\nExample 1: Manual testing session")
    print("-"*60)

    tester = JobApplicationAgentTester()

    # Start test
    tester.start_test(
        job_url="https://www.linkedin.com/jobs/view/123456",
        job_board_type="LinkedIn"
    )

    # Simulate agent running and collecting metrics
    tester.update_metric("Company Name", "Example Corp")
    tester.update_metric("Job Title", "Senior Software Engineer")
    tester.update_metric("Apply Button Found?", "Yes")
    tester.update_metric("Time to Find Apply Button (seconds)", 3.5)
    tester.update_metric("Redirected to External Site?", "Yes")
    tester.update_metric("Login/Auth Required?", "Yes")
    tester.update_metric("Total Form Fields Detected", 18)
    tester.update_metric("Basic Info Filled?", "Yes (4/4 fields)")
    tester.update_metric("Resume Upload Successful?", "Yes")
    tester.update_metric("Work Experience Filled?", "Yes (3/3 positions)")
    tester.update_metric("Education Filled?", "Yes (2/2)")
    tester.update_metric("Final Status", "Success - Stopped Before Submit")
    tester.update_metric("Fields Filled/Total Available", "17/18")

    # End test (will prompt for manual review)
    tester.end_test(manual_review=False)

    # Show overall summary
    tester.show_summary()

    print("\n" + "="*60)
    print("Example 2: Quick test function")
    print("-"*60)
    print("\nUsage in your code:")
    print("""
    quick_test(
        "https://jobs.lever.co/company/job-id",
        company_name="Tech Startup",
        job_title="Backend Engineer",
        apply_button_found=True,
        total_fields=12,
        final_status="Success - Auto Submitted"
    )
    """)
