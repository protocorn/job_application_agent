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
import redis


sys.path.append(os.path.dirname(__file__))
sys.path.append(os.path.join(os.path.dirname(__file__), '..', 'Agents'))
sys.path.append(os.path.join(os.path.dirname(__file__), '..'))  # For logging_config

# Original imports
from resume_tailoring_agent import get_google_services, get_doc_id_from_url, copy_google_doc, read_google_doc_content, apply_json_replacements_to_doc, tailor_resume as tailor_resume_with_agent, tailor_resume_and_return_url
from job_application_agent import run_links_with_refactored_agent
from logging_config import setup_file_logging
from components.session.session_manager import SessionManager
from auth import AuthService, require_auth
from profile_service import ProfileService
from job_search_service import JobSearchService
from google_oauth_service import GoogleOAuthService

# Production infrastructure imports
from rate_limiter import rate_limiter, rate_limit, get_rate_limit_status
from job_queue import job_queue, JobPriority, submit_resume_tailoring_job, submit_job_application_job, submit_job_search_job
from security_manager import security_manager, require_secure_headers, validate_input, get_security_status
from database_optimizer import db_optimizer, setup_database_optimizations, get_database_health
from backup_manager import backup_manager, run_full_backup, schedule_backups
from job_handlers import submit_job_with_validation
from mimikree_service import mimikree_service


#Initialize the app
app = Flask(__name__)

# ============= VNC STREAMING SETUP (NEW) =============
# Global flag for VNC availability
VNC_ENABLED = False
socketio = None

try:
    from flask_socketio import SocketIO
    
    # Initialize Socket.IO for VNC streaming
    socketio = SocketIO(
        app, 
        cors_allowed_origins="*",
        async_mode='threading',
        logger=False,  # Reduce verbosity
        engineio_logger=False
    )
    
    # Import VNC components
    try:
        from vnc_api_endpoints import vnc_api
        from vnc_socketio_handler import setup_vnc_socketio
        from vnc_stream_proxy import setup_vnc_websocket_routes

        # Setup VNC WebSocket handlers (Socket.IO based)
        setup_vnc_socketio(socketio)

        # Setup VNC WebSocket proxy routes (Flask-Sock based)
        vnc_ws_enabled = setup_vnc_websocket_routes(app)
        if vnc_ws_enabled:
            logging.info("✅ VNC WebSocket proxy routes registered")

        # Register VNC API endpoints
        app.register_blueprint(vnc_api)

        VNC_ENABLED = True
        logging.info("✅ VNC streaming initialized successfully")

    except ImportError as e:
        VNC_ENABLED = False
        logging.warning(f"⚠️ VNC endpoints not available: {e}")
        logging.info("   VNC mode disabled - will use standard mode")
        logging.info("   This is normal for local development without VNC dependencies")
        
except ImportError as e:
    # Flask-SocketIO not installed
    VNC_ENABLED = False
    socketio = None
    logging.warning(f"⚠️ Flask-SocketIO not installed: {e}")
    logging.info("   VNC streaming disabled - will use standard mode")
    logging.info("   Install with: pip install flask-socketio")
    
# ============= END VNC SETUP =============

# ============= RESOURCE MANAGEMENT & MONITORING SETUP =============
# Initialize resource manager, connection pool, and health monitor
try:
    from system_initializer import initialize_system, shutdown_system, get_system_status, report_error
    
    # Initialize all resource management components
    initialize_system()
    logging.info("✅ Resource management and monitoring initialized")
    
except ImportError as e:
    logging.warning(f"⚠️ Resource management not available: {e}")
    logging.info("   System will run without advanced resource management")
except Exception as e:
    logging.error(f"❌ Failed to initialize resource management: {e}")
    import traceback
    logging.error(traceback.format_exc())

# ============= END RESOURCE MANAGEMENT SETUP =============

# ============= SENTRY ERROR TRACKING =============
# Initialize Sentry for production error tracking
SENTRY_ENABLED = False
try:
    import sentry_sdk
    from sentry_sdk.integrations.flask import FlaskIntegration

    sentry_dsn = os.getenv('SENTRY_DSN')
    sentry_environment = os.getenv('FLASK_ENV', 'development')

    if sentry_dsn:
        sentry_sdk.init(
            dsn=sentry_dsn,
            integrations=[FlaskIntegration()],
            environment=sentry_environment,
            traces_sample_rate=0.1,  # 10% of transactions for performance monitoring
            profiles_sample_rate=0.1,  # 10% for profiling
            # Filter out health check endpoints from traces
            before_send_transaction=lambda event, hint: None if event.get('transaction', '').startswith('/health') or event.get('transaction', '').startswith('/ready') else event,
        )
        SENTRY_ENABLED = True
        logging.info(f"✅ Sentry error tracking initialized (environment: {sentry_environment})")
    else:
        logging.info("⚠️ Sentry DSN not configured - error tracking disabled")

except ImportError:
    logging.info("⚠️ Sentry SDK not installed - error tracking disabled")
    logging.info("   Install with: pip install sentry-sdk[flask]")

# ============= END SENTRY SETUP =============

# Configure CORS for development and production
# Default includes multiple localhost ports for development and Vercel production
default_origins = 'http://localhost:3000,http://localhost:3001,http://localhost:5173,https://job-agent-frontend-two.vercel.app'
allowed_origins_str = os.getenv('CORS_ORIGINS', default_origins)

# Parse allowed origins
allowed_origins = [origin.strip() for origin in allowed_origins_str.split(',')]

# PRODUCTION SECURITY: Only add Vercel wildcard pattern in development mode
# In production, all allowed origins must be explicitly listed in CORS_ORIGINS env var
flask_env = os.getenv('FLASK_ENV', 'development')
if flask_env == 'development' and any('.vercel.app' in origin for origin in allowed_origins):
    # flask-cors supports regex patterns for origins (development only)
    allowed_origins.append(r'https://.*\.vercel\.app')
    logging.info("⚠️ CORS: Vercel wildcard pattern enabled (development mode)")
else:
    logging.info(f"✅ CORS: Production mode - only explicit origins allowed: {len(allowed_origins)} origins")

# Apply CORS with expanded origins list (supports regex for Vercel in dev only)
CORS(app, origins=allowed_origins, supports_credentials=True)

# Apply security headers to all responses
@app.after_request
@require_secure_headers
def after_request(response):
    return response

JOBS: Dict[str, Dict[str, Any]] = {}
INTERVENTIONS: Dict[str, Dict[str, Any]] = {}  # Store intervention requests from job agents

# Initialize session manager with proper path
import os
session_storage_path = os.path.join(os.path.dirname(__file__), "sessions")
session_manager = SessionManager(session_storage_path)

# Initialize production infrastructure
def initialize_production_infrastructure():
    """Initialize all production infrastructure components"""
    try:
        # Initialize database tables if they don't exist
        from database_config import Base, engine, test_connection
        logging.info("Checking database connection...")
        if test_connection():
            logging.info("✅ Database connection successful")
            logging.info("Initializing database tables...")
            Base.metadata.create_all(bind=engine)
            logging.info("✅ Database tables initialized")
        else:
            raise Exception("Database connection failed")
        
        # Set up database optimizations
        setup_database_optimizations()
        logging.info("✅ Database optimizations initialized")
        
        # Start job queue worker
        job_queue.start_worker()
        logging.info("✅ Job queue worker started")
        
        # Schedule automated backups
        schedule_backups()
        logging.info("✅ Backup scheduler initialized")
        
        # Recover VNC sessions
        try:
            from Agents.components.vnc.vnc_session_manager import vnc_session_manager
            import asyncio
            # We need to run this in the background loop
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            loop.run_until_complete(vnc_session_manager.recover_sessions())
            logging.info("✅ VNC session recovery check complete")
        except Exception as e:
            logging.warning(f"⚠️ VNC recovery failed (non-critical): {e}")
        
        logging.info("🚀 Production infrastructure initialized successfully")
        
    except Exception as e:
        logging.error(f"❌ Failed to initialize production infrastructure: {e}")
        raise

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

def extract_google_doc_with_oauth(resume_url: str, user_id: int) -> str:
    """
    Extract text from Google Doc using OAuth credentials (works with private docs)
    Falls back to public access if OAuth not connected
    """
    try:
        # Check if user has Google OAuth connected
        if GoogleOAuthService.is_connected(user_id):
            try:
                logging.info(f"User {user_id} has OAuth connected, using authenticated access")
                credentials = GoogleOAuthService.get_credentials(user_id)

                # Use the same functions from resume_tailoring_agent
                from resume_tailoring_agent import get_doc_id_from_url, get_google_services, read_google_doc_content

                doc_id = get_doc_id_from_url(resume_url)
                docs_service, _ = get_google_services(credentials)
                resume_text = read_google_doc_content(docs_service, doc_id)

                logging.info(f"Successfully read private Google Doc via OAuth for user {user_id}")
                return resume_text
            except Exception as oauth_err:
                logging.warning(f"OAuth access failed, falling back to public access: {oauth_err}")
                # Fall through to public access method below

        # Fallback to public access method
        return extract_resume_text(resume_url)

    except Exception as e:
        logging.error(f"Error extracting Google Doc: {e}")
        raise

def extract_resume_text(resume_url: str) -> str:
    """Extract text from publicly accessible Google Doc (legacy method)"""
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
            raise ValueError("Google Doc is not publicly accessible. If you have connected your Google account, we can access your private docs. Otherwise, please set sharing to 'Anyone with the link can view' in Google Docs.")
        elif response.status_code == 403:
            raise ValueError("Access denied to Google Doc. Please connect your Google account or make the document public.")
        elif response.status_code == 404:
            raise ValueError("Google Doc not found. Please check the URL.")

        response.raise_for_status()

        # Return the text content
        return response.text.strip()
    except Exception as e:
        logging.error(f"Error fetching Google Doc: {e}")
        if "401" in str(e):
            raise ValueError("Google Doc is not publicly accessible. Connect your Google account to access private docs, or make the document public.")
        elif "403" in str(e):
            raise ValueError("Access denied to Google Doc. Please connect your Google account or check sharing permissions.")
        elif "404" in str(e):
            raise ValueError("Google Doc not found. Please check the URL.")
        else:
            raise ValueError(f"Could not access Google Doc: {str(e)}")

def extract_pdf_text(file_obj) -> str:
    """Extract text from PDF file using PyPDF2"""
    try:
        import PyPDF2
        import io

        # Create a PDF reader object
        pdf_reader = PyPDF2.PdfReader(io.BytesIO(file_obj.read()))

        text = ""
        for page_num, page in enumerate(pdf_reader.pages):
            try:
                extracted = page.extract_text()
                if extracted:
                    text += extracted + "\n"
            except Exception as page_err:
                logging.warning(f"Could not extract text from page {page_num}: {page_err}")
                continue

        text = text.strip()

        if not text:
            raise ValueError("No text could be extracted from the PDF. Please ensure the PDF contains selectable text (not scanned images).")

        logging.info(f"Successfully extracted {len(text)} characters from PDF")
        return text

    except Exception as e:
        logging.error(f"Error extracting PDF text: {e}")
        raise ValueError(f"Failed to extract text from PDF: {str(e)}")


# ============= HEALTH CHECK ENDPOINTS =============

@app.route('/health', methods=['GET'])
def health_check():
    """Basic health check endpoint - returns 200 if server is running"""
    return jsonify({
        "status": "healthy",
        "timestamp": time.time(),
        "vnc_enabled": VNC_ENABLED,
        "sentry_enabled": SENTRY_ENABLED
    }), 200


@app.route('/ready', methods=['GET'])
def readiness_check():
    """
    Readiness check endpoint - verifies all dependencies are working
    Returns 200 if ready, 503 if not ready
    """
    checks = {}
    all_ready = True

    # Check database connection
    try:
        from database_config import engine
        from sqlalchemy import text
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        checks["database"] = {"status": "ready", "message": "Connected"}
    except Exception as e:
        checks["database"] = {"status": "not_ready", "error": str(e)}
        all_ready = False

    # Check Redis connection
    try:
        from rate_limiter import redis_client
        redis_client.ping()
        checks["redis"] = {"status": "ready", "message": "Connected"}
    except Exception as e:
        checks["redis"] = {"status": "not_ready", "error": str(e)}
        all_ready = False

    # Check VNC capacity if enabled
    if VNC_ENABLED:
        try:
            from vnc_api_endpoints import vnc_session_manager
            available_slots = vnc_session_manager.max_sessions - len(vnc_session_manager.sessions)
            checks["vnc_capacity"] = {
                "status": "ready" if available_slots > 0 else "at_capacity",
                "available_slots": available_slots,
                "max_sessions": vnc_session_manager.max_sessions
            }
            if available_slots == 0:
                all_ready = False
        except Exception as e:
            checks["vnc_capacity"] = {"status": "error", "error": str(e)}
            all_ready = False
    else:
        checks["vnc_capacity"] = {"status": "disabled", "message": "VNC not enabled"}

    response = {
        "status": "ready" if all_ready else "not_ready",
        "timestamp": time.time(),
        "checks": checks
    }

    return jsonify(response), 200 if all_ready else 503


# ============= END HEALTH CHECK ENDPOINTS =============


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
@require_auth
def process_resume():
    try:
        # Add debugging
        print(f"Request JSON: {request.json}")
        print(f"Request headers: {request.headers}")

        user_id = request.current_user['id']
        resume_url = request.json['resume_url']

        # Use OAuth-based extraction if user has connected Google account
        resume_text = extract_google_doc_with_oauth(resume_url, user_id)

        if not resume_text:
            return jsonify({"error": "Failed to extract resume text"}), 400

        logging.info(f"Processing resume with LLM (length: {len(resume_text)} chars)")
        profile_data = process_resume_with_llm(resume_text)

        if profile_data is None:
            return jsonify({
                "error": "Failed to process resume with Gemini",
                "success": False
            }), 500

        print(f"Returning profile_data: {profile_data}")

        # Persist resume_url on user's profile
        try:
            from profile_service import ProfileService
            ProfileService.create_or_update_profile(user_id, { 'resume_url': resume_url })
        except Exception as persist_err:
            logging.warning(f"Could not persist resume_url: {persist_err}")

        return jsonify({
            "profile_data": profile_data,
            "success": True,
            "message": "Resume processed successfully" + (" (using private Google Doc access)" if GoogleOAuthService.is_connected(user_id) else ""),
            'error': None
            }), 200

    except Exception as e:
        logging.error(f"Error processing resume: {e}")
        return jsonify({"error": str(e)}), 500

@app.route("/api/upload-resume", methods=['POST'])
@require_auth
def upload_resume():
    """Handle PDF/DOCX resume file upload, convert to Google Doc, and process"""
    try:
        user_id = request.current_user['id']

        # Check if user has connected Google account
        if not GoogleOAuthService.is_connected(user_id):
            return jsonify({
                "error": "Please connect your Google account first to upload resumes. The resume will be converted to Google Docs for easy editing and tailoring.",
                "needs_google_auth": True
            }), 403

        # Check if file was provided
        if 'resume' not in request.files:
            return jsonify({"error": "No file provided"}), 400

        file = request.files['resume']

        if file.filename == '':
            return jsonify({"error": "No file selected"}), 400

        # Get file extension
        filename = file.filename.lower()
        original_filename = file.filename

        # Validate file type
        if not (filename.endswith('.pdf') or filename.endswith('.docx')):
            return jsonify({"error": "Only PDF and DOCX files are supported"}), 400

        # Validate file size (max 10MB)
        file.seek(0, 2)  # Seek to end
        file_size = file.tell()
        file.seek(0)  # Seek back to beginning

        if file_size > 10 * 1024 * 1024:  # 10MB
            return jsonify({"error": "File too large (maximum 10MB)"}), 400

        if file_size == 0:
            return jsonify({"error": "File is empty"}), 400

        # Get user's Google credentials
        credentials = GoogleOAuthService.get_credentials(user_id)
        if not credentials:
            return jsonify({
                "error": "Failed to retrieve Google credentials. Please reconnect your account.",
                "needs_google_auth": True
            }), 403

        # Upload file to Google Drive and convert to Google Docs
        try:
            from googleapiclient.discovery import build
            from googleapiclient.http import MediaIoBaseUpload
            from werkzeug.utils import secure_filename
            import io

            drive_service = build('drive', 'v3', credentials=credentials)
            
            # Prepare file metadata
            safe_filename = secure_filename(original_filename)
            file_name_without_ext = safe_filename.rsplit('.', 1)[0]
            
            file_metadata = {
                'name': file_name_without_ext,
                'mimeType': 'application/vnd.google-apps.document'  # Convert to Google Docs format
            }

            # Determine MIME type for upload
            if filename.endswith('.pdf'):
                mime_type = 'application/pdf'
            elif filename.endswith('.docx'):
                mime_type = 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
            
            # Read file content
            file.seek(0)
            file_content = file.read()
            media = MediaIoBaseUpload(
                io.BytesIO(file_content),
                mimetype=mime_type,
                resumable=True
            )

            # Upload and convert to Google Docs
            logging.info(f"Uploading {original_filename} to Google Drive and converting to Google Docs...")
            uploaded_file = drive_service.files().create(
                body=file_metadata,
                media_body=media,
                fields='id, name, webViewLink'
            ).execute()

            google_doc_id = uploaded_file['id']
            google_doc_url = f"https://docs.google.com/document/d/{google_doc_id}/edit"
            
            logging.info(f"Successfully uploaded and converted to Google Doc: {google_doc_url}")

        except Exception as upload_err:
            logging.error(f"Error uploading to Google Drive: {upload_err}")
            return jsonify({
                "error": f"Failed to upload to Google Drive: {str(upload_err)}",
                "success": False
            }), 500

        # Extract text from the Google Doc
        try:
            from resume_tailoring_agent import get_google_services, read_google_doc_content
            
            docs_service, _ = get_google_services(credentials)
            resume_text = read_google_doc_content(docs_service, google_doc_id)
            
            if not resume_text or len(resume_text.strip()) < 50:
                return jsonify({
                    "error": "Could not extract enough text from the converted Google Doc. Please ensure the file contains selectable text (not scanned images)."
                }), 400

        except Exception as extract_err:
            logging.error(f"Error extracting text from Google Doc: {extract_err}")
            return jsonify({
                "error": f"Failed to extract text from Google Doc: {str(extract_err)}",
                "success": False
            }), 500

        # Process with LLM
        logging.info(f"Processing uploaded resume with LLM (length: {len(resume_text)} chars)")
        profile_data = process_resume_with_llm(resume_text)

        if profile_data is None:
            return jsonify({
                "error": "Failed to process resume with Gemini",
                "success": False
            }), 500

        # Save Google Doc URL to profile
        try:
            from profile_service import ProfileService
            ProfileService.create_or_update_profile(user_id, {
                'resume_url': google_doc_url
            })
        except Exception as persist_err:
            logging.warning(f"Could not persist resume URL: {persist_err}")

        return jsonify({
            "profile_data": profile_data,
            "resume_url": google_doc_url,
            "google_doc_id": google_doc_id,
            "success": True,
            "message": f"Resume uploaded and converted to Google Doc successfully! You can now edit and tailor it.",
            "file_type": "PDF" if filename.endswith('.pdf') else "DOCX"
        }), 200

    except Exception as e:
        logging.error(f"Error uploading resume: {e}")
        return jsonify({"error": f"Failed to upload resume: {str(e)}"}), 500

def extract_docx_text(file_obj) -> str:
    """Extract text from DOCX file using python-docx"""
    try:
        from docx import Document
        import io

        # Create a Document object from the file
        doc = Document(io.BytesIO(file_obj.read()))

        text = ""
        for paragraph in doc.paragraphs:
            if paragraph.text.strip():
                text += paragraph.text + "\n"

        # Also extract text from tables
        for table in doc.tables:
            for row in table.rows:
                for cell in row.cells:
                    if cell.text.strip():
                        text += cell.text + "\n"

        text = text.strip()

        if not text:
            raise ValueError("No text could be extracted from the DOCX file. Please ensure the file is not empty.")

        logging.info(f"Successfully extracted {len(text)} characters from DOCX")
        return text

    except ImportError:
        raise ValueError("DOCX file support not available. Please upload a PDF or Google Docs link instead.")
    except Exception as e:
        logging.error(f"Error extracting DOCX text: {e}")
        raise ValueError(f"Failed to extract text from DOCX: {str(e)}")

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
    """ Search for jobs using Multi-Source Job Discovery Agent and save to PostgreSQL"""
    try:
        import sys
        sys.path.append(os.path.join(os.path.dirname(__file__), '..'))
        from Agents.multi_source_job_discovery_agent import MultiSourceJobDiscoveryAgent

        user_id = request.current_user['id']

        # Get optional min_relevance_score from request body (default: 30)
        min_relevance_score = request.json.get('min_relevance_score', 30) if request.json else 30

        job_discovery_agent = MultiSourceJobDiscoveryAgent(user_id=user_id)

        if not job_discovery_agent.profile_data:
            return jsonify({"error": "Profile data not found for this user"}), 400

        logging.info(f"Searching for jobs across all sources (min relevance score: {min_relevance_score})...")

        # Use search_and_save which searches all sources and saves to database
        response = job_discovery_agent.search_and_save(min_relevance_score=min_relevance_score)

        if 'error' in response:
            return jsonify({"error": response['error']}), 500

        jobs_data = response.get('jobs', [])

        return jsonify({
            "jobs": jobs_data,
            "total_found": response.get('count', 0),
            "sources": response.get('sources', {}),
            "average_score": response.get('average_score', 0),
            "saved_count": response.get('saved_count', 0),
            "updated_count": response.get('updated_count', 0),
            "success": True,
            "message": f"Jobs searched from {len(response.get('sources', {}))} sources and saved to database",
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

@app.route("/api/credits", methods=['GET'])
@require_auth
def get_user_credits():
    """Get user's credit information including usage and limits"""
    try:
        user_id = request.current_user['id']
        user_email = request.current_user.get('email', '')

        # Check if user is admin
        is_admin = user_email in rate_limiter.ADMIN_EMAILS

        # Cache key for this user's credits
        cache_key = f"credits_cache:{user_id}"
        cached_credits = None

        # Try to get cached credits (cache for 10 seconds to reduce Redis calls)
        try:
            import redis
            from rate_limiter import redis_client
            cached_data = redis_client.get(cache_key)
            if cached_data:
                import json
                cached_credits = json.loads(cached_data)
        except Exception as cache_error:
            # If cache fails, continue without it
            logging.debug(f"Cache fetch failed: {cache_error}")

        # If we have valid cached data, return it
        if cached_credits:
            return jsonify({
                "success": True,
                "credits": cached_credits,
                "cached": True
            }), 200

        # Get usage stats for different limit types (only if not cached)
        daily_tailoring = rate_limiter.get_usage_stats('resume_tailoring_per_user_per_day', str(user_id))
        daily_applications = rate_limiter.get_usage_stats('job_applications_per_user_per_day', str(user_id))
        daily_search = rate_limiter.get_usage_stats('job_search_per_user_per_day', str(user_id))

        credits_info = {
            "is_admin": is_admin,
            "resume_tailoring": {
                "daily": {
                    "limit": "unlimited" if is_admin else daily_tailoring.get('limit', 5),
                    "used": 0 if is_admin else daily_tailoring.get('used', 0),
                    "remaining": "unlimited" if is_admin else daily_tailoring.get('remaining', 5),
                    "reset_time": daily_tailoring.get('reset_time'),
                    "window_hours": 24
                }
            },
            "job_applications": {
                "daily": {
                    "limit": "unlimited" if is_admin else daily_applications.get('limit', 20),
                    "used": 0 if is_admin else daily_applications.get('used', 0),
                    "remaining": "unlimited" if is_admin else daily_applications.get('remaining', 20),
                    "reset_time": daily_applications.get('reset_time'),
                    "window_hours": 24
                }
            },
            "job_search": {
                "daily": {
                    "limit": "unlimited" if is_admin else daily_search.get('limit', 5),
                    "used": 0 if is_admin else daily_search.get('used', 0),
                    "remaining": "unlimited" if is_admin else daily_search.get('remaining', 5),
                    "reset_time": daily_search.get('reset_time'),
                    "window_hours": 24
                }
            }
        }

        # Cache the credits info for 10 seconds to reduce Redis load
        try:
            import json
            from rate_limiter import redis_client
            redis_client.setex(cache_key, 10, json.dumps(credits_info))
        except Exception as cache_error:
            # If caching fails, continue without it
            logging.debug(f"Cache set failed: {cache_error}")

        return jsonify({
            "success": True,
            "credits": credits_info
        }), 200
        
    except Exception as e:
        logging.error(f"Error getting user credits: {str(e)}")
        return jsonify({"error": str(e)}), 500

@app.route("/api/tailor-resume", methods=['POST'])
@require_auth
@rate_limit('api_requests_per_user_per_minute')
@validate_input
def tailor_resume():
    """Submit resume tailoring job to queue"""
    try:
        user_id = request.current_user['id']
        data = request.json
        
        # Validate required fields
        job_description = data.get('job_description')
        resume_url = data.get('resume_url')
        
        if not job_description or not resume_url:
            return jsonify({"error": "Job description and resume URL are required"}), 400

        # Check if user has connected Google account
        if not GoogleOAuthService.is_connected(user_id):
            return jsonify({
                "error": "Please connect your Google account first to tailor resumes",
                "needs_google_auth": True
            }), 403

        # Get user's Google credentials
        credentials = GoogleOAuthService.get_credentials(user_id)
        if not credentials:
            return jsonify({
                "error": "Failed to retrieve Google credentials. Please reconnect your account.",
                "needs_google_auth": True
            }), 403

        # Get user's full name from database
        from database_config import SessionLocal, User
        db = SessionLocal()
        try:
            user = db.query(User).filter(User.id == user_id).first()
            user_full_name = f"{user.first_name} {user.last_name}" if user else "Resume"
        finally:
            db.close()

        # Prepare job payload
        # Serialize credentials to JSON-compatible format
        credentials_dict = {
            'token': credentials.token,
            'refresh_token': credentials.refresh_token,
            'token_uri': credentials.token_uri,
            'client_id': credentials.client_id,
            'client_secret': credentials.client_secret,
            'scopes': list(credentials.scopes) if credentials.scopes else None
        }

        payload = {
            'original_resume_url': resume_url,
            'job_description': job_description,
            'job_title': data.get('job_title', 'Unknown Position'),
            'company': data.get('company_name', 'Unknown Company'),
            'credentials': credentials_dict,
            'user_full_name': user_full_name
        }

        # Submit job to queue
        result = submit_job_with_validation(
            user_id=user_id,
            job_type='resume_tailoring',
            payload=payload,
            priority=JobPriority.NORMAL
        )

        if result['success']:
            return jsonify({
                "success": True,
                "job_id": result['job_id'],
                "message": "Resume tailoring job submitted successfully. You will be notified when complete.",
                "queue_position": job_queue.get_queue_stats()['queue_size']
            }), 202  # Accepted
        else:
            return jsonify({
                "error": result['error'],
                "success": False
            }), 400

    except Exception as e:
        logging.error(f"Error submitting resume tailoring job: {str(e)}")
        return jsonify({"error": str(e)}), 500

@app.route("/api/tailor-resume-sync", methods=['POST'])
@require_auth
@rate_limit('resume_tailoring_per_user_per_day')
@validate_input
def tailor_resume_sync():
    """Synchronous resume tailoring (for backward compatibility and urgent requests)"""
    try:
        user_id = request.current_user['id']
        data = request.json
        job_id = data.get('job_id')
        job_description = data.get('job_description')
        company_name = data.get('company_name')
        resume_url = data.get('resume_url')

        if not job_description or not resume_url:
            return jsonify({"error": "Job description and resume URL are required"}), 400

        # Check if user has connected Google account
        if not GoogleOAuthService.is_connected(user_id):
            return jsonify({
                "error": "Please connect your Google account first to tailor resumes",
                "needs_google_auth": True
            }), 403

        try:
            # Get user's Google credentials
            credentials = GoogleOAuthService.get_credentials(user_id)
            if not credentials:
                return jsonify({
                    "error": "Failed to retrieve Google credentials. Please reconnect your account.",
                    "needs_google_auth": True
                }), 403

            # Get user's full name and cover letter template from database
            from database_config import SessionLocal, User
            db = SessionLocal()
            try:
                user = db.query(User).filter(User.id == user_id).first()
                user_full_name = f"{user.first_name} {user.last_name}" if user else "Resume"

                # Get cover letter template if exists
                cover_letter_template = None
                if user and user.profile:
                    cover_letter_template = user.profile.cover_letter_template
            finally:
                db.close()

            # Get Mimikree credentials from environment
            mimikree_email = os.getenv('MIMIKREE_EMAIL')
            mimikree_password = os.getenv('MIMIKREE_PASSWORD')

            # Use the resume tailor agent to create tailored Google Doc
            tailoring_result = tailor_resume_and_return_url(
                resume_url,
                job_description,
                job_id,
                company_name,
                credentials=credentials,
                user_full_name=user_full_name,
                mimikree_email=mimikree_email,
                mimikree_password=mimikree_password
            )

            # Extract URL (now returns dict with metrics)
            tailored_url = tailoring_result.get('url') if isinstance(tailoring_result, dict) else tailoring_result

            # Tailor cover letter if template exists
            tailored_cover_letter_id = None
            if cover_letter_template:
                from Agents.cover_letter_tailoring import tailor_cover_letter
                from googleapiclient.discovery import build

                # Tailor the cover letter text
                tailored_cl_text = tailor_cover_letter(
                    cover_letter_template,
                    job_description,
                    company_name,
                    job_id,
                    user_full_name
                )

                # Create a Google Doc for the cover letter
                docs_service = build('docs', 'v1', credentials=credentials)
                drive_service = build('drive', 'v3', credentials=credentials)

                # Clean company name for filename
                clean_company = ''.join(c if c.isalnum() else '_' for c in company_name)
                cover_letter_title = f"{user_full_name}_{clean_company}_CoverLetter"

                # Create empty document
                doc = docs_service.documents().create(body={'title': cover_letter_title}).execute()
                cover_letter_doc_id = doc['documentId']

                # Insert the tailored cover letter text
                requests = [{
                    'insertText': {
                        'location': {'index': 1},
                        'text': tailored_cl_text
                    }
                }]
                docs_service.documents().batchUpdate(
                    documentId=cover_letter_doc_id,
                    body={'requests': requests}
                ).execute()

                tailored_cover_letter_id = cover_letter_doc_id
                logging.info(f"Created cover letter doc: {cover_letter_doc_id}")

            # Prepare response with metrics
            response_data = {
                "tailored_document_id": tailored_url,
                "tailored_cover_letter_id": tailored_cover_letter_id,
                "success": True,
                "message": "Resume and cover letter tailored successfully" if tailored_cover_letter_id else "Resume tailored successfully",
                "error": None
            }

            # Add tailoring metrics if available
            if isinstance(tailoring_result, dict):
                response_data["metrics"] = tailoring_result
                
                # Debug: Log metrics being sent to frontend
                logging.info(f"📊 Sending metrics to frontend:")
                if 'keywords' in tailoring_result:
                    keywords = tailoring_result['keywords']
                    logging.info(f"   Job Required: {len(keywords.get('job_required', []))}")
                    logging.info(f"   Already Present: {len(keywords.get('already_present', []))}")
                    logging.info(f"   Newly Added: {len(keywords.get('newly_added', []))}")
                    logging.info(f"   Could Not Add: {len(keywords.get('could_not_add', []))}")
                if 'match_stats' in tailoring_result:
                    match_stats = tailoring_result['match_stats']
                    logging.info(f"   Match Percentage: {match_stats.get('match_percentage', 0):.1f}%")

            return jsonify(response_data), 200
        except ValueError as ve:
            # Check if it's an authentication error
            error_msg = str(ve)
            if 'authentication' in error_msg.lower() or 'reconnect' in error_msg.lower():
                logging.warning(f"Authentication error for user {user_id}: {error_msg}")
                return jsonify({
                    "error": error_msg,
                    "needs_google_auth": True
                }), 403
            logging.error(f"Validation error tailoring resume: {error_msg}")
            return jsonify({"error": error_msg}), 400
        except Exception as e:
            logging.error(f"Error tailoring resume: {str(e)}")
            # Check if the error message indicates auth issues
            error_str = str(e).lower()
            if 'invalid_grant' in error_str or 'credentials' in error_str or '401' in error_str:
                return jsonify({
                    "error": "Your Google account connection has expired. Please reconnect your account.",
                    "needs_google_auth": True
                }), 403
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

@app.route("/api/apply-batch-jobs", methods=['POST'])
@require_auth
def apply_batch_jobs():
    """Apply to multiple jobs in batch mode"""
    try:
        data = request.json
        user_id = request.current_user['id']

        if not data:
            logging.error("No data provided in request")
            return jsonify({"error": "No data provided"}), 400

        job_urls = data.get('jobUrls', [])
        resume_url = data.get('resumeUrl', '')
        use_tailored = data.get('useTailored', False)

        logging.info(f"Received batch apply request - {len(job_urls)} jobs, use_tailored: {use_tailored}")

        if not job_urls or not isinstance(job_urls, list):
            logging.error("Invalid or missing jobUrls")
            return jsonify({"error": "jobUrls must be a non-empty list"}), 400

        if not resume_url:
            logging.error("Missing resume URL")
            return jsonify({"error": "Resume URL is required"}), 400

        # Create a batch ID
        batch_id = str(uuid.uuid4())

        # Store batch information
        JOBS[batch_id] = {
            "type": "batch",
            "batch_id": batch_id,
            "status": "queued",
            "total_jobs": len(job_urls),
            "completed_jobs": 0,
            "failed_jobs": 0,
            "job_ids": [],
            "logs": [],
            "created_at": time.time()
        }

        logging.info(f"Batch {batch_id} created with {len(job_urls)} jobs")

        # Define batch processing function
        async def run_batch_applications():
            try:
                JOBS[batch_id]["status"] = "running"
                JOBS[batch_id]["logs"].append({
                    "timestamp": time.time(),
                    "level": "info",
                    "message": f"Starting batch application for {len(job_urls)} jobs"
                })

                for index, job_url in enumerate(job_urls, 1):
                    if batch_id not in JOBS:
                        logging.error(f"Batch {batch_id} no longer exists, stopping")
                        break

                    # Create individual job ID
                    job_id = str(uuid.uuid4())

                    # Add to batch tracking
                    JOBS[batch_id]["job_ids"].append(job_id)

                    # Create individual job entry
                    JOBS[job_id] = {
                        "batch_id": batch_id,
                        "job_url": job_url,
                        "resumeUrl": resume_url,
                        "status": "queued",
                        "links": [job_url],
                        "logs": [],
                        "created_at": time.time(),
                        "job_number": index,
                        "total_in_batch": len(job_urls)
                    }

                    JOBS[batch_id]["logs"].append({
                        "timestamp": time.time(),
                        "level": "info",
                        "message": f"Processing job {index}/{len(job_urls)}: {job_url}"
                    })

                    logging.info(f"Batch {batch_id}: Starting job {index}/{len(job_urls)} - {job_id}")

                    try:
                        # Update individual job status
                        JOBS[job_id]["status"] = "running"

                        # Handle resume tailoring if needed
                        final_resume_url = resume_url
                        if use_tailored:
                            try:
                                JOBS[job_id]["logs"].append({
                                    "timestamp": time.time(),
                                    "level": "info",
                                    "message": "Tailoring resume for this job..."
                                })

                                # You could add actual tailoring logic here if needed
                                # For now, we'll use the original resume

                            except Exception as tailor_error:
                                logging.error(f"Resume tailoring failed for job {job_id}: {tailor_error}")
                                JOBS[job_id]["logs"].append({
                                    "timestamp": time.time(),
                                    "level": "warning",
                                    "message": f"Resume tailoring failed, using original resume: {str(tailor_error)}"
                                })

                        # Run the job agent
                        await run_links_with_refactored_agent(
                            links=[job_url],
                            headless=True,
                            keep_open=False,
                            debug=False,
                            hold_seconds=2,
                            slow_mo_ms=0,
                            job_id=job_id,
                            jobs_dict=JOBS,
                            session_manager=session_manager
                        )

                        # Update individual job status
                        if job_id in JOBS:
                            JOBS[job_id]["status"] = "completed"
                            JOBS[job_id]["logs"].append({
                                "timestamp": time.time(),
                                "level": "success",
                                "message": "Job application completed successfully"
                            })

                        # Update batch completed count
                        if batch_id in JOBS:
                            JOBS[batch_id]["completed_jobs"] += 1
                            JOBS[batch_id]["logs"].append({
                                "timestamp": time.time(),
                                "level": "success",
                                "message": f"Completed job {index}/{len(job_urls)}"
                            })

                        logging.info(f"Batch {batch_id}: Job {index} completed successfully")

                    except Exception as job_error:
                        # Handle individual job failure
                        if job_id in JOBS:
                            JOBS[job_id]["status"] = "failed"
                            JOBS[job_id]["logs"].append({
                                "timestamp": time.time(),
                                "level": "error",
                                "message": f"Job application failed: {str(job_error)}"
                            })

                        # Update batch failed count
                        if batch_id in JOBS:
                            JOBS[batch_id]["failed_jobs"] += 1
                            JOBS[batch_id]["logs"].append({
                                "timestamp": time.time(),
                                "level": "error",
                                "message": f"Failed job {index}/{len(job_urls)}: {str(job_error)}"
                            })

                        logging.error(f"Batch {batch_id}: Job {index} failed - {job_error}")

                        # Continue to next job instead of stopping the batch
                        continue

                # Mark batch as completed
                if batch_id in JOBS:
                    JOBS[batch_id]["status"] = "completed"
                    JOBS[batch_id]["logs"].append({
                        "timestamp": time.time(),
                        "level": "success",
                        "message": f"Batch completed: {JOBS[batch_id]['completed_jobs']} successful, {JOBS[batch_id]['failed_jobs']} failed"
                    })

                logging.info(f"Batch {batch_id} completed")

            except Exception as e:
                # Handle batch-level errors
                if batch_id in JOBS:
                    JOBS[batch_id]["status"] = "failed"
                    JOBS[batch_id]["logs"].append({
                        "timestamp": time.time(),
                        "level": "error",
                        "message": f"Batch processing failed: {str(e)}"
                    })

                logging.error(f"Error in batch application {batch_id}: {e}")
                import traceback
                logging.error(f"Full traceback: {traceback.format_exc()}")

        # Start batch processing in separate thread
        def run_async_batch():
            try:
                logging.info(f"Starting async batch thread for {batch_id}")
                asyncio.run(run_batch_applications())
            except Exception as e:
                logging.error(f"Error running async batch {batch_id}: {e}")
                if batch_id in JOBS:
                    JOBS[batch_id]["status"] = "failed"
                    JOBS[batch_id]["logs"].append({
                        "timestamp": time.time(),
                        "level": "error",
                        "message": f"Failed to start batch: {str(e)}"
                    })
                import traceback
                logging.error(f"Batch thread error traceback: {traceback.format_exc()}")

        # Start the batch in a separate thread
        import threading
        batch_thread = threading.Thread(target=run_async_batch, name=f"batch-{batch_id[:8]}")
        batch_thread.daemon = True
        batch_thread.start()

        logging.info(f"Batch thread started for {batch_id}")

        return jsonify({
            "success": True,
            "batch_id": batch_id,
            "batch_size": len(job_urls),
            "message": f"Batch application started for {len(job_urls)} jobs"
        }), 200

    except Exception as e:
        logging.error(f"Error starting batch application: {e}")
        import traceback
        logging.error(f"Full traceback: {traceback.format_exc()}")
        return jsonify({"error": f"Failed to start batch application: {str(e)}"}), 500

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

@app.route("/api/batch-status/<batch_id>", methods=['GET'])
def get_batch_status(batch_id):
    """Get detailed status of a batch application including all individual jobs"""
    try:
        if batch_id not in JOBS:
            return jsonify({"error": "Batch not found"}), 404

        batch_data = JOBS[batch_id]

        # Get individual job statuses
        job_statuses = []
        for job_id in batch_data.get('job_ids', []):
            if job_id in JOBS:
                job_info = JOBS[job_id]
                job_statuses.append({
                    "job_id": job_id,
                    "job_url": job_info.get('job_url', ''),
                    "status": job_info.get('status', 'unknown'),
                    "job_number": job_info.get('job_number', 0),
                    "logs_count": len(job_info.get('logs', []))
                })

        return jsonify({
            "batch_id": batch_id,
            "status": batch_data.get('status', 'unknown'),
            "total_jobs": batch_data.get('total_jobs', 0),
            "completed_jobs": batch_data.get('completed_jobs', 0),
            "failed_jobs": batch_data.get('failed_jobs', 0),
            "created_at": batch_data.get('created_at', 0),
            "jobs": job_statuses,
            "logs": batch_data.get('logs', [])
        }), 200

    except Exception as e:
        logging.error(f"Error getting batch status for {batch_id}: {e}")
        return jsonify({"error": f"Failed to get batch status: {str(e)}"}), 500

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
        required_fields = ['email', 'password', 'first_name', 'last_name']
        for field in required_fields:
            if not data.get(field):
                return jsonify({"error": f"{field} is required"}), 400

        email = data['email'].strip().lower()
        password = data['password']
        first_name = data['first_name'].strip()
        last_name = data['last_name'].strip()

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
    """User login endpoint with IP-based rate limiting"""
    try:
        data = request.json
        if not data:
            return jsonify({"error": "No data provided"}), 400

        # Validate required fields
        email = data.get('email', '').strip().lower()
        password = data.get('password', '')

        if not email or not password:
            return jsonify({"error": "Email and password are required"}), 400

        # Get client IP address
        client_ip = request.remote_addr

        # Check IP-based rate limiting FIRST (prevents enumeration across accounts)
        ip_allowed, ip_remaining, ip_reason = security_manager.check_ip_login_attempts(client_ip)
        if not ip_allowed:
            logging.warning(f"Login attempt blocked for IP {client_ip}: {ip_reason}")
            return jsonify({
                "success": False,
                "error": ip_reason
            }), 429

        # Check account-specific rate limiting
        account_allowed, account_remaining = security_manager.check_login_attempts(email)
        if not account_allowed:
            return jsonify({
                "success": False,
                "error": f"Account temporarily locked due to too many failed login attempts. Please try again in 15 minutes.",
                "remaining_attempts": 0
            }), 429

        # Authenticate user
        result = AuthService.authenticate_user(email, password)

        # Record login attempt with IP address
        security_manager.record_login_attempt(
            identifier=email,
            success=result['success'],
            user_id=result.get('user', {}).get('id') if result['success'] else None,
            ip_address=client_ip
        )

        if result['success']:
            return jsonify(result), 200
        else:
            # Add remaining attempts info to error response
            return jsonify({
                **result,
                "remaining_attempts": account_remaining - 1,
                "ip_remaining_attempts": ip_remaining - 1
            }), 401

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

@app.route("/api/auth/verify-email", methods=['GET'])
def verify_email():
    """Verify user email with verification token"""
    try:
        token = request.args.get('token')
        if not token:
            return jsonify({"error": "Verification token is required"}), 400

        # Verify email
        result = AuthService.verify_email(token)

        if result['success']:
            return jsonify(result), 200
        else:
            return jsonify(result), 400

    except Exception as e:
        logging.error(f"Error in verify email endpoint: {e}")
        return jsonify({"error": "Email verification failed"}), 500

@app.route("/api/auth/resend-verification", methods=['POST'])
def resend_verification():
    """Resend verification email to user"""
    try:
        data = request.json
        if not data or not data.get('email'):
            return jsonify({"error": "Email address is required"}), 400

        email = data['email'].strip().lower()

        # Basic validation
        if '@' not in email:
            return jsonify({"error": "Please provide a valid email address"}), 400

        # Resend verification email
        result = AuthService.resend_verification_email(email)

        if result['success']:
            return jsonify(result), 200
        else:
            return jsonify(result), 400

    except Exception as e:
        logging.error(f"Error in resend verification endpoint: {e}")
        return jsonify({"error": "Failed to resend verification email"}), 500

# Beta Access Routes
@app.route("/api/beta/request", methods=['POST'])
@require_auth
def request_beta_access():
    """Request beta access for a user"""
    try:
        from database_config import SessionLocal, User
        from datetime import datetime

        data = request.json
        user_id = request.current_user['id']
        reason = data.get('reason', '').strip()

        if not reason:
            return jsonify({"error": "Please provide a reason for requesting beta access"}), 400

        db = SessionLocal()
        try:
            user = db.query(User).filter(User.id == user_id).first()

            if not user:
                return jsonify({"error": "User not found"}), 404

            if user.beta_access_approved:
                return jsonify({
                    "success": False,
                    "error": "You already have beta access"
                }), 400

            if user.beta_access_requested:
                return jsonify({
                    "success": False,
                    "error": "You have already submitted a beta access request. Please wait for approval."
                }), 400

            # Update user with beta request
            user.beta_access_requested = True
            user.beta_request_date = datetime.utcnow()
            user.beta_request_reason = reason

            db.commit()

            logging.info(f"Beta access requested by user {user_id}: {user.email}")

            return jsonify({
                "success": True,
                "message": "Beta access request submitted successfully. We'll review your request and get back to you soon!"
            }), 200

        finally:
            db.close()

    except Exception as e:
        logging.error(f"Error in request beta access endpoint: {e}")
        return jsonify({"error": "Failed to submit beta access request"}), 500

@app.route("/api/beta/status", methods=['GET'])
@require_auth
def get_beta_status():
    """Get beta access status for current user"""
    try:
        from database_config import SessionLocal, User

        user_id = request.current_user['id']

        db = SessionLocal()
        try:
            user = db.query(User).filter(User.id == user_id).first()

            if not user:
                return jsonify({"error": "User not found"}), 404

            return jsonify({
                "success": True,
                "beta_access_requested": user.beta_access_requested or False,
                "beta_access_approved": user.beta_access_approved or False,
                "beta_request_date": user.beta_request_date.isoformat() if user.beta_request_date else None,
                "beta_approved_date": user.beta_approved_date.isoformat() if user.beta_approved_date else None
            }), 200

        finally:
            db.close()

    except Exception as e:
        logging.error(f"Error in get beta status endpoint: {e}")
        return jsonify({"error": "Failed to get beta access status"}), 500

@app.route("/api/admin/beta/requests", methods=['GET'])
@require_auth
def get_beta_requests():
    """Get all pending beta access requests (admin only)"""
    try:
        from database_config import SessionLocal, User

        # Check if user is admin (you can add an is_admin field or check specific emails)
        user_email = request.current_user['email']

        # TODO: Update this with your admin email(s)
        admin_emails = os.getenv('ADMIN_EMAILS', '').split(',')
        if user_email not in admin_emails:
            return jsonify({"error": "Unauthorized - Admin access required"}), 403

        db = SessionLocal()
        try:
            # Get all users who requested beta access
            pending_requests = db.query(User).filter(
                User.beta_access_requested == True,
                User.beta_access_approved == False
            ).order_by(User.beta_request_date.desc()).all()

            approved_users = db.query(User).filter(
                User.beta_access_approved == True
            ).order_by(User.beta_approved_date.desc()).all()

            pending_list = [{
                "id": user.id,
                "email": user.email,
                "first_name": user.first_name,
                "last_name": user.last_name,
                "reason": user.beta_request_reason,
                "request_date": user.beta_request_date.isoformat() if user.beta_request_date else None,
                "created_at": user.created_at.isoformat()
            } for user in pending_requests]

            approved_list = [{
                "id": user.id,
                "email": user.email,
                "first_name": user.first_name,
                "last_name": user.last_name,
                "approved_date": user.beta_approved_date.isoformat() if user.beta_approved_date else None
            } for user in approved_users]

            return jsonify({
                "success": True,
                "pending_requests": pending_list,
                "approved_users": approved_list,
                "stats": {
                    "pending_count": len(pending_list),
                    "approved_count": len(approved_list)
                }
            }), 200

        finally:
            db.close()

    except Exception as e:
        logging.error(f"Error in get beta requests endpoint: {e}")
        return jsonify({"error": "Failed to get beta requests"}), 500

@app.route("/api/admin/beta/approve/<string:user_id>", methods=['POST'])
@require_auth
def approve_beta_access(user_id):
    """Approve beta access for a user (admin only)"""
    try:
        from database_config import SessionLocal, User
        from datetime import datetime
        from email_service import email_service
        from uuid import UUID

        # Check if user is admin
        user_email = request.current_user['email']
        admin_emails = os.getenv('ADMIN_EMAILS', '').split(',')
        if user_email not in admin_emails:
            return jsonify({"error": "Unauthorized - Admin access required"}), 403

        # Convert string UUID to UUID object
        try:
            user_uuid = UUID(user_id)
        except ValueError:
            return jsonify({"error": "Invalid user ID format"}), 400

        db = SessionLocal()
        try:
            user = db.query(User).filter(User.id == user_uuid).first()

            if not user:
                return jsonify({"error": "User not found"}), 404

            if user.beta_access_approved:
                return jsonify({
                    "success": False,
                    "error": "User already has beta access"
                }), 400

            # Approve beta access
            user.beta_access_approved = True
            user.beta_approved_date = datetime.utcnow()

            db.commit()

            # Send approval email
            try:
                email_service.send_beta_approval_email(
                    to_email=user.email,
                    first_name=user.first_name
                )
            except Exception as e:
                logging.error(f"Failed to send beta approval email: {e}")

            logging.info(f"Beta access approved for user {user_id}: {user.email}")

            return jsonify({
                "success": True,
                "message": f"Beta access approved for {user.email}"
            }), 200

        finally:
            db.close()

    except Exception as e:
        logging.error(f"Error in approve beta access endpoint: {e}")
        return jsonify({"error": "Failed to approve beta access"}), 500

@app.route("/api/admin/beta/reject/<string:user_id>", methods=['POST'])
@require_auth
def reject_beta_access(user_id):
    """Reject beta access for a user (admin only)"""
    try:
        from database_config import SessionLocal, User
        from uuid import UUID

        # Check if user is admin
        user_email = request.current_user['email']
        admin_emails = os.getenv('ADMIN_EMAILS', '').split(',')
        if user_email not in admin_emails:
            return jsonify({"error": "Unauthorized - Admin access required"}), 403

        # Convert string UUID to UUID object
        try:
            user_uuid = UUID(user_id)
        except ValueError:
            return jsonify({"error": "Invalid user ID format"}), 400

        # Get rejection reason from request body
        data = request.get_json()
        rejection_reason = data.get('reason', 'Your request does not meet our current beta criteria.')

        db = SessionLocal()
        try:
            user = db.query(User).filter(User.id == user_uuid).first()

            if not user:
                return jsonify({"error": "User not found"}), 404

            # Store user info before resetting
            user_email_addr = user.email
            user_first_name = user.first_name

            # Reset beta access request
            user.beta_access_requested = False
            user.beta_request_date = None
            user.beta_request_reason = None

            db.commit()

            logging.info(f"Beta access rejected for user {user_id}: {user_email_addr}")

            # Send rejection email
            from server.email_service import email_service
            email_sent = email_service.send_beta_rejection_email(
                to_email=user_email_addr,
                first_name=user_first_name,
                rejection_reason=rejection_reason
            )

            if email_sent:
                logging.info(f"Rejection email sent to {user_email_addr}")
            else:
                logging.warning(f"Failed to send rejection email to {user_email_addr}")

            return jsonify({
                "success": True,
                "message": f"Beta access request rejected for {user_email_addr}",
                "email_sent": email_sent
            }), 200

        finally:
            db.close()

    except Exception as e:
        logging.error(f"Error in reject beta access endpoint: {e}")
        return jsonify({"error": "Failed to reject beta access"}), 500

# ============================================================
# BETA FEEDBACK ENDPOINTS
# ============================================================

@app.route("/api/beta/feedback/status", methods=['GET'])
@require_auth
def get_feedback_status():
    """Check if user has already submitted beta feedback"""
    try:
        from database_config import SessionLocal, User

        user_id = request.current_user['id']
        db = SessionLocal()

        try:
            user = db.query(User).filter(User.id == user_id).first()
            if not user:
                return jsonify({"error": "User not found"}), 404

            return jsonify({
                "success": True,
                "has_submitted_feedback": user.has_submitted_beta_feedback or False
            }), 200

        finally:
            db.close()

    except Exception as e:
        logging.error(f"Error checking feedback status: {e}")
        return jsonify({"error": "Failed to check feedback status"}), 500

@app.route("/api/beta/feedback/submit", methods=['POST'])
@require_auth
@rate_limit('api_requests_per_user_per_minute')
@validate_input
def submit_beta_feedback():
    """Submit beta feedback and receive credit reward"""
    try:
        from database_config import SessionLocal, User, BetaFeedback

        user_id = request.current_user['id']
        data = request.get_json()

        if not data:
            return jsonify({"error": "No data provided"}), 400

        db = SessionLocal()

        try:
            # Check if user has already submitted feedback
            user = db.query(User).filter(User.id == user_id).first()
            if not user:
                return jsonify({"error": "User not found"}), 404

            if user.has_submitted_beta_feedback:
                return jsonify({"error": "You have already submitted feedback"}), 400

            # Check if feedback already exists (double-check)
            existing_feedback = db.query(BetaFeedback).filter(
                BetaFeedback.user_id == user_id
            ).first()

            if existing_feedback:
                return jsonify({"error": "Feedback already submitted"}), 400

            # Validate required fields
            required_fields = ['overall_rating', 'ease_of_use', 'tailoring_quality', 'recommend_score']
            for field in required_fields:
                if field not in data:
                    return jsonify({"error": f"Missing required field: {field}"}), 400

            # Validate rating ranges
            if not (1 <= data['overall_rating'] <= 5):
                return jsonify({"error": "overall_rating must be between 1 and 5"}), 400
            if not (1 <= data['ease_of_use'] <= 5):
                return jsonify({"error": "ease_of_use must be between 1 and 5"}), 400
            if not (1 <= data['tailoring_quality'] <= 5):
                return jsonify({"error": "tailoring_quality must be between 1 and 5"}), 400
            if not (0 <= data['recommend_score'] <= 10):
                return jsonify({"error": "recommend_score must be between 0 and 10"}), 400

            # Create feedback record
            feedback = BetaFeedback(
                user_id=user_id,
                user_email=user.email,

                # Ratings
                overall_rating=data['overall_rating'],
                ease_of_use=data['ease_of_use'],
                tailoring_quality=data['tailoring_quality'],
                recommend_score=data['recommend_score'],

                # Feature feedback
                most_useful_feature=data.get('most_useful_feature', '').strip() or None,
                least_useful_feature=data.get('least_useful_feature', '').strip() or None,
                missing_features=data.get('missing_features', '').strip() or None,

                # Tailoring comments
                tailoring_comments=data.get('tailoring_comments', '').strip() or None,

                # Future features interest
                interested_cover_letter=data.get('interested_cover_letter', False),
                interested_job_tracking=data.get('interested_job_tracking', False),
                interested_interview_prep=data.get('interested_interview_prep', False),
                interested_salary_insights=data.get('interested_salary_insights', False),
                other_feature_requests=data.get('other_feature_requests', '').strip() or None,

                # Open feedback
                what_worked_well=data.get('what_worked_well', '').strip() or None,
                what_needs_improvement=data.get('what_needs_improvement', '').strip() or None,
                additional_comments=data.get('additional_comments', '').strip() or None,

                # Credits
                credits_awarded=10
            )

            db.add(feedback)

            # Mark user as having submitted feedback
            user.has_submitted_beta_feedback = True

            # Award 10 resume tailoring credits by removing old usage entries
            # The rate limiter uses a sorted set with timestamps
            try:
                # Get the correct Redis key format used by the rate limiter
                key = f"rate_limit:resume_tailoring_per_user_per_day:{user_id}"

                # Remove the oldest 10 entries from the sorted set to free up credits
                # This effectively gives them 10 more uses
                removed_count = 0
                for _ in range(10):
                    # Remove the oldest entry (smallest score/timestamp)
                    removed = rate_limiter.redis_client.zpopmin(key, 1)
                    if removed:
                        removed_count += 1

                logging.info(f"Awarded {removed_count} resume tailoring credits to user {user_id} by removing old usage entries")
            except Exception as credit_error:
                logging.warning(f"Could not award credits in Redis: {credit_error}")
                # Continue anyway - feedback is more important than credit tracking

            db.commit()

            # Get updated credits info
            daily_tailoring = rate_limiter.get_usage_stats('resume_tailoring_per_user_per_day', str(user_id))

            logging.info(f"Beta feedback submitted by user {user_id} ({user.email}), awarded 10 credits")

            return jsonify({
                "success": True,
                "message": "Thank you for your feedback! You've been awarded 10 resume tailoring credits.",
                "credits_awarded": 10,
                "remaining_credits": daily_tailoring.get('remaining', 0)
            }), 200

        finally:
            db.close()

    except Exception as e:
        logging.error(f"Error submitting beta feedback: {e}")
        return jsonify({"error": f"Failed to submit feedback: {str(e)}"}), 500

@app.route("/api/admin/feedback/all", methods=['GET'])
@require_auth
def get_all_feedback():
    """Get all beta feedback (admin only)"""
    try:
        from database_config import SessionLocal, User, BetaFeedback

        # Check if user is admin
        user_email = request.current_user['email']
        admin_emails = os.getenv('ADMIN_EMAILS', '').split(',')
        if user_email not in admin_emails:
            return jsonify({"error": "Unauthorized - Admin access required"}), 403

        db = SessionLocal()

        try:
            # Get all feedback, ordered by most recent first
            feedbacks = db.query(BetaFeedback).order_by(
                BetaFeedback.submitted_at.desc()
            ).all()

            feedback_list = []
            for feedback in feedbacks:
                feedback_list.append({
                    "id": feedback.id,
                    "user_id": feedback.user_id,
                    "user_email": feedback.user_email,

                    # Ratings
                    "overall_rating": feedback.overall_rating,
                    "ease_of_use": feedback.ease_of_use,
                    "tailoring_quality": feedback.tailoring_quality,
                    "recommend_score": feedback.recommend_score,

                    # Feature feedback
                    "most_useful_feature": feedback.most_useful_feature,
                    "least_useful_feature": feedback.least_useful_feature,
                    "missing_features": feedback.missing_features,

                    # Tailoring comments
                    "tailoring_comments": feedback.tailoring_comments,

                    # Future features
                    "interested_cover_letter": feedback.interested_cover_letter,
                    "interested_job_tracking": feedback.interested_job_tracking,
                    "interested_interview_prep": feedback.interested_interview_prep,
                    "interested_salary_insights": feedback.interested_salary_insights,
                    "other_feature_requests": feedback.other_feature_requests,

                    # Open feedback
                    "what_worked_well": feedback.what_worked_well,
                    "what_needs_improvement": feedback.what_needs_improvement,
                    "additional_comments": feedback.additional_comments,

                    # Metadata
                    "credits_awarded": feedback.credits_awarded,
                    "submitted_at": feedback.submitted_at.isoformat() if feedback.submitted_at else None
                })

            return jsonify({
                "success": True,
                "count": len(feedback_list),
                "feedback": feedback_list
            }), 200

        finally:
            db.close()

    except Exception as e:
        logging.error(f"Error fetching feedback: {e}")
        return jsonify({"error": "Failed to fetch feedback"}), 500

# ============================================================
# GDPR ACCOUNT MANAGEMENT ENDPOINTS
# ============================================================

@app.route("/api/account/change-password", methods=['POST'])
@require_auth
def change_password():
    """Change user password (GDPR compliance)"""
    try:
        from database_config import SessionLocal, User

        data = request.get_json()
        current_password = data.get('current_password')
        new_password = data.get('new_password')

        if not current_password or not new_password:
            return jsonify({"error": "Current password and new password are required"}), 400

        if len(new_password) < 8:
            return jsonify({"error": "New password must be at least 8 characters long"}), 400

        user_id = request.current_user['id']
        db = SessionLocal()

        try:
            user = db.query(User).filter(User.id == user_id).first()
            if not user:
                return jsonify({"error": "User not found"}), 404

            # Verify current password
            if not AuthService.verify_password(current_password, user.password_hash):
                return jsonify({"error": "Current password is incorrect"}), 401

            # Hash and update new password
            user.password_hash = AuthService.hash_password(new_password)
            db.commit()

            logging.info(f"Password changed successfully for user {user.email}")

            return jsonify({
                "success": True,
                "message": "Password changed successfully"
            }), 200

        finally:
            db.close()

    except Exception as e:
        logging.error(f"Error changing password: {e}")
        return jsonify({"error": "Failed to change password"}), 500

@app.route("/api/account/export-data", methods=['GET'])
@require_auth
def export_user_data():
    """Export all user data in JSON format (GDPR Right to Data Portability)"""
    try:
        from database_config import SessionLocal, User

        user_id = request.current_user['id']
        user_email = request.current_user['email']

        db = SessionLocal()

        try:
            user = db.query(User).filter(User.id == user_id).first()
            if not user:
                return jsonify({"error": "User not found"}), 404

            # Get profile data
            profile_data = ProfileService.get_profile(user_id)

            # Compile all user data
            user_data = {
                "account_information": {
                    "user_id": user.id,
                    "email": user.email,
                    "first_name": user.first_name,
                    "last_name": user.last_name,
                    "created_at": user.created_at.isoformat() if user.created_at else None,
                    "email_verified": user.email_verified,
                    "beta_access_requested": user.beta_access_requested,
                    "beta_access_approved": user.beta_access_approved,
                    "beta_request_date": user.beta_request_date.isoformat() if user.beta_request_date else None,
                    "beta_approved_date": user.beta_approved_date.isoformat() if user.beta_approved_date else None,
                    "beta_request_reason": user.beta_request_reason,
                    "google_oauth_connected": bool(user.google_refresh_token),
                    "google_account_email": user.google_account_email,
                    "mimikree_connected": user.mimikree_is_connected,
                    "mimikree_email": user.mimikree_email if user.mimikree_is_connected else None
                },
                "profile_data": profile_data.get('profile') if profile_data.get('success') else {},
                "export_metadata": {
                    "export_date": datetime.utcnow().isoformat(),
                    "export_format": "JSON",
                    "gdpr_compliance": "This data export complies with GDPR Article 20 (Right to Data Portability)"
                }
            }

            logging.info(f"Data export completed for user {user_email}")

            return jsonify({
                "success": True,
                "data": user_data
            }), 200

        finally:
            db.close()

    except Exception as e:
        logging.error(f"Error exporting user data: {e}")
        return jsonify({"error": "Failed to export user data"}), 500

@app.route("/api/account/delete", methods=['DELETE'])
@require_auth
def delete_account():
    """Delete user account and all associated data (GDPR Right to Erasure)"""
    try:
        from database_config import SessionLocal, User

        data = request.get_json()
        password = data.get('password')
        confirmation = data.get('confirmation')

        if not password:
            return jsonify({"error": "Password is required to delete account"}), 400

        if confirmation != "DELETE":
            return jsonify({"error": "Please type DELETE to confirm account deletion"}), 400

        user_id = request.current_user['id']
        user_email = request.current_user['email']

        db = SessionLocal()

        try:
            user = db.query(User).filter(User.id == user_id).first()
            if not user:
                return jsonify({"error": "User not found"}), 404

            # Verify password
            if not AuthService.verify_password(password, user.password_hash):
                return jsonify({"error": "Incorrect password"}), 401

            # Delete all user data (CASCADE should handle related tables)
            # But explicitly delete profile data first
            try:
                ProfileService.delete_profile(user_id)
            except Exception as profile_err:
                logging.warning(f"Error deleting profile for user {user_id}: {profile_err}")

            # Delete user account
            db.delete(user)
            db.commit()

            logging.info(f"Account deleted successfully for user {user_email}")

            return jsonify({
                "success": True,
                "message": "Account and all associated data have been permanently deleted"
            }), 200

        finally:
            db.close()

    except Exception as e:
        logging.error(f"Error deleting account: {e}")
        return jsonify({"error": "Failed to delete account"}), 500

# Google OAuth Routes
@app.route("/api/oauth/authorize", methods=['GET'])
@require_auth
def oauth_authorize():
    """Get Google OAuth authorization URL"""
    try:
        user_id = request.current_user['id']
        auth_url = GoogleOAuthService.get_authorization_url(user_id)
        return jsonify({
            "success": True,
            "authorization_url": auth_url
        }), 200
    except Exception as e:
        logging.error(f"Error generating OAuth URL: {e}")
        return jsonify({"error": str(e)}), 500

@app.route("/api/oauth/callback", methods=['GET'])
def oauth_callback():
    """Handle Google OAuth callback"""
    try:
        # Check for error parameter from Google (user denied access, scope changed, etc.)
        error = request.args.get('error')
        if error:
            error_description = request.args.get('error_description', 'Authorization denied')
            logging.warning(f"OAuth error from Google: {error} - {error_description}")
            return f"""
                <html>
                    <head>
                        <style>
                            body {{ font-family: Arial, sans-serif; text-align: center; padding: 50px; }}
                            .error {{ color: #d32f2f; }}
                        </style>
                    </head>
                    <body>
                        <h2 class="error">✗ Authorization Failed</h2>
                        <p>{error_description}</p>
                        <p>Please close this window and try connecting your Google account again.</p>
                        <script>
                            // Send error message to parent window
                            if (window.opener) {{
                                window.opener.postMessage({{
                                    type: 'GOOGLE_AUTH_ERROR',
                                    error: '{error_description}'
                                }}, '*');
                            }}
                        </script>
                    </body>
                </html>
            """

        code = request.args.get('code')
        state = request.args.get('state')  # user_id (UUID string)

        if not code or not state:
            return jsonify({"error": "Missing code or state parameter"}), 400

        user_id = state  # Keep as UUID string, don't convert to int
        result = GoogleOAuthService.handle_oauth_callback(code, user_id)

        if result['success']:
            # Send success message to parent window and auto-close
            return f"""
                <html>
                    <head>
                        <style>
                            body {{ font-family: Arial, sans-serif; text-align: center; padding: 50px; }}
                            .success {{ color: #2e7d32; }}
                            .countdown {{
                                font-size: 14px;
                                color: #666;
                                margin-top: 10px;
                            }}
                        </style>
                    </head>
                    <body>
                        <h2 class="success">✓ Authorization Successful!</h2>
                        <p>Your Google account has been connected successfully.</p>
                        <p>Email: {result.get('google_email', '')}</p>
                        <p class="countdown">This window will close automatically in <span id="countdown">3</span> seconds...</p>
                        <p style="font-size: 12px; color: #999;">You can close this window manually if it doesn't close automatically.</p>
                        <script>
                            // Send success message to parent window
                            if (window.opener) {{
                                window.opener.postMessage({{
                                    type: 'GOOGLE_AUTH_SUCCESS',
                                    email: '{result.get('google_email', '')}'
                                }}, '*');
                            }}

                            // Countdown and auto-close
                            let countdown = 2;
                            const countdownElement = document.getElementById('countdown');
                            const interval = setInterval(() => {{
                                countdown--;
                                if (countdownElement) {{
                                    countdownElement.textContent = countdown;
                                }}
                                if (countdown <= 0) {{
                                    clearInterval(interval);
                                    // Try to close the window
                                    window.close();
                                    // If window.close() doesn't work, try alternative methods
                                    setTimeout(() => {{
                                        if (!window.closed) {{
                                            // Create a fallback if browser blocks window.close()
                                            document.body.innerHTML = '<div style="text-align: center; padding: 50px; font-family: Arial;"><h2 style="color: #2e7d32;">✓ Success!</h2><p>You can close this window now.</p></div>';
                                        }}
                                    }}, 500);
                                }}
                            }}, 1000);
                        </script>
                    </body>
                </html>
            """
        else:
            return f"""
                <html>
                    <head>
                        <style>
                            body {{ font-family: Arial, sans-serif; text-align: center; padding: 50px; }}
                            .error {{ color: #d32f2f; }}
                        </style>
                    </head>
                    <body>
                        <h2 class="error">✗ Authorization Failed</h2>
                        <p>{result.get('error', 'Unknown error occurred')}</p>
                        <p>Please close this window and try connecting your Google account again.</p>
                        <script>
                            // Send error message to parent window
                            if (window.opener) {{
                                window.opener.postMessage({{
                                    type: 'GOOGLE_AUTH_ERROR',
                                    error: '{result.get('error', 'Unknown error occurred')}'
                                }}, '*');
                            }}
                        </script>
                    </body>
                </html>
            """
    except Exception as e:
        logging.error(f"Error in OAuth callback: {e}")
        return jsonify({"error": str(e)}), 500

@app.route("/api/oauth/status", methods=['GET'])
@require_auth
def oauth_status():
    """Check if user has connected Google account"""
    try:
        user_id = request.current_user['id']
        is_connected = GoogleOAuthService.is_connected(user_id)
        google_email = GoogleOAuthService.get_google_email(user_id) if is_connected else None

        return jsonify({
            "success": True,
            "is_connected": is_connected,
            "google_email": google_email
        }), 200
    except Exception as e:
        logging.error(f"Error checking OAuth status: {e}")
        return jsonify({"error": str(e)}), 500

@app.route("/api/oauth/disconnect", methods=['POST'])
@require_auth
def oauth_disconnect():
    """Disconnect Google account"""
    try:
        user_id = request.current_user['id']
        result = GoogleOAuthService.disconnect_google_account(user_id)

        if result['success']:
            return jsonify(result), 200
        else:
            return jsonify(result), 400
    except Exception as e:
        logging.error(f"Error disconnecting Google account: {e}")
        return jsonify({"error": str(e)}), 500

# ============================================================
# MIMIKREE CONNECTION ENDPOINTS
# ============================================================

@app.route("/api/mimikree/status", methods=['GET'])
@require_auth
def get_mimikree_status():
    """Get user's Mimikree connection status"""
    try:
        user_id = request.current_user['id']
        result = mimikree_service.get_user_mimikree_status(user_id)
        
        if result['success']:
            return jsonify(result), 200
        else:
            return jsonify(result), 400
            
    except Exception as e:
        logging.error(f"Error getting Mimikree status: {e}")
        return jsonify({"error": str(e)}), 500

@app.route("/api/mimikree/connect", methods=['POST'])
@require_auth
@rate_limit('api_requests_per_user_per_minute')
@validate_input
def connect_mimikree():
    """Connect user's Mimikree account"""
    try:
        user_id = request.current_user['id']
        data = request.json
        
        if not data:
            return jsonify({"error": "No data provided"}), 400
        
        email = data.get('email', '').strip()
        password = data.get('password', '')
        
        if not email or not password:
            return jsonify({"error": "Email and password are required"}), 400
        
        # Validate email format
        import re
        email_pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
        if not re.match(email_pattern, email):
            return jsonify({"error": "Invalid email format"}), 400
        
        # Connect Mimikree account
        result = mimikree_service.connect_user_mimikree(user_id, email, password)
        
        if result['success']:
            return jsonify(result), 200
        else:
            return jsonify(result), 400
            
    except Exception as e:
        logging.error(f"Error connecting Mimikree: {e}")
        return jsonify({"error": str(e)}), 500

@app.route("/api/mimikree/disconnect", methods=['POST'])
@require_auth
def disconnect_mimikree():
    """Disconnect user's Mimikree account"""
    try:
        user_id = request.current_user['id']
        result = mimikree_service.disconnect_user_mimikree(user_id)
        
        if result['success']:
            return jsonify(result), 200
        else:
            return jsonify(result), 400
            
    except Exception as e:
        logging.error(f"Error disconnecting Mimikree: {e}")
        return jsonify({"error": str(e)}), 500

@app.route("/api/mimikree/test", methods=['POST'])
@require_auth
@rate_limit('api_requests_per_user_per_minute')
def test_mimikree_connection():
    """Test user's Mimikree connection"""
    try:
        user_id = request.current_user['id']
        result = mimikree_service.test_user_connection(user_id)
        
        if result['success']:
            return jsonify(result), 200
        else:
            return jsonify(result), 400
            
    except Exception as e:
        logging.error(f"Error testing Mimikree connection: {e}")
        return jsonify({"error": str(e)}), 500

@app.route("/", methods=['GET'])
def root():
    """Root endpoint for basic health check"""
    return jsonify({
        "status": "ok",
        "service": "Job Application Agent API",
        "version": "1.0.0"
    })

@app.route("/api/health", methods=['GET'])
@app.route("/health", methods=['GET'])
def health():
    """Detailed health check endpoint with resource monitoring"""
    health_data = {
        "status": "ok",
        "vnc_enabled": VNC_ENABLED if 'VNC_ENABLED' in globals() else False,
        "timestamp": time.time()
    }
    
    # Add system resource status if available
    try:
        from system_initializer import get_system_status
        system_status = get_system_status()
        
        if system_status.get('initialized'):
            health_data['resource_management'] = {
                'enabled': True,
                'resource_manager': system_status.get('resource_manager', {}),
                'connection_pool': system_status.get('connection_pool', {}),
                'health_status': system_status.get('health', {}).get('current_status', 'unknown')
            }
    except Exception as e:
        logging.debug(f"Resource management status not available: {e}")
    
    return jsonify(health_data)


@app.route("/api/system/status", methods=['GET'])
@require_auth
def system_status():
    """Comprehensive system status endpoint (requires authentication)"""
    try:
        from system_initializer import get_system_status
        status = get_system_status()
        return jsonify(status), 200
    except Exception as e:
        logging.error(f"Error getting system status: {e}")
        return jsonify({
            "error": str(e),
            "message": "System status unavailable"
        }), 500

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

# ============================================================
# PROJECT MANAGEMENT API ROUTES
# ============================================================

@app.route("/api/projects", methods=['GET'])
@require_auth
def get_projects():
    """Get all projects for the authenticated user"""
    try:
        from database_config import SessionLocal
        from migrate_add_projects import Project

        user_id = request.current_user['id']
        db = SessionLocal()

        try:
            projects = db.query(Project).filter(Project.user_id == user_id).all()

            projects_data = []
            for project in projects:
                projects_data.append({
                    'id': project.id,
                    'name': project.name,
                    'description': project.description,
                    'technologies': project.technologies or [],
                    'github_url': project.github_url,
                    'live_url': project.live_url,
                    'features': project.features or [],
                    'detailed_bullets': project.detailed_bullets or [],
                    'tags': project.tags or [],
                    'start_date': project.start_date,
                    'end_date': project.end_date,
                    'team_size': project.team_size,
                    'role': project.role,
                    'is_on_resume': project.is_on_resume,
                    'display_order': project.display_order,
                    'times_used': project.times_used,
                    'avg_relevance_score': project.avg_relevance_score,
                    'last_used_at': project.last_used_at.isoformat() if project.last_used_at else None,
                    'created_at': project.created_at.isoformat() if project.created_at else None
                })

            return jsonify({
                "success": True,
                "projects": projects_data
            }), 200

        finally:
            db.close()

    except Exception as e:
        logging.error(f"Error getting projects: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/projects", methods=['POST'])
@require_auth
def create_project():
    """Create a new project"""
    try:
        from database_config import SessionLocal
        from migrate_add_projects import Project

        user_id = request.current_user['id']
        data = request.json

        db = SessionLocal()

        try:
            project = Project(
                user_id=user_id,
                name=data.get('name'),
                description=data.get('description'),
                technologies=data.get('technologies', []),
                github_url=data.get('github_url'),
                live_url=data.get('live_url'),
                features=data.get('features', []),
                detailed_bullets=data.get('detailed_bullets', []),
                tags=data.get('tags', []),
                start_date=data.get('start_date'),
                end_date=data.get('end_date'),
                team_size=data.get('team_size'),
                role=data.get('role'),
                is_on_resume=data.get('is_on_resume', False),
                display_order=data.get('display_order', 0)
            )

            db.add(project)
            db.commit()
            db.refresh(project)

            return jsonify({
                "success": True,
                "project": {
                    'id': project.id,
                    'name': project.name,
                    'description': project.description,
                    'technologies': project.technologies
                }
            }), 201

        finally:
            db.close()

    except Exception as e:
        logging.error(f"Error creating project: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/projects/<int:project_id>", methods=['PUT'])
@require_auth
def update_project(project_id):
    """Update an existing project"""
    try:
        from database_config import SessionLocal
        from migrate_add_projects import Project

        user_id = request.current_user['id']
        data = request.json

        db = SessionLocal()

        try:
            project = db.query(Project).filter(
                Project.id == project_id,
                Project.user_id == user_id
            ).first()

            if not project:
                return jsonify({"error": "Project not found"}), 404

            # Update fields
            for key, value in data.items():
                if hasattr(project, key) and key not in ['id', 'user_id', 'created_at']:
                    setattr(project, key, value)

            db.commit()
            db.refresh(project)

            return jsonify({
                "success": True,
                "message": "Project updated successfully"
            }), 200

        finally:
            db.close()

    except Exception as e:
        logging.error(f"Error updating project: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/projects/<int:project_id>", methods=['DELETE'])
@require_auth
def delete_project(project_id):
    """Delete a project"""
    try:
        from database_config import SessionLocal
        from migrate_add_projects import Project

        user_id = request.current_user['id']
        db = SessionLocal()

        try:
            project = db.query(Project).filter(
                Project.id == project_id,
                Project.user_id == user_id
            ).first()

            if not project:
                return jsonify({"error": "Project not found"}), 404

            db.delete(project)
            db.commit()

            return jsonify({
                "success": True,
                "message": "Project deleted successfully"
            }), 200

        finally:
            db.close()

    except Exception as e:
        logging.error(f"Error deleting project: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/tailoring/analyze-projects", methods=['POST'])
@require_auth
def analyze_projects():
    """Analyze projects for relevance to a job"""
    try:
        from database_config import SessionLocal
        from migrate_add_projects import Project
        import sys
        sys.path.append(os.path.join(os.path.dirname(__file__), '..', 'Agents'))
        from project_selection.relevance_engine import ProjectRelevanceEngine
        from project_selection.mimikree_project_discovery import MimikreeProjectDiscovery
        from mimikree_integration import MimikreeClient
        import re

        user_id = request.current_user['id']
        data = request.json

        job_description = data.get('job_description', '')
        job_keywords = data.get('job_keywords', [])
        discover_new = data.get('discover_new_projects', False)

        if not job_description:
            return jsonify({"error": "Job description is required"}), 400

        # Initialize services
        gemini_api_key = os.getenv('GOOGLE_API_KEY')
        relevance_engine = ProjectRelevanceEngine(gemini_api_key)

        db = SessionLocal()

        try:
            # Get all user projects
            projects = db.query(Project).filter(Project.user_id == user_id).all()

            projects_data = []
            current_projects = []
            alternative_projects = []

            for project in projects:
                proj_dict = {
                    'id': project.id,
                    'name': project.name,
                    'description': project.description,
                    'technologies': project.technologies or [],
                    'features': project.features or [],
                    'detailed_bullets': project.detailed_bullets or [],
                    'end_date': project.end_date
                }

                projects_data.append(proj_dict)

                if project.is_on_resume:
                    current_projects.append(proj_dict)
                else:
                    alternative_projects.append(proj_dict)

            # Score all projects
            ranked_projects = relevance_engine.rank_projects(
                projects_data,
                job_keywords,
                data.get('required_technologies', []),
                data.get('job_domain')
            )

            # Categorize results
            current_scored = []
            alternative_scored = []

            for project, scores in ranked_projects:
                proj_result = {
                    'project': project,
                    'scores': scores
                }

                if project['id'] in [p['id'] for p in current_projects]:
                    current_scored.append(proj_result)
                else:
                    alternative_scored.append(proj_result)

            # Get swap recommendations
            swap_recommendations = relevance_engine.recommend_project_swaps(
                current_projects,
                projects_data,
                job_keywords,
                data.get('required_technologies', []),
                data.get('job_domain'),
                min_improvement_threshold=15.0
            )

            # Discover new projects if requested
            discovered_projects = []
            if discover_new:
                try:
                    mimikree_email = os.getenv('MIMIKREE_EMAIL')
                    mimikree_password = os.getenv('MIMIKREE_PASSWORD')

                    if mimikree_email and mimikree_password:
                        mimikree_client = MimikreeClient()
                        mimikree_client.authenticate(mimikree_email, mimikree_password)

                        discovery = MimikreeProjectDiscovery(gemini_api_key)
                        new_projects, _ = discovery.discover_projects(
                            mimikree_client,
                            job_keywords,
                            job_description,
                            current_projects,
                            max_questions=8
                        )

                        discovered_projects = discovery.enrich_discovered_projects(
                            new_projects,
                            job_keywords
                        )

                except Exception as e:
                    logging.error(f"Failed to discover new projects: {e}")

            return jsonify({
                "success": True,
                "current_projects": current_scored,
                "alternative_projects": alternative_scored,
                "swap_recommendations": swap_recommendations,
                "discovered_projects": discovered_projects
            }), 200

        finally:
            db.close()

    except Exception as e:
        logging.error(f"Error analyzing projects: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/tailoring/generate-project-bullets", methods=['POST'])
@require_auth
def generate_project_bullets():
    """Generate tailored bullets for a project"""
    try:
        import sys
        sys.path.append(os.path.join(os.path.dirname(__file__), '..', 'Agents'))
        from bullet_generation.project_bullet_generator import ProjectBulletGenerator

        data = request.json

        project = data.get('project')
        job_keywords = data.get('job_keywords', [])
        job_description = data.get('job_description', '')
        target_bullet_count = data.get('target_bullet_count', 3)
        mimikree_context = data.get('mimikree_context')

        if not project:
            return jsonify({"error": "Project data is required"}), 400

        gemini_api_key = os.getenv('GOOGLE_API_KEY')
        generator = ProjectBulletGenerator(gemini_api_key)

        bullets = generator.generate_bullets(
            project,
            job_keywords,
            job_description,
            target_bullet_count,
            mimikree_context=mimikree_context
        )

        return jsonify({
            "success": True,
            "bullets": bullets
        }), 200

    except Exception as e:
        logging.error(f"Error generating bullets: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/projects/save-discovered", methods=['POST'])
@require_auth
def save_discovered_projects():
    """Save discovered projects to database"""
    try:
        from database_config import SessionLocal
        from migrate_add_projects import Project

        user_id = request.current_user['id']
        data = request.json

        projects_to_save = data.get('projects', [])

        if not projects_to_save:
            return jsonify({"error": "No projects to save"}), 400

        db = SessionLocal()
        saved_projects = []

        try:
            for proj_data in projects_to_save:
                project = Project(
                    user_id=user_id,
                    name=proj_data.get('name'),
                    description=proj_data.get('description'),
                    technologies=proj_data.get('technologies', []),
                    github_url=proj_data.get('github_url'),
                    live_url=proj_data.get('live_url'),
                    features=proj_data.get('features', []),
                    detailed_bullets=proj_data.get('detailed_bullets', []),
                    tags=proj_data.get('tags', []),
                    is_on_resume=False,
                    display_order=0
                )

                db.add(project)
                saved_projects.append(project.name)

            db.commit()

            return jsonify({
                "success": True,
                "message": f"Saved {len(saved_projects)} projects",
                "projects": saved_projects
            }), 201

        finally:
            db.close()

    except Exception as e:
        logging.error(f"Error saving discovered projects: {e}")
        return jsonify({"error": str(e)}), 500


# ============================================================
# PRODUCTION MONITORING & MANAGEMENT ENDPOINTS
# ============================================================

@app.route("/api/admin/system-status", methods=['GET'])
@require_auth
def get_system_status():
    """Get comprehensive system status for monitoring"""
    try:
        # Check if user is admin (you can implement admin role checking)
        user_id = request.current_user['id']
        
        status = {
            'timestamp': datetime.utcnow().isoformat(),
            'rate_limits': get_rate_limit_status(),
            'job_queue': job_queue.get_queue_stats(),
            'database': get_database_health(),
            'security': get_security_status(),
            'backups': backup_manager.get_backup_status()
        }
        
        return jsonify(status), 200
        
    except Exception as e:
        logging.error(f"Error getting system status: {e}")
        return jsonify({"error": str(e)}), 500

@app.route("/api/admin/job-queue/stats", methods=['GET'])
@require_auth
def get_job_queue_stats():
    """Get detailed job queue statistics"""
    try:
        return jsonify(job_queue.get_queue_stats()), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/jobs/<job_id>/status", methods=['GET'])
@require_auth
def get_job_status_api(job_id):
    """Get status of a specific job"""
    try:
        user_id = request.current_user['id']
        
        # Get job status
        status = job_queue.get_job_status(job_id)
        if not status:
            return jsonify({"error": "Job not found"}), 404
        
        # Verify job ownership (basic security)
        user_jobs = job_queue.get_user_jobs(user_id)
        if not any(job['job_id'] == job_id for job in user_jobs):
            return jsonify({"error": "Access denied"}), 403
        
        return jsonify(status.to_dict()), 200
        
    except Exception as e:
        logging.error(f"Error getting job status: {e}")
        return jsonify({"error": str(e)}), 500

@app.route("/api/jobs/<job_id>/cancel", methods=['POST'])
@require_auth
def cancel_job_api(job_id):
    """Cancel a job"""
    try:
        user_id = request.current_user['id']
        
        success = job_queue.cancel_job(job_id, user_id)
        if success:
            return jsonify({"success": True, "message": "Job cancelled successfully"}), 200
        else:
            return jsonify({"error": "Failed to cancel job or job not found"}), 400
            
    except Exception as e:
        logging.error(f"Error cancelling job: {e}")
        return jsonify({"error": str(e)}), 500

@app.route("/api/user/jobs", methods=['GET'])
@require_auth
def get_user_jobs_api():
    """Get all jobs for the current user"""
    try:
        user_id = request.current_user['id']
        jobs = job_queue.get_user_jobs(user_id)
        return jsonify({"jobs": jobs}), 200
        
    except Exception as e:
        logging.error(f"Error getting user jobs: {e}")
        return jsonify({"error": str(e)}), 500

@app.route("/api/admin/backups", methods=['GET'])
@require_auth
def list_backups_api():
    """List all available backups"""
    try:
        backup_type = request.args.get('type')  # database, files, logs
        backups = backup_manager.list_backups(backup_type)
        return jsonify({"backups": backups}), 200
        
    except Exception as e:
        logging.error(f"Error listing backups: {e}")
        return jsonify({"error": str(e)}), 500

@app.route("/api/admin/backups/create", methods=['POST'])
@require_auth
def create_backup_api():
    """Create a new backup"""
    try:
        data = request.json or {}
        backup_type = data.get('type', 'full')  # full, database, files, logs
        
        if backup_type == 'full':
            result = run_full_backup()
        elif backup_type == 'database':
            result = backup_manager.backup_database()
        elif backup_type == 'files':
            result = backup_manager.backup_files()
        elif backup_type == 'logs':
            result = backup_manager.backup_logs()
        else:
            return jsonify({"error": "Invalid backup type"}), 400
        
        return jsonify(result), 200 if result.get('success') else 500
        
    except Exception as e:
        logging.error(f"Error creating backup: {e}")
        return jsonify({"error": str(e)}), 500

@app.route("/api/admin/backups/<backup_id>/restore", methods=['POST'])
@require_auth
def restore_backup_api(backup_id):
    """Restore from a backup"""
    try:
        # This is a dangerous operation - add additional security checks
        result = backup_manager.restore_database(backup_id)
        return jsonify(result), 200 if result.get('success') else 500
        
    except Exception as e:
        logging.error(f"Error restoring backup: {e}")
        return jsonify({"error": str(e)}), 500

@app.route("/api/admin/security/events", methods=['GET'])
@require_auth
def get_security_events_api():
    """Get recent security events"""
    try:
        limit = request.args.get('limit', 50, type=int)
        events = security_manager.get_security_events(limit)
        return jsonify({"events": events}), 200
        
    except Exception as e:
        logging.error(f"Error getting security events: {e}")
        return jsonify({"error": str(e)}), 500

@app.route("/api/admin/security/audit", methods=['POST'])
@require_auth
def run_security_audit_api():
    """Run security audit"""
    try:
        audit_results = security_manager.run_security_audit()
        return jsonify(audit_results), 200
        
    except Exception as e:
        logging.error(f"Error running security audit: {e}")
        return jsonify({"error": str(e)}), 500

if __name__ == "__main__":
    # Set up file logging for API server with DEBUG level to capture everything
    log_file = setup_file_logging(log_level=logging.DEBUG, console_logging=True)
    logging.info(f"API Server starting. Logs will be saved to: {log_file}")

    # Initialize production infrastructure
    try:
        initialize_production_infrastructure()
    except Exception as e:
        logging.error(f"Failed to initialize production infrastructure: {e}")
        sys.exit(1)

    # ============= GRACEFUL SHUTDOWN HANDLERS =============
    import signal

    def graceful_shutdown(signum, frame):
        """Handle graceful shutdown on SIGTERM or SIGINT"""
        signal_name = 'SIGTERM' if signum == signal.SIGTERM else 'SIGINT'
        logging.info(f"🛑 Received {signal_name}, initiating graceful shutdown...")

        try:
            # Stop job queue worker
            logging.info("Stopping job queue worker...")
            job_queue.stop_worker()
            logging.info("✓ Job queue worker stopped")
        except Exception as e:
            logging.error(f"Error stopping job queue: {e}")

        try:
            # Close any active VNC sessions
            if VNC_ENABLED:
                logging.info("Closing active VNC sessions...")
                from vnc_api_endpoints import vnc_session_manager
                import asyncio
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                for session_id in list(vnc_session_manager.sessions.keys()):
                    try:
                        loop.run_until_complete(vnc_session_manager.end_session(session_id))
                    except Exception as e:
                        logging.error(f"Error closing VNC session {session_id}: {e}")
                logging.info("✓ VNC sessions closed")
        except Exception as e:
            logging.error(f"Error closing VNC sessions: {e}")

        logging.info("✅ Graceful shutdown complete")
        sys.exit(0)

    # Register signal handlers for graceful shutdown
    signal.signal(signal.SIGTERM, graceful_shutdown)
    signal.signal(signal.SIGINT, graceful_shutdown)
    logging.info("✅ Graceful shutdown handlers registered (SIGTERM, SIGINT)")

    # ============= END GRACEFUL SHUTDOWN HANDLERS =============

    # Check if we're in development or production mode
    import os
    is_development = os.getenv('FLASK_ENV') == 'development'

    # Get port from environment variable (Railway, Heroku, etc.) or default to 5000
    port = int(os.getenv('PORT', 5000))

    # Use socketio.run() if VNC is enabled, otherwise use app.run()
    if socketio and VNC_ENABLED:
        logging.info(f"🚀 Starting server with Socket.IO support on port {port}")
        logging.info(f"   Mode: {'DEVELOPMENT' if is_development else 'PRODUCTION'}")
        logging.info(f"   VNC Streaming: ENABLED ✅")
        
        socketio.run(
            app,
            host='0.0.0.0',
            port=port,
            debug=is_development,
            allow_unsafe_werkzeug=True,
            use_reloader=False  # Disable reloader to prevent Windows socket issues
        )
    else:
        logging.info(f"🚀 Starting server on port {port}")
        logging.info(f"   Mode: {'DEVELOPMENT' if is_development else 'PRODUCTION'}")
        logging.info(f"   VNC Streaming: DISABLED (Socket.IO not available)")
        
        # Use standard Flask server
        app.run(
            host='0.0.0.0',
            port=port,
            debug=is_development,
            use_reloader=False
        )

    # Cleanup on shutdown
    try:
        job_queue.stop_worker()
        logging.info("Job queue worker stopped")
    except:
        pass
    
    print("API Server stopped")