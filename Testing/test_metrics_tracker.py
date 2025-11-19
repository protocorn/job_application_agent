"""
Job Application Agent - Testing Metrics Tracker
Tracks comprehensive metrics for testing the job application agent
"""

import csv
import os
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, Optional


class TestMetricsTracker:
    """Tracks and persists test metrics for job application agent testing"""

    def __init__(self, base_dir: str = "Testing"):
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(exist_ok=True)

        # Define CSV file paths
        self.main_results_file = self.base_dir / "test_results_main.csv"
        self.failure_analysis_file = self.base_dir / "failure_analysis.csv"
        self.job_board_performance_file = self.base_dir / "job_board_performance.csv"
        self.time_analysis_file = self.base_dir / "time_analysis.csv"

        # Initialize CSV files if they don't exist
        self._initialize_csv_files()

    def _initialize_csv_files(self):
        """Create CSV files with headers if they don't exist"""

        # Main Results CSV Headers
        main_headers = [
            # Basic Info
            "Job URL",
            "Job Board/Site Type",
            "Company Name",
            "Job Title",
            "Date/Time of Test",

            # Discovery & Navigation
            "Apply Button Found?",
            "Time to Find Apply Button (seconds)",
            "Redirected to External Site?",

            # Authentication & Blockers
            "Login/Auth Required?",
            "CAPTCHA Encountered?",
            "Popup Detected?",
            "Popup Resolved?",

            # Form Detection
            "Total Form Fields Detected",
            "Form Type",

            # Basic Fields
            "Basic Info Filled?",
            "Resume Upload Successful?",
            "Cover Letter Section?",

            # Experience Sections
            "Work Experience Section Available?",
            "Work Experience Filled?",
            "Education Section Available?",
            "Education Filled?",
            "Skills Section Available?",
            "Skills Filled?",
            "Projects Section Available?",
            "Projects Filled?",

            # Complex Fields
            "Custom Questions Present?",
            "Custom Questions Answered?",
            "Question Types",
            "Sponsorship Question Handled?",
            "Salary Question Handled?",
            "Demographic Questions (EEO)?",

            # Outcome Tracking
            "Final Status",
            "Failure Point",
            "State Saved for User?",
            "Total Time Taken (seconds)",

            # Quality Metrics
            "Accuracy Score (1-10)",
            "Fields Filled/Total Available",
            "Resume Tailored?",

            # Notes
            "Error Messages Encountered",
            "Unique Challenges",
            "Would This Be Frustrating for User?"
        ]

        if not self.main_results_file.exists():
            with open(self.main_results_file, 'w', newline='', encoding='utf-8') as f:
                writer = csv.writer(f)
                writer.writerow(main_headers)

        # Failure Analysis CSV
        failure_headers = ["Failure Type", "Count", "% of Total", "Fix Priority"]
        if not self.failure_analysis_file.exists():
            with open(self.failure_analysis_file, 'w', newline='', encoding='utf-8') as f:
                writer = csv.writer(f)
                writer.writerow(failure_headers)

        # Job Board Performance CSV
        job_board_headers = ["Job Board Type", "Tests Run", "Success Rate", "Avg Time (seconds)", "Notes"]
        if not self.job_board_performance_file.exists():
            with open(self.job_board_performance_file, 'w', newline='', encoding='utf-8') as f:
                writer = csv.writer(f)
                writer.writerow(job_board_headers)

        # Time Analysis CSV
        time_headers = ["Application Complexity", "Count", "Avg Time (seconds)", "Manual Time Estimate (seconds)", "Time Saved (seconds)"]
        if not self.time_analysis_file.exists():
            with open(self.time_analysis_file, 'w', newline='', encoding='utf-8') as f:
                writer = csv.writer(f)
                writer.writerow(time_headers)

    def record_test_result(self, metrics: Dict[str, Any]):
        """
        Record a test result to the main CSV file

        Args:
            metrics: Dictionary containing all test metrics
        """
        # Ensure datetime is set
        if "Date/Time of Test" not in metrics:
            metrics["Date/Time of Test"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # Read existing data to maintain order
        with open(self.main_results_file, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            headers = reader.fieldnames

        # Append new row
        with open(self.main_results_file, 'a', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=headers)
            writer.writerow(metrics)

        print(f"✓ Test result recorded to {self.main_results_file}")

        # Update aggregate sheets
        self._update_failure_analysis()
        self._update_job_board_performance()
        self._update_time_analysis()

    def _update_failure_analysis(self):
        """Analyze all test results and update failure analysis sheet"""
        # Read all main results
        with open(self.main_results_file, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            results = list(reader)

        if not results:
            return

        # Count failures by type
        failure_counts = {}
        total_tests = len(results)

        for result in results:
            # Check various failure types
            if result.get("CAPTCHA Encountered?", "").lower() in ["yes", "true"]:
                failure_counts["CAPTCHA"] = failure_counts.get("CAPTCHA", 0) + 1

            if result.get("Login/Auth Required?", "").lower() in ["yes", "true"]:
                failure_counts["Auth Required"] = failure_counts.get("Auth Required", 0) + 1

            if result.get("Popup Resolved?", "").lower() in ["no", "false", "partial"]:
                failure_counts["Popup Not Resolved"] = failure_counts.get("Popup Not Resolved", 0) + 1

            if result.get("Apply Button Found?", "").lower() in ["no", "false"]:
                failure_counts["Apply Button Not Found"] = failure_counts.get("Apply Button Not Found", 0) + 1

            failure_point = result.get("Failure Point", "").strip()
            if failure_point and failure_point.lower() not in ["", "n/a", "na", "none"]:
                failure_counts[failure_point] = failure_counts.get(failure_point, 0) + 1

        # Determine priorities
        priority_map = {
            "CAPTCHA": "Low (can't fix)",
            "Auth Required": "Low (expected)",
            "Field Detection Error": "High",
            "Popup Not Resolved": "High",
            "Form Submission": "High",
            "Apply Button Not Found": "High",
            "Other": "Medium"
        }

        # Write to failure analysis CSV
        with open(self.failure_analysis_file, 'w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerow(["Failure Type", "Count", "% of Total", "Fix Priority"])

            for failure_type, count in sorted(failure_counts.items(), key=lambda x: x[1], reverse=True):
                percentage = (count / total_tests * 100) if total_tests > 0 else 0
                priority = priority_map.get(failure_type, "Medium")
                writer.writerow([failure_type, count, f"{percentage:.1f}%", priority])

        print(f"✓ Failure analysis updated")

    def _update_job_board_performance(self):
        """Analyze all test results and update job board performance sheet"""
        with open(self.main_results_file, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            results = list(reader)

        if not results:
            return

        # Group by job board type
        board_stats = {}

        for result in results:
            board_type = result.get("Job Board/Site Type", "Unknown")
            if board_type not in board_stats:
                board_stats[board_type] = {
                    "total": 0,
                    "success": 0,
                    "times": []
                }

            board_stats[board_type]["total"] += 1

            # Check if successful
            final_status = result.get("Final Status", "")
            if "success" in final_status.lower():
                board_stats[board_type]["success"] += 1

            # Track time
            try:
                time_taken = float(result.get("Total Time Taken (seconds)", 0))
                if time_taken > 0:
                    board_stats[board_type]["times"].append(time_taken)
            except (ValueError, TypeError):
                pass

        # Write to job board performance CSV
        with open(self.job_board_performance_file, 'w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerow(["Job Board Type", "Tests Run", "Success Rate", "Avg Time (seconds)", "Notes"])

            for board_type, stats in sorted(board_stats.items()):
                tests_run = stats["total"]
                success_rate = f"{(stats['success'] / tests_run * 100):.1f}%" if tests_run > 0 else "0%"
                avg_time = sum(stats["times"]) / len(stats["times"]) if stats["times"] else 0

                writer.writerow([board_type, tests_run, success_rate, f"{avg_time:.1f}", ""])

        print(f"✓ Job board performance updated")

    def _update_time_analysis(self):
        """Analyze all test results and update time analysis sheet"""
        with open(self.main_results_file, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            results = list(reader)

        if not results:
            return

        # Group by complexity
        complexity_stats = {
            "Simple (<10 fields)": {"times": [], "manual_estimate": 15 * 60},
            "Medium (10-20 fields)": {"times": [], "manual_estimate": 25 * 60},
            "Complex (>20 fields)": {"times": [], "manual_estimate": 40 * 60}
        }

        for result in results:
            form_type = result.get("Form Type", "")

            try:
                time_taken = float(result.get("Total Time Taken (seconds)", 0))
                if time_taken > 0:
                    if "simple" in form_type.lower():
                        complexity_stats["Simple (<10 fields)"]["times"].append(time_taken)
                    elif "medium" in form_type.lower():
                        complexity_stats["Medium (10-20 fields)"]["times"].append(time_taken)
                    elif "complex" in form_type.lower():
                        complexity_stats["Complex (>20 fields)"]["times"].append(time_taken)
            except (ValueError, TypeError):
                pass

        # Write to time analysis CSV
        with open(self.time_analysis_file, 'w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerow(["Application Complexity", "Count", "Avg Time (seconds)", "Manual Time Estimate (seconds)", "Time Saved (seconds)"])

            for complexity, stats in complexity_stats.items():
                count = len(stats["times"])
                avg_time = sum(stats["times"]) / count if count > 0 else 0
                manual_estimate = stats["manual_estimate"]
                time_saved = manual_estimate - avg_time if avg_time > 0 else 0

                writer.writerow([complexity, count, f"{avg_time:.1f}", manual_estimate, f"{time_saved:.1f}"])

        print(f"✓ Time analysis updated")

    def create_test_template(self) -> Dict[str, Any]:
        """
        Create a template dictionary with all required fields

        Returns:
            Dictionary with all metric fields initialized
        """
        return {
            # Basic Info
            "Job URL": "",
            "Job Board/Site Type": "",  # LinkedIn, Indeed, Greenhouse, Workday, Lever, Company Website - Custom, Other ATS
            "Company Name": "",
            "Job Title": "",
            "Date/Time of Test": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),

            # Discovery & Navigation
            "Apply Button Found?": "No",
            "Time to Find Apply Button (seconds)": 0,
            "Redirected to External Site?": "No",

            # Authentication & Blockers
            "Login/Auth Required?": "No",
            "CAPTCHA Encountered?": "No",
            "Popup Detected?": "No",
            "Popup Resolved?": "N/A",

            # Form Detection
            "Total Form Fields Detected": 0,
            "Form Type": "",  # Simple/Medium/Complex

            # Basic Fields
            "Basic Info Filled?": "No",
            "Resume Upload Successful?": "No",
            "Cover Letter Section?": "N/A",

            # Experience Sections
            "Work Experience Section Available?": "No",
            "Work Experience Filled?": "No",
            "Education Section Available?": "No",
            "Education Filled?": "No",
            "Skills Section Available?": "No",
            "Skills Filled?": "No",
            "Projects Section Available?": "No",
            "Projects Filled?": "No",

            # Complex Fields
            "Custom Questions Present?": "No",
            "Custom Questions Answered?": "0/0",
            "Question Types": "",
            "Sponsorship Question Handled?": "N/A",
            "Salary Question Handled?": "N/A",
            "Demographic Questions (EEO)?": "N/A",

            # Outcome Tracking
            "Final Status": "",  # Success - Auto Submitted/Success - Stopped Before Submit/Partial - User Action Needed/Failed
            "Failure Point": "",  # Auth/CAPTCHA/Field Detection/Form Submission/Other
            "State Saved for User?": "No",
            "Total Time Taken (seconds)": 0,

            # Quality Metrics
            "Accuracy Score (1-10)": 0,
            "Fields Filled/Total Available": "0/0",
            "Resume Tailored?": "No",

            # Notes
            "Error Messages Encountered": "",
            "Unique Challenges": "",
            "Would This Be Frustrating for User?": "No"
        }

    def print_summary(self):
        """Print a summary of all test results"""
        with open(self.main_results_file, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            results = list(reader)

        total_tests = len(results)

        if total_tests == 0:
            print("\nNo test results recorded yet.")
            return

        # Calculate success metrics
        successes = sum(1 for r in results if "success" in r.get("Final Status", "").lower())
        success_rate = (successes / total_tests * 100) if total_tests > 0 else 0

        print("\n" + "="*60)
        print("TEST SUMMARY")
        print("="*60)
        print(f"Total Tests Run: {total_tests}")
        print(f"Successful Applications: {successes}")
        print(f"Success Rate: {success_rate:.1f}%")
        print(f"\nDetailed results available in:")
        print(f"  - Main Results: {self.main_results_file}")
        print(f"  - Failure Analysis: {self.failure_analysis_file}")
        print(f"  - Job Board Performance: {self.job_board_performance_file}")
        print(f"  - Time Analysis: {self.time_analysis_file}")
        print("="*60 + "\n")


# Example usage
if __name__ == "__main__":
    tracker = TestMetricsTracker()

    # Create a sample test result
    test_metrics = tracker.create_test_template()
    test_metrics.update({
        "Job URL": "https://example.com/job/123",
        "Job Board/Site Type": "LinkedIn",
        "Company Name": "Example Corp",
        "Job Title": "Software Engineer",
        "Apply Button Found?": "Yes",
        "Time to Find Apply Button (seconds)": 5.2,
        "Total Form Fields Detected": 15,
        "Form Type": "Medium (10-20 fields)",
        "Final Status": "Success - Auto Submitted",
        "Total Time Taken (seconds)": 180,
        "Accuracy Score (1-10)": 9,
        "Fields Filled/Total Available": "14/15"
    })

    tracker.record_test_result(test_metrics)
    tracker.print_summary()
