"""
Multi-Source Job Discovery Agent
Aggregates jobs from multiple sources and ranks them by relevance
"""

import os
import sys
import json
import logging
from typing import Dict, Any, List, Optional
from dotenv import load_dotenv
from google import genai

# Add parent directory to path
sys.path.append(os.path.join(os.path.dirname(__file__), '..'))
from logging_config import setup_file_logging

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Import our new modules
from job_api_adapters import JobAPIFactory, JobAPIAdapter
from job_relevance_scorer import rank_jobs


class MultiSourceJobDiscoveryAgent:
    """Job Discovery Agent that searches across multiple job boards"""

    def __init__(self, user_id=None):
        self.user_id = user_id
        self.profile_data = self._load_profile_data()
        self.adapters = JobAPIFactory.get_all_adapters()
        self.gemini_client = self._initialize_gemini()
        logger.info(f"Initialized with {len(self.adapters)} job API adapters")

    def _load_profile_data(self) -> Dict[str, Any]:
        """Load profile data from PostgreSQL database OR JSON file (based on env settings)"""
        # Check environment variables for development mode
        run_mode = os.getenv('RUN_MODE', 'Production')
        dev_settings = os.getenv('DEV_SETTINGS', 'Use_database')
        
        logger.info(f"🔧 RUN_MODE: {run_mode}, DEV_SETTINGS: {dev_settings}")
        
        try:
            # Development mode with JSON file
            if run_mode == 'Development' and dev_settings == 'Dont_use_database':
                logger.info("📁 Loading profile from JSON file (Development mode)")
                
                # Get path to profile_data.json
                current_dir = os.path.dirname(os.path.abspath(__file__))
                project_root = os.path.dirname(current_dir)
                json_path = os.path.join(project_root, 'ProfileBuilder', 'profile_data.json')
                
                if not os.path.exists(json_path):
                    logger.error(f"❌ profile_data.json not found at: {json_path}")
                    return {}
                
                with open(json_path, 'r', encoding='utf-8') as f:
                    profile = json.load(f)
                
                logger.info(f"✅ Loaded profile from JSON: {profile.get('first name', 'N/A')} {profile.get('last name', 'N/A')}")
                return profile
            
            # Production mode OR Development with database
            else:
                logger.info("🗄️ Loading profile from PostgreSQL database")
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
            logger.error(f"Error loading profile: {e}")
            return {}

    def _initialize_gemini(self):
        """Initialize Gemini 2.0 Flash client"""
        load_dotenv()

        try:
            api_key = os.getenv('GOOGLE_API_KEY')
            if not api_key:
                logger.error("GOOGLE_API_KEY not found in environment variables")
                return None
            model = genai.Client(api_key=api_key)
            logger.info("Gemini API initialized successfully")
            return model

        except Exception as e:
            logger.error(f"Error initializing Gemini: {e}")
            return None

    def _build_query_params(self) -> Dict[str, Any]:
        """Build query parameters from user profile"""
        profile = self.profile_data

        # Extract keywords from profile
        keywords = self._extract_keywords()

        # Build location query
        location = self._build_location_query()

        # Build query parameters
        params = {
            "keywords": keywords,
            "location": location,
            "limit": 20,  # Per source
            "page": 1,
            "remote_only": profile.get("open_to_remote", False),
            "date_posted": "month",  # Last month
        }

        # Add employment types
        desired_job_types = profile.get("desired_job_types", [])
        if desired_job_types:
            # Map to API-specific formats
            employment_types = []
            for jt in desired_job_types:
                if jt.lower() == "full-time":
                    employment_types.append("FULLTIME")
                elif jt.lower() == "part-time":
                    employment_types.append("PARTTIME")
                elif jt.lower() == "contract":
                    employment_types.append("CONTRACTOR")
                elif jt.lower() == "internship":
                    employment_types.append("INTERN")
            params["employment_types"] = employment_types

        # Add experience level filter
        desired_levels = profile.get("desired_experience_levels", [])
        years_exp = profile.get("years_of_experience", 0)

        if years_exp < 3:
            params["job_requirements"] = "under_3_years_experience"
        elif years_exp >= 3:
            params["job_requirements"] = "more_than_3_years_experience"

        # Add salary filter (if supported by API)
        min_salary = profile.get("minimum_salary")
        if min_salary:
            params["salary_min"] = min_salary

        max_salary = profile.get("maximum_salary")
        if max_salary:
            params["salary_max"] = max_salary

        logger.info(f"Built query parameters: {params}")
        return params

    def _extract_keywords(self) -> str:
        """Extract search keywords from profile"""
        profile = self.profile_data
        keywords = []

        # Get job titles from work experience
        work_exp = profile.get("work_experience", [])
        if work_exp and isinstance(work_exp, list) and len(work_exp) > 0:
            latest_job = work_exp[0]
            if isinstance(latest_job, dict):
                title = latest_job.get("title", "")
                if title:
                    keywords.append(title)

        # Get top skills
        skills = profile.get("skills", {})
        technical_skills = skills.get("technical", [])
        if technical_skills and isinstance(technical_skills, list):
            # Take top 3 technical skills
            keywords.extend(technical_skills[:3])

        # Get programming languages
        prog_langs = skills.get("programming_languages", [])
        if prog_langs and isinstance(prog_langs, list):
            keywords.extend(prog_langs[:2])

        # If no keywords found, use a default
        if not keywords:
            keywords = ["software engineer"]

        # Join keywords
        keyword_str = " ".join(keywords)
        logger.info(f"Extracted keywords: {keyword_str}")
        return keyword_str

    def _build_location_query(self) -> str:
        """Build location query from profile"""
        profile = self.profile_data

        # If open to anywhere or remote, return empty (search all)
        if profile.get("open_to_anywhere", False):
            return ""

        if profile.get("open_to_remote", False):
            return "Remote"

        # Check preferred cities
        preferred_cities = profile.get("preferred_cities", [])
        if preferred_cities and isinstance(preferred_cities, list) and len(preferred_cities) > 0:
            # Use first preferred city
            return preferred_cities[0]

        # Check preferred states
        preferred_states = profile.get("preferred_states", [])
        if preferred_states and isinstance(preferred_states, list) and len(preferred_states) > 0:
            # Use first preferred state
            return preferred_states[0]

        # Check user's current location
        city = profile.get("city", "")
        state = profile.get("state", "")
        if city and state:
            return f"{city}, {state}"
        elif state:
            return state
        elif city:
            return city

        # Default to country
        country = profile.get("country", "United States")
        return country

    def search_all_sources(self, min_relevance_score: int = 30) -> Dict[str, Any]:
        """
        Search all job sources and aggregate results

        Args:
            min_relevance_score: Minimum relevance score to include (0-100)

        Returns:
            {
                "data": [jobs sorted by relevance],
                "count": total_count,
                "sources": {source_name: count},
                "average_score": float
            }
        """
        try:
            query_params = self._build_query_params()
            all_jobs = []
            source_counts = {}

            # Search each source
            for adapter in self.adapters:
                try:
                    logger.info(f"Searching {adapter.api_name}...")

                    # Adapt query params for specific adapter
                    adapted_params = self._adapt_params_for_source(query_params, adapter)

                    result = adapter.search_jobs(adapted_params)

                    if result.get("data"):
                        jobs = result["data"]
                        all_jobs.extend(jobs)
                        source_counts[adapter.api_name] = len(jobs)
                        logger.info(f"{adapter.api_name}: Found {len(jobs)} jobs")
                    else:
                        source_counts[adapter.api_name] = 0
                        logger.warning(f"{adapter.api_name}: No jobs found")

                except Exception as e:
                    logger.error(f"Error searching {adapter.api_name}: {e}")
                    source_counts[adapter.api_name] = 0

            logger.info(f"Total jobs before deduplication: {len(all_jobs)}")

            # Deduplicate jobs
            unique_jobs = self._deduplicate_jobs(all_jobs)
            logger.info(f"Total jobs after deduplication: {len(unique_jobs)}")

            # Rank jobs by relevance
            if self.profile_data:
                ranked_jobs = rank_jobs(unique_jobs, self.profile_data, min_score=min_relevance_score)
            else:
                ranked_jobs = unique_jobs
                logger.warning("No profile data available, skipping ranking")

            logger.info(f"Total jobs after filtering (min_score={min_relevance_score}): {len(ranked_jobs)}")

            # Calculate average score
            avg_score = sum(job.get("relevance_score", 0) for job in ranked_jobs) / len(ranked_jobs) if ranked_jobs else 0

            return {
                "data": ranked_jobs,
                "count": len(ranked_jobs),
                "sources": source_counts,
                "average_score": round(avg_score, 2),
                "total_before_filter": len(unique_jobs)
            }

        except Exception as e:
            logger.error(f"Error in search_all_sources: {e}")
            return {
                "data": [],
                "count": 0,
                "sources": {},
                "average_score": 0,
                "error": str(e)
            }

    def _generate_api_params_with_gemini(self, adapter: JobAPIAdapter) -> Dict[str, Any]:
        """Use Gemini to generate optimal API parameters for a specific job source"""
        if not self.gemini_client:
            logger.warning("Gemini client not initialized, falling back to basic params")
            return self._build_query_params()

        # Define API parameter structures for each source
        api_specs = {
            "TheMuse": {
                "description": "The Muse API - Focus on company culture and detailed job descriptions",
                "parameters": {
                    "page": "The page number to load (default: 1)",
                    "category": f"Job category - MUST choose ONE from: {', '.join(['Data Science', 'Software Engineering', 'Design and UX', 'Product Management', 'Marketing', 'Sales', 'Data and Analytics', 'Human Resources and Recruiting', 'IT', 'Science and Engineering'])}",
                    "level": f"Experience level - MUST choose ONE from: {', '.join(['Entry Level', 'Mid Level', 'Senior Level', 'management', 'Internship'])}",
                    "locations": "Array of job locations - IMPORTANT: 'Flexible / Remote' is a valid location option. Use ONLY 1-2 locations max (API may treat multiple as AND). Recommend: user's PRIMARY preferred city/state OR country, plus 'Flexible / Remote'. Examples: ['United States', 'Flexible / Remote'] or ['New York, NY', 'Flexible / Remote']"
                },
                "schema": {
                    "type": "object",
                    "properties": {
                        "page": {"type": "integer"},
                        "category": {"type": "string"},
                        "level": {"type": "string"},
                        "locations": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "List of locations including cities, states, and 'Flexible / Remote'"
                        }
                    },
                    "required": ["page"]
                }
            },
            "JSearch": {
                "description": "JSearch API (RapidAPI) - Best quality job data",
                "parameters": {
                    "query": "Job title, keywords, or company name",
                    "page": "Page number (default: 1)",
                    "num_pages": "Number of pages (default: 1, max: 5)",
                    "date_posted": "Filter by date: all, today, 3days, week, month",
                    "remote_jobs_only": "true/false",
                    "employment_types": "Comma-separated: FULLTIME, CONTRACTOR, PARTTIME, INTERN"
                },
                "schema": {
                    "type": "object",
                    "properties": {
                        "keywords": {"type": "string"},
                        "page": {"type": "integer"},
                        "num_pages": {"type": "integer"},
                        "date_posted": {"type": "string"},
                        "remote_only": {"type": "boolean"},
                        "employment_types": {"type": "array", "items": {"type": "string"}},
                        "location": {"type": "string"}
                    },
                    "required": ["keywords"]
                }
            },
            "Adzuna": {
                "description": "Adzuna API - Free tier, good coverage",
                "parameters": {
                    "what": "Keywords (keep it simple, 2-3 words max)",
                    "where": "Location (city or state)",
                    "page": "Page number",
                    "results_per_page": "Number of results (max: 50)",
                    "max_days_old": "Filter by posting date (days)"
                },
                "schema": {
                    "type": "object",
                    "properties": {
                        "keywords": {"type": "string"},
                        "location": {"type": "string"},
                        "page": {"type": "integer"},
                        "limit": {"type": "integer"},
                        "max_days_old": {"type": "integer"}
                    },
                    "required": ["keywords"]
                }
            },
            "TheirStack": {
                "description": "TheirStack API - Largest database of jobs and technographics with advanced filtering",
                "parameters": {
                    "posted_at_max_age_days": "Max age in days (REQUIRED - default: 30 for last month)",
                    "job_title_pattern_or": "Array of regex patterns for job titles (e.g., ['data scientist', 'machine learning engineer'])",
                    "job_country_code_or": "Array of ISO2 country codes (e.g., ['US', 'CA'])",
                    "remote": "Boolean - true for only remote jobs, false for non-remote, null for all",
                    "min_salary_usd": "Minimum annual salary in USD",
                    "max_salary_usd": "Maximum annual salary in USD",
                    "employment_statuses_or": "Array of employment types: ['full_time', 'part_time', 'contract', 'internship', 'temporary']",
                    "limit": "Number of results (default: 25, max recommended: 50)"
                },
                "schema": {
                    "type": "object",
                    "properties": {
                        "posted_at_max_age_days": {"type": "integer", "description": "Required - days since posting"},
                        "job_title_pattern_or": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Regex patterns for job titles"
                        },
                        "job_country_code_or": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "ISO2 country codes like US, CA, GB"
                        },
                        "remote": {"type": "boolean", "description": "Filter for remote jobs"},
                        "min_salary_usd": {"type": "integer"},
                        "max_salary_usd": {"type": "integer"},
                        "employment_statuses_or": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Employment types"
                        },
                        "limit": {"type": "integer", "description": "Number of results"}
                    },
                    "required": ["posted_at_max_age_days"]
                }
            }
        }

        api_spec = api_specs.get(adapter.api_name)
        if not api_spec:
            # Fallback to basic params for unknown adapters
            return self._build_query_params()

        prompt = f"""
You are a job search optimization expert. Given a user's profile, generate the BEST API parameters to find the MAXIMUM number of RELEVANT jobs.

**User Profile:**
{json.dumps(self.profile_data, indent=2)}

**API: {adapter.api_name}**
{api_spec['description']}

**Available Parameters:**
{json.dumps(api_spec['parameters'], indent=2)}

**Instructions:**
1. Analyze the user's profile carefully (work experience, skills, education, preferences)
2. Generate optimal parameters that will return the MAXIMUM relevant jobs
3. For category/level fields, choose the SINGLE BEST option from the allowed values
4. For The Muse API locations field: Use ONLY 1-2 locations (API may treat multiple as AND condition). ALWAYS include "Flexible / Remote". Use either user's country OR primary city, NOT both. Examples: ["United States", "Flexible / Remote"] or ["San Francisco, CA", "Flexible / Remote"]
5. Use broad but relevant search terms to maximize results
6. Return ONLY a JSON object matching the schema below - NO explanations, NO markdown

**Response Schema:**
{json.dumps(api_spec['schema'], indent=2)}

Return the optimized parameters as JSON:
"""

        try:
            logger.info(f"Generating {adapter.api_name} parameters with Gemini...")

            response = self.gemini_client.models.generate_content(
                model='gemini-2.0-flash-exp',
                contents=prompt,
                config={
                    "response_mime_type": "application/json",
                    "response_schema": api_spec['schema']
                }
            )

            params = json.loads(response.text)
            logger.info(f"{adapter.api_name} parameters from Gemini: {params}")
            return params

        except Exception as e:
            logger.error(f"Error generating params with Gemini for {adapter.api_name}: {e}")
            return self._build_query_params()

    def _adapt_params_for_source(self, params: Dict[str, Any], adapter: JobAPIAdapter) -> Dict[str, Any]:
        """Adapt generic query parameters for specific API adapter - DEPRECATED, use Gemini instead"""
        # Use Gemini to generate API-specific parameters
        if adapter.api_name in ["TheMuse", "JSearch", "Adzuna", "TheirStack"]:
            return self._generate_api_params_with_gemini(adapter)

        # Fallback for older adapters
        adapted = params.copy()

        # Active Jobs DB uses different parameter format
        if adapter.api_name == "ActiveJobsDB":
            # Convert keywords to advanced_title_filter format
            keywords = params.get("keywords", "")
            keyword_list = keywords.split()

            # Build advanced title filter with OR operators
            if keyword_list:
                advanced_filter = " | ".join([f"'{kw}'" for kw in keyword_list])
                adapted["advanced_title_filter"] = advanced_filter

            # Location filter
            location = params.get("location", "")
            if location:
                adapted["location_filter"] = location

            # Date filter
            adapted["date_filter"] = ""

        return adapted

    def _deduplicate_jobs(self, jobs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Deduplicate jobs based on URL or title+company combination
        """
        seen = set()
        unique_jobs = []

        for job in jobs:
            # Create unique identifier
            job_url = job.get("job_url", "")
            title = job.get("title", "").lower().strip()
            company = job.get("company", "").lower().strip()

            # Use URL as primary identifier
            if job_url and job_url in seen:
                continue
            elif job_url:
                seen.add(job_url)
                unique_jobs.append(job)
            else:
                # Use title+company as fallback
                identifier = f"{title}|{company}"
                if identifier not in seen and title and company:
                    seen.add(identifier)
                    unique_jobs.append(job)

        return unique_jobs

    def search_and_save(self, min_relevance_score: int = 30) -> Dict[str, Any]:
        """
        Search for jobs and save to database

        Args:
            min_relevance_score: Minimum relevance score to include (0-100)

        Returns:
            Result dictionary with saved jobs
        """
        try:
            # Search all sources
            result = self.search_all_sources(min_relevance_score)

            if result.get("data"):
                # Save to database
                from job_search_service import JobSearchService

                jobs_to_save = result["data"]

                save_result = JobSearchService.save_job_listings(
                    user_id=self.user_id,
                    jobs_data=jobs_to_save,
                    search_source="multi_source"
                )

                logger.info(f"Saved {save_result.get('saved_count', 0)} new jobs, updated {save_result.get('updated_count', 0)} existing jobs")

                return {
                    "success": True,
                    "jobs": jobs_to_save,
                    "count": result["count"],
                    "sources": result["sources"],
                    "average_score": result["average_score"],
                    "saved_count": save_result.get("saved_count", 0),
                    "updated_count": save_result.get("updated_count", 0)
                }
            else:
                return {
                    "success": True,
                    "jobs": [],
                    "count": 0,
                    "sources": result.get("sources", {}),
                    "message": "No jobs found matching criteria"
                }

        except Exception as e:
            logger.error(f"Error in search_and_save: {e}")
            return {
                "success": False,
                "error": str(e),
                "jobs": [],
                "count": 0
            }


if __name__ == "__main__":
    # Test the agent
    agent = MultiSourceJobDiscoveryAgent()
    result = agent.search_and_save(min_relevance_score=30)

    print(f"\nSearch Results:")
    print(f"Total jobs found: {result.get('count', 0)}")
    print(f"Average relevance score: {result.get('average_score', 0)}")
    print(f"Sources: {result.get('sources', {})}")
    print(f"Saved to database: {result.get('saved_count', 0)}")

    # Print top 5 jobs
    if result.get("jobs"):
        print(f"\nTop 5 Jobs:")
        for i, job in enumerate(result["jobs"][:5], 1):
            print(f"{i}. {job.get('title')} at {job.get('company')} - Score: {job.get('relevance_score', 0)}")
