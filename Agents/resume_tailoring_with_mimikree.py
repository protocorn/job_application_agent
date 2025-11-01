"""
Resume Tailoring Agent with Mimikree Integration

This script demonstrates how to use the resume tailoring agent with Mimikree profile integration.
It will:
1. Connect to your Mimikree account
2. Generate relevant questions based on the job description
3. Query your Mimikree chatbot for personalized information
4. Use that information to tailor your resume for the specific job

Usage:
    python resume_tailoring_with_mimikree.py

Configuration:
    Set the following environment variables in your .env file:
    - GEMINI_API_KEY: Your Google Gemini API key
    - MIMIKREE_EMAIL: Your Mimikree account email (optional)
    - MIMIKREE_PASSWORD: Your Mimikree account password (optional)
    - MIMIKREE_BASE_URL: Mimikree API URL (default: http://localhost:3000)
"""

import os
import dotenv
from resume_tailoring_agent import tailor_resume_and_return_url

dotenv.load_dotenv()


def tailor_resume_with_mimikree(
    original_resume_url: str,
    job_description: str,
    job_title: str,
    company: str,
    mimikree_email: str = None,
    mimikree_password: str = None,
    credentials=None
):
    """Tailor resume with Mimikree profile integration.

    This function uses the integrated Mimikree functionality in the resume tailoring agent
    to create a personalized, enhanced resume.

    Args:
        original_resume_url: URL to the original Google Doc resume
        job_description: Job description text
        job_title: Job title for the position
        company: Company name
        mimikree_email: Optional Mimikree account email
        mimikree_password: Optional Mimikree account password
        credentials: Optional Google OAuth2 Credentials object

    Returns:
        URL to the tailored Google Doc resume
    """
    print("\n" + "=" * 80)
    print("RESUME TAILORING WITH MIMIKREE INTEGRATION")
    print("=" * 80)

    # Check if Mimikree credentials are provided
    use_mimikree = bool(mimikree_email and mimikree_password)

    if use_mimikree:
        print("✓ Mimikree credentials provided - will enhance with AI profile data")
    else:
        print("⚠️  No Mimikree credentials - proceeding with standard tailoring")
        print("   Tip: Set MIMIKREE_EMAIL and MIMIKREE_PASSWORD in .env to unlock AI enhancement")

    try:
        # Call the integrated function with Mimikree parameters
        tailored_url = tailor_resume_and_return_url(
            original_resume_url,
            job_description,
            job_title,
            company,
            credentials=credentials,
            mimikree_email=mimikree_email,
            mimikree_password=mimikree_password
        )

        print("\n" + "=" * 80)
        print("✅ RESUME TAILORING COMPLETED SUCCESSFULLY!")
        print("=" * 80)

        if use_mimikree:
            print("\n🎉 Your resume has been enhanced with information from your Mimikree AI profile!")
            print("   The tailored resume includes:")
            print("   • Relevant projects and experiences from your Mimikree profile")
            print("   • Skills and achievements specific to this job")
            print("   • Additional context gathered through personalized questions")

        print(f"\n🔗 Tailored Resume URL: {tailored_url}")
        print()

        return tailored_url

    except Exception as e:
        print(f"\n❌ Error during resume tailoring: {e}")
        raise


def tailor_resume_interactive():
    """Interactive command-line interface for resume tailoring with Mimikree."""
    print("\n" + "=" * 80)
    print("RESUME TAILORING ASSISTANT WITH MIMIKREE")
    print("=" * 80)
    print()

    # Collect inputs
    print("Please provide the following information:\n")

    resume_url = input("📄 Google Doc Resume URL: ").strip()
    if not resume_url:
        print("❌ Resume URL is required!")
        return

    job_title = input("💼 Job Title: ").strip()
    if not job_title:
        print("❌ Job title is required!")
        return

    company = input("🏢 Company Name: ").strip()
    if not company:
        print("❌ Company name is required!")
        return

    print("\n📋 Please paste the Job Description (press Enter, then Ctrl+D when done):")
    print("-" * 80)
    job_description_lines = []
    try:
        while True:
            line = input()
            job_description_lines.append(line)
    except EOFError:
        pass

    job_description = "\n".join(job_description_lines).strip()
    if not job_description:
        print("❌ Job description is required!")
        return

    print("\n" + "-" * 80)
    print("🤖 MIMIKREE ENHANCEMENT (Optional)")
    print("-" * 80)
    print("Mimikree can enhance your resume with information from your AI profile.")
    print("This includes projects, skills, and experiences from your connected accounts.")
    print()

    use_mimikree = input("Enable Mimikree enhancement? (y/n): ").strip().lower()

    mimikree_email = None
    mimikree_password = None

    if use_mimikree == 'y':
        mimikree_email = input("Mimikree Email: ").strip()
        if mimikree_email:
            import getpass
            mimikree_password = getpass.getpass("Mimikree Password: ")

            if not mimikree_password:
                print("⚠️  No password provided. Continuing without Mimikree enhancement.")
                mimikree_email = None

    # Execute tailoring
    print("\n" + "=" * 80)
    print("Starting resume tailoring process...")
    print("=" * 80)

    try:
        tailored_url = tailor_resume_with_mimikree(
            original_resume_url=resume_url,
            job_description=job_description,
            job_title=job_title,
            company=company,
            mimikree_email=mimikree_email,
            mimikree_password=mimikree_password
        )

        print("\n✅ SUCCESS!")
        print(f"Your tailored resume is ready: {tailored_url}")

    except Exception as e:
        print(f"\n❌ ERROR: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    # Option 1: Run interactively
    tailor_resume_interactive()

    # Option 2: Run programmatically (uncomment to use)
    """
    tailored_url = tailor_resume_with_mimikree(
        original_resume_url="https://docs.google.com/document/d/YOUR_DOC_ID/edit",
        job_description="Your job description here...",
        job_title="Data Scientist",
        company="Figma",
        mimikree_email="your_email@example.com",
        mimikree_password="your_password"
    )
    print(f"Tailored resume: {tailored_url}")
    """
