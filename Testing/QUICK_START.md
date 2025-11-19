# Quick Start Guide - Automated Testing

## Overview
The testing framework now **automatically extracts metrics** from your agent's logs. You only need to:
1. Confirm Company Name & Job Title
2. Provide an Accuracy Score (1-10)
3. Optionally correct any wrong auto-extractions

## How to Run a Test

```bash
python Testing/run_agent_with_tracking.py --links "YOUR_JOB_URL" --headful --keep-open --slowmo 20
```

## What Happens

1. **Agent Runs** - The agent processes the job application automatically
2. **Auto-Extraction** - Metrics are extracted from logs:
   - Apply button found/clicked
   - Total form fields detected
   - Fields filled
   - Resume upload status
   - Work experience, education, skills filled
   - CAPTCHA, popups, redirects
   - Final status (success/failed)
   - Error messages
3. **Manual Review** - You only provide:
   - Company Name (if not auto-detected)
   - Job Title (if not auto-detected)
   - Accuracy Score (1-10)
   - Optional corrections if auto-extraction was wrong
4. **CSV Updated** - All 4 tracking sheets are automatically updated

## What Gets Auto-Extracted (39 fields)

✅ **Automatically from logs:**
- Job Board Type
- Apply Button Found
- Redirects
- Login/Auth Required
- CAPTCHA Encountered
- Popups Detected/Resolved
- Total Form Fields
- Form Type (Simple/Medium/Complex)
- Basic Info Filled
- Resume Upload
- Work Experience, Education, Skills, Projects
- Custom Questions
- Sponsorship/Salary Questions
- Final Status
- Failure Points
- Errors
- Time Taken
- Fields Filled Ratio

❓ **Manual input only:**
- Company Name (if not in logs)
- Job Title (if not in logs)
- Accuracy Score (your assessment)

## Example Workflow

```bash
# 1. Run test
python Testing/run_agent_with_tracking.py --links "https://ats.rippling.com/company/jobs/12345" --headful --keep-open --slowmo 20

# 2. Agent runs and processes the job

# 3. When done, press Ctrl+C in the agent window

# 4. You'll see:
============================================================
AGENT FINISHED - EXTRACTING METRICS FROM LOGS
============================================================

📄 Analyzing log file: job_application_agent_20251116_160500.log
------------------------------------------------------------
✅ Automatically extracted metrics from log file
   - Found 15 form fields
   - Status: Success - Stopped Before Submit
   - Apply button: Yes

------------------------------------------------------------
MANUAL REVIEW - BASIC INFO
------------------------------------------------------------
Auto-detected Company: Desri
  Is this correct? [Yes/Edit]: Yes

Auto-detected Job Title: Software Engineer
  Is this correct? [Yes/Edit]: Yes

------------------------------------------------------------
FINAL REVIEW - Please verify the auto-extracted data
------------------------------------------------------------

📊 Auto-Extracted Metrics Summary:
  Apply Button Found: Yes
  Total Fields: 15
  Fields Filled: 14/15
  Form Type: Medium (10-20 fields)
  Final Status: Success - Stopped Before Submit
  Resume Uploaded: Yes
  Work Experience: Yes (3 positions)
  Education: Yes

------------------------------------------------------------
ACCURACY ASSESSMENT
------------------------------------------------------------

Accuracy Score [1-10]: 9

------------------------------------------------------------
CORRECTIONS (Optional)
------------------------------------------------------------
Any corrections needed? [Yes/No]: No

============================================================
SAVING TEST RESULTS...
============================================================
✓ Test result recorded
✓ Failure analysis updated
✓ Job board performance updated
✓ Time analysis updated

✅ Test completed and metrics saved!

Results saved to:
  📊 Testing/test_results_main.csv
  📊 Testing/failure_analysis.csv
  📊 Testing/job_board_performance.csv
  📊 Testing/time_analysis.csv
```

## View Results

### Option 1: Open CSV Files
- `Testing/test_results_main.csv` - All test details
- `Testing/failure_analysis.csv` - Failure patterns
- `Testing/job_board_performance.csv` - Success by job board
- `Testing/time_analysis.csv` - Time savings

### Option 2: Python Summary
```python
from Testing.test_runner import JobApplicationAgentTester

tester = JobApplicationAgentTester()
tester.show_summary()
```

## Tips

1. **Keep browser open** with `--keep-open` so you can see what happened
2. **Use --slowmo 20** to slow down actions for easier observation
3. **Press Ctrl+C** when you're ready to finish and collect metrics
4. **Review accuracy carefully** - this is your human assessment of correctness
5. **Run 20-30 tests** across different job boards for good data

## Troubleshooting

**No metrics extracted?**
- Check that log files are in `logs/` directory
- The most recent log will be automatically used

**Wrong auto-extraction?**
- Select "Yes" when asked about corrections
- Manually override the incorrect field

**Want to skip a test?**
- Press Ctrl+C during the manual review
- Test won't be saved

## Next Steps

After collecting 20-30 tests:
1. Open the CSV files in Excel/Google Sheets
2. Analyze failure patterns
3. Identify which job boards work best
4. Calculate time savings
5. Prioritize improvements based on data
