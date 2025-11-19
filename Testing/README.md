# Job Application Agent - Testing Framework

## Overview

This testing framework provides comprehensive metrics tracking for testing the job application agent. It automatically collects 41 different metrics across 4 tracking sheets to help you analyze performance, identify issues, and measure improvements.

## Files Created

1. **test_metrics_tracker.py** - Core metrics tracking system
2. **test_runner.py** - Test wrapper and helper functions
3. **job_application_agent_test.py** - Copy of the original agent for testing (preserves original)

## Quick Start

### Method 1: Using the Test Runner (Recommended)

```python
from Testing.test_runner import JobApplicationAgentTester

# Create tester instance
tester = JobApplicationAgentTester()

# Start a test
tester.start_test(
    job_url="https://www.linkedin.com/jobs/view/123456",
    job_board_type="LinkedIn"  # Optional, auto-detected from URL
)

# Update metrics as your agent runs
tester.update_metric("Company Name", "Google")
tester.update_metric("Job Title", "Software Engineer")
tester.update_metric("Apply Button Found?", "Yes")
tester.update_metric("Total Form Fields Detected", 15)
tester.update_metric("Final Status", "Success - Auto Submitted")

# End test (will prompt for manual quality review)
tester.end_test()
```

### Method 2: Quick Test Function

```python
from Testing.test_runner import quick_test

quick_test(
    "https://jobs.lever.co/company/job-id",
    company_name="Tech Startup",
    job_title="Backend Engineer",
    apply_button_found=True,
    total_fields=12,
    final_status="Success - Auto Submitted"
)
```

## Output Files

All results are saved as CSV files in the `Testing/` directory:

1. **test_results_main.csv** - Detailed results for each test run (41 columns)
2. **failure_analysis.csv** - Aggregated failure statistics
3. **job_board_performance.csv** - Performance by job board type
4. **time_analysis.csv** - Time savings analysis by complexity

## Tracked Metrics (41 Total)

### Basic Info (5 metrics)
- Job URL
- Job Board/Site Type
- Company Name
- Job Title
- Date/Time of Test

### Discovery & Navigation (3 metrics)
- Apply Button Found?
- Time to Find Apply Button (seconds)
- Redirected to External Site?

### Authentication & Blockers (4 metrics)
- Login/Auth Required?
- CAPTCHA Encountered?
- Popup Detected?
- Popup Resolved?

### Form Detection (2 metrics)
- Total Form Fields Detected
- Form Type (Simple/Medium/Complex)

### Basic Fields (3 metrics)
- Basic Info Filled? (Name, Email, Phone, Address)
- Resume Upload Successful?
- Cover Letter Section?

### Experience Sections (8 metrics)
- Work Experience Section Available?
- Work Experience Filled?
- Education Section Available?
- Education Filled?
- Skills Section Available?
- Skills Filled?
- Projects Section Available?
- Projects Filled?

### Complex Fields (6 metrics)
- Custom Questions Present?
- Custom Questions Answered?
- Question Types
- Sponsorship Question Handled?
- Salary Question Handled?
- Demographic Questions (EEO)?

### Outcome Tracking (4 metrics)
- Final Status
- Failure Point
- State Saved for User?
- Total Time Taken (seconds)

### Quality Metrics (3 metrics)
- Accuracy Score (1-10)
- Fields Filled/Total Available
- Resume Tailored?

### Notes (3 metrics)
- Error Messages Encountered
- Unique Challenges
- Would This Be Frustrating for User?

## Field Value Guidelines

### Final Status Options
- `Success - Auto Submitted` - Application completed and submitted
- `Success - Stopped Before Submit` - Filled out but didn't submit (safety)
- `Partial - User Action Needed` - Some fields filled, needs user intervention
- `Failed` - Could not complete

### Failure Point Options
- `Auth` - Authentication blocked progress
- `CAPTCHA` - CAPTCHA could not be resolved
- `Field Detection` - Could not detect form fields
- `Form Submission` - Failed to submit
- `Other` - Other issues

### Form Type (Auto-calculated from field count)
- `Simple (<10 fields)` - Less than 10 form fields
- `Medium (10-20 fields)` - 10-20 form fields
- `Complex (>20 fields)` - More than 20 form fields

### Job Board Types (Auto-detected)
- LinkedIn
- Indeed
- Greenhouse
- Workday
- Lever
- Ashby
- SmartRecruiters
- iCIMS
- Company Website - Custom
- Other ATS

## Example Integration with Your Agent

Here's how to integrate metrics tracking into your agent:

```python
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from Testing.test_runner import JobApplicationAgentTester
from Agents.job_application_agent_test import JobApplicationAgent  # Your agent

def test_agent_with_metrics(job_url: str):
    # Initialize tester
    tester = JobApplicationAgentTester()
    tester.start_test(job_url)

    try:
        # Initialize your agent
        agent = JobApplicationAgent()

        # Track navigation
        apply_button_found = agent.find_apply_button()
        tester.update_metric("Apply Button Found?", "Yes" if apply_button_found else "No")

        # Track form fields
        fields = agent.detect_form_fields()
        tester.update_metric("Total Form Fields Detected", len(fields))

        # Track what was filled
        filled = agent.fill_application()
        tester.update_metric("Basic Info Filled?", "Yes" if filled['basic'] else "No")
        tester.update_metric("Resume Upload Successful?", "Yes" if filled['resume'] else "No")

        # Track outcome
        if agent.was_successful():
            tester.update_metric("Final Status", "Success - Auto Submitted")
        else:
            tester.update_metric("Final Status", "Failed")
            tester.update_metric("Failure Point", agent.get_failure_reason())

    except Exception as e:
        tester.update_metric("Final Status", "Failed")
        tester.update_metric("Error Messages Encountered", str(e))

    finally:
        # Always end test to save results
        tester.end_test(manual_review=True)

# Run test
test_agent_with_metrics("https://www.linkedin.com/jobs/view/123456")
```

## Manual Testing Workflow

1. **Run your agent** on a job URL
2. **Start a test session** with the URL
3. **Update metrics** as the agent progresses
4. **End the test** - you'll be prompted to provide:
   - Company Name (if not auto-detected)
   - Job Title (if not auto-detected)
   - Accuracy Score (1-10)
   - Frustration level
   - Additional notes

## Viewing Results

### In Python
```python
from Testing.test_runner import JobApplicationAgentTester

tester = JobApplicationAgentTester()
tester.show_summary()
```

### In Excel/Google Sheets
Simply open the CSV files in the `Testing/` directory:
- `test_results_main.csv`
- `failure_analysis.csv`
- `job_board_performance.csv`
- `time_analysis.csv`

## Tips for Effective Testing

1. **Test diverse job boards** - LinkedIn, company sites, various ATS systems
2. **Test different complexities** - Simple, medium, and complex applications
3. **Track errors carefully** - Note exact error messages
4. **Be honest about accuracy** - Rate field accuracy objectively
5. **Test edge cases** - Jobs requiring sponsorship, salary questions, etc.
6. **Manual verification** - Periodically check that filled data is correct

## Analyzing Results

### Success Rate by Board Type
Check `job_board_performance.csv` to see which job boards work best

### Common Failure Points
Check `failure_analysis.csv` to identify what needs fixing

### Time Savings
Check `time_analysis.csv` to see how much time the agent saves

### Overall Success Rate
Run `tester.show_summary()` to see aggregate statistics

## Next Steps

1. Run at least 20-30 tests across different job boards
2. Analyze failure patterns
3. Prioritize fixes based on failure_analysis.csv
4. Re-test after fixes to measure improvement
5. Track success rate over time

## Troubleshooting

**CSV files not created?**
- The files are created on first test run
- Check that you have write permissions in the Testing/ directory

**Metrics not updating?**
- Make sure you called `start_test()` before `update_metric()`
- Check metric key spelling (case-sensitive)

**Want to reset all data?**
- Delete all CSV files in Testing/ directory
- They will be recreated on next test run

## Support

For issues with the testing framework, check:
1. Console output for error messages
2. CSV files are being created in Testing/ directory
3. Python version is 3.7+
