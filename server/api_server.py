from flask import Flask, request, jsonify
import os
import sys
import requests
from google import genai
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
import json
from flask_cors import CORS
from typing import Dict, Any
import logging
import time
import base64
import uuid
import asyncio


sys.path.append(os.path.dirname(__file__))
sys.path.append(os.path.join(os.path.dirname(__file__), '..', 'Agents'))
sys.path.append(os.path.join(os.path.dirname(__file__), '..'))  # For logging_config
from resume_tailoring_agent import get_google_services, get_doc_id_from_url, copy_google_doc, read_google_doc_content, apply_json_replacements_to_doc, tailor_resume as tailor_resume_with_agent, tailor_resume_and_return_url
from job_application_agent import run_links_with_refactored_agent
from logging_config import setup_file_logging
from components.session.session_manager import SessionManager
from auth import AuthService, require_auth
from profile_service import ProfileService
from job_search_service import JobSearchService


#Initialize the app
app = Flask(__name__)
CORS(app)

JOBS: Dict[str, Dict[str, Any]] = {}
INTERVENTIONS: Dict[str, Dict[str, Any]] = {}  # Store intervention requests from job agents

# Initialize session manager with proper path
import os
session_storage_path = os.path.join(os.path.dirname(__file__), "sessions")
session_manager = SessionManager(session_storage_path)

def initialize_gemini():
    api_key = os.getenv('GOOGLE_API_KEY')
    return genai.Client(api_key=api_key)

def process_resume_with_llm(resume_text: str) -> Dict[str, Any]:
    client = initialize_gemini()
    profile_schema={
            "type": "object",
            "properties": {
                "first name": {"type": "string"},
                "last name": {"type": "string"},
                "email": {"type": "string"},
                "date of birth": {"type": "string"},
                "phone": {"type": "string"},
                "address": {"type": "string"},
                "city": {"type": "string"},
                "state": {"type": "string"},
                "zip": {"type": "string"},
                "country": {"type": "string"},
                "country_code": {"type": "string"},
                "state_code": {"type": "string"},
                "linkedin": {"type": "string"},
                "github": {"type": "string"},
                "other links": {
                    "type": "array",
                    "items": {"type": "string"}
                },
                "education": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "degree": {"type": "string"},
                            "institution": {"type": "string"},
                            "graduation_year": {"type": "string"},
                            "gpa": {"type": "string"},
                            "relevant_courses": {
                                "type": "array",
                                "items": {"type": "string"}
                            }
                        }
                    }
                },
                "work experience": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "title": {"type": "string"},
                            "company": {"type": "string"},
                            "start_date": {"type": "string"},
                            "end_date": {"type": "string"},
                            "description": {"type": "string"},
                            "achievements": {
                                "type": "array",
                                "items": {"type": "string"}
                            }
                        }
                    }
                },
                "projects": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string"},
                            "description": {"type": "string"},
                            "technologies": {
                                "type": "array",
                                "items": {"type": "string"}
                            },
                            "github_url": {"type": "string"},
                            "live_url": {"type": "string"},
                            "features": {
                                "type": "array",
                                "items": {"type": "string"}
                            }
                        }
                    }
                },
                "skills": {
                    "type": "object",
                    "properties": {
                        "technical": {
                            "type": "array",
                            "items": {"type": "string"}
                        },
                        "programming_languages": {
                            "type": "array",
                            "items": {"type": "string"}
                        },
                        "frameworks": {
                            "type": "array",
                            "items": {"type": "string"}
                        },
                        "tools": {
                            "type": "array",
                            "items": {"type": "string"}
                        },
                        "soft_skills": {
                            "type": "array",
                            "items": {"type": "string"}
                        },
                        "languages": {
                            "type": "array",
                            "items": {"type": "string"}
                        }
                    }
                },
                "summary": {"type": "string"}
            }
        }
    # Create a template JSON structure for Gemini to fill
    json_template = {
        "first name": "",
        "last name": "",
        "email": "",
        "phone": "",
        "address": "",
        "city": "",
        "state": "",
        "zip": "",
        "country": "",
        "country_code": "",
        "state_code": "",
        "linkedin": "",
        "github": "",
        "other links": [],
        "date of birth": "",
        "education": [
            {
                "degree": "",
                "institution": "",
                "graduation_year": "",
                "gpa": "",
                "relevant_courses": []
            }
        ],
        "work experience": [
            {
                "title": "",
                "company": "",
                "start_date": "",
                "end_date": "",
                "description": "",
                "achievements": []
            }
        ],
        "projects": [
            {
                "name": "",
                "description": "",
                "technologies": [],
                "github_url": "",
                "live_url": "",
                "features": []
            }
        ],
        "skills": {
            "technical": [],
            "programming_languages": [],
            "frameworks": [],
            "tools": [],
            "soft_skills": [],
            "languages": []
        },
        "summary": ""
    }

    prompt = f"""
    You are an expert at extracting information from resumes and structuring it into a standardized profile format.

    Please analyze the following resume text and extract the relevant information to populate the JSON template below.

    Resume Text:
    {resume_text}

    Fill in the following JSON template with the extracted information. Replace empty strings and empty arrays with actual data from the resume:

    {json.dumps(json_template, indent=2)}

    Instructions:
    1. Extract the person's name and split it into first name and last name
    2. Look for contact information (email, phone, address, city, state, zip, country)
    3. Extract education history - create multiple entries in the education array if there are multiple degrees
    4. Extract work experience - create multiple entries in the work experience array for each job
    5. Extract projects - create multiple entries in the projects array for each project
    6. Extract and categorize skills:
       - technical: Core technical skills (e.g., "Machine Learning", "Data Science", "Cloud Computing")
       - programming_languages: Programming languages (e.g., "Python", "JavaScript", "Java", "C++")
       - frameworks: Frameworks and libraries (e.g., "React", "Django", "TensorFlow", "Spring")
       - tools: Tools and technologies (e.g., "AWS", "Docker", "Git", "PostgreSQL", "MongoDB")
       - soft_skills: Soft skills (e.g., "Leadership", "Communication", "Problem Solving")
       - languages: Spoken languages with proficiency (e.g., "English (Native)", "Spanish (Fluent)")
    7. Create a professional summary (2-3 sentences highlighting key strengths)
    8. Look for LinkedIn, GitHub, and other professional links
    9. Use full official names for countries and states (e.g., "United States of America", "California")
    10. Leave fields empty if information is not available

    Return ONLY the filled JSON object (not an array). Do not include any markdown formatting, code blocks, or additional text.
    """

    print(f"Sending prompt to Gemini")

    try:
        response = client.models.generate_content(
            model="gemini-2.0-flash",
            contents=prompt,
            config={
                "response_mime_type": "application/json"
            }
        )
        
        print(f"Raw Gemini response: {response.text}")
        print(f"Response type: {type(response.text)}")
        print(f"Response length: {len(response.text)}")
        
        # Clean the response text to extract JSON
        response_text = response.text.strip()
        print(f"Cleaned response text: {response_text[:200]}...")  # First 200 chars
        
        # Remove any markdown formatting if present
        if response_text.startswith('```json'):
            response_text = response_text[7:]
            print("Removed ```json prefix")
        if response_text.endswith('```'):
            response_text = response_text[:-3]
            print("Removed ``` suffix")
        if response_text.startswith('```'):
            response_text = response_text[3:]
            print("Removed ``` prefix")
            
        response_text = response_text.strip()
        print(f"Final cleaned text: {response_text[:200]}...")  # First 200 chars
        
        try:
            profile_data = json.loads(response_text)
            print(f"Successfully parsed JSON")
            print(f"Parsed data type: {type(profile_data)}")
            print(f"Parsed data keys: {list(profile_data.keys()) if isinstance(profile_data, dict) else 'Not a dict'}")
            
            # Handle case where Gemini returns an array instead of object
            if isinstance(profile_data, list) and len(profile_data) > 0:
                print("Gemini returned an array, extracting first element")
                profile_data = profile_data[0]
                print(f"Extracted object keys: {list(profile_data.keys())}")
            elif not isinstance(profile_data, dict):
                print(f"ERROR: Expected dict or list, got {type(profile_data)}")
                return None
                
            print(f"Final profile data: {profile_data}")
            validated_data = _validate_profile_data(profile_data, profile_schema)
            print(f"Validated profile data: {validated_data}")
            return validated_data
        except json.JSONDecodeError as json_err:
            print(f"JSON parsing error: {json_err}")
            print(f"Response text that failed to parse: {response_text}")
            return None
    except Exception as e:
        print(f"Error sending prompt to Gemini: {e}")
        return None

def _validate_profile_data(profile_data: Dict[str, Any], schema: Dict[str, Any]) -> Dict[str, Any]:
    """Validate and clean the profile data to match the schema"""
    # Extract the actual properties from the JSON schema
    if "properties" in schema:
        schema_properties = schema["properties"]
    else:
        schema_properties = schema
    
    validated_data = {}
    
    # Define default values for each field type
    default_values = {
        "first name": "",
        "last name": "",
        "email": "",
        "date of birth": "",
        "phone": "",
        "address": "",
        "city": "",
        "state": "",
        "zip": "",
        "country": "",
        "country_code": "",
        "state_code": "",
        "linkedin": "",
        "github": "",
        "other links": [],
        "education": [],
        "work experience": [],
        "projects": [],
        "skills": {
            "technical": [],
            "programming_languages": [],
            "frameworks": [],
            "tools": [],
            "soft_skills": [],
            "languages": []
        },
        "summary": ""
    }
    
    for key, default_value in default_values.items():
        if key in profile_data:
            value = profile_data[key]
            
            # Handle None values
            if value is None:
                validated_data[key] = default_value
            # Validate based on expected type
            elif isinstance(default_value, str):
                validated_data[key] = str(value) if value else ""
            elif isinstance(default_value, list):
                if isinstance(value, list):
                    validated_data[key] = value
                else:
                    validated_data[key] = []
            elif isinstance(default_value, dict):
                if isinstance(value, dict):
                    validated_data[key] = value
                else:
                    validated_data[key] = default_value
            else:
                validated_data[key] = value
        else:
            validated_data[key] = default_value
    
    return validated_data

def extract_resume_text(resume_url: str) -> str:
    try:
        print(f"Starting to extract text from: {resume_url}")
        from urllib.parse import urlparse, parse_qs
        #Extract the document ID from the URL
        parsed_url = urlparse(resume_url)
        print(f"Parsed URL: {parsed_url}")

        if 'docs.google.com' not in parsed_url.netloc:
            print(f"Invalid Google Docs URL: {resume_url}")
            raise ValueError("Invalid Google Docs URL")

       # Extract document ID from path
        path_parts = parsed_url.path.split('/')
        print(f"Path parts: {path_parts}")

        if 'document' not in path_parts or 'd' not in path_parts:
            print(f"Invalid Google Docs URL format: {resume_url}")
            raise ValueError("Invalid Google Docs URL format")
        
        doc_id = None
        for i, part in enumerate(path_parts):
            if part == 'd' and i + 1 < len(path_parts):
                doc_id = path_parts[i + 1]
                break
        
        print(f"Document ID: {doc_id}")
        

        if not doc_id:
            print(f"Could not extract document ID from URL: {resume_url}")
            raise ValueError("Could not extract document ID from URL")
        
        # Convert to export URL for plain text
        export_url = f"https://docs.google.com/document/d/{doc_id}/export?format=txt"
        print(f"Export URL: {export_url}")

        # Make request to get the document content
        response = requests.get(export_url, timeout=30)
        print(f"Response: {response}")
        
        # Handle specific error cases
        if response.status_code == 401:
            raise ValueError("Google Doc is not publicly accessible. Please set sharing to 'Anyone with the link can view' in Google Docs.")
        elif response.status_code == 403:
            raise ValueError("Access denied to Google Doc. Please check sharing permissions.")
        elif response.status_code == 404:
            raise ValueError("Google Doc not found. Please check the URL.")
        
        response.raise_for_status()
        
        # Return the text content
        return response.text.strip()
    except Exception as e:
        logging.error(f"Error fetching Google Doc: {e}")
        if "401" in str(e):
            raise ValueError("Google Doc is not publicly accessible. Please set sharing to 'Anyone with the link can view' in Google Docs.")
        elif "403" in str(e):
            raise ValueError("Access denied to Google Doc. Please check sharing permissions.")
        elif "404" in str(e):
            raise ValueError("Google Doc not found. Please check the URL.")
        else:
            raise ValueError(f"Could not access Google Doc: {str(e)}")


@app.route("/api/profile", methods=["GET"])
@require_auth
def get_profile():
    """Get user profile data from PostgreSQL"""
    try:
        user_id = request.current_user['id']
        result = ProfileService.get_complete_profile(user_id)

        if result['success']:
            return jsonify({
                "resumeData": result['profile'],
                "resume_url": result['profile'].get("resume_url", ""),
                "success": True,
                "message": "Profile fetched successfully",
                "error": None
            }), 200
        else:
            return jsonify({
                "error": result['error'],
                "success": False
            }), 404

    except Exception as e:
        logging.error(f"Error getting profile: {e}")
        return jsonify({"error": f"Failed to get profile: {str(e)}"}), 500

@app.route("/api/profile", methods=["POST"])
@require_auth
def save_profile():
    """Save user profile data to PostgreSQL"""
    try:
        user_id = request.current_user['id']
        profile_data = request.json

        if not profile_data:
            return jsonify({"error": "No profile data provided"}), 400

        # Save to PostgreSQL
        result = ProfileService.create_or_update_profile(user_id, profile_data)

        if result['success']:
            return jsonify({
                "success": True,
                "message": "Profile saved successfully to database"
            }), 200
        else:
            return jsonify({
                "error": result['error'],
                "success": False
            }), 500

    except Exception as e:
        logging.error(f"Error saving profile: {e}")
        return jsonify({"error": f"Failed to save profile: {str(e)}"}), 500

@app.route("/api/process-resume", methods=['POST'])
def process_resume():
    try:
        # Add debugging
        print(f"Request JSON: {request.json}")
        print(f"Request headers: {request.headers}")

        resume_url = request.json['resume_url']
        resume_text = extract_resume_text(resume_url)

        if not resume_text:
            return jsonify({"error": "Failed to extract resume text"}), 400
        
        logging.info(f"Processing resume with LLM: {resume_text}")
        profile_data = process_resume_with_llm(resume_text)
        
        if profile_data is None:
            return jsonify({
                "error": "Failed to process resume with Gemini",
                "success": False
            }), 500
        
        print(f"Returning profile_data: {profile_data}")

        # If user is authenticated, persist resume_url on their profile
        try:
            token = request.headers.get('Authorization')
            if token and token.startswith('Bearer '):
                token = token[7:]
            from auth import AuthService
            user = AuthService.get_user_from_token(token) if token else None
            if user:
                from profile_service import ProfileService
                # Save only resume_url without touching user fields
                ProfileService.create_or_update_profile(user['id'], { 'resume_url': resume_url })
        except Exception as persist_err:
            logging.warning(f"Could not persist resume_url: {persist_err}")

        return jsonify({
            "profile_data": profile_data,
            "success": True,
            "message": "Resume processed successfully",
            'error': None
            }), 200

    except Exception as e:
        logging.error(f"Error processing resume: {e}")
        return jsonify({"error": str(e)}), 500

# ---------------- Action History Endpoints ----------------
@app.route("/api/action-history", methods=["POST"])
@require_auth
def save_action_history_api():
    try:
        user_id = request.current_user['id']
        data = request.json or {}
        job_id = data.get('job_id')
        action_log = data.get('action_log')
        if not job_id or action_log is None:
            return jsonify({ 'success': False, 'error': 'job_id and action_log are required' }), 400
        result = ProfileService.save_action_history(user_id, job_id, action_log)
        status = 200 if result.get('success') else 500
        return jsonify(result), status
    except Exception as e:
        logging.error(f"Error saving action history: {e}")
        return jsonify({ 'success': False, 'error': 'Failed to save action history' }), 500

@app.route("/api/action-history", methods=["GET"])
@require_auth
def get_action_history_api():
    try:
        user_id = request.current_user['id']
        job_id = request.args.get('job_id')
        if not job_id:
            return jsonify({ 'success': False, 'error': 'job_id is required' }), 400
        result = ProfileService.get_action_history(user_id, job_id)
        status = 200 if result.get('success') else 500
        return jsonify(result), status
    except Exception as e:
        logging.error(f"Error fetching action history: {e}")
        return jsonify({ 'success': False, 'error': 'Failed to fetch action history' }), 500

@app.route("/api/action-history", methods=["DELETE"])
@require_auth
def delete_action_history_api():
    try:
        user_id = request.current_user['id']
        data = request.json or {}
        job_id = data.get('job_id')
        if not job_id:
            return jsonify({ 'success': False, 'error': 'job_id is required' }), 400
        result = ProfileService.mark_action_history_completed(user_id, job_id)
        status = 200 if result.get('success') else 500
        return jsonify(result), status
    except Exception as e:
        logging.error(f"Error deleting action history: {e}")
        return jsonify({ 'success': False, 'error': 'Failed to delete action history' }), 500
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        logging.error(f"Error processing resume: {str(e)}")
        return jsonify({"error": str(e)}), 500

@app.route("/api/search-jobs", methods=['POST'])
@require_auth
def search_jobs():
    """ Search for jobs using Job Discovery Agent and save to PostgreSQL"""
    try:
        import sys
        sys.path.append(os.path.join(os.path.dirname(__file__), '..'))
        from Agents.job_discovery_agent import JobDiscoveryAgent

        user_id = request.current_user['id']
        job_discovery_agent = JobDiscoveryAgent(user_id=user_id)

        if not job_discovery_agent.profile_data:
            return jsonify({"error": "Profile data not found for this user"}), 400

        
        logging.info("Searching for jobs...")
        response = job_discovery_agent.search_jobs_with_rapidapi()

        if 'error' in response:
            return jsonify({"error": response['error']}), 500

        # Save job listings to PostgreSQL
        jobs_data = response.get('data', [])
        if jobs_data:
            logging.info(f"Saving {len(jobs_data)} job listings to database...")
            save_result = JobSearchService.save_job_listings(user_id, jobs_data, "rapidapi")
            
            if not save_result['success']:
                logging.warning(f"Failed to save some job listings: {save_result.get('error', 'Unknown error')}")
            else:
                logging.info(f"Saved {save_result['saved_count']} new jobs, updated {save_result['updated_count']} existing jobs")

        return jsonify({
            "jobs": jobs_data,
            "total_found": response['count'],
            "success": True,
            "message": "Jobs searched successfully and saved to database",
            "error": None
            }), 200
    except Exception as e:
        logging.error(f"Error searching for jobs: {str(e)}")
        return jsonify({"error": str(e)}), 500

@app.route("/api/job-listings", methods=['GET'])
@require_auth
def get_job_listings():
    """Get saved job listings from PostgreSQL database"""
    try:
        limit = request.args.get('limit', 100, type=int)
        offset = request.args.get('offset', 0, type=int)
        
        result = JobSearchService.get_job_listings(limit=limit, offset=offset)
        
        if result['success']:
            return jsonify({
                "jobs": result['jobs'],
                "total_count": result['total_count'],
                "limit": result['limit'],
                "offset": result['offset'],
                "success": True,
                "message": "Job listings retrieved successfully"
            }), 200
        else:
            return jsonify({"error": result['error']}), 500
            
    except Exception as e:
        logging.error(f"Error getting job listings: {str(e)}")
        return jsonify({"error": str(e)}), 500

@app.route("/api/recent-job-listings", methods=['GET'])
@require_auth
def get_recent_job_listings():
    """Get recently added job listings from PostgreSQL database"""
    try:
        hours = request.args.get('hours', 24, type=int)
        limit = request.args.get('limit', 50, type=int)
        
        result = JobSearchService.get_recent_job_listings(hours=hours, limit=limit)
        
        if result['success']:
            return jsonify({
                "jobs": result['jobs'],
                "count": result['count'],
                "hours": result['hours'],
                "success": True,
                "message": "Recent job listings retrieved successfully"
            }), 200
        else:
            return jsonify({"error": result['error']}), 500
            
    except Exception as e:
        logging.error(f"Error getting recent job listings: {str(e)}")
        return jsonify({"error": str(e)}), 500

@app.route("/api/tailor-resume", methods=['POST'])
def tailor_resume():
    """Tailor resume for a specific job using the resume tailor agent"""
    try:
        data = request.json
        job_id = data.get('job_id')
        job_description = data.get('job_description')
        company_name = data.get('company_name')
        resume_url = data.get('resume_url')

        if not job_description or not resume_url:
            return jsonify({"error": "Job description and resume URL are required"}), 400
        
        try:
            # Use the resume tailor agent to create tailored Google Doc
            tailored_url = tailor_resume_and_return_url(resume_url, job_description, job_id, company_name)
            return jsonify({
                "tailored_document_id": tailored_url,
                "success": True,
                "message": "Resume tailored successfully",
                "error": None
                }), 200
        except Exception as e:
            logging.error(f"Error tailoring resume: {str(e)}")
            return jsonify({"error": str(e)}), 500
    except Exception as e:
        logging.error(f"Error tailoring resume: {str(e)}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/apply-job", methods=['POST'])
def apply_job():
    try:
        data = request.json
        if not data:
            logging.error("No data provided in request")
            return jsonify({"error": "No data provided"}), 400
        
        job_url = data.get('jobUrl', '')
        resume_url = data.get('resumeUrl', '')
        use_tailored = data.get('useTailored', False)
        tailored_resume_url = data.get('tailoredResumeUrl', '')

        logging.info(f"Received payload - job_url: {job_url}, resume_url: {resume_url}, use_tailored: {use_tailored}, tailored_resume_url: {tailored_resume_url}")

        if not job_url or not resume_url:
            logging.error(f"Missing required fields - job_url: {bool(job_url)}, resume_url: {bool(resume_url)}")
            return jsonify({"error": "Job URL and resume URL are required"}), 400
        
        # Determine which resume to use
        final_resume_url = tailored_resume_url if use_tailored and tailored_resume_url else resume_url

        logging.info(f"Starting job application for: {job_url}")
        logging.info(f"Using resume: {resume_url}")

        import uuid
        import asyncio
        import time
        from concurrent.futures import ThreadPoolExecutor

        # Create an unique job id
        job_id = str(uuid.uuid4())

        # Store the job in the JOBS dictionary
        JOBS[job_id] = {
            "job_url": job_url,
            "resumeUrl": resume_url,
            "status": "queued",
            "links" : [job_url],
            "logs" : [],
            "created_at" : time.time(),
        }

        logging.info(f"Job {job_id} stored in JOBS dictionary")
        logging.info(f"JOBS keys: {list(JOBS.keys())}")

        # Define the job application function
        async def run_job_application():
            try:
                # Check if job still exists
                if job_id not in JOBS:
                    logging.error(f"Job {job_id} not found when starting execution")
                    return

                # Update status to running
                JOBS[job_id]["status"] = "running"
                JOBS[job_id]["logs"].append({
                    "timestamp": time.time(),
                    "level": "info",
                    "message": f"Starting job application for: {job_url}"
                })

                logging.info(f"Job {job_id} starting execution")

                # Run the refactored job agent with better error handling
                try:
                    await run_links_with_refactored_agent(
                        links=[job_url],
                        headless=True,   # headless mode for background processing
                        keep_open=False,  # don't keep open to avoid hanging processes
                        debug=False,     # no debug mode for background processing
                        hold_seconds=2,   # reduced hold time
                        slow_mo_ms=0,     # no slow motion for background processing
                        job_id=job_id,    # pass job_id for intervention notifications
                        jobs_dict=JOBS,   # pass shared JOBS dictionary for logging
                        session_manager=session_manager  # pass session manager for freezing
                    )
                    
                    # Update status to completed
                    if job_id in JOBS:
                        JOBS[job_id]["status"] = "completed"
                        JOBS[job_id]["logs"].append({
                            "timestamp": time.time(),
                            "level": "success",
                            "message": "Job application completed successfully"
                        })

                        # Also update session status if session exists
                        session_id = JOBS[job_id].get('session_id')
                        if session_id:
                            session_manager.update_session(session_id, status="completed")
                            logging.info(f"Updated session {session_id} status to completed")

                        logging.info(f"Job {job_id} completed successfully")
                    
                except Exception as e:
                    # Handle agent-specific errors
                    if job_id in JOBS:
                        JOBS[job_id]["status"] = "failed"
                        JOBS[job_id]["logs"].append({
                            "timestamp": time.time(),
                            "level": "error",
                            "message": f"Job agent failed: {str(e)}"
                        })

                        # Also update session status if session exists
                        session_id = JOBS[job_id].get('session_id')
                        if session_id:
                            session_manager.update_session(session_id, status="failed")
                            logging.info(f"Updated session {session_id} status to failed")

                    logging.error(f"Job agent error for {job_id}: {e}")
                    raise
            
            except Exception as e:
                # Update status to failed
                if job_id in JOBS:
                    JOBS[job_id]["status"] = "failed"
                    JOBS[job_id]["logs"].append({
                        "timestamp": time.time(),
                        "level": "error",
                        "message": f"Job application failed: {str(e)}"
                    })

                    # Also update session status if session exists
                    session_id = JOBS[job_id].get('session_id')
                    if session_id:
                        session_manager.update_session(session_id, status="failed")
                        logging.info(f"Updated session {session_id} status to failed")

                logging.error(f"Error in job application {job_id}: {e}")
                import traceback
                logging.error(f"Full traceback: {traceback.format_exc()}")

        # Submit the job to thread pool
        def run_async_job():
            try:
                logging.info(f"Starting async job thread for {job_id}")
                asyncio.run(run_job_application())
            except Exception as e:
                logging.error(f"Error running async job {job_id}: {e}")
                if job_id in JOBS:
                    JOBS[job_id]["status"] = "failed"
                    JOBS[job_id]["logs"].append({
                        "timestamp": time.time(),
                        "level": "error", 
                        "message": f"Failed to start job: {str(e)}"
                    })
                import traceback
                logging.error(f"Thread error traceback: {traceback.format_exc()}")
        
        # Start the job in a separate thread
        import threading
        job_thread = threading.Thread(target=run_async_job, name=f"job-{job_id[:8]}")
        job_thread.daemon = True
        job_thread.start()
        
        logging.info(f"Job thread started for {job_id}")
        
        job_response = {"job_id": job_id, "status": "queued"}
        
        return jsonify({
            "success": True,
            "job_id": job_response.get('job_id'),
            "message": "Job application started successfully"
        }), 200
        
    except Exception as e:
        logging.error(f"Error starting job application: {e}")
        return jsonify({"error": f"Failed to start job application: {str(e)}"}), 500

@app.route("/api/job-logs/<job_or_session_id>", methods=['GET'])
def get_job_logs(job_or_session_id):
    """Get job logs for a specific job or session"""
    try:
        # First try to find by job_id directly
        if job_or_session_id in JOBS:
            return jsonify(JOBS[job_or_session_id].get('logs', [])), 200

        # If not found, try to find job by session_id
        for job_id, job_data in JOBS.items():
            if job_data.get('session_id') == job_or_session_id:
                return jsonify(job_data.get('logs', [])), 200

        # If still not found, return empty logs
        return jsonify([]), 200
    except Exception as e:
        logging.error(f"Error getting job logs for {job_or_session_id}: {e}")
        return jsonify({"error": f"Failed to get job logs: {str(e)}"}), 500

@app.route("/api/job-status/<job_or_session_id>", methods=['GET'])
def get_job_status(job_or_session_id):
    """Get job status and details for a specific job or session"""
    try:
        job_data = None
        actual_job_id = job_or_session_id

        # First try to find by job_id directly
        if job_or_session_id in JOBS:
            job_data = JOBS[job_or_session_id]
        else:
            # If not found, try to find job by session_id
            for job_id, data in JOBS.items():
                if data.get('session_id') == job_or_session_id:
                    job_data = data
                    actual_job_id = job_id
                    break

        if not job_data:
            return jsonify({"error": "Job not found"}), 404

        return jsonify({
            "job_id": actual_job_id,
            "status": job_data.get('status', 'unknown'),
            "job_url": job_data.get('job_url', ''),
            "created_at": job_data.get('created_at', 0),
            "logs_count": len(job_data.get('logs', [])),
            "last_updated": job_data.get('last_updated', job_data.get('created_at', 0))
        }), 200
    except Exception as e:
        logging.error(f"Error getting job status for {job_or_session_id}: {e}")
        return jsonify({"error": f"Failed to get job status: {str(e)}"}), 500

@app.route("/api/resume-job/<job_id>", methods=['POST'])
def resume_job(job_id):
    """Resume a job that requires human intervention"""
    try:
        if job_id not in JOBS:
            return jsonify({"error": "Job not found"}), 404
        
        job_data = JOBS[job_id]
        
        # Check if job is actually in intervention state
        if job_data.get('status') != 'intervention':
            return jsonify({"error": f"Job is not in intervention state. Current status: {job_data.get('status')}"}), 400
        
        # Update job status to running
        JOBS[job_id]['status'] = 'running'
        JOBS[job_id]['last_updated'] = time.time()
        JOBS[job_id]['logs'].append({
            "timestamp": time.time(),
            "level": "info",
            "message": "Human intervention resolved - Resuming job application"
        })
        
        logging.info(f"Job {job_id} resumed from intervention")
        
        # Note: The actual resumption of the job agent process would need to be implemented
        # based on how the intervention mechanism works in the job_application_agent.py
        # For now, we'll just update the status and let the agent continue
        
        return jsonify({
            "success": True,
            "message": "Job resumed successfully",
            "job_id": job_id,
            "status": "running"
        }), 200
        
    except Exception as e:
        logging.error(f"Error resuming job {job_id}: {e}")
        return jsonify({"error": f"Failed to resume job: {str(e)}"}), 500


# Authentication API Routes

@app.route("/api/auth/signup", methods=['POST'])
def signup():
    """User registration endpoint"""
    try:
        data = request.json
        if not data:
            return jsonify({"error": "No data provided"}), 400

        # Validate required fields
        required_fields = ['email', 'password', 'firstName', 'lastName']
        for field in required_fields:
            if not data.get(field):
                return jsonify({"error": f"{field} is required"}), 400

        email = data['email'].strip().lower()
        password = data['password']
        first_name = data['firstName'].strip()
        last_name = data['lastName'].strip()

        # Basic validation
        if len(password) < 6:
            return jsonify({"error": "Password must be at least 6 characters long"}), 400

        if '@' not in email:
            return jsonify({"error": "Please provide a valid email address"}), 400

        # Register user
        result = AuthService.register_user(email, password, first_name, last_name)

        if result['success']:
            return jsonify(result), 201
        else:
            return jsonify(result), 400

    except Exception as e:
        logging.error(f"Error in signup endpoint: {e}")
        return jsonify({"error": "Registration failed. Please try again."}), 500

@app.route("/api/auth/login", methods=['POST'])
def login():
    """User login endpoint"""
    try:
        data = request.json
        if not data:
            return jsonify({"error": "No data provided"}), 400

        # Validate required fields
        email = data.get('email', '').strip().lower()
        password = data.get('password', '')

        if not email or not password:
            return jsonify({"error": "Email and password are required"}), 400

        # Authenticate user
        result = AuthService.authenticate_user(email, password)

        if result['success']:
            return jsonify(result), 200
        else:
            return jsonify(result), 401

    except Exception as e:
        logging.error(f"Error in login endpoint: {e}")
        return jsonify({"error": "Login failed. Please try again."}), 500

@app.route("/api/auth/verify", methods=['GET'])
@require_auth
def verify_token():
    """Verify JWT token and return user info"""
    try:
        # User info is already available from the @require_auth decorator
        return jsonify({
            "success": True,
            "user": request.current_user,
            "message": "Token is valid"
        }), 200
    except Exception as e:
        logging.error(f"Error in verify token endpoint: {e}")
        return jsonify({"error": "Token verification failed"}), 500

@app.route("/api/auth/logout", methods=['POST'])
@require_auth
def logout():
    """Logout user (client-side token removal)"""
    try:
        # For JWT tokens, logout is mainly client-side token removal
        # In a production system, you might want to maintain a blacklist
        return jsonify({
            "success": True,
            "message": "Logged out successfully"
        }), 200
    except Exception as e:
        logging.error(f"Error in logout endpoint: {e}")
        return jsonify({"error": "Logout failed"}), 500

@app.route("/api/health", methods=['GET'])
def health():
    return jsonify({"status": "ok"})

# Session Management API Routes

@app.route("/api/sessions/dashboard", methods=['GET'])
def get_dashboard_data():
    """Get dashboard data with session statistics"""
    try:
        dashboard_data = session_manager.get_dashboard_data()
        
        # Debug: Check for coroutine objects before JSON serialization
        import json
        json.dumps(dashboard_data)  # This will throw the exact error if there's a coroutine
        
        return jsonify(dashboard_data), 200
    except Exception as e:
        logging.error(f"Error getting dashboard data: {e}")
        logging.error(f"Dashboard data type: {type(dashboard_data)}")
        
        # Try to identify the problematic object
        if isinstance(dashboard_data, dict) and 'sessions' in dashboard_data:
            for i, session in enumerate(dashboard_data['sessions']):
                try:
                    json.dumps(session)
                except Exception as session_error:
                    logging.error(f"Session {i} serialization error: {session_error}")
                    logging.error(f"Problematic session keys: {list(session.keys())}")
                    for key, value in session.items():
                        try:
                            json.dumps({key: value})
                        except Exception as key_error:
                            logging.error(f"Problematic key '{key}': {type(value)} - {key_error}")
        
        return jsonify({"error": f"Failed to get dashboard data: {str(e)}"}), 500

@app.route("/api/sessions", methods=['GET'])
def get_all_sessions():
    """Get all sessions"""
    try:
        sessions = session_manager.get_all_sessions()
        return jsonify([session.to_dict() for session in sessions]), 200
    except Exception as e:
        logging.error(f"Error getting sessions: {e}")
        return jsonify({"error": f"Failed to get sessions: {str(e)}"}), 500

@app.route("/api/sessions/<session_id>", methods=['GET'])
def get_session(session_id):
    """Get specific session by ID"""
    try:
        session = session_manager.get_session(session_id)
        if not session:
            return jsonify({"error": "Session not found"}), 404
        return jsonify(session.to_dict()), 200
    except Exception as e:
        logging.error(f"Error getting session {session_id}: {e}")
        return jsonify({"error": f"Failed to get session: {str(e)}"}), 500

@app.route("/api/sessions/<session_id>/resume", methods=['POST'])
def resume_session_api(session_id):
    """Resume a frozen session by opening browser"""
    try:
        session = session_manager.get_session(session_id)
        if not session:
            return jsonify({"error": "Session not found"}), 404
        
        # Create a new job for resuming this session
        job_id = str(uuid.uuid4())
        
        # Store the job with resume information
        JOBS[job_id] = {
            "session_id": session_id,
            "job_url": session.job_url,
            "resumeUrl": "",
            "status": "queued",
            "links": [session.job_url],
            "logs": [],
            "created_at": time.time(),
            "resume_mode": True  # Flag to indicate this is a resume operation
        }
        
        logging.info(f"Created resume job {job_id} for session {session_id}")
        
        # Define the resume job function
        async def run_resume_job():
            try:
                if job_id not in JOBS:
                    logging.error(f"Resume job {job_id} not found when starting execution")
                    return

                # Update status to running
                JOBS[job_id]["status"] = "running"
                JOBS[job_id]["logs"].append({
                    "timestamp": time.time(),
                    "level": "info",
                    "message": f"Resuming session for: {session.job_url}"
                })

                logging.info(f"Resume job {job_id} starting execution")

                # Create a simple agent just for resuming
                from playwright.async_api import async_playwright

                JOBS[job_id]["logs"].append({
                    "timestamp": time.time(),
                    "level": "info",
                    "message": "🔄 Preparing to resume session... Please wait while we restore your progress."
                })

                p = await async_playwright().start()
                try:
                    # Open visible browser and replay actions directly (user can see progress)
                    JOBS[job_id]["logs"].append({
                        "timestamp": time.time(),
                        "level": "info",
                        "message": "🎬 Opening browser and replaying your form progress..."
                    })

                    # Create visible browser for action replay
                    visible_browser = await p.chromium.launch(headless=False, slow_mo=100)
                    visible_context = await visible_browser.new_context()
                    visible_page = await visible_context.new_page()

                    # Resume the session using action replay in VISIBLE browser
                    try:
                        success = await asyncio.wait_for(
                            session_manager.resume_session_with_actions(session_id, visible_page),
                            timeout=120  # 2 minutes timeout for action replay
                        )
                    except asyncio.TimeoutError:
                        JOBS[job_id]["logs"].append({
                            "timestamp": time.time(),
                            "level": "error",
                            "message": "⏰ Action replay timed out. The session may have too many actions or encountered an issue."
                        })
                        success = False

                    if not success:
                        await visible_browser.close()
                        JOBS[job_id]["status"] = "failed"
                        JOBS[job_id]["logs"].append({
                            "timestamp": time.time(),
                            "level": "error",
                            "message": "❌ Failed to replay actions. Please try again or start a new application."
                        })
                        return

                    JOBS[job_id]["logs"].append({
                        "timestamp": time.time(),
                        "level": "success",
                        "message": "✅ Form restored! You can now review and submit your application."
                    })

                    # Use the visible page for user interaction
                    page = visible_page
                    browser = visible_browser
                    
                    if success:
                        # Check if we landed on an authentication page
                        current_url = page.url.lower()
                        is_auth_page = any(auth_keyword in current_url for auth_keyword in ['login', 'auth', 'sign-in', 'signin'])
                        
                        if is_auth_page:
                            JOBS[job_id]["status"] = "requires_auth"
                            JOBS[job_id]["logs"].append({
                                "timestamp": time.time(),
                                "level": "warning", 
                                "message": f"⚠️ Authentication required! Please log in at: {page.url}"
                            })
                            logging.warning(f"Session {session_id} resumed but requires authentication at: {page.url}")
                            session_manager.update_session(session_id, status="requires_authentication")
                        else:
                            JOBS[job_id]["status"] = "resumed"
                            JOBS[job_id]["logs"].append({
                                "timestamp": time.time(),
                                "level": "success",
                                "message": "✅ Session resumed! Form should be pre-filled. Browser is open for you to continue."
                            })
                            logging.info(f"Session {session_id} resumed successfully with form restoration")
                        
                        # Keep browser open for manual completion
                        logging.info(f"Session {session_id} - keeping browser open for manual completion")

                        JOBS[job_id]["logs"].append({
                            "timestamp": time.time(),
                            "level": "success",
                            "message": "🎉 Browser is now ready! All your previous progress has been restored. You can continue your application."
                        })

                        # Keep browser open until user closes it
                        try:
                            while not page.is_closed():
                                await asyncio.sleep(5)
                        except Exception:
                            pass
                    else:
                        JOBS[job_id]["status"] = "failed"
                        JOBS[job_id]["logs"].append({
                            "timestamp": time.time(),
                            "level": "error",
                            "message": "Failed to resume session"
                        })
                        
                finally:
                    await p.stop()
                    
            except Exception as e:
                if job_id in JOBS:
                    JOBS[job_id]["status"] = "failed"
                    JOBS[job_id]["logs"].append({
                        "timestamp": time.time(),
                        "level": "error",
                        "message": f"Resume failed: {str(e)}"
                    })
                logging.error(f"Resume job {job_id} failed: {e}")

        # Start the resume job in background
        import asyncio
        from concurrent.futures import ThreadPoolExecutor
        
        def start_resume_task():
            asyncio.run(run_resume_job())
        
        executor = ThreadPoolExecutor()
        executor.submit(start_resume_task)
        
        return jsonify({
            "success": True,
            "message": f"Session {session_id} is being resumed. Browser will open shortly.",
            "job_id": job_id,
            "session": session.to_dict()
        }), 200
        
    except Exception as e:
        logging.error(f"Error resuming session {session_id}: {e}")
        return jsonify({"error": f"Failed to resume session: {str(e)}"}), 500

@app.route("/api/sessions/<session_id>", methods=['DELETE'])
def delete_session(session_id):
    """Delete a session"""
    try:
        success = session_manager.delete_session(session_id)
        if not success:
            return jsonify({"error": "Session not found or failed to delete"}), 404
        
        return jsonify({"success": True, "message": f"Session {session_id} deleted"}), 200
    except Exception as e:
        logging.error(f"Error deleting session {session_id}: {e}")
        return jsonify({"error": f"Failed to delete session: {str(e)}"}), 500

@app.route("/api/sessions/<session_id>/mark-complete", methods=['POST'])
def mark_session_complete(session_id):
    """Mark a session as manually completed"""
    try:
        session = session_manager.get_session(session_id)
        if not session:
            return jsonify({"error": "Session not found"}), 404
        
        # Update session status to manually_completed
        session_manager.update_session(session_id, status="manually_completed")
        
        return jsonify({
            "success": True, 
            "message": f"Session {session_id} marked as manually completed",
            "session": session.to_dict()
        }), 200
        
    except Exception as e:
        logging.error(f"Error marking session {session_id} as complete: {e}")
        return jsonify({"error": f"Failed to mark session as complete: {str(e)}"}), 500

@app.route("/api/sessions/<session_id>/screenshot", methods=['GET'])
def get_session_screenshot(session_id):
    """Get session screenshot"""
    try:
        session = session_manager.get_session(session_id)
        if not session or not session.screenshot_path:
            return jsonify({"error": "Screenshot not found"}), 404
        
        if not os.path.exists(session.screenshot_path):
            return jsonify({"error": "Screenshot file not found"}), 404
        
        # Return the screenshot as base64
        with open(session.screenshot_path, 'rb') as f:
            screenshot_data = base64.b64encode(f.read()).decode('utf-8')
        
        return jsonify({
            "screenshot": f"data:image/png;base64,{screenshot_data}"
        }), 200
    except Exception as e:
        logging.error(f"Error getting screenshot for session {session_id}: {e}")
        return jsonify({"error": f"Failed to get screenshot: {str(e)}"}), 500

@app.route("/api/sessions/batch-apply", methods=['POST'])
def batch_apply():
    """Start batch application process"""   
    try:
        data = request.json
        if not data:
            return jsonify({"error": "No data provided"}), 400
        
        job_urls = data.get('jobUrls', [])
        if not job_urls:
            return jsonify({"error": "No job URLs provided"}), 400
        
        # Create sessions for each job
        created_sessions = []
        for job_url in job_urls:
            session = session_manager.create_session(job_url)
            created_sessions.append(session.to_dict())
        
        logging.info(f"Created {len(created_sessions)} sessions for batch application")
        
        # TODO: Trigger the actual batch processing
        # For now, just return the created sessions
        
        return jsonify({
            "success": True,
            "message": f"Created {len(created_sessions)} sessions for batch processing",
            "sessions": created_sessions
        }), 200
        
    except Exception as e:
        logging.error(f"Error in batch apply: {e}")
        return jsonify({"error": f"Failed to start batch application: {str(e)}"}), 500

if __name__ == "__main__":
    # Set up file logging for API server with DEBUG level to capture everything
    log_file = setup_file_logging(log_level=logging.DEBUG, console_logging=True)
    logging.info(f"API Server starting. Logs will be saved to: {log_file}")
    
    # Check if we're in development or production mode
    import os
    is_development = os.getenv('FLASK_ENV') == 'development'
    
    if is_development:
        # Development mode with auto-reload disabled to prevent Windows socket issues
        logging.info("🔧 Running in DEVELOPMENT mode")
        app.run(host='0.0.0.0', port=5000, debug=True, use_reloader=False)
    else:
        # Production mode - more stable
        logging.info("🚀 Running in PRODUCTION mode")
        from waitress import serve
        try:
            serve(app, host='0.0.0.0', port=5000, threads=4)
        except ImportError:
            logging.warning("Waitress not installed, falling back to Flask dev server")
            app.run(host='0.0.0.0', port=5000, debug=False, use_reloader=False)

    print("API Server started")