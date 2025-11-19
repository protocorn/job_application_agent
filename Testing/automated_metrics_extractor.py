"""
Automated Metrics Extractor
Parses job application agent logs to automatically extract test metrics
"""

import re
import os
from pathlib import Path
from datetime import datetime
from typing import Dict, Any, Optional


class AutomatedMetricsExtractor:
    """Automatically extracts metrics from job application agent logs"""

    def __init__(self, log_file_path: str):
        self.log_file_path = log_file_path
        self.log_content = ""
        self.metrics = {}

        # Load log file
        if os.path.exists(log_file_path):
            with open(log_file_path, 'r', encoding='utf-8') as f:
                self.log_content = f.read()
        else:
            raise FileNotFoundError(f"Log file not found: {log_file_path}")

    def extract_all_metrics(self, job_url: str) -> Dict[str, Any]:
        """
        Extract all metrics from the log file

        Returns:
            Dictionary with all extracted metrics
        """
        metrics = {}

        # Basic info
        metrics["Job URL"] = job_url
        metrics["Job Board/Site Type"] = self._detect_job_board_type(job_url)
        metrics["Company Name"] = self._extract_company_name()
        metrics["Job Title"] = self._extract_job_title()
        metrics["Date/Time of Test"] = self._extract_test_datetime()

        # Discovery & Navigation
        metrics["Apply Button Found?"] = self._extract_apply_button_found()
        metrics["Time to Find Apply Button (seconds)"] = self._extract_time_to_apply_button()
        metrics["Redirected to External Site?"] = self._extract_redirect_status()

        # Authentication & Blockers
        metrics["Login/Auth Required?"] = self._extract_login_required()
        metrics["CAPTCHA Encountered?"] = self._extract_captcha_status()
        metrics["Popup Detected?"] = self._extract_popup_detected()
        metrics["Popup Resolved?"] = self._extract_popup_resolved()

        # Form Detection
        metrics["Total Form Fields Detected"] = self._extract_total_fields()
        metrics["Form Type"] = self._determine_form_type(metrics["Total Form Fields Detected"])

        # Basic Fields
        metrics["Basic Info Filled?"] = self._extract_basic_info_filled()
        metrics["Resume Upload Successful?"] = self._extract_resume_upload()
        metrics["Cover Letter Section?"] = self._extract_cover_letter()

        # Experience Sections
        metrics["Work Experience Section Available?"] = self._extract_work_experience_available()
        metrics["Work Experience Filled?"] = self._extract_work_experience_filled()
        metrics["Education Section Available?"] = self._extract_education_available()
        metrics["Education Filled?"] = self._extract_education_filled()
        metrics["Skills Section Available?"] = self._extract_skills_available()
        metrics["Skills Filled?"] = self._extract_skills_filled()
        metrics["Projects Section Available?"] = self._extract_projects_available()
        metrics["Projects Filled?"] = self._extract_projects_filled()

        # Complex Fields
        metrics["Custom Questions Present?"] = self._extract_custom_questions()
        metrics["Custom Questions Answered?"] = self._extract_custom_questions_answered()
        metrics["Question Types"] = self._extract_question_types()
        metrics["Sponsorship Question Handled?"] = self._extract_sponsorship()
        metrics["Salary Question Handled?"] = self._extract_salary()
        metrics["Demographic Questions (EEO)?"] = self._extract_eeo()

        # Outcome Tracking
        metrics["Final Status"] = self._extract_final_status()
        metrics["Failure Point"] = self._extract_failure_point()
        metrics["State Saved for User?"] = self._extract_state_saved()
        metrics["Total Time Taken (seconds)"] = self._extract_total_time()

        # Quality Metrics
        metrics["Accuracy Score (1-10)"] = 0  # Will be filled in manual review
        metrics["Fields Filled/Total Available"] = self._extract_fields_ratio()
        metrics["Resume Tailored?"] = "No"  # Default, can be updated in review

        # Notes
        metrics["Error Messages Encountered"] = self._extract_errors()
        metrics["Unique Challenges"] = self._extract_challenges()
        metrics["Would This Be Frustrating for User?"] = self._assess_frustration()

        return metrics

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
        elif "rippling.com" in url_lower:
            return "Rippling ATS"
        else:
            return "Company Website - Custom"

    def _extract_company_name(self) -> str:
        """Extract company name from logs"""
        # Look for company name patterns in logs
        patterns = [
            r"company[:\s]+([A-Za-z0-9\s&.,-]+)",
            r"applying to[:\s]+([A-Za-z0-9\s&.,-]+)",
            r"Company Name[:\s]+([A-Za-z0-9\s&.,-]+)"
        ]

        for pattern in patterns:
            match = re.search(pattern, self.log_content, re.IGNORECASE)
            if match:
                return match.group(1).strip()

        return ""  # Will be filled manually

    def _extract_job_title(self) -> str:
        """Extract job title from logs"""
        patterns = [
            r"job title[:\s]+([A-Za-z0-9\s/,-]+)",
            r"position[:\s]+([A-Za-z0-9\s/,-]+)",
            r"applying for[:\s]+([A-Za-z0-9\s/,-]+)"
        ]

        for pattern in patterns:
            match = re.search(pattern, self.log_content, re.IGNORECASE)
            if match:
                return match.group(1).strip()

        return ""  # Will be filled manually

    def _extract_test_datetime(self) -> str:
        """Extract test start time from first log line"""
        match = re.search(r"(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})", self.log_content)
        if match:
            return match.group(1)
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    def _extract_apply_button_found(self) -> str:
        """Check if apply button was found"""
        patterns = [
            r"apply button found",
            r"found apply button",
            r"clicking apply button",
            r"✅.*apply",
            r"detected apply button"
        ]

        for pattern in patterns:
            if re.search(pattern, self.log_content, re.IGNORECASE):
                return "Yes"

        # Check for failures
        if re.search(r"could not find apply|apply button not found|failed to find apply", self.log_content, re.IGNORECASE):
            return "No"

        return "Unknown"

    def _extract_time_to_apply_button(self) -> float:
        """Extract time taken to find apply button"""
        # This would require timestamp analysis - simplified for now
        return 0

    def _extract_redirect_status(self) -> str:
        """Check if there was a redirect"""
        if re.search(r"redirect|navigating to external|redirected to", self.log_content, re.IGNORECASE):
            return "Yes"
        return "No"

    def _extract_login_required(self) -> str:
        """Check if login was required"""
        if re.search(r"login required|authentication required|sign in|please log in", self.log_content, re.IGNORECASE):
            return "Yes"
        return "No"

    def _extract_captcha_status(self) -> str:
        """Check for CAPTCHA"""
        if re.search(r"captcha|recaptcha|hcaptcha", self.log_content, re.IGNORECASE):
            if re.search(r"recaptcha", self.log_content, re.IGNORECASE):
                return "Yes - reCAPTCHA"
            elif re.search(r"hcaptcha", self.log_content, re.IGNORECASE):
                return "Yes - hCAPTCHA"
            return "Yes"
        return "No"

    def _extract_popup_detected(self) -> str:
        """Check for popups"""
        if re.search(r"popup|modal|dialog|overlay", self.log_content, re.IGNORECASE):
            return "Yes"
        return "No"

    def _extract_popup_resolved(self) -> str:
        """Check if popup was resolved"""
        if self._extract_popup_detected() == "No":
            return "N/A"

        if re.search(r"closed popup|dismissed popup|popup resolved|popup handled", self.log_content, re.IGNORECASE):
            return "Yes"
        elif re.search(r"popup failed|could not close popup", self.log_content, re.IGNORECASE):
            return "No"

        return "Partial"

    def _extract_total_fields(self) -> int:
        """Extract total number of form fields detected"""
        patterns = [
            r"detected\s+(\d+)\s+fields",
            r"found\s+(\d+)\s+form fields",
            r"(\d+)\s+fields detected",
            r"total fields[:\s]+(\d+)"
        ]

        for pattern in patterns:
            match = re.search(pattern, self.log_content, re.IGNORECASE)
            if match:
                return int(match.group(1))

        # Try to count field filling mentions
        field_fills = len(re.findall(r"filled field|filling field|setting field", self.log_content, re.IGNORECASE))
        if field_fills > 0:
            return field_fills

        return 0

    def _determine_form_type(self, total_fields: int) -> str:
        """Determine form complexity based on field count"""
        if total_fields == 0:
            return "Unknown"
        elif total_fields < 10:
            return "Simple (<10 fields)"
        elif total_fields <= 20:
            return "Medium (10-20 fields)"
        else:
            return "Complex (>20 fields)"

    def _extract_basic_info_filled(self) -> str:
        """Check if basic info was filled"""
        basic_fields = ["name", "email", "phone", "address"]
        filled_count = 0

        for field in basic_fields:
            if re.search(rf"filled.*{field}|{field}.*filled|setting {field}", self.log_content, re.IGNORECASE):
                filled_count += 1

        if filled_count == 4:
            return "Yes (4/4 fields)"
        elif filled_count > 0:
            return f"Partial ({filled_count}/4 fields)"

        return "No"

    def _extract_resume_upload(self) -> str:
        """Check if resume was uploaded"""
        if re.search(r"uploaded resume|resume uploaded successfully|attached resume", self.log_content, re.IGNORECASE):
            return "Yes"
        elif re.search(r"failed to upload resume|resume upload failed", self.log_content, re.IGNORECASE):
            return "No"
        elif re.search(r"no resume upload|resume not required", self.log_content, re.IGNORECASE):
            return "N/A"

        return "Unknown"

    def _extract_cover_letter(self) -> str:
        """Check cover letter status"""
        if re.search(r"filled cover letter|cover letter filled|uploaded cover letter", self.log_content, re.IGNORECASE):
            return "Filled"
        elif re.search(r"skipped cover letter|cover letter skipped", self.log_content, re.IGNORECASE):
            return "Skipped"
        elif re.search(r"cover letter available|found cover letter", self.log_content, re.IGNORECASE):
            return "Available"

        return "N/A"

    def _extract_work_experience_available(self) -> str:
        """Check if work experience section exists"""
        if re.search(r"work experience|employment history|job history", self.log_content, re.IGNORECASE):
            return "Yes"
        return "No"

    def _extract_work_experience_filled(self) -> str:
        """Check if work experience was filled"""
        if self._extract_work_experience_available() == "No":
            return "N/A"

        # Count experience entries
        experience_count = len(re.findall(r"filled work experience|added job|employment entry", self.log_content, re.IGNORECASE))

        if experience_count > 0:
            return f"Yes ({experience_count} positions)"

        if re.search(r"filled.*work experience|work experience.*filled", self.log_content, re.IGNORECASE):
            return "Yes"

        return "No"

    def _extract_education_available(self) -> str:
        """Check if education section exists"""
        if re.search(r"education|degree|university|college", self.log_content, re.IGNORECASE):
            return "Yes"
        return "No"

    def _extract_education_filled(self) -> str:
        """Check if education was filled"""
        if self._extract_education_available() == "No":
            return "N/A"

        if re.search(r"filled education|education filled|added degree", self.log_content, re.IGNORECASE):
            return "Yes"

        return "No"

    def _extract_skills_available(self) -> str:
        """Check if skills section exists"""
        if re.search(r"skills section|skills field|list.*skills", self.log_content, re.IGNORECASE):
            return "Yes"
        return "No"

    def _extract_skills_filled(self) -> str:
        """Check if skills were filled"""
        if self._extract_skills_available() == "No":
            return "N/A"

        if re.search(r"filled skills|skills filled|added skills", self.log_content, re.IGNORECASE):
            return "Yes"

        return "No"

    def _extract_projects_available(self) -> str:
        """Check if projects section exists"""
        if re.search(r"projects section|project field", self.log_content, re.IGNORECASE):
            return "Yes"
        return "No"

    def _extract_projects_filled(self) -> str:
        """Check if projects were filled"""
        if self._extract_projects_available() == "No":
            return "N/A"

        if re.search(r"filled projects|projects filled|added project", self.log_content, re.IGNORECASE):
            return "Yes"

        return "No"

    def _extract_custom_questions(self) -> str:
        """Check for custom questions"""
        question_count = len(re.findall(r"custom question|additional question|screening question", self.log_content, re.IGNORECASE))

        if question_count > 0:
            return f"Yes ({question_count})"

        return "No"

    def _extract_custom_questions_answered(self) -> str:
        """Check how many custom questions were answered"""
        total_match = re.search(r"(\d+)\s+custom questions", self.log_content, re.IGNORECASE)
        answered_match = re.search(r"answered\s+(\d+)", self.log_content, re.IGNORECASE)

        if total_match and answered_match:
            return f"{answered_match.group(1)}/{total_match.group(1)}"

        return "0/0"

    def _extract_question_types(self) -> str:
        """Extract types of questions encountered"""
        types = []

        if re.search(r"dropdown|select", self.log_content, re.IGNORECASE):
            types.append("Dropdown")
        if re.search(r"radio|radio button", self.log_content, re.IGNORECASE):
            types.append("Radio")
        if re.search(r"checkbox", self.log_content, re.IGNORECASE):
            types.append("Checkbox")
        if re.search(r"text input|text field", self.log_content, re.IGNORECASE):
            types.append("Text")
        if re.search(r"textarea|paragraph", self.log_content, re.IGNORECASE):
            types.append("Paragraph")

        return ", ".join(types) if types else "N/A"

    def _extract_sponsorship(self) -> str:
        """Check sponsorship question handling"""
        if re.search(r"sponsorship|visa|work authorization", self.log_content, re.IGNORECASE):
            if re.search(r"answered.*sponsorship|sponsorship.*answered|handled.*sponsorship", self.log_content, re.IGNORECASE):
                return "Yes"
            return "No"
        return "N/A"

    def _extract_salary(self) -> str:
        """Check salary question handling"""
        if re.search(r"salary|compensation|pay", self.log_content, re.IGNORECASE):
            if re.search(r"answered.*salary|salary.*answered|filled.*salary", self.log_content, re.IGNORECASE):
                return "Yes"
            return "No"
        return "N/A"

    def _extract_eeo(self) -> str:
        """Check EEO/demographic questions"""
        if re.search(r"eeo|equal opportunity|demographic|race|gender|veteran", self.log_content, re.IGNORECASE):
            if re.search(r"filled.*eeo|eeo.*filled|answered.*demographic", self.log_content, re.IGNORECASE):
                return "Filled"
            elif re.search(r"skipped.*eeo|eeo.*skipped", self.log_content, re.IGNORECASE):
                return "Skipped"
            return "Present"
        return "N/A"

    def _extract_final_status(self) -> str:
        """Determine final status"""
        if re.search(r"submitted successfully|application submitted|✅.*submitted", self.log_content, re.IGNORECASE):
            return "Success - Auto Submitted"
        elif re.search(r"stopped before submit|ready for submission|review and submit", self.log_content, re.IGNORECASE):
            return "Success - Stopped Before Submit"
        elif re.search(r"partial|incomplete|needs user action", self.log_content, re.IGNORECASE):
            return "Partial - User Action Needed"
        elif re.search(r"failed|error|exception", self.log_content, re.IGNORECASE):
            return "Failed"

        return "Unknown"

    def _extract_failure_point(self) -> str:
        """Extract failure point if applicable"""
        if "Failed" not in self._extract_final_status():
            return ""

        if re.search(r"auth.*failed|login.*failed", self.log_content, re.IGNORECASE):
            return "Auth"
        elif re.search(r"captcha", self.log_content, re.IGNORECASE):
            return "CAPTCHA"
        elif re.search(r"field detection|could not find field", self.log_content, re.IGNORECASE):
            return "Field Detection"
        elif re.search(r"submission failed|could not submit", self.log_content, re.IGNORECASE):
            return "Form Submission"

        return "Other"

    def _extract_state_saved(self) -> str:
        """Check if state was saved"""
        if re.search(r"saved state|session saved|state persisted|💾", self.log_content, re.IGNORECASE):
            return "Yes"
        return "No"

    def _extract_total_time(self) -> float:
        """Calculate total time from logs"""
        timestamps = re.findall(r"(\d{2}:\d{2}:\d{2})", self.log_content)

        if len(timestamps) >= 2:
            try:
                start = datetime.strptime(timestamps[0], "%H:%M:%S")
                end = datetime.strptime(timestamps[-1], "%H:%M:%S")
                delta = (end - start).total_seconds()
                return max(0, delta)  # Ensure non-negative
            except:
                pass

        return 0

    def _extract_fields_ratio(self) -> str:
        """Extract filled/total fields ratio"""
        total_fields = self._extract_total_fields()

        # Try to find filled count
        filled_match = re.search(r"filled\s+(\d+)\s+(?:of|/)\s+(\d+)", self.log_content, re.IGNORECASE)
        if filled_match:
            return f"{filled_match.group(1)}/{filled_match.group(2)}"

        # Estimate based on total fields (assume most filled if no errors)
        if total_fields > 0 and "Failed" not in self._extract_final_status():
            return f"{total_fields}/{total_fields}"

        return f"0/{total_fields}"

    def _extract_errors(self) -> str:
        """Extract error messages"""
        errors = re.findall(r"ERROR.*?(?:\n|$)|Failed.*?(?:\n|$)|Exception.*?(?:\n|$)", self.log_content, re.IGNORECASE)

        if errors:
            # Return first few errors, truncated
            error_str = "; ".join(errors[:3])
            return error_str[:200]  # Limit length

        return ""

    def _extract_challenges(self) -> str:
        """Extract unique challenges"""
        challenges = []

        if re.search(r"unusual|unexpected|complex", self.log_content, re.IGNORECASE):
            challenges.append("Unusual form structure")

        if re.search(r"dynamic|javascript|spa", self.log_content, re.IGNORECASE):
            challenges.append("Dynamic content")

        if re.search(r"iframe|embedded", self.log_content, re.IGNORECASE):
            challenges.append("Embedded forms")

        return ", ".join(challenges) if challenges else ""

    def _assess_frustration(self) -> str:
        """Assess if this would be frustrating for user"""
        frustration_indicators = [
            r"failed",
            r"error",
            r"could not",
            r"unable to",
            r"timeout"
        ]

        frustration_count = sum(
            len(re.findall(pattern, self.log_content, re.IGNORECASE))
            for pattern in frustration_indicators
        )

        if frustration_count > 5:
            return "Yes"
        elif frustration_count > 2:
            return "Maybe"

        return "No"


if __name__ == "__main__":
    # Example usage
    import sys

    if len(sys.argv) > 1:
        log_file = sys.argv[1]
        job_url = sys.argv[2] if len(sys.argv) > 2 else "https://example.com/job"

        extractor = AutomatedMetricsExtractor(log_file)
        metrics = extractor.extract_all_metrics(job_url)

        print("Extracted Metrics:")
        print("="*60)
        for key, value in metrics.items():
            print(f"{key}: {value}")
    else:
        print("Usage: python automated_metrics_extractor.py <log_file> <job_url>")
