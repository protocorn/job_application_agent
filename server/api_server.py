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

sys.path.append(os.path.dirname(__file__))
sys.path.append(os.path.join(os.path.dirname(__file__), '..', 'Agents'))
from resume_tailoring_agent import get_google_services, get_doc_id_from_url, copy_google_doc, read_google_doc_content, apply_json_replacements_to_doc, tailor_resume as tailor_resume_with_agent, tailor_resume_and_return_url


#Initialize the app
app = Flask(__name__)
CORS(app)

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
    prompt = f"""
     You are an expert at extracting information from resumes and structuring it into a standardized profile format.

    Please analyze the following resume text and extract the relevant information to populate the profile schema.

    Resume Text:
    {resume_text}

    Please return a JSON object that matches this exact schema structure. Fill in only the fields where you can find relevant information from the resume. Leave empty strings for missing information and empty arrays for missing lists.

    Schema:
    {json.dumps(profile_schema, indent=2)}

    Important instructions:
        1. Extract the person's name and split it into first name and last name
        2. Look for contact information (email, phone, address, city, state, zip, country)
        3. Extract education history with degrees, institutions, graduation years, GPAs, and relevant courses
        4. Extract work experience with job titles, companies, start/end dates, descriptions, and achievements
        5. Extract projects with names, descriptions, technologies used, GitHub URLs, and live URLs
        6. Extract and categorize skills and technologies mentioned throughout the resume:
           - technical: Core technical skills (e.g., Machine Learning, Data Science, Cloud Computing, Database Design)
           - programming_languages: Programming languages (e.g., Python, JavaScript, Java, C++, SQL)
           - frameworks: Frameworks and libraries (e.g., React, Django, TensorFlow, Spring, Express.js)
           - tools: Tools and technologies (e.g., AWS, Docker, Git, PostgreSQL, MongoDB, Jenkins)
           - soft_skills: Soft skills and competencies (e.g., Leadership, Communication, Problem Solving, Team Management)
           - languages: Spoken languages with proficiency levels (e.g., English (Native), Spanish (Fluent))
        7. Create a professional summary based on the resume content (2-3 sentences highlighting key strengths)
        8. Look for LinkedIn, GitHub, and other professional links
        9. For arrays (education, work experience, projects, skills categories), create multiple entries if multiple items exist
        10. Return the data in the exact same JSON structure as the schema

        CRITICAL LOCATION FORMATTING RULES:
        - For country: Use full official names (e.g., "United States of America", "United Kingdom", "Canada", "India")
        - For state/province: Use full state names (e.g., "California", "New York", "Texas", "Ontario", "Maharashtra")
        - For city: Use standard city names as they appear in official databases
        - For zip/postal code: Extract the complete postal code (e.g., "90210", "M5H 2N2", "400001")
        - If location information is incomplete or unclear, leave those fields empty rather than guessing

        Examples of correct formatting:
        - Country: "United States of America" (NOT "USA", "US", or "United States")
        - State: "California" (NOT "CA" or "Calif.")
        - City: "San Francisco" (NOT "SF" or "San Fran")
        - ZIP: "94102" (complete postal code)

        Return only the JSON object, no additional text or formatting.
    """

    print(f"Sending prompt to Gemini")

    try:
        response = client.models.generate_content(
            model="gemini-2.0-flash",
            contents=prompt,
            config={
                "response_schema":profile_schema,
                "response_mime_type": "application/json"
            }
        )
        profile_data = json.loads(response.text.strip())
        return _validate_profile_data(profile_data, profile_schema)
    except Exception as e:
        print(f"Error sending prompt to Gemini: {e}")
        return None

def _validate_profile_data(profile_data: Dict[str, Any], schema: Dict[str, Any]) -> Dict[str, Any]:
    """Validate and clean the profile data to match the schema"""
    validated_data = {}
    
    for key, default_value in schema.items():
        if key in profile_data:
            value = profile_data[key]
            
            # Validate based on expected type
            if isinstance(default_value, str):
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
def get_profile():
    """Get user profile data"""
    try:
        profile_path = os.path.join(os.path.dirname(__file__), '..', 'ProfileBuilder', 'profile_data.json')
        
        with open(profile_path, 'r', encoding='utf-8') as file:
            profile_data = json.load(file)
        return jsonify({
            "resumeData": profile_data,
            "resume_url": profile_data.get("resume_url", ""),
            "success": True,
            "message": "Profile fetched successfully",
            "error": None
            }), 200
    except Exception as e:
        logging.error(f"Error getting profile: {e}")
        return jsonify({"error": f"Failed to get profile: {str(e)}"}), 500

@app.route("/api/profile", methods=["POST"])
def save_profile():
    """Save user profile data"""
    try:
        profile_data = request.json
        profile_path = os.path.join(os.path.dirname(__file__), '..', 'ProfileBuilder', 'profile_data.json')
        
        # Ensure directory exists
        os.makedirs(os.path.dirname(profile_path), exist_ok=True)
        
        # Save profile data
        with open(profile_path, 'w', encoding='utf-8') as file:
            json.dump(profile_data, file, indent=2, ensure_ascii=False)
        
        return jsonify({
            "success": True,
            "message": "Profile saved successfully"
        }), 200
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
        return jsonify({
            "profile_data": profile_data,
            "success": True,
            "message": "Resume processed successfully",
            'error': None
            }), 200
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        logging.error(f"Error processing resume: {str(e)}")
        return jsonify({"error": str(e)}), 500

@app.route("/api/search-jobs", methods=['POST'])
def search_jobs():
    """ Search for jobs using Job Discovery Agent"""
    try:
        import sys
        sys.path.append(os.path.join(os.path.dirname(__file__), '..'))
        from Agents.job_discovery_agent import JobDiscoveryAgent

        job_discovery_agent = JobDiscoveryAgent()

        if not job_discovery_agent.profile_data:
            return jsonify({"error": "Profile data not found"}), 400

        
        logging.info("Searching for jobs...")
        response = job_discovery_agent.search_jobs_with_rapidapi()

        return jsonify({
            "jobs": response['data'],
            "total_found": response['count'],
            "success": True,
            "message": "Jobs searched successfully",
            "error": None
            }), 200
    except Exception as e:
        logging.error(f"Error searching for jobs: {str(e)}")
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



@app.route("/api/health", methods=['GET'])
def health():
    return jsonify({"status": "ok"})

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=5000, debug=True)