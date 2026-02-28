"""
Mimikree Integration Module for Resume Tailoring Agent

This module provides functions to authenticate users and query the Mimikree AI chatbot
to gather additional information about the user that may not be present in their resume.
"""

import requests
import os
from typing import List, Dict, Optional
import dotenv

dotenv.load_dotenv()

# Configuration: use development URL (localhost:8080) unless running in production
def _get_mimikree_base_url() -> str:
    if os.getenv('FLASK_ENV') == 'production':
        return os.getenv('MIMIKREE_BASE_URL', 'https://www.mimikree.com')
    return os.getenv('MIMIKREE_BASE_URL', 'http://localhost:8080')


MIMIKREE_BASE_URL = _get_mimikree_base_url()
MIMIKREE_AUTH_ENDPOINT = f'{MIMIKREE_BASE_URL}/api/external/authenticate'
MIMIKREE_BATCH_QUESTIONS_ENDPOINT = f'{MIMIKREE_BASE_URL}/api/external/batch-questions'


class MimikreeClient:
    """Client for interacting with Mimikree AI chatbot API."""

    def __init__(self, base_url: Optional[str] = None):
        """Initialize Mimikree client.

        Args:
            base_url: Optional custom base URL for the API (defaults to env variable or localhost)
        """
        self.base_url = base_url or MIMIKREE_BASE_URL
        self.auth_endpoint = f'{self.base_url}/api/external/authenticate'
        self.batch_questions_endpoint = f'{self.base_url}/api/external/batch-questions'
        self.username = None
        self.user_name = None

    def authenticate(self, email: str, password: str) -> bool:
        """Authenticate user with Mimikree credentials.

        Args:
            email: User's Mimikree account email
            password: User's Mimikree account password

        Returns:
            True if authentication successful, False otherwise

        Raises:
            Exception: If authentication fails with an error
        """
        try:
            response = requests.post(
                self.auth_endpoint,
                json={
                    'email': email,
                    'password': password
                },
                timeout=30
            )

            if response.status_code == 200:
                data = response.json()
                if data.get('success'):
                    self.username = data.get('username')
                    self.user_name = data.get('name')
                    print(f"✓ Authenticated as: {self.user_name} (@{self.username})")
                    return True
                else:
                    print(f"✗ Authentication failed: {data.get('message', 'Unknown error')}")
                    return False
            else:
                error_data = response.json() if response.headers.get('content-type', '').startswith('application/json') else {}
                print(f"✗ Authentication failed with status {response.status_code}: {error_data.get('message', 'Unknown error')}")
                return False

        except requests.exceptions.Timeout:
            print("✗ Authentication request timed out. Please check your connection.")
            return False
        except requests.exceptions.ConnectionError:
            print(f"✗ Could not connect to Mimikree server at {self.base_url}")
            print("   Make sure the Mimikree server is running locally.")
            return False
        except Exception as e:
            print(f"✗ Authentication error: {e}")
            return False

    def ask_batch_questions(self, questions: List[str], max_retries: int = 3) -> Dict:
        """Ask multiple questions to the user's Mimikree chatbot.

        Args:
            questions: List of questions to ask (max 20)
            max_retries: Maximum number of retry attempts on failure

        Returns:
            Dictionary with:
                - success: True if request succeeded
                - responses: List of response objects with question/answer pairs
                - total_questions: Total number of questions asked
                - successful_responses: Number of successful responses

        Raises:
            ValueError: If user is not authenticated or questions list is invalid
        """
        if not self.username:
            raise ValueError("Not authenticated. Call authenticate() first.")

        if not questions or not isinstance(questions, list):
            raise ValueError("Questions must be a non-empty list")

        if len(questions) > 20:
            raise ValueError("Maximum 20 questions allowed per request")

        for attempt in range(max_retries):
            try:
                print(f"📤 Sending {len(questions)} questions to Mimikree chatbot...")

                response = requests.post(
                    self.batch_questions_endpoint,
                    json={
                        'username': self.username,
                        'questions': questions
                    },
                    timeout=120  # 2 minutes timeout for batch processing
                )

                if response.status_code == 200:
                    data = response.json()
                    if data.get('success'):
                        successful = data.get('successfulResponses', 0)
                        total = data.get('totalQuestions', 0)
                        print(f"✓ Received {successful}/{total} responses from Mimikree")
                        return data
                    else:
                        print(f"✗ Request failed: {data.get('message', 'Unknown error')}")
                        return {
                            'success': False,
                            'message': data.get('message', 'Unknown error'),
                            'responses': []
                        }
                else:
                    error_data = response.json() if response.headers.get('content-type', '').startswith('application/json') else {}
                    print(f"✗ Request failed with status {response.status_code}: {error_data.get('message', 'Unknown error')}")

                    if attempt < max_retries - 1:
                        print(f"🔄 Retrying ({attempt + 1}/{max_retries - 1})...")
                        continue

                    return {
                        'success': False,
                        'message': error_data.get('message', f'Request failed with status {response.status_code}'),
                        'responses': []
                    }

            except requests.exceptions.Timeout:
                print(f"✗ Request timed out (attempt {attempt + 1}/{max_retries})")
                if attempt < max_retries - 1:
                    print("🔄 Retrying...")
                    continue
                return {
                    'success': False,
                    'message': 'Request timed out',
                    'responses': []
                }
            except requests.exceptions.ConnectionError:
                print(f"✗ Could not connect to Mimikree server at {self.base_url}")
                return {
                    'success': False,
                    'message': 'Connection error',
                    'responses': []
                }
            except Exception as e:
                print(f"✗ Error sending questions: {e}")
                if attempt < max_retries - 1:
                    print("🔄 Retrying...")
                    continue
                return {
                    'success': False,
                    'message': str(e),
                    'responses': []
                }

        return {
            'success': False,
            'message': 'Max retries exceeded',
            'responses': []
        }

    def extract_successful_answers(self, response_data: Dict) -> Dict[str, str]:
        """Extract successful question-answer pairs from response data.

        Args:
            response_data: Response data from ask_batch_questions()

        Returns:
            Dictionary mapping questions to answers (only successful responses)
        """
        if not response_data.get('success'):
            return {}

        answers = {}
        for response in response_data.get('responses', []):
            if response.get('success'):
                question = response.get('question', '')
                answer = response.get('answer', '')
                if question and answer:
                    answers[question] = answer

        return answers


def generate_questions_from_resume_and_jd(resume_text: str, job_description: str, max_questions: int = 10) -> List[str]:
    """Generate intelligent questions based on gaps between resume and job description.

    This function analyzes what's in the job description but missing from the resume,
    and generates targeted questions to ask the Mimikree chatbot.

    Args:
        resume_text: The user's resume text
        job_description: The job description text
        max_questions: Maximum number of questions to generate

    Returns:
        List of questions to ask
    """
    from google import genai

    try:
        client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))

        prompt = f"""Analyze this resume and job description to identify information gaps.

Generate specific, targeted questions to ask the candidate's AI assistant about skills, experiences,
or projects that:
1. Are mentioned in the job description
2. Are NOT clearly present in the resume
3. The candidate might have but didn't include

Focus on:
- Technical skills mentioned in JD but not in resume
- Project experience relevant to the role
- Specific tools/technologies the job requires
- Accomplishments that would be relevant

Generate ONLY questions that would help strengthen the resume for this specific job.
Return a JSON list of questions (max {max_questions}).

Format:
{{
    "questions": [
        "Do you have experience with [technology from JD]?",
        "Have you worked on any projects involving [skill from JD]?",
        ...
    ]
}}

RESUME:
{resume_text}

JOB DESCRIPTION:
{job_description}
"""

        response = client.models.generate_content(
            model="gemini-2.0-flash",
            contents=prompt
        )

        import json
        text = response.candidates[0].content.parts[0].text.strip()
        if text.startswith('```'):
            text = text.replace('```json', '').replace('```', '').strip()

        data = json.loads(text)
        questions = data.get('questions', [])

        # Limit to max_questions
        return questions[:max_questions]

    except Exception as e:
        print(f"Warning: Could not generate questions: {e}")
        # Return some default questions
        return [
            "What are your strongest technical skills?",
            "Do you have any projects or achievements not listed in your resume?",
            "What technologies or tools are you most experienced with?"
        ]


def enhance_resume_with_mimikree(resume_text: str, job_description: str,
                                  mimikree_email: str, mimikree_password: str) -> Optional[str]:
    """Complete workflow to enhance resume with Mimikree information.

    This function:
    1. Analyzes resume and job description to find gaps
    2. Generates targeted questions
    3. Authenticates with Mimikree
    4. Gets answers from Mimikree chatbot
    5. Returns additional information to incorporate

    Args:
        resume_text: The user's resume text
        job_description: The job description text
        mimikree_email: User's Mimikree account email
        mimikree_password: User's Mimikree account password

    Returns:
        String with additional information to incorporate into resume, or None if failed
    """
    try:
        # Initialize client
        client = MimikreeClient()

        # Authenticate
        print("\n🔐 Authenticating with Mimikree...")
        if not client.authenticate(mimikree_email, mimikree_password):
            print("❌ Authentication failed. Continuing without Mimikree enhancement.")
            return None

        # Generate questions
        print("\n💭 Analyzing resume gaps and generating questions...")
        questions = generate_questions_from_resume_and_jd(resume_text, job_description, max_questions=10)

        if not questions:
            print("⚠️  No questions generated. Continuing without Mimikree enhancement.")
            return None

        print(f"📋 Generated {len(questions)} questions:")
        for i, q in enumerate(questions, 1):
            print(f"   {i}. {q}")

        # Ask questions
        print("\n🤖 Querying Mimikree chatbot...")
        result = client.ask_batch_questions(questions)

        if not result.get('success'):
            print(f"❌ Failed to get responses: {result.get('message', 'Unknown error')}")
            return None

        # Extract answers
        answers = client.extract_successful_answers(result)

        if not answers:
            print("⚠️  No successful responses received.")
            return None

        print(f"\n✅ Received {len(answers)} answers from Mimikree:")

        # Format the additional information
        additional_info = "=== ADDITIONAL INFORMATION FROM MIMIKREE ===\n\n"
        additional_info += "This information was gathered from the user's Mimikree AI profile:\n\n"

        for question, answer in answers.items():
            additional_info += f"Q: {question}\n"
            additional_info += f"A: {answer}\n\n"

        print("\n📝 Additional information gathered:")
        print("-" * 60)
        print(additional_info)
        print("-" * 60)

        return additional_info

    except Exception as e:
        print(f"❌ Error during Mimikree enhancement: {e}")
        return None


# Example usage
if __name__ == "__main__":
    # Test authentication
    client = MimikreeClient()

    # Replace with test credentials
    test_email = "test@example.com"
    test_password = "testpassword"

    if client.authenticate(test_email, test_password):
        # Test questions
        test_questions = [
            "What programming languages are you most proficient in?",
            "Do you have experience with cloud platforms like AWS or Azure?",
            "Have you led any teams or projects?"
        ]

        result = client.ask_batch_questions(test_questions)

        if result.get('success'):
            answers = client.extract_successful_answers(result)

            print("\n=== ANSWERS ===")
            for question, answer in answers.items():
                print(f"\nQ: {question}")
                print(f"A: {answer}")
        else:
            print(f"Failed to get responses: {result.get('message')}")
    else:
        print("Authentication failed")
