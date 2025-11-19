"""
Wrapper to run the job application agent with automatic metrics tracking
This runs the agent and then prompts you to fill in the test metrics

Usage:
    python Testing/run_agent_with_tracking.py --links "https://job-url-here" --headful --keep-open --slowmo 20
"""

import sys
import subprocess
import argparse
from pathlib import Path
from datetime import datetime
import glob
import os

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from Testing.test_runner import JobApplicationAgentTester
from Testing.automated_metrics_extractor import AutomatedMetricsExtractor


def run_agent_and_track(job_url: str, agent_args: list):
    """
    Run the agent with given arguments, then collect metrics

    Args:
        job_url: The job URL being tested
        agent_args: Additional arguments to pass to the agent
    """

    # Initialize tester
    tester = JobApplicationAgentTester()

    print("\n" + "="*60)
    print("JOB APPLICATION AGENT - AUTOMATED TEST WITH METRICS")
    print("="*60)
    print(f"\nJob URL: {job_url}")
    print(f"Starting test session at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("="*60 + "\n")

    # Start test session
    tester.start_test(job_url)

    # Build command to run the agent
    agent_path = Path(__file__).parent.parent / "Agents" / "job_application_agent_test.py"

    cmd = [sys.executable, str(agent_path)] + agent_args

    print(f"Running agent with command:")
    print(f"  {' '.join(cmd)}\n")
    print("-"*60)
    print("AGENT OUTPUT:")
    print("-"*60 + "\n")

    # Run the agent
    try:
        result = subprocess.run(
            cmd,
            capture_output=False,  # Show output in real-time
            text=True
        )

        agent_success = result.returncode == 0

    except KeyboardInterrupt:
        print("\n\n⚠ Agent interrupted by user (Ctrl+C)")
        agent_success = False
    except Exception as e:
        print(f"\n\n❌ Error running agent: {e}")
        agent_success = False

    # Now collect metrics AUTOMATICALLY from logs
    print("\n" + "="*60)
    print("AGENT FINISHED - EXTRACTING METRICS FROM LOGS")
    print("="*60)

    # Find the most recent log file
    log_dir = Path(__file__).parent.parent / "logs"
    log_files = sorted(glob.glob(str(log_dir / "job_application_agent_*.log")), key=os.path.getmtime, reverse=True)

    if log_files:
        latest_log = log_files[0]
        print(f"\n📄 Analyzing log file: {Path(latest_log).name}")
        print("-"*60)

        try:
            # Extract metrics automatically
            extractor = AutomatedMetricsExtractor(latest_log)
            auto_metrics = extractor.extract_all_metrics(job_url)

            # Apply all auto-extracted metrics
            for key, value in auto_metrics.items():
                tester.update_metric(key, value)

            print("✅ Automatically extracted metrics from log file")
            print(f"   - Found {auto_metrics['Total Form Fields Detected']} form fields")
            print(f"   - Status: {auto_metrics['Final Status']}")
            print(f"   - Apply button: {auto_metrics['Apply Button Found?']}")

        except Exception as e:
            print(f"⚠ Error extracting metrics automatically: {e}")
            print("   Falling back to manual entry...")

    else:
        print("⚠ No log file found, using manual entry...")

    # Manual review for Company Name and Job Title (if not auto-extracted)
    print("\n" + "-"*60)
    print("MANUAL REVIEW - BASIC INFO")
    print("-"*60)

    current_company = tester.current_metrics.get("Company Name", "")
    if not current_company:
        company = input("Company Name (required): ").strip()
        if company:
            tester.update_metric("Company Name", company)
    else:
        print(f"Auto-detected Company: {current_company}")
        company_confirm = input("  Is this correct? [Yes/Edit]: ").strip().lower()
        if company_confirm == 'edit':
            company = input("  Enter correct company name: ").strip()
            if company:
                tester.update_metric("Company Name", company)

    current_title = tester.current_metrics.get("Job Title", "")
    if not current_title:
        job_title = input("Job Title (required): ").strip()
        if job_title:
            tester.update_metric("Job Title", job_title)
    else:
        print(f"Auto-detected Job Title: {current_title}")
        title_confirm = input("  Is this correct? [Yes/Edit]: ").strip().lower()
        if title_confirm == 'edit':
            job_title = input("  Enter correct job title: ").strip()
            if job_title:
                tester.update_metric("Job Title", job_title)

    # Final Review - Only critical items
    print("\n" + "-"*60)
    print("FINAL REVIEW - Please verify the auto-extracted data")
    print("-"*60)

    print("\n📊 Auto-Extracted Metrics Summary:")
    print(f"  Apply Button Found: {tester.current_metrics.get('Apply Button Found?', 'Unknown')}")
    print(f"  Total Fields: {tester.current_metrics.get('Total Form Fields Detected', 0)}")
    print(f"  Fields Filled: {tester.current_metrics.get('Fields Filled/Total Available', '0/0')}")
    print(f"  Form Type: {tester.current_metrics.get('Form Type', 'Unknown')}")
    print(f"  Final Status: {tester.current_metrics.get('Final Status', 'Unknown')}")
    print(f"  Resume Uploaded: {tester.current_metrics.get('Resume Upload Successful?', 'Unknown')}")
    print(f"  Work Experience: {tester.current_metrics.get('Work Experience Filled?', 'N/A')}")
    print(f"  Education: {tester.current_metrics.get('Education Filled?', 'N/A')}")

    print("\n" + "-"*60)
    print("ACCURACY ASSESSMENT")
    print("-"*60)

    print("\nAccuracy Score (1-10) - How accurately were fields filled?:")
    print("  1-3: Many errors, incorrect data")
    print("  4-6: Some errors, mostly correct")
    print("  7-9: Very accurate, minor issues")
    print("  10: Perfect, no errors")

    accuracy = input("\nAccuracy Score [1-10]: ").strip()
    if accuracy:
        try:
            tester.update_metric("Accuracy Score (1-10)", int(accuracy))
        except ValueError:
            pass

    # Optional: Override any incorrect auto-extracted values
    print("\n" + "-"*60)
    print("CORRECTIONS (Optional)")
    print("-"*60)
    print("Press Enter to skip, or type corrections if auto-extraction was wrong")

    corrections = input("\nAny corrections needed? [Yes/No]: ").strip().lower()
    if corrections == 'yes':
        print("\nWhich field needs correction?")
        print("  1. Final Status")
        print("  2. Total Fields Detected")
        print("  3. Other (manual entry)")

        correction_choice = input("Choice [1-3 or Enter to skip]: ").strip()

        if correction_choice == '1':
            print("\n1. Success - Auto Submitted")
            print("2. Success - Stopped Before Submit")
            print("3. Partial - User Action Needed")
            print("4. Failed")
            new_status = input("Correct status [1-4]: ").strip()
            status_map = {
                '1': 'Success - Auto Submitted',
                '2': 'Success - Stopped Before Submit',
                '3': 'Partial - User Action Needed',
                '4': 'Failed'
            }
            if new_status in status_map:
                tester.update_metric("Final Status", status_map[new_status])

        elif correction_choice == '2':
            new_total = input("Correct total fields: ").strip()
            if new_total.isdigit():
                tester.update_metric("Total Form Fields Detected", int(new_total))

    # Optional notes
    additional_notes = input("\nAny additional notes? (Press Enter to skip): ").strip()
    if additional_notes:
        tester.update_metric("Unique Challenges", additional_notes)

    # End test and save
    print("\n" + "="*60)
    print("SAVING TEST RESULTS...")
    print("="*60)

    tester.end_test(manual_review=False)
    tester.show_summary()

    print("\n✅ Test completed and metrics saved!")
    print("\nResults saved to:")
    print("  📊 Testing/test_results_main.csv")
    print("  📊 Testing/failure_analysis.csv")
    print("  📊 Testing/job_board_performance.csv")
    print("  📊 Testing/time_analysis.csv")
    print("\n")


def main():
    """Main entry point"""

    parser = argparse.ArgumentParser(
        description='Run job application agent with metrics tracking',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python Testing/run_agent_with_tracking.py --links "https://job-url" --headful --slowmo 20
  python Testing/run_agent_with_tracking.py --links "https://job-url" --headful --keep-open
        """
    )

    parser.add_argument('--links', required=True, help='Job URL to test')
    parser.add_argument('--headful', action='store_true', help='Run browser in headful mode')
    parser.add_argument('--keep-open', action='store_true', help='Keep browser open after completion')
    parser.add_argument('--slowmo', type=int, help='Slow down operations by N milliseconds')
    parser.add_argument('--user-id', type=str, help='User ID (UUID) to load profile for (optional, defaults to latest user)')

    # Parse known args (allows passing through other args to the agent)
    args, unknown_args = parser.parse_known_args()

    # Build agent arguments
    agent_args = ['--links', args.links]

    if args.headful:
        agent_args.append('--headful')

    if args.keep_open:
        agent_args.append('--keep-open')

    if args.slowmo:
        agent_args.extend(['--slowmo', str(args.slowmo)])

    if args.user_id:
        agent_args.extend(['--user-id', str(args.user_id)])

    # Add any unknown args
    agent_args.extend(unknown_args)

    # Run agent with tracking
    try:
        run_agent_and_track(args.links, agent_args)
    except KeyboardInterrupt:
        print("\n\n⚠ Test interrupted by user")
        sys.exit(1)
    except Exception as e:
        print(f"\n\n❌ Error during test: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
