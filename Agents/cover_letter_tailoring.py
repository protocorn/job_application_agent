"""
Advanced Cover Letter Tailoring using Google Gemini
"""
import google.generativeai as genai
import os
from dotenv import load_dotenv
from datetime import datetime

load_dotenv()

# Configure Gemini
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
genai.configure(api_key=GEMINI_API_KEY)

def tailor_cover_letter(template, job_description, company_name, job_title, user_full_name):
    """
    Tailor a cover letter template for a specific job with advanced extraction and customization

    Args:
        template: Cover letter template text
        job_description: Job description
        company_name: Company name
        job_title: Job title
        user_full_name: User's full name

    Returns:
        Tailored cover letter text
    """
    try:
        model = genai.GenerativeModel('gemini-2.0-flash')

        # Get current date
        current_date = datetime.now().strftime("%B %d, %Y")

        prompt = f"""You are an expert cover letter writer with exceptional attention to detail. Your goal is to create a cover letter that requires ZERO edits from the user.

**Original Cover Letter Template:**
{template}

**Job Details:**
- Company: {company_name}
- Position: {job_title}
- Applicant Name: {user_full_name}
- Current Date: {current_date}

**Job Description:**
{job_description}

**CRITICAL INSTRUCTIONS - Follow these steps meticulously:**

**STEP 1: EXTRACT KEY INFORMATION FROM JOB DESCRIPTION**
Carefully analyze the job description and extract:
1. **Hiring Manager Name**: Look for phrases like "report to", "hiring manager", contact person, or any person's name mentioned. If found, use "Dear [Name]". If not found, use "Dear Hiring Manager" or "Dear [Company] Hiring Team".
2. **Position Title**: Extract the exact job title as written in the job description. If not explicitly stated, use the provided job_title: {job_title}
3. **Location/Office**: Look for any location, city, office address, or work location mentioned. Use this for the company address line if your template has one. If not found, use your knowledge of the company's headquarters or main office.
4. **Key Requirements**: Identify the top 3-5 most important skills, qualifications, or experiences mentioned in the job description.
5. **Company-Specific Details**: Extract any unique company values, products, technologies, or initiatives mentioned.

**STEP 2: TAILOR THE COVER LETTER**
1. **Date**: Replace any date placeholder with: {current_date}
2. **Recipient**: Use the hiring manager's name if found, otherwise use appropriate salutation
3. **Company Address**: If the template has a company address section, fill it with the location found in job description or use your knowledge of the company's address
4. **Position Title**: Use the exact position title extracted from job description
5. **Company Name**: Replace all instances with {company_name}
6. **Applicant Name**: Replace with {user_full_name}
7. **Body Content**:
   - Match the job requirements with relevant experiences from the template
   - Incorporate specific technologies, tools, or skills mentioned in the job description
   - Reference company-specific details to show research and genuine interest
   - Align the tone with the company culture (formal for traditional companies, conversational for startups)
8. **Keep it concise**: 250-400 words total

**STEP 3: QUALITY CHECKS**
- Ensure NO placeholders remain (no [brackets], no XXX, no blanks)
- Use ONLY plain text - absolutely NO markdown formatting (* ** # - etc.)
- Check that all dates are {current_date}
- Verify hiring manager name or appropriate salutation is used
- Confirm the cover letter flows naturally and reads professionally

**STEP 4: OUTPUT FORMAT**
Return ONLY the final tailored cover letter text. Do not include any explanations, notes, or commentary. The output should be ready to copy-paste directly into a document.

Tailored Cover Letter:"""

        response = model.generate_content(prompt)
        tailored_text = response.text.strip()

        # Remove any markdown formatting that might have been added
        tailored_text = tailored_text.replace('**', '').replace('*', '').replace('#', '').replace('```', '')

        # Remove common markdown list indicators
        tailored_text = tailored_text.replace('- ', '')

        print(f"✅ Cover letter tailored successfully ({len(tailored_text)} characters)")
        print(f"📅 Date used: {current_date}")
        return tailored_text

    except Exception as e:
        print(f"❌ Error tailoring cover letter: {e}")
        # Return original template with basic replacements as fallback
        current_date = datetime.now().strftime("%B %d, %Y")
        fallback = template.replace('[Company Name]', company_name)
        fallback = fallback.replace('[Job Title]', job_title)
        fallback = fallback.replace('[Your Name]', user_full_name)
        fallback = fallback.replace('[Company]', company_name)
        fallback = fallback.replace('[Position]', job_title)
        fallback = fallback.replace('[Date]', current_date)
        return fallback

if __name__ == "__main__":
    # Test the function
    template = """Dear Hiring Manager,

I am writing to express my interest in the [Job Title] position at [Company Name]. With my background in software development and passion for innovation, I believe I would be a valuable addition to your team.

[Your Name]"""

    tailored = tailor_cover_letter(
        template,
        "We are looking for a Python developer with experience in web development...",
        "Google",
        "Senior Software Engineer",
        "John Doe"
    )
    print("\n" + tailored)
