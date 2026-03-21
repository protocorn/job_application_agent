from flask import Flask, request, jsonify, send_file, redirect
import os
import sys
import requests
import secrets
import html
from google import genai
from googleapiclient.discovery import build
import json
from flask_cors import CORS
from typing import Dict, Any
import logging
import time
import base64
import uuid
import hashlib
import asyncio
import redis
from datetime import datetime
from urllib.parse import urlencode


sys.path.append(os.path.dirname(__file__))
sys.path.append(os.path.join(os.path.dirname(__file__), '..', 'Agents'))
sys.path.append(os.path.join(os.path.dirname(__file__), '..'))  # For logging_config

# Original imports
from resume_tailoring_agent import get_google_services, get_doc_id_from_url, read_google_doc_content
from latex_tailoring_agent import parse_latex_zip, get_main_tex_preview_from_base64, compile_latex_zip_to_pdf
from job_application_agent import run_links_with_refactored_agent
from logging_config import setup_file_logging
from auth import AuthService, require_auth, require_admin
from profile_service import ProfileService
from google_oauth_service import GoogleOAuthService

# Production infrastructure imports
from rate_limiter import rate_limiter, rate_limit, get_rate_limit_status
from job_queue import job_queue, JobPriority
from security_manager import security_manager, require_secure_headers, validate_input, get_security_status
from database_optimizer import setup_database_optimizations, get_database_health
from backup_manager import backup_manager, run_full_backup, schedule_backups
from job_handlers import submit_job_with_validation
from mimikree_service import mimikree_service
from bug_bounty import (
    SEVERITY_REWARD_MAP,
    normalize_severity,
    get_reward_for_severity,
    get_user_bonus_for_limit,
    effective_limit,
    validate_bug_report_payload,
    build_dedupe_key,
)


#Initialize the app
app = Flask(__name__)

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

flask_env = os.getenv('FLASK_ENV', 'development')
logging.info(f"✅ CORS: Explicit origins configured: {len(allowed_origins)} origins")

# Apply CORS with expanded origins list (supports regex for Vercel in dev only)
CORS(
    app, 
    origins=allowed_origins, 
    supports_credentials=True,
    allow_headers=['Content-Type', 'Authorization', 'Accept'],
    methods=['GET', 'POST', 'PUT', 'DELETE', 'OPTIONS', 'PATCH'],
    expose_headers=['Content-Type', 'Authorization']
)

# Global handler for CORS preflight OPTIONS requests
@app.before_request
def handle_preflight():
    if request.method == 'OPTIONS':
        response = app.make_default_options_response()
        return response

# Apply security headers to all responses
@app.after_request
@require_secure_headers
def after_request(response):
    return response

JOBS: Dict[str, Dict[str, Any]] = {}

# OAuth state uses cryptographically signed tokens (survives server restarts)
from itsdangerous import URLSafeTimedSerializer, SignatureExpired, BadSignature
OAUTH_STATE_TTL_SECONDS = int(os.getenv("OAUTH_STATE_TTL_SECONDS", "600"))
_oauth_serializer = URLSafeTimedSerializer(
    os.getenv("JWT_SECRET_KEY", "dev-fallback-key-change-in-production")
)

# Keep a consumed-set so each token can only be used once (within a single server run)
_CONSUMED_OAUTH_NONCES: Dict[str, float] = {}


def _create_oauth_state(user_id: str, origin: str) -> str:
    nonce = secrets.token_urlsafe(8)
    return _oauth_serializer.dumps({
        "user_id": str(user_id),
        "origin": origin,
        "nonce": nonce,
    })


def _consume_oauth_state(state_token: str) -> Dict[str, Any]:
    try:
        data = _oauth_serializer.loads(state_token, max_age=OAUTH_STATE_TTL_SECONDS)
    except (SignatureExpired, BadSignature):
        return {}

    nonce = data.get("nonce", "")
    if nonce in _CONSUMED_OAUTH_NONCES:
        return {}
    _CONSUMED_OAUTH_NONCES[nonce] = time.time()

    # Prune old nonces to prevent unbounded growth
    cutoff = time.time() - OAUTH_STATE_TTL_SECONDS * 2
    stale = [k for k, v in _CONSUMED_OAUTH_NONCES.items() if v < cutoff]
    for k in stale:
        _CONSUMED_OAUTH_NONCES.pop(k, None)

    return data


def _get_default_frontend_origin() -> str:
    # Explicit override takes precedence.
    explicit_origin = (
        os.getenv("FRONTEND_URL")
        or ""
    ).strip()
    if explicit_origin.startswith("http"):
        return explicit_origin

    http_origins = [
        origin for origin in allowed_origins
        if isinstance(origin, str) and origin.startswith("http")
    ]
    non_localhost = [
        origin for origin in http_origins
        if "localhost" not in origin and "127.0.0.1" not in origin
    ]

    # In production, prefer non-localhost origins to avoid wrong fallback target.
    if flask_env != "development":
        if non_localhost:
            return non_localhost[0]
        if http_origins:
            return http_origins[0]
        return "https://www.launchway.app/"

    # In development, prefer localhost origins first.
    if http_origins:
        return http_origins[0]
    return "http://localhost:3000"


def _build_frontend_redirect(path: str, params: Dict[str, Any]) -> str:
    """Build a frontend URL with query params for browser-based auth flows."""
    base_origin = (_get_default_frontend_origin() or "http://localhost:3000").rstrip("/")
    query = urlencode({k: v for k, v in (params or {}).items() if v is not None})
    if query:
        return f"{base_origin}{path}?{query}"
    return f"{base_origin}{path}"

# Initialize production infrastructure
def initialize_production_infrastructure():
    """Initialize all production infrastructure components"""
    try:
        # Initialize database tables if they don't exist
        from database_config import Base, engine, test_connection, _apply_incremental_migrations
        logging.info("Checking database connection...")
        if test_connection():
            logging.info("✅ Database connection successful")
            logging.info("Initializing database tables...")
            Base.metadata.create_all(bind=engine)
            _apply_incremental_migrations()
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

    logging.info("Sending resume text to Gemini for profile extraction")

    try:
        response = client.models.generate_content(
            model="gemini-2.0-flash",
            contents=prompt,
            config={
                "response_mime_type": "application/json"
            }
        )

        # Clean the response text to extract JSON
        response_text = response.text.strip()

        # Remove any markdown formatting if present
        if response_text.startswith('```json'):
            response_text = response_text[7:]
        if response_text.endswith('```'):
            response_text = response_text[:-3]
        if response_text.startswith('```'):
            response_text = response_text[3:]

        response_text = response_text.strip()

        try:
            profile_data = json.loads(response_text)

            # Handle case where Gemini returns an array instead of object
            if isinstance(profile_data, list) and len(profile_data) > 0:
                profile_data = profile_data[0]
            elif not isinstance(profile_data, dict):
                logging.error(f"process_resume_with_llm: unexpected response type {type(profile_data)}")
                return None

            validated_data = _validate_profile_data(profile_data, profile_schema)
            logging.info("Resume profile extraction successful")
            return validated_data
        except json.JSONDecodeError as json_err:
            logging.error(f"process_resume_with_llm: JSON parse error: {json_err}")
            return None
    except Exception as e:
        logging.error(f"process_resume_with_llm: Gemini error: {e}")
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

def _normalize_resume_text_for_hash(resume_text: str) -> str:
    """Normalize resume text to detect meaningful content changes."""
    if not resume_text:
        return ""
    return " ".join(resume_text.split()).strip().lower()

def _compute_resume_text_hash(resume_text: str) -> str:
    """Compute a stable hash for resume keyword cache invalidation."""
    normalized = _normalize_resume_text_for_hash(resume_text)
    if not normalized:
        return ""
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()

def extract_google_doc_with_oauth(resume_url: str, user_id: int) -> str:
    """
    Extract text from Google Doc using OAuth credentials (works with private docs)
    Falls back to public access if OAuth not connected
    """
    try:
        # Check if user has Google OAuth connected
        if GoogleOAuthService.is_connected(user_id):
            # Prefer OAuth access for connected users (supports private docs).
            try:
                logging.info(f"User {user_id} has OAuth connected, using authenticated access")
                credentials = GoogleOAuthService.get_credentials(user_id)

                doc_id = get_doc_id_from_url(resume_url)
                docs_service, _ = get_google_services(credentials)
                resume_text = read_google_doc_content(docs_service, doc_id)

                if resume_text and resume_text.strip():
                    logging.info(f"Successfully read private Google Doc via OAuth for user {user_id}")
                    return resume_text

                # OAuth returned empty text after retries - transient Google API issue.
                logging.warning(f"OAuth read returned empty text for user {user_id}")
                raise ValueError(
                    "Google's API returned an empty response. This is usually temporary - "
                    "please wait a moment and try again."
                )
            except ValueError:
                raise  # propagate our own clear messages unchanged
            except Exception as oauth_err:
                logging.warning(f"OAuth access failed for user {user_id}: {oauth_err}")
                oauth_err_text = str(oauth_err).lower()

                # Recover from revoked/expired Google tokens. This typically appears as
                # invalid_grant during refresh. In that case, clear stored OAuth tokens
                # and try public-link extraction as a graceful fallback.
                if "invalid_grant" in oauth_err_text:
                    try:
                        GoogleOAuthService.disconnect_google_account(user_id)
                        logging.info(f"Cleared stale Google OAuth tokens for user {user_id}")
                    except Exception as disconnect_err:
                        logging.warning(
                            f"Failed to clear stale Google OAuth tokens for user {user_id}: {disconnect_err}"
                        )

                    try:
                        logging.info(f"Falling back to public Google Doc access for user {user_id}")
                        return extract_resume_text(resume_url)
                    except Exception as public_err:
                        raise ValueError(
                            "Your Google account connection expired. Reconnect Google in the Resume menu, "
                            f"or make the doc viewable by link. Public fallback failed: {public_err}"
                        )

                raise ValueError(
                    f"Could not read your Google Doc (temporary error: {oauth_err}). "
                    "Please try again in a moment."
                )

        # No OAuth - try public access (doc must be shared as 'Anyone with the link')
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
        if response.status_code in (401, 403):
            raise ValueError(
                "This Google Doc is private. Please either:\n"
                "  • Connect your Google account (option in the Resume menu), or\n"
                "  • Set sharing to 'Anyone with the link can view' in Google Docs."
            )
        elif response.status_code == 404:
            raise ValueError("Google Doc not found. Please check the URL.")

        response.raise_for_status()

        # Return the text content
        return response.text.strip()
    except ValueError:
        raise  # propagate our own clear messages unchanged
    except Exception as e:
        logging.error(f"Error fetching Google Doc: {e}")
        err = str(e)
        if "401" in err or "403" in err:
            raise ValueError(
                "This Google Doc is private. Please either connect your Google account "
                "or set sharing to 'Anyone with the link can view'."
            )
        elif "404" in err:
            raise ValueError("Google Doc not found. Please check the URL.")
        else:
            raise ValueError(f"Could not access Google Doc: {err}")

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
@app.route('/api/health', methods=['GET'])
def health_check():
    """Basic health check endpoint - returns 200 if server is running"""
    return jsonify({
        "status": "healthy",
        "timestamp": time.time(),
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
            profile = dict(result.get('profile') or {})
            source_type = profile.get("resume_source_type", "google_doc")

            # Normalize resume fields by source type to avoid leaking stale/sensitive payloads.
            if source_type in ('pdf', 'docx'):
                profile["resume_url"] = ""
            else:
                profile["resume_text"] = ""
                profile["resume_filename"] = ""
                profile["resume_file_base64"] = ""

            return jsonify({
                "resumeData": profile,
                "resume_url": profile.get("resume_url", ""),
                "resume_source_type": source_type,
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
        return jsonify({"error": "Failed to get profile"}), 500

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
        return jsonify({"error": "Failed to save profile"}), 500

@app.route("/api/settings/ai-keys", methods=['GET'])
@require_auth
def get_ai_key_settings():
    """
    Return the user's current AI Engine configuration.
    The custom key is masked - only its last 4 chars are returned to the client.
    """
    try:
        user_id = request.current_user['id']
        result = ProfileService.get_complete_profile(user_id)
        if not result.get("success"):
            return jsonify({"error": "Profile not found"}), 404

        profile = result["profile"]
        primary_mode = profile.get("api_primary_mode") or None   # None = not yet configured
        secondary_mode = profile.get("api_secondary_mode") or None

        # Determine whether a custom key is saved (mask it for the client)
        has_custom_key = False
        masked_key = None
        try:
            from database_config import SessionLocal, UserProfile
            db = SessionLocal()
            db_profile = db.query(UserProfile).filter(UserProfile.user_id == user_id).first()
            if db_profile and db_profile.custom_gemini_api_key:
                has_custom_key = True
                decrypted = security_manager.decrypt_sensitive_data(db_profile.custom_gemini_api_key)
                masked_key = "•" * (len(decrypted) - 4) + decrypted[-4:]
            db.close()
        except Exception as key_err:
            logging.warning(f"Could not check custom key: {key_err}")

        return jsonify({
            "success": True,
            "api_primary_mode": primary_mode,
            "api_secondary_mode": secondary_mode,
            "has_custom_key": has_custom_key,
            "masked_custom_key": masked_key,
            "configured": primary_mode is not None,
        }), 200

    except Exception as e:
        logging.error(f"Error getting AI key settings: {e}", exc_info=True)
        return jsonify({"error": "Failed to get AI key settings"}), 500


@app.route("/api/settings/ai-keys", methods=['POST'])
@require_auth
def save_ai_key_settings():
    """
    Save the user's AI Engine configuration.

    Body (JSON):
        primary_mode   : 'launchway' | 'custom'   (required)
        secondary_mode : 'launchway' | 'custom' | null  (optional)
        custom_api_key : plain-text Gemini API key  (required when either mode == 'custom')
    """
    try:
        user_id = request.current_user['id']
        body = request.json or {}

        primary_mode = body.get("primary_mode", "").strip()
        secondary_mode = body.get("secondary_mode") or None
        custom_api_key_plain = (body.get("custom_api_key") or "").strip()

        # Validate
        valid_modes = {"launchway", "custom"}
        if primary_mode not in valid_modes:
            return jsonify({"error": "primary_mode must be 'launchway' or 'custom'"}), 400
        if secondary_mode and secondary_mode not in valid_modes:
            return jsonify({"error": "secondary_mode must be 'launchway', 'custom', or null"}), 400
        if primary_mode == secondary_mode and primary_mode:
            return jsonify({"error": "primary_mode and secondary_mode cannot be the same"}), 400
        if "custom" in {primary_mode, secondary_mode} and not custom_api_key_plain:
            return jsonify({"error": "A Gemini API key is required when using 'custom' mode"}), 400

        # Encrypt the key if provided
        encrypted_key = None
        if custom_api_key_plain:
            # Basic sanity check - Gemini keys start with "AIza"
            if not custom_api_key_plain.startswith("AIza"):
                return jsonify({"error": "That doesn't look like a valid Gemini API key (should start with 'AIza')"}), 400
            encrypted_key = security_manager.encrypt_sensitive_data(custom_api_key_plain)

        # Persist to profile
        update_payload = {
            "api_primary_mode": primary_mode,
            "api_secondary_mode": secondary_mode,
        }
        if encrypted_key is not None:
            update_payload["custom_gemini_api_key"] = encrypted_key

        ProfileService.create_or_update_profile(user_id, update_payload)

        return jsonify({
            "success": True,
            "message": "AI Engine settings saved.",
            "api_primary_mode": primary_mode,
            "api_secondary_mode": secondary_mode,
            "has_custom_key": bool(custom_api_key_plain),
        }), 200

    except Exception as e:
        logging.error(f"Error saving AI key settings: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


@app.route("/api/profile/keywords/extract", methods=['POST'])
@require_auth
@rate_limit('profile_keyword_extract_per_user_per_day')
@rate_limit('api_requests_per_user_per_minute')
def extract_profile_keywords():
    """
    Extract structured keywords from the authenticated user's resume using Gemini.
    Routes all Gemini calls through the user's configured GeminiKeyManager so that
    the user's chosen primary/secondary key method is always honoured.

    Optional JSON body:
        { "resume_text": "..." }   - supply raw text instead of fetching from URL
    """
    try:
        from resume_keyword_extractor import ResumeKeywordExtractor
        from agent_profile_service import AgentProfileService
        from gemini_key_manager import AiEngineNotConfiguredError

        user_id = request.current_user['id']
        body = request.json or {}

        # --- Guard: AI Engine must be configured ---
        try:
            key_manager = AgentProfileService.get_gemini_key_manager(user_id)
        except Exception:
            key_manager = None

        if key_manager is None or not key_manager.is_configured:
            return jsonify({
                "error": "ai_engine_not_configured",
                "message": "Please configure your AI Engine (primary key method) before using AI features.",
            }), 403

        keywords = None
        resume_text = ""
        profile_result = ProfileService.get_complete_profile(user_id)
        profile = profile_result.get("profile", {}) if profile_result.get("success") else {}
        existing_keywords = profile.get("resume_keywords") if isinstance(profile, dict) else {}
        if not isinstance(existing_keywords, dict):
            existing_keywords = {}
        existing_hash = str(existing_keywords.get("resume_text_hash") or "")

        # Option 1: caller supplies raw resume text
        raw_text = body.get("resume_text", "").strip()
        if raw_text:
            resume_text = raw_text

        # Option 2: fetch from the user's saved resume (URL or extracted text)
        if not resume_text:
            if not profile_result.get("success"):
                return jsonify({"error": "Profile not found"}), 404

            resume_url = profile.get("resume_url", "")
            stored_text = profile.get("resume_text", "")

            if resume_url:
                resume_text = extract_google_doc_with_oauth(resume_url, user_id)
                if not resume_text:
                    return jsonify({
                        "error": "Could not fetch resume. Make sure it is shared as 'Anyone with the link can view'."
                    }), 400
            elif stored_text:
                resume_text = stored_text
            else:
                return jsonify({
                    "error": "No resume found in your profile. Please upload a resume first."
                }), 400

        resume_hash = _compute_resume_text_hash(resume_text)
        has_cached_keywords = any(existing_keywords.get(k) for k in ("skills", "domains", "job_titles", "industries"))
        if resume_hash and existing_hash == resume_hash and has_cached_keywords:
            return jsonify({
                "success": True,
                "resume_keywords": existing_keywords,
                "cached": True,
                "message": "Resume text unchanged; using cached keywords."
            }), 200

        extractor = ResumeKeywordExtractor(key_manager=key_manager)
        keywords = extractor.extract_from_text(resume_text)

        if not keywords:
            return jsonify({"error": "Keyword extraction failed"}), 500

        if isinstance(keywords, dict):
            keywords["resume_text_hash"] = resume_hash

        ProfileService.create_or_update_profile(user_id, {"resume_keywords": keywords})
        return jsonify({
            "success": True,
            "resume_keywords": keywords,
            "cached": False
        }), 200

    except AiEngineNotConfiguredError as e:
        return jsonify({
            "error": "ai_engine_not_configured",
            "message": str(e),
        }), 403
    except Exception as e:
        logging.error(f"Error extracting resume keywords: {e}", exc_info=True)
        return jsonify({"error": "Keyword extraction failed"}), 500


@app.route("/api/process-resume", methods=['POST'])
@require_auth
@rate_limit('resume_processing_per_user_per_day')
@rate_limit('api_requests_per_user_per_minute')
def process_resume():
    try:
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

        # Persist resume_url + all LLM-extracted profile fields.
        # Explicitly clear PDF-specific fields so stale data from a previous
        # PDF/DOCX upload never bleeds into a Google Doc profile.
        try:
            from profile_service import ProfileService
            save_payload = {
                **profile_data,
                'resume_url': resume_url,
                'resume_source_type': 'google_doc',
                'resume_text': '',          # clear any previously stored PDF text
                'resume_filename': '',      # clear previously uploaded filename
                'resume_file_base64': '',   # clear previously uploaded file bytes
            }
            ProfileService.create_or_update_profile(user_id, save_payload)
        except Exception as persist_err:
            logging.warning(f"Could not persist resume data: {persist_err}")

        return jsonify({
            "profile_data": profile_data,
            "success": True,
            "message": "Resume processed successfully" + (" (using private Google Doc access)" if GoogleOAuthService.is_connected(user_id) else ""),
            'error': None
            }), 200

    except Exception as e:
        logging.error(f"Error processing resume: {e}")
        return jsonify({"error": "Failed to process resume"}), 500

@app.route("/api/upload-resume", methods=['POST'])
@require_auth
@rate_limit('resume_processing_per_user_per_day')
@rate_limit('api_requests_per_user_per_minute')
def upload_resume():
    """
    Handle PDF/DOCX resume file upload.
    - Extracts text directly (no Google account required).
    - Processes profile data with Gemini.
    - Stores extracted text + source type in the profile.
    - Resume tailoring is NOT available for PDF/DOCX - only Google Doc URLs support tailoring.
    """
    try:
        user_id = request.current_user['id']

        if 'resume' not in request.files:
            return jsonify({"error": "No file provided"}), 400

        file = request.files['resume']
        if file.filename == '':
            return jsonify({"error": "No file selected"}), 400

        filename = file.filename.lower()
        if not (filename.endswith('.pdf') or filename.endswith('.docx')):
            return jsonify({"error": "Only PDF and DOCX files are supported"}), 400

        file.seek(0, 2)
        file_size = file.tell()
        file.seek(0)
        if file_size > 10 * 1024 * 1024:
            return jsonify({"error": "File too large (maximum 10MB)"}), 400
        if file_size == 0:
            return jsonify({"error": "File is empty"}), 400

        # ── Extract text directly from the uploaded file ──────────────────
        file_bytes = file.read()
        resume_text = ""
        source_type = ""

        if filename.endswith('.pdf'):
            source_type = 'pdf'
            try:
                import io
                from PyPDF2 import PdfReader
                reader = PdfReader(io.BytesIO(file_bytes))
                pages = [p.extract_text() or "" for p in reader.pages]
                resume_text = "\n".join(pages).strip()
            except Exception as pdf_err:
                logging.error(f"PDF text extraction failed: {pdf_err}")
                return jsonify({"error": "Could not read PDF"}), 400

        elif filename.endswith('.docx'):
            source_type = 'docx'
            try:
                import io
                try:
                    import docx
                except ImportError:
                    return jsonify({
                        "error": "python-docx is not installed on the server. Please contact support or upload a PDF instead."
                    }), 500
                doc = docx.Document(io.BytesIO(file_bytes))
                resume_text = "\n".join(p.text for p in doc.paragraphs if p.text.strip()).strip()
            except Exception as docx_err:
                logging.error(f"DOCX text extraction failed: {docx_err}")
                return jsonify({"error": "Could not read DOCX"}), 400

        if not resume_text or len(resume_text) < 50:
            return jsonify({
                "error": (
                    "Could not extract enough text from the file. "
                    "Please ensure it contains selectable text (not a scanned image)."
                )
            }), 400

        logging.info(f"Extracted {len(resume_text)} chars from {source_type.upper()} for user {user_id}")

        # ── Process with LLM ──────────────────────────────────────────────
        profile_data = process_resume_with_llm(resume_text)
        if profile_data is None:
            return jsonify({"error": "Failed to process resume with Gemini", "success": False}), 500

        # ── Persist extracted text + source type + LLM-extracted profile fields ──
        import base64
        original_filename = file.filename or f'resume.{source_type}'
        try:
            save_payload = {
                **profile_data,                  # all LLM-extracted fields
                'resume_url': '',                # clear any previously saved Google Doc URL
                'resume_source_type': source_type,
                'resume_text': resume_text,
                'resume_filename': original_filename,
                'resume_file_base64': base64.b64encode(file_bytes).decode('utf-8'),
            }
            ProfileService.create_or_update_profile(user_id, save_payload)
        except Exception as persist_err:
            logging.warning(f"Could not persist resume data: {persist_err}")

        return jsonify({
            "success": True,
            "profile_data": profile_data,
            "resume_url": "",
            "source_type": source_type,
            "resume_filename": original_filename,
            "tailoring_available": False,
            "message": (
                f"{source_type.upper()} resume uploaded and profile populated successfully. "
                "⚠️ Resume tailoring is not available for PDF/DOCX uploads. "
                "To enable tailoring, please upload your resume as a Google Doc URL."
            ),
        }), 200

    except Exception as e:
        logging.error(f"Error uploading resume: {e}")
        return jsonify({"error": "Failed to upload resume"}), 500


@app.route("/api/upload-latex-resume", methods=['POST'])
@require_auth
@rate_limit('resume_processing_per_user_per_day')
@rate_limit('api_requests_per_user_per_minute')
def upload_latex_resume():
    """Handle LaTeX ZIP upload (Overleaf exports), store in profile, and parse profile data."""
    try:
        user_id = request.current_user['id']
        if 'resume_zip' not in request.files:
            return jsonify({"error": "No ZIP file provided"}), 400

        file = request.files['resume_zip']
        if file.filename == '':
            return jsonify({"error": "No ZIP file selected"}), 400

        filename = file.filename.lower()
        if not filename.endswith('.zip'):
            return jsonify({"error": "Only ZIP files are supported for LaTeX resumes"}), 400

        file.seek(0, 2)
        file_size = file.tell()
        file.seek(0)
        if file_size > 20 * 1024 * 1024:
            return jsonify({"error": "ZIP file too large (maximum 20MB)"}), 400
        if file_size == 0:
            return jsonify({"error": "ZIP file is empty"}), 400

        main_tex_file = (request.form.get('main_tex_file') or "").strip() or None
        zip_bytes = file.read()
        parsed = parse_latex_zip(zip_bytes, requested_main_tex=main_tex_file)

        # Extract profile data from plain-text version of LaTeX source
        profile_data = process_resume_with_llm(parsed.plain_text)
        if profile_data is None:
            return jsonify({
                "error": "Failed to process LaTeX resume with Gemini",
                "success": False
            }), 500

        # Persist LaTeX source and metadata to profile
        ProfileService.create_or_update_profile(user_id, {
            'resume_source_type': 'latex_zip',
            'resume_url': '',
            'latex_zip_base64': parsed.zip_base64,
            'latex_main_tex_path': parsed.main_tex_file,
            'latex_file_manifest': parsed.file_manifest,
            'latex_uploaded_at': datetime.utcnow(),
        })

        # Best-effort: compile and save a PDF so job-apply can use a real resume file path.
        pdf_generated = False
        pdf_path = None
        pdf_error = None
        try:
            project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            resumes_dir = os.path.join(project_root, "Resumes")
            os.makedirs(resumes_dir, exist_ok=True)
            pdf_path = os.path.join(resumes_dir, f"latex_resume_{user_id}.pdf")

            compile_result = compile_latex_zip_to_pdf(
                latex_zip_base64=parsed.zip_base64,
                main_tex_file=parsed.main_tex_file,
                output_pdf_path=pdf_path,
                timeout_seconds=90,
            )
            pdf_generated = bool(compile_result.get("success"))
            if not pdf_generated:
                pdf_path = None
                pdf_error = compile_result.get("error")
            else:
                # Update resume_url to point at local PDF path for automation usage
                ProfileService.create_or_update_profile(user_id, {
                    'resume_url': pdf_path,
                })
        except Exception as _pdf_e:
            pdf_generated = False
            pdf_path = None
            pdf_error = str(_pdf_e)

        return jsonify({
            "success": True,
            "message": "LaTeX ZIP uploaded successfully. Tailoring will use your stored LaTeX source.",
            "profile_data": profile_data,
            "resume_source_type": "latex_zip",
            "main_tex_file": parsed.main_tex_file,
            "tex_files": parsed.tex_files,
            "main_tex_preview": parsed.main_tex_preview,
            "main_plain_preview": parsed.main_plain_preview,
            "latex_file_manifest": parsed.file_manifest,
            "pdf_generated": pdf_generated,
            "pdf_path": pdf_path,
            "pdf_error": pdf_error,
        }), 200
    except Exception as e:
        logging.error(f"Error uploading LaTeX resume: {e}")
        return jsonify({"error": f"Failed to upload LaTeX resume: {str(e)}"}), 500


@app.route("/api/latex-resume/preview", methods=['GET'])
@require_auth
def get_latex_resume_preview():
    """Get preview text for the stored LaTeX resume source."""
    try:
        user_id = request.current_user['id']
        from database_config import SessionLocal, UserProfile
        db = SessionLocal()
        try:
            profile = db.query(UserProfile).filter(UserProfile.user_id == user_id).first()
            if not profile or not profile.latex_zip_base64 or not profile.latex_main_tex_path:
                return jsonify({
                    "success": False,
                    "error": "No LaTeX resume source found for this user."
                }), 404

            preview = get_main_tex_preview_from_base64(
                latex_zip_base64=profile.latex_zip_base64,
                main_tex_file=profile.latex_main_tex_path
            )
            return jsonify({
                "success": True,
                "main_tex_file": profile.latex_main_tex_path,
                "main_tex_preview": preview.get("main_tex_preview", ""),
                "main_plain_preview": preview.get("main_plain_preview", ""),
            }), 200
        finally:
            db.close()
    except Exception as e:
        logging.error(f"Error getting LaTeX preview: {e}")
        return jsonify({"error": f"Failed to get LaTeX preview: {str(e)}"}), 500


@app.route("/api/resume/pdf", methods=['GET'])
@require_auth
def download_resume_pdf():
    """
    Download the authenticated user's Google Doc resume as a PDF.

    Uses the stored Google OAuth credentials so private docs work without
    requiring the document to be publicly shared.  Accepts an optional
    ?url= query param to override the resume URL stored in the profile.

    Returns the raw PDF bytes (application/pdf).
    """
    try:
        user_id = request.current_user['id']

        resume_url = request.args.get('url', '').strip()
        if not resume_url:
            result = ProfileService.get_profile(user_id)
            resume_url = (result or {}).get('resume_url', '').strip()

        if not resume_url:
            return jsonify({"error": "No resume URL found in profile and none supplied via ?url="}), 400

        if 'docs.google.com' not in resume_url and 'drive.google.com' not in resume_url:
            return jsonify({"error": "URL is not a Google Docs / Drive URL"}), 400

        # Extract document ID
        import re as _re
        doc_match = _re.search(r'/(?:document|file)/d/([a-zA-Z0-9-_]+)', resume_url)
        if not doc_match:
            return jsonify({"error": "Could not parse document ID from URL"}), 400
        doc_id = doc_match.group(1)

        # Try OAuth export first (private doc support)
        credentials = GoogleOAuthService.get_credentials(user_id)
        if credentials:
            try:
                from googleapiclient.discovery import build as _build
                from googleapiclient.http import MediaIoBaseDownload as _DL
                import io as _io

                drive_svc = _build('drive', 'v3', credentials=credentials)
                req = drive_svc.files().export_media(fileId=doc_id, mimeType='application/pdf')
                buf = _io.BytesIO()
                dl = _DL(buf, req)
                done = False
                while not done:
                    _, done = dl.next_chunk()

                pdf_bytes = buf.getvalue()
                if pdf_bytes:
                    logging.info(f"Served resume PDF via OAuth for user {user_id} ({len(pdf_bytes)} bytes)")
                    return send_file(
                        _io.BytesIO(pdf_bytes),
                        mimetype='application/pdf',
                        as_attachment=True,
                        download_name='resume.pdf'
                    )
                logging.warning(f"OAuth export returned empty PDF for user {user_id}")
            except Exception as oauth_err:
                logging.warning(f"OAuth PDF export failed for user {user_id}: {oauth_err}")

        # Fallback: public export (works if doc is shared as 'Anyone with the link')
        export_url = f"https://docs.google.com/document/d/{doc_id}/export?format=pdf"
        resp = requests.get(export_url, timeout=30)
        if resp.status_code == 200 and 'application/pdf' in resp.headers.get('Content-Type', ''):
            import io as _io
            logging.info(f"Served resume PDF via public export for user {user_id}")
            return send_file(
                _io.BytesIO(resp.content),
                mimetype='application/pdf',
                as_attachment=True,
                download_name='resume.pdf'
            )

        if not credentials:
            return jsonify({
                "error": "Google account not connected. Connect your Google account in the app to allow private Doc access.",
                "google_not_connected": True,
            }), 403

        return jsonify({"error": "Could not export Google Doc as PDF. Check that the connected account has access."}), 500

    except Exception as e:
        logging.error(f"Error in download_resume_pdf: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/latex-resume/pdf", methods=['GET'])
@require_auth
def download_latex_resume_pdf():
    """Download compiled PDF for stored LaTeX resume (best-effort compile if missing)."""
    try:
        user_id = request.current_user['id']
        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        resumes_dir = os.path.join(project_root, "Resumes")
        os.makedirs(resumes_dir, exist_ok=True)
        pdf_path = os.path.join(resumes_dir, f"latex_resume_{user_id}.pdf")

        # If missing, try compiling from stored LaTeX
        if not os.path.exists(pdf_path):
            from database_config import SessionLocal, UserProfile
            db = SessionLocal()
            try:
                profile = db.query(UserProfile).filter(UserProfile.user_id == user_id).first()
                if not profile or not profile.latex_zip_base64 or not profile.latex_main_tex_path:
                    return jsonify({"success": False, "error": "No LaTeX resume source found for this user."}), 404

                compile_result = compile_latex_zip_to_pdf(
                    latex_zip_base64=profile.latex_zip_base64,
                    main_tex_file=profile.latex_main_tex_path,
                    output_pdf_path=pdf_path,
                    timeout_seconds=90,
                )
                if not compile_result.get("success"):
                    return jsonify({
                        "success": False,
                        "error": compile_result.get("error") or "Failed to compile LaTeX to PDF.",
                    }), 500
            finally:
                db.close()

        # Send as attachment
        try:
            return send_file(pdf_path, mimetype="application/pdf", as_attachment=True, download_name="resume.pdf")
        except TypeError:
            # Flask < 2.0 compatibility
            return send_file(pdf_path, mimetype="application/pdf", as_attachment=True, attachment_filename="resume.pdf")

    except Exception as e:
        logging.error(f"Error downloading LaTeX PDF: {e}")
        return jsonify({"error": f"Failed to download LaTeX PDF: {str(e)}"}), 500

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

@app.route("/api/search-jobs", methods=['POST'])
@require_auth
@rate_limit('job_search_per_user_per_day')
def search_jobs():
    """Search for jobs via Multi-Source Job Discovery Agent. Supports manual or profile-based params."""
    try:
        from Agents.multi_source_job_discovery_agent import MultiSourceJobDiscoveryAgent

        user_id = request.current_user['id']
        # Invalidate credits cache so frontend reflects fresh counts immediately
        try:
            from rate_limiter import redis_client as _rc
            _rc.delete(f"credits_cache:{user_id}")
        except Exception:
            pass
        data = request.json or {}

        min_relevance_score = data.get('min_relevance_score', 30)
        keywords = data.get('keywords')
        location = data.get('location')
        remote = data.get('remote', False)
        easy_apply = data.get('easy_apply', False)
        hours_old = data.get('hours_old')

        job_discovery_agent = MultiSourceJobDiscoveryAgent(user_id=user_id)

        if not job_discovery_agent.profile_data:
            return jsonify({"error": "Profile data not found for this user"}), 400

        logging.info(f"Searching for jobs (keywords={keywords}, min_relevance={min_relevance_score})...")

        if keywords:
            search_overrides = {'easy_apply': easy_apply}
            if hours_old:
                search_overrides['hours_old'] = hours_old
            result = job_discovery_agent.search_all_sources(
                min_relevance_score=min_relevance_score,
                manual_keywords=keywords,
                manual_location=location or None,
                manual_remote=remote,
                manual_search_overrides=search_overrides,
            )
            if 'error' in result:
                return jsonify({"error": result['error']}), 500
            jobs_data = result.get('data', [])
            sources = result.get('sources', {})
            avg_score = result.get('average_score', 0)
            saved_count = 0
            updated_count = 0
        else:
            response = job_discovery_agent.search_and_save(min_relevance_score=min_relevance_score)
            if 'error' in response:
                return jsonify({"error": response['error']}), 500
            jobs_data = response.get('jobs', [])
            sources = response.get('sources', {})
            avg_score = response.get('average_score', 0)
            saved_count = response.get('saved_count', 0)
            updated_count = response.get('updated_count', 0)

        return jsonify({
            "jobs": jobs_data,
            "total_found": len(jobs_data),
            "sources": sources,
            "average_score": avg_score,
            "saved_count": saved_count,
            "updated_count": updated_count,
            "success": True,
            "message": f"Jobs searched from {len(sources)} sources",
            "error": None,
        }), 200
    except Exception as e:
        logging.error(f"Error searching for jobs: {str(e)}")
        return jsonify({"error": str(e)}), 500

def _invalidate_credits_cache(user_id: str) -> None:
    try:
        from rate_limiter import redis_client as _rc
        _rc.delete(f"credits_cache:{user_id}")
    except Exception:
        pass


def _get_user_and_limit(user_id: str, limit_type: str):
    """Return (user_obj_or_none, effective_limit_int)."""
    from database_config import SessionLocal, User
    import uuid as uuid_module

    base_limit = int(rate_limiter.LIMITS[limit_type].requests)
    db = SessionLocal()
    try:
        user_uuid = uuid_module.UUID(str(user_id))
        user = db.query(User).filter(User.id == user_uuid).first()
        if not user:
            return None, base_limit
        bonus_limit = get_user_bonus_for_limit(user, limit_type)
        return user, effective_limit(base_limit, bonus_limit)
    except Exception:
        return None, base_limit
    finally:
        db.close()


@app.route("/api/credits/consume", methods=['POST'])
@require_auth
@rate_limit('api_requests_per_user_per_minute')
def consume_credit():
    """Consume one credit unit for a given service (called by CLI after a local task completes)."""
    try:
        user_id = request.current_user['id']
        data = request.json or {}
        service = data.get('service')

        SERVICE_MAP = {
            'resume_tailoring': 'resume_tailoring_per_user_per_day',
            'job_applications':  'job_applications_per_user_per_day',
            'job_search':        'job_search_per_user_per_day',
        }

        if service not in SERVICE_MAP:
            return jsonify({"error": f"Unknown service '{service}'. Valid: {list(SERVICE_MAP)}"}), 400

        limit_type = SERVICE_MAP[service]
        _, effective_daily_limit = _get_user_and_limit(str(user_id), limit_type)

        # check_limit() atomically checks AND increments the counter
        allowed, info = rate_limiter.check_limit(
            limit_type,
            str(user_id),
            custom_limit=effective_daily_limit
        )

        # Invalidate the per-user credits cache so GET /api/credits is always fresh
        _invalidate_credits_cache(str(user_id))

        if not allowed:
            return jsonify({
                "success":    False,
                "error":      "Daily limit reached",
                "remaining":  0,
                "limit":      info.get('limit'),
                "reset_time": info.get('reset_time'),
            }), 429

        return jsonify({
            "success":    True,
            "remaining":  info.get('remaining'),
            "limit":      info.get('limit'),
            "reset_time": info.get('reset_time'),
        }), 200

    except Exception as e:
        logging.error(f"Error consuming credit: {e}")
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

        # Resolve effective per-user limits (base + approved bug bounty bonus)
        user_obj, tailoring_effective_limit = _get_user_and_limit(
            str(user_id),
            'resume_tailoring_per_user_per_day'
        )
        _, applications_effective_limit = _get_user_and_limit(
            str(user_id),
            'job_applications_per_user_per_day'
        )
        search_effective_limit = int(rate_limiter.LIMITS['job_search_per_user_per_day'].requests)
        bonus_resume = int(getattr(user_obj, "bonus_resume_tailoring_max", 0) or 0)
        bonus_apply = int(getattr(user_obj, "bonus_job_applications_max", 0) or 0)

        # Get usage stats for different limit types (only if not cached)
        daily_tailoring = rate_limiter.get_usage_stats(
            'resume_tailoring_per_user_per_day',
            str(user_id),
            custom_limit=tailoring_effective_limit
        )
        daily_applications = rate_limiter.get_usage_stats(
            'job_applications_per_user_per_day',
            str(user_id),
            custom_limit=applications_effective_limit
        )
        daily_search = rate_limiter.get_usage_stats(
            'job_search_per_user_per_day',
            str(user_id),
            custom_limit=search_effective_limit
        )

        credits_info = {
            "is_admin": is_admin,
            "resume_tailoring": {
                "daily": {
                    "limit": "unlimited" if is_admin else daily_tailoring.get('limit', tailoring_effective_limit),
                    "used": 0 if is_admin else daily_tailoring.get('used', 0),
                    "remaining": "unlimited" if is_admin else daily_tailoring.get('remaining', tailoring_effective_limit),
                    "reset_time": daily_tailoring.get('reset_time'),
                    "window_hours": 24
                }
            },
            "job_applications": {
                "daily": {
                    "limit": "unlimited" if is_admin else daily_applications.get('limit', applications_effective_limit),
                    "used": 0 if is_admin else daily_applications.get('used', 0),
                    "remaining": "unlimited" if is_admin else daily_applications.get('remaining', applications_effective_limit),
                    "reset_time": daily_applications.get('reset_time'),
                    "window_hours": 24
                }
            },
            "job_search": {
                "daily": {
                    "limit": "unlimited" if is_admin else daily_search.get('limit', 20),
                    "used": 0 if is_admin else daily_search.get('used', 0),
                    "remaining": "unlimited" if is_admin else daily_search.get('remaining', 20),
                    "reset_time": daily_search.get('reset_time'),
                    "window_hours": 24
                }
            },
            "bonuses": {
                "resume_tailoring_bonus": 0 if is_admin else bonus_resume,
                "job_applications_bonus": 0 if is_admin else bonus_apply
            }
        }

        # Cache the credits info for 5 seconds to reduce Redis load
        try:
            import json
            from rate_limiter import redis_client
            redis_client.setex(cache_key, 5, json.dumps(credits_info))
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
        # Invalidate credits cache so frontend reflects fresh counts immediately
        try:
            from rate_limiter import redis_client as _rc
            _rc.delete(f"credits_cache:{user_id}")
        except Exception:
            pass
        data = request.json
        
        # Validate required fields
        job_description = data.get('job_description')
        resume_url = data.get('resume_url')
        if not job_description:
            return jsonify({"error": "Job description is required"}), 400

        # Determine current resume source mode from saved profile
        profile_result = ProfileService.get_profile(user_id)
        profile_data = profile_result.get('profile') if profile_result.get('success') else {}
        resume_source_type = (profile_data or {}).get('resume_source_type', 'google_doc')

        credentials = None
        credentials_dict = None
        if resume_source_type != 'latex_zip':
            if not resume_url:
                return jsonify({"error": "Resume URL is required for Google Docs tailoring"}), 400

            # Check if user has connected Google account
            if not GoogleOAuthService.is_connected(user_id):
                return jsonify({
                    "error": "Please connect your Google account first to tailor Google Docs resumes",
                    "needs_google_auth": True
                }), 403

            # Get user's Google credentials
            credentials = GoogleOAuthService.get_credentials(user_id)
            if not credentials:
                return jsonify({
                    "error": "Failed to retrieve Google credentials. Please reconnect your account.",
                    "needs_google_auth": True
                }), 403

            # Serialize credentials to JSON-compatible format
            credentials_dict = {
                'token': credentials.token,
                'refresh_token': credentials.refresh_token,
                'token_uri': credentials.token_uri,
                'client_id': credentials.client_id,
                'client_secret': credentials.client_secret,
                'scopes': list(credentials.scopes) if credentials.scopes else None
            }
        else:
            # In LaTeX mode, payload uses saved ZIP source from profile; no Google required.
            resume_url = None

        # Get user's full name from database
        from database_config import SessionLocal, User
        db = SessionLocal()
        try:
            user = db.query(User).filter(User.id == user_id).first()
            user_full_name = f"{user.first_name} {user.last_name}" if user else "Resume"
        finally:
            db.close()

        # Prepare job payload
        payload = {
            'resume_source_type': resume_source_type,
            'original_resume_url': resume_url,
            'job_description': job_description,
            'job_title': data.get('job_title', 'Unknown Position'),
            'company': data.get('company_name', 'Unknown Company'),
            'credentials': credentials_dict,
            'user_full_name': user_full_name,
            'latex_main_tex_path': (profile_data or {}).get('latex_main_tex_path')
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

# Auto job apply endpoint removed - feature only available via CLI

# Auto batch job apply endpoint removed - feature only available via CLI


# Authentication API Routes

@app.route("/api/auth/signup", methods=['POST', 'OPTIONS'])
def signup():
    """User registration endpoint"""
    # Handle CORS preflight request
    if request.method == 'OPTIONS':
        return '', 204
    
    try:
        data = request.json
        if not data:
            return jsonify({"error": "No data provided"}), 400

        # Validate required fields
        required_fields = ['email', 'password', 'first_name', 'last_name']
        for field in required_fields:
            if not data.get(field):
                pretty = field.replace('_', ' ').title()
                return jsonify({
                    "success": False,
                    "error": f"{pretty} is required",
                    "error_code": "validation_failed",
                    "field_errors": {field: f"{pretty} is required."}
                }), 400

        email = data['email'].strip().lower()
        password = data['password']
        first_name = data['first_name'].strip()
        last_name = data['last_name'].strip()

        # Field validation with actionable feedback
        field_errors = {}
        if '@' not in email:
            field_errors['email'] = "Please provide a valid email address."
        if len(password) < 8:
            field_errors['password'] = "Password must be at least 8 characters long."
        if not first_name:
            field_errors['first_name'] = "First name is required."
        if not last_name:
            field_errors['last_name'] = "Last name is required."

        # Beta request fields (included in the signup form)
        beta_request_reason = (data.get('beta_request_reason') or '').strip()
        survey_consent = bool(data.get('survey_consent'))
        if not beta_request_reason:
            field_errors['beta_request_reason'] = "Please tell us why you want beta access."
        elif len(beta_request_reason) < 20:
            field_errors['beta_request_reason'] = "Please provide at least 20 characters."
        if not survey_consent:
            field_errors['survey_consent'] = "Please agree to the weekly survey."

        if field_errors:
            return jsonify({
                "success": False,
                "error": "Please fix the highlighted fields.",
                "field_errors": field_errors,
                "error_code": "validation_failed"
            }), 400

        # Register user (beta request is baked in)
        result = AuthService.register_user(
            email, password, first_name, last_name,
            beta_request_reason=beta_request_reason,
            survey_consent=survey_consent,
        )

        if result['success']:
            return jsonify(result), 201
        else:
            status_code = 409 if result.get('error_code') == 'email_already_exists' else 400
            return jsonify(result), status_code

    except Exception as e:
        logging.error(f"Error in signup endpoint: {e}")
        return jsonify({"error": "Registration failed. Please try again."}), 500

@app.route("/api/auth/login", methods=['POST', 'OPTIONS'])
def login():
    """User login endpoint with IP-based rate limiting"""
    # Handle CORS preflight request
    if request.method == 'OPTIONS':
        return '', 204
    
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

        # beta_not_approved means the credentials ARE correct — count it as a
        # credential success so the account is never locked out for this reason.
        credentials_ok = result['success'] or result.get('beta_not_approved', False)

        # Record login attempt with IP address
        security_manager.record_login_attempt(
            identifier=email,
            success=credentials_ok,
            user_id=result.get('user', {}).get('id') if credentials_ok else None,
            ip_address=client_ip
        )

        if result['success']:
            return jsonify(result), 200
        elif result.get('beta_not_approved'):
            # Credentials are valid but beta not yet approved — return 200 so the
            # frontend can reliably detect the flag and redirect to /beta-pending.
            return jsonify(result), 200
        else:
            # Genuine auth failure — include remaining-attempts hint
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
        should_redirect = (request.args.get('redirect', '') or '').strip().lower() in ('1', 'true', 'yes')
        token = request.args.get('token')
        if not token:
            if should_redirect:
                return redirect(_build_frontend_redirect('/login', {
                    'verified': '0',
                    'message': 'Verification token is required'
                }))
            return jsonify({"error": "Verification token is required"}), 400

        # Verify email
        result = AuthService.verify_email(token)

        if should_redirect:
            if result.get('success'):
                return redirect(_build_frontend_redirect('/login', {
                    'verified': '1',
                    'message': result.get('message', 'Email verified successfully')
                }))
            return redirect(_build_frontend_redirect('/login', {
                'verified': '0',
                'message': result.get('error', 'Email verification failed')
            }))

        if result['success']:
            return jsonify(result), 200
        else:
            return jsonify(result), 400

    except Exception as e:
        logging.error(f"Error in verify email endpoint: {e}")
        return jsonify({"error": "Email verification failed"}), 500

@app.route("/api/auth/resend-verification", methods=['POST', 'OPTIONS'])
def resend_verification():
    """Resend verification email to user"""
    # Handle CORS preflight request
    if request.method == 'OPTIONS':
        return '', 204
    
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

@app.route("/api/feedback/bug-report", methods=['POST'])
@require_auth
def submit_bug_report():
    """Submit a structured beta bug report for admin review."""
    try:
        from database_config import SessionLocal, BugReport

        user_id = request.current_user['id']
        user_email = request.current_user.get('email', '')
        data = request.get_json() or {}

        is_valid, validation_error = validate_bug_report_payload(data)
        if not is_valid:
            return jsonify({"error": validation_error}), 400

        dedupe_key = build_dedupe_key(
            str(user_id),
            data.get("title", ""),
            data.get("steps_to_reproduce", ""),
            data.get("actual_behavior", ""),
        )

        db = SessionLocal()
        try:
            existing = db.query(BugReport).filter(
                BugReport.user_id == user_id,
                BugReport.dedupe_key == dedupe_key
            ).first()
            if existing:
                return jsonify({
                    "success": True,
                    "message": "A similar report is already under review.",
                    "report_id": existing.id,
                    "status": existing.status,
                    "duplicate": True
                }), 200

            severity = normalize_severity(data.get("severity"))
            report = BugReport(
                user_id=user_id,
                user_email=user_email,
                title=(data.get("title") or "").strip(),
                summary=(data.get("summary") or "").strip(),
                steps_to_reproduce=(data.get("steps_to_reproduce") or "").strip(),
                expected_behavior=(data.get("expected_behavior") or "").strip(),
                actual_behavior=(data.get("actual_behavior") or "").strip(),
                environment=(data.get("environment") or "").strip(),
                attachments_or_logs=(data.get("attachments_or_logs") or "").strip() or None,
                suggested_fix=(data.get("suggested_fix") or "").strip() or None,
                severity=severity,
                status="pending",
                dedupe_key=dedupe_key,
            )
            db.add(report)
            db.commit()
            db.refresh(report)

            return jsonify({
                "success": True,
                "message": "Bug report submitted successfully. We will review it shortly.",
                "report_id": report.id,
                "status": report.status,
            }), 201
        finally:
            db.close()
    except Exception as e:
        logging.error(f"Error submitting bug report: {e}")
        return jsonify({"error": "Failed to submit bug report"}), 500


@app.route("/api/admin/feedback/bug-reports", methods=['GET'])
@require_auth
@require_admin
def list_bug_reports():
    """List bug reports for admin moderation with optional status filter."""
    try:
        from database_config import SessionLocal, BugReport

        status_filter = (request.args.get("status") or "pending").strip().lower()
        severity_filter = (request.args.get("severity") or "").strip().lower()
        limit = min(max(int(request.args.get("limit", 100)), 1), 500)

        db = SessionLocal()
        try:
            query = db.query(BugReport)
            if status_filter and status_filter != "all":
                query = query.filter(BugReport.status == status_filter)
            if severity_filter and severity_filter in SEVERITY_REWARD_MAP:
                query = query.filter(BugReport.severity == severity_filter)

            reports = query.order_by(BugReport.submitted_at.desc()).limit(limit).all()
            return jsonify({
                "success": True,
                "reports": [
                    {
                        "id": report.id,
                        "user_id": str(report.user_id),
                        "user_email": report.user_email,
                        "title": report.title,
                        "summary": report.summary,
                        "steps_to_reproduce": report.steps_to_reproduce,
                        "expected_behavior": report.expected_behavior,
                        "actual_behavior": report.actual_behavior,
                        "environment": report.environment,
                        "attachments_or_logs": report.attachments_or_logs,
                        "suggested_fix": report.suggested_fix,
                        "severity": report.severity,
                        "status": report.status,
                        "admin_notes": report.admin_notes,
                        "rejection_reason": report.rejection_reason,
                        "reward_resume_bonus": report.reward_resume_bonus,
                        "reward_job_apply_bonus": report.reward_job_apply_bonus,
                        "cash_reward_amount": report.cash_reward_amount,
                        "cash_reward_note": report.cash_reward_note,
                        "submitted_at": report.submitted_at.isoformat() if report.submitted_at else None,
                        "processed_at": report.processed_at.isoformat() if report.processed_at else None,
                    }
                    for report in reports
                ]
            }), 200
        finally:
            db.close()
    except Exception as e:
        logging.error(f"Error listing bug reports: {e}")
        return jsonify({"error": "Failed to fetch bug reports"}), 500


@app.route("/api/admin/feedback/bug-reports/<int:report_id>/approve", methods=['POST'])
@require_auth
@require_admin
def approve_bug_report(report_id: int):
    """Approve a bug report and grant permanent per-user bonus limits."""
    try:
        from database_config import SessionLocal, BugReport, User
        from email_service import email_service
        import uuid as uuid_module

        data = request.get_json() or {}
        admin_notes = (data.get("admin_notes") or "").strip() or None
        selected_severity = normalize_severity(data.get("severity"))

        db = SessionLocal()
        try:
            report = db.query(BugReport).filter(BugReport.id == report_id).first()
            if not report:
                return jsonify({"error": "Bug report not found"}), 404

            # Idempotency guard: processed reports cannot be rewarded again.
            if report.status == "approved" and report.reward_applied_at:
                return jsonify({
                    "success": True,
                    "message": "Bug report already approved. Reward was already applied.",
                    "already_processed": True
                }), 200
            if report.status == "rejected":
                return jsonify({
                    "error": "This bug report was already rejected and cannot be approved."
                }), 409

            report.severity = selected_severity
            reward = get_reward_for_severity(selected_severity)
            reward_resume = int(reward["resume_bonus"])
            reward_apply = int(reward["job_apply_bonus"])

            user = db.query(User).filter(User.id == report.user_id).first()
            if not user:
                return jsonify({"error": "Report owner not found"}), 404

            user.bonus_resume_tailoring_max = int(user.bonus_resume_tailoring_max or 0) + reward_resume
            user.bonus_job_applications_max = int(user.bonus_job_applications_max or 0) + reward_apply

            report.status = "approved"
            report.admin_notes = admin_notes
            report.rejection_reason = None
            report.reward_resume_bonus = reward_resume
            report.reward_job_apply_bonus = reward_apply
            report.cash_reward_amount = data.get("cash_reward_amount")
            report.cash_reward_note = (data.get("cash_reward_note") or "").strip() or None
            report.reward_applied_at = datetime.utcnow()
            report.processed_at = datetime.utcnow()
            report.processed_by_admin_id = uuid_module.UUID(str(request.current_user["id"]))

            db.commit()

            _invalidate_credits_cache(str(report.user_id))

            try:
                email_service.send_bug_report_approved_email(
                    to_email=user.email,
                    first_name=user.first_name,
                    report_title=report.title,
                    severity=selected_severity,
                    reward_resume_bonus=reward_resume,
                    reward_job_apply_bonus=reward_apply,
                )
            except Exception as email_error:
                logging.error(f"Failed to send bug report approval email: {email_error}")

            return jsonify({
                "success": True,
                "message": "Bug report approved and reward applied.",
                "reward": {
                    "severity": selected_severity,
                    "resume_bonus": reward_resume,
                    "job_applications_bonus": reward_apply
                }
            }), 200
        finally:
            db.close()
    except Exception as e:
        logging.error(f"Error approving bug report: {e}")
        return jsonify({"error": "Failed to approve bug report"}), 500


@app.route("/api/admin/feedback/bug-reports/<int:report_id>/reject", methods=['POST'])
@require_auth
@require_admin
def reject_bug_report(report_id: int):
    """Reject a bug report with explicit reason and notify the reporter."""
    try:
        from database_config import SessionLocal, BugReport, User
        from email_service import email_service
        import uuid as uuid_module

        data = request.get_json() or {}
        rejection_reason = (data.get("rejection_reason") or "").strip()
        admin_notes = (data.get("admin_notes") or "").strip() or None
        if not rejection_reason:
            return jsonify({"error": "rejection_reason is required"}), 400

        db = SessionLocal()
        try:
            report = db.query(BugReport).filter(BugReport.id == report_id).first()
            if not report:
                return jsonify({"error": "Bug report not found"}), 404

            if report.status == "approved":
                return jsonify({"error": "Approved report cannot be rejected."}), 409
            if report.status == "rejected":
                return jsonify({
                    "success": True,
                    "message": "Bug report already rejected.",
                    "already_processed": True
                }), 200

            report.status = "rejected"
            report.rejection_reason = rejection_reason
            report.admin_notes = admin_notes
            report.processed_at = datetime.utcnow()
            report.processed_by_admin_id = uuid_module.UUID(str(request.current_user["id"]))

            db.commit()

            user = db.query(User).filter(User.id == report.user_id).first()
            if user:
                try:
                    email_service.send_bug_report_rejected_email(
                        to_email=user.email,
                        first_name=user.first_name,
                        report_title=report.title,
                        rejection_reason=rejection_reason,
                    )
                except Exception as email_error:
                    logging.error(f"Failed to send bug report rejection email: {email_error}")

            return jsonify({
                "success": True,
                "message": "Bug report rejected."
            }), 200
        finally:
            db.close()
    except Exception as e:
        logging.error(f"Error rejecting bug report: {e}")
        return jsonify({"error": "Failed to reject bug report"}), 500


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

@app.route("/api/account/email", methods=['PUT'])
@require_auth
def update_email():
    """Update the authenticated user's email address (CLI endpoint)."""
    try:
        from database_config import SessionLocal, User
        data     = request.get_json() or {}
        new_email = (data.get("email") or "").strip().lower()

        if not new_email or "@" not in new_email:
            return jsonify({"error": "A valid email address is required"}), 400

        user_id = request.current_user['id']
        db      = SessionLocal()
        try:
            # Check uniqueness
            existing = db.query(User).filter(User.email == new_email).first()
            if existing and str(existing.id) != str(user_id):
                return jsonify({"error": "Email already in use"}), 409

            user = db.query(User).filter(User.id == user_id).first()
            if not user:
                return jsonify({"error": "User not found"}), 404

            user.email = new_email
            db.commit()
            logging.info(f"Email updated for user {user_id}")
            return jsonify({"success": True, "message": "Email updated successfully"}), 200
        finally:
            db.close()
    except Exception as e:
        logging.error(f"Error updating email: {e}")
        return jsonify({"error": "Failed to update email"}), 500


@app.route("/api/account/request-email-change", methods=['POST', 'OPTIONS'])
@require_auth
def request_email_change():
    """Send a verification link to a new email address to confirm an email change."""
    if request.method == 'OPTIONS':
        return '', 204
    try:
        data = request.get_json() or {}
        new_email = (data.get('new_email') or '').strip().lower()
        if not new_email:
            return jsonify({'error': 'new_email is required'}), 400

        user_id = request.current_user['id']
        result = AuthService.request_email_change(user_id, new_email)
        if result['success']:
            return jsonify(result), 200
        return jsonify(result), 400
    except Exception as e:
        logging.error(f"Error in request_email_change: {e}")
        return jsonify({'error': 'Failed to initiate email change'}), 500


@app.route("/api/auth/verify-email-change", methods=['GET'])
def verify_email_change():
    """Confirm an email change using the token sent to the new address."""
    try:
        should_redirect = (request.args.get('redirect', '') or '').strip().lower() in ('1', 'true', 'yes')
        token = request.args.get('token')
        if not token:
            if should_redirect:
                return redirect(_build_frontend_redirect('/login', {
                    'email_change_verified': '0',
                    'message': 'Verification token is required'
                }))
            return jsonify({'error': 'Verification token is required'}), 400

        result = AuthService.verify_email_change(token)
        if should_redirect:
            if result.get('success'):
                return redirect(_build_frontend_redirect('/login', {
                    'email_change_verified': '1',
                    'message': result.get('message', 'Email updated successfully')
                }))
            return redirect(_build_frontend_redirect('/login', {
                'email_change_verified': '0',
                'message': result.get('error', 'Email change verification failed')
            }))

        if result['success']:
            return jsonify(result), 200
        return jsonify(result), 400
    except Exception as e:
        logging.error(f"Error in verify_email_change: {e}")
        return jsonify({'error': 'Email change verification failed'}), 500


@app.route("/api/account/info", methods=['GET'])
@require_auth
def get_account_info():
    """Return basic account info + application count (used by CLI)."""
    try:
        from database_config import SessionLocal, User, JobApplication
        user_id = request.current_user['id']
        db      = SessionLocal()
        try:
            user = db.query(User).filter(User.id == user_id).first()
            if not user:
                return jsonify({"error": "User not found"}), 404

            app_count = db.query(JobApplication).filter(
                JobApplication.user_id == user_id
            ).count()

            return jsonify({
                "success": True,
                "account": {
                    "user_id":        str(user.id),
                    "email":          user.email,
                    "pending_email":  user.pending_email or None,
                    "first_name":     user.first_name,
                    "last_name":      user.last_name,
                    "created_at":     user.created_at.isoformat() if user.created_at else None,
                    "email_verified": user.email_verified,
                    "is_active":      user.is_active,
                    "total_applications": app_count,
                },
            }), 200
        finally:
            db.close()
    except Exception as e:
        logging.error(f"Error fetching account info: {e}")
        return jsonify({"error": "Failed to fetch account info"}), 500


@app.route("/api/cli/applications", methods=['GET'])
@require_auth
def cli_get_applications():
    """Return the user's application history (used by CLI)."""
    try:
        from database_config import SessionLocal, JobApplication
        user_id    = request.current_user['id']
        limit      = min(int(request.args.get("limit", 50)), 200)
        urls_only  = request.args.get("urls_only", "false").lower() == "true"
        db         = SessionLocal()
        try:
            total_count = (
                db.query(JobApplication)
                .filter(JobApplication.user_id == user_id)
                .count()
            )
            query = (
                db.query(JobApplication)
                .filter(JobApplication.user_id == user_id)
                .order_by(JobApplication.created_at.desc())
                .limit(limit)
            )
            apps = query.all()

            if urls_only:
                return jsonify({
                    "success": True,
                    "total_count": total_count,
                    "urls": [
                        a.job_url for a in apps
                        if a.job_url and a.status in ("completed", "in_progress", "queued")
                    ],
                }), 200

            return jsonify({
                "success": True,
                "total_count": total_count,
                "returned_count": len(apps),
                "limit": limit,
                "applications": [
                    {
                        "id":         str(a.id),
                        "job_title":  a.job_title,
                        "company":    a.company_name,
                        "job_url":    a.job_url,
                        "status":     a.status,
                        "applied_at": a.applied_at.isoformat() if a.applied_at else None,
                        "created_at": a.created_at.isoformat() if a.created_at else None,
                    }
                    for a in apps
                ],
            }), 200
        finally:
            db.close()
    except Exception as e:
        logging.error(f"Error fetching CLI applications: {e}")
        return jsonify({"error": "Failed to fetch applications"}), 500


@app.route("/api/cli/applications", methods=['POST'])
@require_auth
def cli_record_application():
    """Record a completed job application from the CLI."""
    try:
        from database_config import SessionLocal, JobApplication
        from datetime import datetime
        user_id = request.current_user['id']
        data    = request.get_json() or {}

        job_url = (data.get("job_url") or "").strip()
        if not job_url:
            return jsonify({"error": "job_url is required"}), 400

        db = SessionLocal()
        try:
            application = JobApplication(
                user_id=user_id,
                job_id=f"cli_{datetime.utcnow().timestamp()}",
                company_name=data.get("company", "Unknown Company"),
                job_title=data.get("title", "Unknown Position"),
                job_url=job_url,
                status=data.get("status", "completed"),
                applied_at=datetime.utcnow(),
            )
            db.add(application)
            db.commit()
            return jsonify({"success": True, "message": "Application recorded"}), 201
        finally:
            db.close()
    except Exception as e:
        logging.error(f"Error recording CLI application: {e}")
        return jsonify({"error": "Failed to record application"}), 500


@app.route("/api/cli/agent-key", methods=['GET'])
@require_auth
def get_cli_agent_key():
    """
    Return the AES runtime key for local agent decryption (authenticated CLI only).
    Also returns shared service credentials so agents work out-of-the-box for
    users who chose "Launchway AI" (no personal API key).
    """
    runtime_key = (os.getenv("AGENT_RUNTIME_KEY") or "").strip()
    if not runtime_key:
        logging.error("CLI agent key request failed: AGENT_RUNTIME_KEY is not configured")
        return jsonify({
            "error": "AGENT_RUNTIME_KEY is not configured on the server."
        }), 500

    gemini_key = (os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY") or "").strip()
    shared_gemini_configured = bool(gemini_key)
    return jsonify({
        "key": runtime_key,
        "runtime_key_configured": True,
        "gemini_key": gemini_key,
        "shared_gemini_configured": shared_gemini_configured,
        "mimikree_url": os.getenv("MIMIKREE_BASE_URL", "https://www.mimikree.com"),
    }), 200


@app.route("/api/cli/apply", methods=['POST'])
@require_auth
@rate_limit('api_requests_per_user_per_minute')
def cli_submit_apply():
    """Submit a job application job to the server-side queue (CLI endpoint)."""
    try:
        from database_config import SessionLocal, UserProfile
        import uuid as uuid_module

        user_id = request.current_user['id']
        data = request.get_json() or {}

        job_url = (data.get("job_url") or "").strip()
        if not job_url:
            return jsonify({"error": "job_url is required"}), 400

        # Short idempotency window blocks rapid duplicate queue submissions.
        normalized_job_url = job_url.rstrip('/').lower()
        dedupe_hash = hashlib.sha256(f"{user_id}:{normalized_job_url}".encode('utf-8')).hexdigest()
        dedupe_key = f"cli_apply_dedupe:{dedupe_hash}"
        try:
            from rate_limiter import redis_client as _rc
            if not _rc.set(dedupe_key, "1", ex=120, nx=True):
                return jsonify({
                    "error": "Duplicate apply request detected. Please wait 2 minutes before retrying this URL."
                }), 409
        except Exception:
            # Continue if dedupe store is unavailable; rate limits still apply.
            pass

        tailor_resume_flag = data.get("tailor_resume", False)

        db = SessionLocal()
        try:
            user_uuid = uuid_module.UUID(str(user_id))
            profile = db.query(UserProfile).filter(UserProfile.user_id == user_uuid).first()
            resume_url = (profile.resume_url or "").strip() if profile else ""
        finally:
            db.close()

        if not resume_url:
            return jsonify({"error": "No resume URL found in your profile. Please add one first."}), 400

        payload = {
            'job_url': job_url,
            'resume_url': resume_url,
            'use_tailored': tailor_resume_flag,
        }

        result = submit_job_with_validation(
            user_id=user_id,
            job_type='job_application',
            payload=payload,
            priority=JobPriority.NORMAL,
        )

        if result['success']:
            return jsonify({
                "success": True,
                "job_id": result['job_id'],
                "message": "Job application submitted to queue.",
            }), 202
        else:
            return jsonify({"error": result['error'], "success": False}), 400

    except Exception as e:
        logging.error(f"Error submitting CLI apply job: {e}")
        return jsonify({"error": str(e)}), 500


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
        request_origin = request.headers.get("Origin", "")
        trusted_origin = request_origin if request_origin in allowed_origins else _get_default_frontend_origin()
        state_token = _create_oauth_state(user_id=user_id, origin=trusted_origin)
        auth_url = GoogleOAuthService.get_authorization_url(user_id, state_token)
        return jsonify({
            "success": True,
            "authorization_url": auth_url
        }), 200
    except Exception as e:
        logging.error(f"Error generating OAuth URL: {e}")
        return jsonify({"error": "Failed to generate OAuth authorization URL"}), 500

@app.route("/api/oauth/callback", methods=['GET'])
def oauth_callback():
    """Handle Google OAuth callback"""
    try:
        state_token = request.args.get('state')
        state_payload = _consume_oauth_state(state_token or "")
        callback_origin = state_payload.get("origin") or _get_default_frontend_origin()

        def _popup_html(success: bool, message: str, email: str = "", error_message: str = "") -> str:
            safe_message = html.escape(message or "")
            safe_email = html.escape(email or "")
            payload = {
                "type": "GOOGLE_AUTH_SUCCESS" if success else "GOOGLE_AUTH_ERROR",
                "email": email or "",
                "error": error_message or message or "",
            }
            payload_json = json.dumps(payload)
            target_origin_json = json.dumps(callback_origin)
            status_color = "#2e7d32" if success else "#d32f2f"
            status_title = "Authorization Successful!" if success else "Authorization Failed"
            return f"""
                <html>
                    <head>
                        <style>
                            body {{ font-family: Arial, sans-serif; text-align: center; padding: 50px; }}
                            .status {{ color: {status_color}; }}
                            .countdown {{ font-size: 14px; color: #666; margin-top: 10px; }}
                        </style>
                    </head>
                    <body>
                        <h2 class="status">{'✓' if success else '✗'} {status_title}</h2>
                        <p>{safe_message}</p>
                        {"<p>Email: " + safe_email + "</p>" if safe_email else ""}
                        <p class="countdown">This window will close automatically in <span id="countdown">3</span> seconds...</p>
                        <script>
                            const payload = {payload_json};
                            const targetOrigin = {target_origin_json};
                            if (window.opener && targetOrigin) {{
                                window.opener.postMessage(payload, targetOrigin);
                            }}

                            let countdown = 2;
                            const countdownElement = document.getElementById('countdown');
                            const interval = setInterval(() => {{
                                countdown--;
                                if (countdownElement) countdownElement.textContent = countdown;
                                if (countdown <= 0) {{
                                    clearInterval(interval);
                                    window.close();
                                }}
                            }}, 1000);
                        </script>
                    </body>
                </html>
            """

        if not state_payload:
            logging.warning("OAuth callback rejected due to invalid/expired state")
            return _popup_html(
                success=False,
                message="Invalid or expired OAuth session. Please try connecting again.",
                error_message="Invalid or expired OAuth session.",
            )

        # Check for error parameter from Google (user denied access, scope changed, etc.)
        error = request.args.get('error')
        if error:
            error_description = request.args.get('error_description', 'Authorization denied')
            logging.warning(f"OAuth error from Google: {error} - {error_description}")
            return _popup_html(
                success=False,
                message="Google denied authorization. Please try connecting again.",
                error_message=error_description,
            )

        code = request.args.get('code')

        if not code:
            return jsonify({"error": "Missing code parameter"}), 400

        user_id = state_payload["user_id"]
        result = GoogleOAuthService.handle_oauth_callback(code, user_id)

        if result['success']:
            return _popup_html(
                success=True,
                message="Your Google account has been connected successfully.",
                email=result.get('google_email', ''),
            )
        else:
            return _popup_html(
                success=False,
                message="Google account connection failed. Please try again.",
                error_message=result.get('error', 'Unknown error occurred'),
            )
    except Exception as e:
        logging.error(f"Error in OAuth callback: {e}")
        return jsonify({"error": "OAuth callback failed"}), 500

@app.route("/api/oauth/status", methods=['GET'])
@require_auth
def oauth_status():
    """
    Check if user has a valid, working Google connection.

    We go beyond a simple DB field check: we call get_credentials() which
    attempts a token refresh when the access token is expired.  If the refresh
    token itself is dead (invalid_grant / revoked), get_credentials() clears
    the stored tokens and returns None - we surface that as token_expired=True
    so the frontend can prompt a reconnect instead of silently showing stale
    "Connected" state.
    """
    try:
        user_id = request.current_user['id']

        # Fast path: no token in DB at all
        if not GoogleOAuthService.is_connected(user_id):
            return jsonify({
                "success": True,
                "is_connected": False,
                "token_expired": False,
                "google_email": None
            }), 200

        # Attempt to get (and if needed, refresh) valid credentials
        google_email = GoogleOAuthService.get_google_email(user_id)
        credentials = GoogleOAuthService.get_credentials(user_id)

        if credentials is None:
            # Refresh token was revoked / expired (invalid_grant).
            # get_credentials() already cleared the DB tokens.
            logging.info(f"Google token validation failed for user {user_id} - marking as expired")
            return jsonify({
                "success": True,
                "is_connected": False,
                "token_expired": True,
                "google_email": google_email  # keep email so UI can say "…for account X"
            }), 200

        return jsonify({
            "success": True,
            "is_connected": True,
            "token_expired": False,
            "google_email": google_email
        }), 200

    except Exception as e:
        logging.error(f"Error checking OAuth status: {e}")
        return jsonify({"error": "Failed to check OAuth status"}), 500

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
        return jsonify({"error": "Failed to disconnect Google account"}), 500

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

@app.route("/api/mimikree/credentials", methods=['GET'])
@require_auth
def get_mimikree_credentials():
    """
    Return the decrypted Mimikree credentials for the current user.

    Used exclusively by the CLI so the local resume-tailoring agent can
    authenticate against the (separate) Mimikree server.  The credentials
    are transmitted over HTTPS only, and only to the authenticated owner.
    """
    try:
        user_id = request.current_user['id']
        status  = mimikree_service.get_user_mimikree_status(user_id)
        if not status.get('success') or not status.get('is_connected'):
            return jsonify({"error": "Mimikree is not connected"}), 404

        creds = mimikree_service.get_user_mimikree_credentials(user_id)
        if not creds or not creds[0]:
            return jsonify({"error": "Mimikree credentials not found"}), 404

        email, password = creds
        return jsonify({"success": True, "email": email, "password": password}), 200

    except Exception as e:
        logging.error(f"Error fetching Mimikree credentials: {e}")
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
@require_admin
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
        return jsonify({"error": "Failed to get system status"}), 500

@app.route("/api/admin/job-queue/stats", methods=['GET'])
@require_auth
@require_admin
def get_job_queue_stats():
    """Get detailed job queue statistics"""
    try:
        return jsonify(job_queue.get_queue_stats()), 200
    except Exception as e:
        logging.error(f"Error getting job queue stats: {e}")
        return jsonify({"error": "Failed to get job queue stats"}), 500

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
@require_admin
def list_backups_api():
    """List all available backups"""
    try:
        backup_type = request.args.get('type')  # database, files, logs
        backups = backup_manager.list_backups(backup_type)
        return jsonify({"backups": backups}), 200
        
    except Exception as e:
        logging.error(f"Error listing backups: {e}")
        return jsonify({"error": "Failed to list backups"}), 500

@app.route("/api/admin/backups/create", methods=['POST'])
@require_auth
@require_admin
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
        return jsonify({"error": "Failed to create backup"}), 500

@app.route("/api/admin/backups/<backup_id>/restore", methods=['POST'])
@require_auth
@require_admin
def restore_backup_api(backup_id):
    """Restore from a backup"""
    try:
        # This is a dangerous operation - add additional security checks
        result = backup_manager.restore_database(backup_id)
        return jsonify(result), 200 if result.get('success') else 500
        
    except Exception as e:
        logging.error(f"Error restoring backup: {e}")
        return jsonify({"error": "Failed to restore backup"}), 500

@app.route("/api/admin/security/events", methods=['GET'])
@require_auth
@require_admin
def get_security_events_api():
    """Get recent security events"""
    try:
        limit = request.args.get('limit', 50, type=int)
        events = security_manager.get_security_events(limit)
        return jsonify({"events": events}), 200
        
    except Exception as e:
        logging.error(f"Error getting security events: {e}")
        return jsonify({"error": "Failed to get security events"}), 500

@app.route("/api/admin/security/audit", methods=['POST'])
@require_auth
@require_admin
def run_security_audit_api():
    """Run security audit"""
    try:
        audit_results = security_manager.run_security_audit()
        return jsonify(audit_results), 200
        
    except Exception as e:
        logging.error(f"Error running security audit: {e}")
        return jsonify({"error": "Failed to run security audit"}), 500

# ══════════════════════════════════════════════════════════════════════════════
#  Page reactions & visitor tracking  (public - no auth required)
# ══════════════════════════════════════════════════════════════════════════════

ALLOWED_REACTIONS = {
    "🔥": "I need this right now",
    "👀": "I'm keeping an eye on it",
    "🤔": "I still have questions",
    "😬": "Not for me",
}

def _get_ip_hash() -> str:
    """Return SHA-256 of the requester's IP - we never store raw IPs."""
    ip = (
        request.headers.get("X-Forwarded-For", "").split(",")[0].strip()
        or request.headers.get("X-Real-IP", "")
        or request.remote_addr
        or "unknown"
    )
    return hashlib.sha256(ip.encode()).hexdigest()


@app.route("/api/page-reactions", methods=["GET", "OPTIONS"])
def get_page_reactions():
    """Return all reactions and unique visitor count. Public endpoint."""
    try:
        from database_config import SessionLocal, PageReaction, PageVisit
        db = SessionLocal()
        try:
            total_reactions = db.query(PageReaction).count()
            reactions = (
                db.query(PageReaction)
                .order_by(PageReaction.created_at.desc())
                .limit(50)
                .all()
            )
            visitor_count = db.query(PageVisit).count()
            return jsonify({
                "reactions": [
                    {"emoji": r.emoji, "label": r.label, "id": r.id}
                    for r in reactions
                ],
                "total_reactions": total_reactions,
                "visitor_count": visitor_count,
            }), 200
        finally:
            db.close()
    except Exception as e:
        logging.error(f"Error fetching page reactions: {e}")
        return jsonify({"error": "Failed to fetch reactions"}), 500


@app.route("/api/page-reactions", methods=["POST"])
def post_page_reaction():
    """Submit a reaction. One per IP - duplicate IPs get a 409."""
    try:
        data = request.get_json(silent=True) or {}
        emoji = data.get("emoji", "").strip()
        label = ALLOWED_REACTIONS.get(emoji)

        if not label:
            return jsonify({"error": "Invalid reaction"}), 400

        ip_hash = _get_ip_hash()
        is_dev = os.getenv("FLASK_ENV", "production") == "development"

        from database_config import SessionLocal, PageReaction
        db = SessionLocal()
        try:
            existing = db.query(PageReaction).filter_by(ip_hash=ip_hash).first()
            if existing and not is_dev:
                return jsonify({"error": "already_reacted", "reaction": {"emoji": existing.emoji, "label": existing.label}}), 409
            if existing and is_dev:
                db.delete(existing)
                db.flush()

            reaction = PageReaction(ip_hash=ip_hash, emoji=emoji, label=label)
            db.add(reaction)
            db.commit()
            reaction_number = db.query(PageReaction).count()
            return jsonify({
                "success": True,
                "reaction": {"emoji": emoji, "label": label},
                "reaction_number": reaction_number,
            }), 201
        finally:
            db.close()
    except Exception as e:
        logging.error(f"Error saving page reaction: {e}")
        return jsonify({"error": "Failed to save reaction"}), 500



@app.route("/api/page-reactions/mine", methods=["DELETE"])
def delete_my_reaction():
    """Dev helper - deletes the reaction for the current IP so you can re-react."""
    try:
        ip_hash = _get_ip_hash()
        from database_config import SessionLocal, PageReaction
        db = SessionLocal()
        try:
            deleted = db.query(PageReaction).filter_by(ip_hash=ip_hash).delete()
            db.commit()
            return jsonify({"success": True, "deleted": deleted}), 200
        finally:
            db.close()
    except Exception as e:
        logging.error(f"Error deleting reaction: {e}")
        return jsonify({"error": "Failed to delete reaction"}), 500


@app.route("/api/page-visits", methods=["POST"])
def record_page_visit():
    """Record a unique visitor. Silently deduplicates by IP hash."""
    try:
        ip_hash = _get_ip_hash()
        from database_config import SessionLocal, PageVisit
        db = SessionLocal()
        try:
            existing = db.query(PageVisit).filter_by(ip_hash=ip_hash).first()
            if not existing:
                db.add(PageVisit(ip_hash=ip_hash))
                db.commit()
            count = db.query(PageVisit).count()
            return jsonify({"visitor_count": count}), 200
        finally:
            db.close()
    except Exception as e:
        logging.error(f"Error recording page visit: {e}")
        return jsonify({"error": "Failed to record visit"}), 500


if __name__ == "__main__":
    # Set up file logging for API server with DEBUG level to capture everything
    log_file = setup_file_logging(log_level=logging.DEBUG, console_logging=True)
    logging.info(f"API Server starting. Logs will be saved to: {log_file}")

    # Initialize production infrastructure (non-blocking - allow server to start)
    try:
        initialize_production_infrastructure()
    except Exception as e:
        logging.error(f"⚠️ Failed to initialize production infrastructure: {e}")
        logging.warning("⚠️ Server will start anyway - some features may be limited")
        logging.info("   Health check endpoint will be available")
        logging.info("   Readiness check will show component status")
        # Don't exit - let the server start so health checks work
        # Railway can detect the issue via /ready endpoint

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

    logging.info(f"🚀 Starting server on port {port}")
    logging.info(f"   Mode: {'DEVELOPMENT' if is_development else 'PRODUCTION'}")
    
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