"""
Simple script to run a single test on the job application agent
Usage: python run_single_test.py <job_url>
"""

import sys
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from Testing.test_runner import JobApplicationAgentTester


def main():
    """Main test function"""

    # Get job URL from command line or prompt
    if len(sys.argv) > 1:
        job_url = sys.argv[1]
    else:
        print("\n" + "="*60)
        print("JOB APPLICATION AGENT - SINGLE TEST")
        print("="*60)
        job_url = input("\nEnter job URL to test: ").strip()

        if not job_url:
            print("Error: No URL provided")
            sys.exit(1)

    # Initialize tester
    tester = JobApplicationAgentTester()

    print("\n📋 Starting test session...")
    tester.start_test(job_url)

    print("\n" + "="*60)
    print("INSTRUCTIONS FOR MANUAL TESTING")
    print("="*60)
    print("""
1. Open the job URL in your browser
2. Run your agent manually
3. Observe what happens at each stage
4. Answer the questions below based on what you observe
    """)

    print("\n" + "-"*60)
    print("BASIC INFORMATION")
    print("-"*60)

    company = input("Company Name: ").strip()
    if company:
        tester.update_metric("Company Name", company)

    job_title = input("Job Title: ").strip()
    if job_title:
        tester.update_metric("Job Title", job_title)

    print("\n" + "-"*60)
    print("NAVIGATION & DISCOVERY")
    print("-"*60)

    apply_found = input("Apply button found? [Yes/No]: ").strip()
    tester.update_metric("Apply Button Found?", apply_found)

    if apply_found.lower() in ['yes', 'y']:
        time_to_find = input("Time to find apply button (seconds): ").strip()
        if time_to_find:
            try:
                tester.update_metric("Time to Find Apply Button (seconds)", float(time_to_find))
            except ValueError:
                pass

    redirected = input("Redirected to external site? [Yes/No]: ").strip()
    tester.update_metric("Redirected to External Site?", redirected)

    print("\n" + "-"*60)
    print("AUTHENTICATION & BLOCKERS")
    print("-"*60)

    login_required = input("Login/Auth required? [Yes/No]: ").strip()
    tester.update_metric("Login/Auth Required?", login_required)

    captcha = input("CAPTCHA encountered? [Yes/No/Type]: ").strip()
    tester.update_metric("CAPTCHA Encountered?", captcha)

    popup = input("Popup detected? [Yes/No]: ").strip()
    tester.update_metric("Popup Detected?", popup)

    if popup.lower() in ['yes', 'y']:
        popup_resolved = input("Popup resolved? [Yes/No/Partial]: ").strip()
        tester.update_metric("Popup Resolved?", popup_resolved)

    print("\n" + "-"*60)
    print("FORM FIELDS")
    print("-"*60)

    total_fields = input("Total form fields detected (count): ").strip()
    if total_fields:
        try:
            tester.update_metric("Total Form Fields Detected", int(total_fields))
        except ValueError:
            pass

    print("\n" + "-"*60)
    print("BASIC FIELDS FILLED")
    print("-"*60)

    basic_info = input("Basic info filled? (Name/Email/Phone/Address) [Yes/No/Partial]: ").strip()
    tester.update_metric("Basic Info Filled?", basic_info)

    resume = input("Resume uploaded successfully? [Yes/No/N/A]: ").strip()
    tester.update_metric("Resume Upload Successful?", resume)

    cover_letter = input("Cover letter section? [Available/Filled/Skipped/N/A]: ").strip()
    tester.update_metric("Cover Letter Section?", cover_letter)

    print("\n" + "-"*60)
    print("EXPERIENCE SECTIONS")
    print("-"*60)

    work_exp_avail = input("Work experience section available? [Yes/No]: ").strip()
    tester.update_metric("Work Experience Section Available?", work_exp_avail)

    if work_exp_avail.lower() in ['yes', 'y']:
        work_exp_filled = input("Work experience filled? [Yes/No/Partial - e.g., 2/3]: ").strip()
        tester.update_metric("Work Experience Filled?", work_exp_filled)

    edu_avail = input("Education section available? [Yes/No]: ").strip()
    tester.update_metric("Education Section Available?", edu_avail)

    if edu_avail.lower() in ['yes', 'y']:
        edu_filled = input("Education filled? [Yes/No/Partial]: ").strip()
        tester.update_metric("Education Filled?", edu_filled)

    skills_avail = input("Skills section available? [Yes/No]: ").strip()
    tester.update_metric("Skills Section Available?", skills_avail)

    if skills_avail.lower() in ['yes', 'y']:
        skills_filled = input("Skills filled? [Yes/No/Partial]: ").strip()
        tester.update_metric("Skills Filled?", skills_filled)

    projects_avail = input("Projects section available? [Yes/No]: ").strip()
    tester.update_metric("Projects Section Available?", projects_avail)

    if projects_avail.lower() in ['yes', 'y']:
        projects_filled = input("Projects filled? [Yes/No/Partial]: ").strip()
        tester.update_metric("Projects Filled?", projects_filled)

    print("\n" + "-"*60)
    print("COMPLEX FIELDS")
    print("-"*60)

    custom_questions = input("Custom questions present? [Yes/No - count]: ").strip()
    tester.update_metric("Custom Questions Present?", custom_questions)

    if custom_questions.lower() in ['yes', 'y'] or any(c.isdigit() for c in custom_questions):
        custom_answered = input("Custom questions answered? [e.g., 5/7]: ").strip()
        tester.update_metric("Custom Questions Answered?", custom_answered)

        question_types = input("Question types encountered [Dropdown/Radio/Checkbox/Text/Paragraph]: ").strip()
        tester.update_metric("Question Types", question_types)

    sponsorship = input("Sponsorship question handled? [Yes/No/N/A]: ").strip()
    tester.update_metric("Sponsorship Question Handled?", sponsorship)

    salary = input("Salary question handled? [Yes/No/N/A]: ").strip()
    tester.update_metric("Salary Question Handled?", salary)

    eeo = input("Demographic/EEO questions? [Present/Filled/Skipped/N/A]: ").strip()
    tester.update_metric("Demographic Questions (EEO)?", eeo)

    print("\n" + "-"*60)
    print("OUTCOME")
    print("-"*60)

    print("\nFinal Status Options:")
    print("  1. Success - Auto Submitted")
    print("  2. Success - Stopped Before Submit")
    print("  3. Partial - User Action Needed")
    print("  4. Failed")

    final_status = input("\nFinal status [1-4 or full text]: ").strip()

    status_map = {
        '1': 'Success - Auto Submitted',
        '2': 'Success - Stopped Before Submit',
        '3': 'Partial - User Action Needed',
        '4': 'Failed'
    }

    final_status_text = status_map.get(final_status, final_status)
    tester.update_metric("Final Status", final_status_text)

    if 'fail' in final_status_text.lower():
        failure_point = input("Failure point [Auth/CAPTCHA/Field Detection/Form Submission/Other]: ").strip()
        tester.update_metric("Failure Point", failure_point)

    state_saved = input("State saved for user? [Yes/No/N/A]: ").strip()
    tester.update_metric("State Saved for User?", state_saved)

    fields_ratio = input("Fields filled / Total available [e.g., 15/18]: ").strip()
    if fields_ratio:
        tester.update_metric("Fields Filled/Total Available", fields_ratio)

    resume_tailored = input("Resume tailored for this job? [Yes/No]: ").strip()
    tester.update_metric("Resume Tailored?", resume_tailored)

    print("\n" + "-"*60)
    print("NOTES & QUALITY")
    print("-"*60)

    errors = input("Error messages encountered (if any): ").strip()
    if errors:
        tester.update_metric("Error Messages Encountered", errors)

    challenges = input("Unique challenges or observations: ").strip()
    if challenges:
        tester.update_metric("Unique Challenges", challenges)

    print("\nAccuracy Score (1-10):")
    print("  1-3: Many errors, incorrect data")
    print("  4-6: Some errors, mostly correct")
    print("  7-9: Very accurate, minor issues")
    print("  10: Perfect, no errors")

    accuracy = input("Accuracy score [1-10]: ").strip()
    if accuracy:
        try:
            tester.update_metric("Accuracy Score (1-10)", int(accuracy))
        except ValueError:
            pass

    frustrating = input("\nWould this be frustrating for user? [Yes/No]: ").strip()
    tester.update_metric("Would This Be Frustrating for User?", frustrating)

    # Save results
    print("\n" + "="*60)
    tester.end_test(manual_review=False)

    # Show summary
    tester.show_summary()

    print("\n✅ Test completed! Results saved to CSV files in Testing/ directory")
    print("\nNext steps:")
    print("  - Review test_results_main.csv for detailed metrics")
    print("  - Check failure_analysis.csv for failure patterns")
    print("  - Check job_board_performance.csv for board-specific stats")
    print("  - Run more tests to build comprehensive dataset\n")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n⚠ Test interrupted by user")
        sys.exit(1)
    except Exception as e:
        print(f"\n\n❌ Error during test: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
