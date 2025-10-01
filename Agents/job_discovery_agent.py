import os
import sys
import json
from google import genai
from dotenv import load_dotenv
from typing import Dict, Any, Tuple, List, Optional
import logging
import http.client
import urllib.parse

# Add parent directory to path for logging_config import
sys.path.append(os.path.join(os.path.dirname(__file__), '..'))
from logging_config import setup_file_logging

# Configure logging (will be overridden if setup_file_logging is called elsewhere)
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


class JobDiscoveryAgent:
    """Job Discovery Agent that uses Gemini 2.0 Flash to search for jobs"""

    query_parameters = {
        "type": "object",
        "properties": {
        "limit": {"type": "string"},
        "advanced_title_filter":{"type": "string"},
        "location_filter":{"type": "string"},
        "description_type":{"type": "string"},
        "date_filter": {"type": "string"},
        #"include_ai": {"type": "boolean"},
        #"ai_employement_type_filter": {"type": "string"},
        #"ai_work_arrangement_filter": {"type": "string"},
        #"ai_experience_level_filter": {"type": "string"},
        },
        "required": ["limit", "advanced_title_filter", "location_filter", "description_type", "date_filter"]
    }

    def __init__(self, user_id=None):
        self.user_id = user_id
        self.profile_data = self._load_profile_data()
        self.gemini_client = self._initialize_gemini()
        self.active_jobs_data = {}

    def _load_profile_data(self):
        """Load profile data from PostgreSQL database"""
        try:
            from agent_profile_service import AgentProfileService

            if self.user_id:
                profile = AgentProfileService.get_profile_by_user_id(self.user_id)
            else:
                # For backward compatibility, get the latest user's profile
                profile = AgentProfileService.get_latest_user_profile()

            if profile:
                logger.info(f"Successfully loaded profile for {profile.get('first name', 'N/A')} {profile.get('last name', 'N/A')} from database")
                return profile
            else:
                logger.error("No profile data found in database")
                return {}

        except Exception as e:
            logger.error(f"Error loading profile from database: {e}")
            return {}
    
    def _initialize_gemini(self):
        """Initialize Gemini 2.0 Flash client"""
        load_dotenv()

        try:
            api_key = os.getenv('GOOGLE_API_KEY')
            if not api_key:
                logger.error("GEMINI_API_KEY not found in environment variables")
                return None
            model= genai.Client(api_key=api_key)
            logger.info("Gemini API initialized successfully")
            return model

        except Exception as e:
            logger.error(f"Error initializing Gemini: {e}")
            return None
        
    def _construct_prompt(self):
        """Construct prompt for job discovery"""
        prompt = f"""
        You are a job discovery agent and your task is to create a single powerful search query that will result in the most relevant jobs for the user.
        Here is the user's profile:
        {json.dumps(self.profile_data, indent=2)}

        Return the query parameters in a JSON object with the following structure:
        {{
            "limit": 10,
            "advanced_title_filter": "",
            "location_filter": "",
            "description_type": "text",
            "date_filter": ""
        }}

        What to do:
        1. For limit, keep it as it is. That is 10.
        2. For advanced_title_filter use the following rules:
        Instead of using natural language like 'OR' you need to use operators like:
            1. & (AND)
            2. | (OR)
            3. ! (NOT)
            4. <-> (FOLLOWED BY)
            5. ' ' (FOLLOWED BY alternative, does not work with 6. Prefix Wildcard)
            6. :* (Prefix Wildcard)
        For example:

        (AI | 'Machine Learning' | 'Robotics') & ! Marketing

        Will return all jobs with ai, or machine learning, or robotics in the title except titles with marketing

        Project <-> Manag:*

        Will return jobs like Project Manager or Project Management

        3. For location_filter, use the following rules:
            1. Filter on location. Please do not search on abbreviations like US, UK, NYC. Instead, search on full names like United States, New York, United Kingdom.
            2. Here you wil strictly have to use OR operator.
            For example: Dubai OR Netherlands OR Belgium
        
        4. For description_type, keep it as it is. That is text.

        5. For date_filter, keep it as it is. That is empty.

        10. Return the query parameters in a JSON object with the exact structure shown above.
        """
        return prompt
    
    def _search_jobs(self):
        """Search for jobs using the query parameters"""
        print(self.query_parameters)
        prompt = self._construct_prompt()
        response = self.gemini_client.models.generate_content(
            model="gemini-2.0-flash",
            contents=prompt,
            config={
                "response_schema":self.query_parameters,
                "response_mime_type": "application/json"
            }
        )
        self.query_parameters = json.loads(response.text)
        return response.text
    
    def _build_api_url(self, query_parameters: Dict[str, Any]) -> str:
        """Build the API URL"""
        
        # Remove empty parameters
        api_params = {k: v for k, v in query_parameters.items() if v and v != '""'}

        # Build query string
        query_string = '&'.join([f"{k}={urllib.parse.quote(str(v))}" for k, v in api_params.items()])

        return f"/active-ats-7d?{query_string}"
    
    def search_jobs_with_rapidapi(self):
        """Search for jobs using RapidAPI"""
        try:
            # Get parameters from Gemini
            logger.info("Getting job search parameters from Gemini...")
            params_json = self._search_jobs()
            logger.info(f"Received parameters: {params_json}")
            
            # Parse the JSON response
            params = json.loads(params_json)
            
            # Build API URL
            api_url = self._build_api_url(params)
            logger.info(f"API URL: {api_url}")
            
            # Make API request
            conn = http.client.HTTPSConnection("active-jobs-db.p.rapidapi.com")
            
            headers = {
                'x-rapidapi-key': '5da97ff77emshe8c06807a5985e3p158ad3jsnbab5006c61bd',
                'x-rapidapi-host': 'active-jobs-db.p.rapidapi.com'
            }
            
            logger.info("Making API request to RapidAPI...")
            conn.request("GET", api_url, headers=headers)
            
            res = conn.getresponse()
            data = res.read()
            
            # Debug: Print raw response
            raw_response = data.decode("utf-8")
            logger.info(f"Raw API response: {raw_response[:500]}...")  # First 500 chars
            
            # Parse and return the response
            jobs_data = json.loads(raw_response)
            
            # Check if the response contains an error
            if isinstance(jobs_data, dict) and 'code' in jobs_data:
                logger.error(f"API Error: {jobs_data.get('message', 'Unknown error')}")
                return {"error": f"API Error: {jobs_data.get('message', 'Unknown error')}", "data": [], "count": 0}
            
            # Handle different response formats
            if isinstance(jobs_data, list):
                logger.info(f"Found {len(jobs_data)} jobs")
                return {"data": jobs_data, "count": len(jobs_data)}
            elif isinstance(jobs_data, dict):
                # Check if it has a data field
                if 'data' in jobs_data:
                    job_count = len(jobs_data.get('data', []))
                    logger.info(f"Found {job_count} jobs")
                    self.active_jobs_data = jobs_data
                    return jobs_data
                else:
                    # If no data field, treat the whole dict as the job data
                    logger.info(f"Found 1 job record")
                    return {"data": [jobs_data], "count": 1}
            else:
                logger.warning(f"Unexpected response format: {type(jobs_data)}")
                return {"data": [], "count": 0, "raw_response": jobs_data}
            
        except json.JSONDecodeError as e:
            logger.error(f"Error parsing JSON response from Gemini: {e}")
            return {"error": "Failed to parse Gemini response"}
        except Exception as e:
            logger.error(f"Error searching for jobs: {e}")
            return {"error": str(e)} 
    
    
if __name__ == "__main__":
    job_discovery_agent = JobDiscoveryAgent()
    jobs_data = job_discovery_agent.search_jobs_with_rapidapi()
    print(jobs_data)
    
