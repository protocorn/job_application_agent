"""
Job API Adapters - Multi-source job search integration
Supports: JSearch (RapidAPI), Adzuna, Active Jobs DB, Google Jobs, SerpAPI, The Muse, TheirStack
"""

import os
import http.client
import urllib.parse
import json
import logging
import requests
from typing import Dict, List, Any, Optional
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)


class JobAPIAdapter:
    """Base adapter class for job APIs"""

    def __init__(self):
        self.api_name = "Base"

    def search_jobs(self, query_params: Dict[str, Any]) -> Dict[str, Any]:
        """
        Search for jobs using the API
        Returns: {"data": [...], "count": int, "source": str}
        """
        raise NotImplementedError("Subclasses must implement search_jobs")

    def normalize_job(self, raw_job: Dict[str, Any]) -> Dict[str, Any]:
        """
        Normalize job data to a standard format
        Standard format:
        {
            "title": str,
            "company": str,
            "location": str,
            "salary": str,
            "description": str,
            "requirements": str,
            "job_url": str,
            "posted_date": str,
            "job_type": str (full-time, part-time, contract, internship),
            "experience_level": str (entry, mid, senior, lead, executive),
            "is_remote": bool,
            "salary_min": int,
            "salary_max": int,
            "salary_currency": str
        }
        """
        raise NotImplementedError("Subclasses must implement normalize_job")


class JSearchAdapter(JobAPIAdapter):
    """JSearch API Adapter (RapidAPI) - Best quality data"""

    def __init__(self):
        super().__init__()
        self.api_name = "JSearch"
        self.api_key = os.getenv('JSEARCH_RAPIDAPI_KEY')
        self.host = "jsearch.p.rapidapi.com"

    def search_jobs(self, query_params: Dict[str, Any]) -> Dict[str, Any]:
        """
        JSearch Parameters:
        - query: Job title, keywords, or company name
        - page: Page number (default: 1)
        - num_pages: Number of pages (default: 1, max: 20)
        - date_posted: all, today, 3days, week, month
        - remote_jobs_only: true/false
        - employment_types: FULLTIME, CONTRACTOR, PARTTIME, INTERN
        - job_requirements: under_3_years_experience, more_than_3_years_experience, no_experience, no_degree
        - job_titles: Exact job title filter
        - company_types: Any company type filter
        - country: Country code (US, GB, etc.)
        - radius: Distance from location in km
        """
        try:
            if not self.api_key:
                logger.warning("JSearch API key not found")
                return {"data": [], "count": 0, "source": self.api_name}

            # Build query parameters
            params = {
                "query": query_params.get("keywords", "software engineer"),
                "page": str(query_params.get("page", 1)),
                "num_pages": str(min(query_params.get("num_pages", 1), 5)),  # Limit pages
            }

            # Optional parameters
            if query_params.get("date_posted"):
                params["date_posted"] = query_params["date_posted"]

            if query_params.get("remote_only"):
                params["remote_jobs_only"] = "true"

            if query_params.get("employment_types"):
                params["employment_types"] = ",".join(query_params["employment_types"])

            if query_params.get("job_requirements"):
                params["job_requirements"] = query_params["job_requirements"]

            if query_params.get("location"):
                params["query"] += f" in {query_params['location']}"

            # Build URL
            query_string = "&".join([f"{k}={urllib.parse.quote(str(v))}" for k, v in params.items()])

            conn = http.client.HTTPSConnection(self.host)
            headers = {
                'x-rapidapi-key': self.api_key,
                'x-rapidapi-host': self.host
            }

            logger.info(f"JSearch: Searching with params: {params}")
            conn.request("GET", f"/search?{query_string}", headers=headers)

            res = conn.getresponse()
            data = res.read()
            response = json.loads(data.decode("utf-8"))

            if response.get("status") == "OK" and "data" in response:
                jobs = response["data"]
                logger.info(f"JSearch: Found {len(jobs)} jobs")

                # Normalize all jobs
                normalized_jobs = [self.normalize_job(job) for job in jobs]

                return {
                    "data": normalized_jobs,
                    "count": len(normalized_jobs),
                    "source": self.api_name
                }
            else:
                logger.warning(f"JSearch: No jobs found or error: {response.get('message', 'Unknown error')}")
                return {"data": [], "count": 0, "source": self.api_name}

        except Exception as e:
            logger.error(f"JSearch API error: {e}")
            return {"data": [], "count": 0, "source": self.api_name}

    def normalize_job(self, raw_job: Dict[str, Any]) -> Dict[str, Any]:
        """Normalize JSearch job data"""
        return {
            "title": raw_job.get("job_title", ""),
            "company": raw_job.get("employer_name", ""),
            "location": raw_job.get("job_city", "") or raw_job.get("job_state", "") or raw_job.get("job_country", ""),
            "salary": self._format_salary(raw_job),
            "description": raw_job.get("job_description", ""),
            "requirements": raw_job.get("job_highlights", {}).get("Qualifications", []),
            "job_url": raw_job.get("job_apply_link", ""),
            "posted_date": raw_job.get("job_posted_at_datetime_utc", ""),
            "job_type": self._map_employment_type(raw_job.get("job_employment_type", "")),
            "experience_level": self._map_experience_level(raw_job),
            "is_remote": raw_job.get("job_is_remote", False),
            "salary_min": raw_job.get("job_min_salary"),
            "salary_max": raw_job.get("job_max_salary"),
            "salary_currency": raw_job.get("job_salary_currency", "USD")
        }

    def _format_salary(self, job: Dict[str, Any]) -> str:
        """Format salary information"""
        min_sal = job.get("job_min_salary")
        max_sal = job.get("job_max_salary")
        currency = job.get("job_salary_currency", "USD")

        if min_sal and max_sal:
            return f"{currency} {min_sal:,} - {max_sal:,}"
        elif min_sal:
            return f"{currency} {min_sal:,}+"
        elif max_sal:
            return f"{currency} Up to {max_sal:,}"
        return ""

    def _map_employment_type(self, emp_type: str) -> str:
        """Map employment type to standard format"""
        mapping = {
            "FULLTIME": "full-time",
            "PARTTIME": "part-time",
            "CONTRACTOR": "contract",
            "INTERN": "internship"
        }
        return mapping.get(emp_type.upper(), "full-time")

    def _map_experience_level(self, job: Dict[str, Any]) -> str:
        """Infer experience level from job data"""
        title = job.get("job_title", "").lower()

        if any(word in title for word in ["senior", "sr.", "lead", "principal", "staff"]):
            return "senior"
        elif any(word in title for word in ["junior", "jr.", "entry", "associate", "grad"]):
            return "entry"
        elif any(word in title for word in ["intern", "internship"]):
            return "internship"
        elif any(word in title for word in ["director", "vp", "head of", "chief"]):
            return "executive"
        else:
            return "mid"


class AdzunaAdapter(JobAPIAdapter):
    """Adzuna API Adapter - Free tier available, good coverage"""

    def __init__(self):
        super().__init__()
        self.api_name = "Adzuna"
        self.app_id = os.getenv('ADZUNA_APP_ID')
        self.app_key = os.getenv('ADZUNA_APP_KEY')
        self.base_url = "https://api.adzuna.com/v1/api/jobs"

    def search_jobs(self, query_params: Dict[str, Any]) -> Dict[str, Any]:
        """
        Adzuna Parameters:
        - what: Keywords
        - where: Location
        - results_per_page: Number of results (default: 20, max: 50)
        - page: Page number
        - sort_by: date, relevance, salary
        - max_days_old: Filter by posting date (days)
        - salary_min: Minimum salary
        - salary_max: Maximum salary
        - full_time: 0 or 1
        - part_time: 0 or 1
        - contract: 0 or 1
        - permanent: 0 or 1
        """
        try:
            if not self.app_id or not self.app_key:
                logger.warning("Adzuna API credentials not found")
                return {"data": [], "count": 0, "source": self.api_name}

            # Country code (default: us)
            country = query_params.get("country", "us").lower()

            # Page number goes in the URL path, not query params!
            page = query_params.get("page", 1)

            # Build parameters - ONLY query parameters, NOT path parameters
            params = {
                "app_id": self.app_id,
                "app_key": self.app_key,
                "results_per_page": min(query_params.get("limit", 20), 50)
            }

            # Simplify keywords - Adzuna doesn't like too many keywords
            keywords = query_params.get("keywords", "software engineer")
            # Take first 2-3 main keywords only
            keyword_parts = keywords.split()[:3]
            params["what"] = " ".join(keyword_parts)

            # Optional parameters
            if query_params.get("location"):
                # Adzuna prefers simple location formats
                # Extract just city or state, not "City, State"
                location = query_params["location"]
                # If location has comma, try just the state/second part
                if "," in location:
                    parts = [p.strip() for p in location.split(",")]
                    # Use state (second part) for better results
                    params["where"] = parts[-1] if len(parts) > 1 else parts[0]
                else:
                    params["where"] = location

            if query_params.get("max_days_old"):
                params["max_days_old"] = query_params["max_days_old"]

            if query_params.get("salary_min"):
                params["salary_min"] = query_params["salary_min"]

            if query_params.get("salary_max"):
                params["salary_max"] = query_params["salary_max"]

            if query_params.get("full_time"):
                params["full_time"] = 1

            if query_params.get("part_time"):
                params["part_time"] = 1

            if query_params.get("contract"):
                params["contract"] = 1

            # Page goes in the URL path: /us/search/{page}
            url = f"{self.base_url}/{country}/search/{page}"
            logger.info(f"Adzuna: Searching with URL: {url}")
            logger.info(f"Adzuna: Params: {params}")

            response = requests.get(url, params=params, timeout=30)

            # Log response for debugging
            if response.status_code != 200:
                logger.error(f"Adzuna: HTTP {response.status_code} - {response.text[:500]}")
                return {"data": [], "count": 0, "source": self.api_name}

            data = response.json()
            jobs = data.get("results", [])
            logger.info(f"Adzuna: Found {len(jobs)} jobs")

            # Normalize all jobs
            normalized_jobs = [self.normalize_job(job) for job in jobs]

            return {
                "data": normalized_jobs,
                "count": len(normalized_jobs),
                "source": self.api_name
            }

        except requests.exceptions.HTTPError as e:
            logger.error(f"Adzuna HTTP error: {e}")
            if hasattr(e.response, 'text'):
                logger.error(f"Response: {e.response.text[:500]}")
            return {"data": [], "count": 0, "source": self.api_name}
        except Exception as e:
            logger.error(f"Adzuna API error: {e}")
            return {"data": [], "count": 0, "source": self.api_name}

    def normalize_job(self, raw_job: Dict[str, Any]) -> Dict[str, Any]:
        """Normalize Adzuna job data"""
        # Build proper details URL instead of using redirect_url which triggers bot detection
        job_id = raw_job.get("id", "")
        job_url = f"https://www.adzuna.com/details/{job_id}" if job_id else raw_job.get("redirect_url", "")

        # Add query params from redirect_url if available
        redirect_url = raw_job.get("redirect_url", "")
        if redirect_url and "?" in redirect_url:
            query_params = redirect_url.split("?")[1]
            job_url = f"{job_url}?{query_params}"

        return {
            "title": raw_job.get("title", ""),
            "company": raw_job.get("company", {}).get("display_name", ""),
            "location": raw_job.get("location", {}).get("display_name", ""),
            "salary": self._format_salary(raw_job),
            "description": raw_job.get("description", ""),
            "requirements": "",  # Adzuna doesn't separate requirements
            "job_url": job_url,  # Use details URL instead of redirect URL
            "posted_date": raw_job.get("created", ""),
            "job_type": self._map_contract_type(raw_job.get("contract_type", "")),
            "experience_level": self._infer_experience_level(raw_job.get("title", "")),
            "is_remote": "remote" in raw_job.get("location", {}).get("display_name", "").lower(),
            "salary_min": int(raw_job.get("salary_min", 0)) if raw_job.get("salary_min") else None,
            "salary_max": int(raw_job.get("salary_max", 0)) if raw_job.get("salary_max") else None,
            "salary_currency": "USD"  # Adzuna uses local currency
        }

    def _format_salary(self, job: Dict[str, Any]) -> str:
        """Format salary information"""
        min_sal = job.get("salary_min")
        max_sal = job.get("salary_max")

        if min_sal and max_sal:
            return f"${int(min_sal):,} - ${int(max_sal):,}"
        elif min_sal:
            return f"${int(min_sal):,}+"
        elif max_sal:
            return f"Up to ${int(max_sal):,}"
        return ""

    def _map_contract_type(self, contract_type: str) -> str:
        """Map contract type to standard format"""
        ct = contract_type.lower()
        if "permanent" in ct or "full" in ct:
            return "full-time"
        elif "part" in ct:
            return "part-time"
        elif "contract" in ct:
            return "contract"
        return "full-time"

    def _infer_experience_level(self, title: str) -> str:
        """Infer experience level from title"""
        title_lower = title.lower()

        if any(word in title_lower for word in ["senior", "sr.", "lead", "principal"]):
            return "senior"
        elif any(word in title_lower for word in ["junior", "jr.", "entry", "graduate"]):
            return "entry"
        elif any(word in title_lower for word in ["director", "vp", "chief", "head"]):
            return "executive"
        else:
            return "mid"


class ActiveJobsDBAdapter(JobAPIAdapter):
    """Active Jobs DB Adapter (RapidAPI) - Your current implementation"""

    def __init__(self):
        super().__init__()
        self.api_name = "ActiveJobsDB"
        self.api_key = os.getenv('RAPIDAPI_KEY')
        self.host = "active-jobs-db.p.rapidapi.com"

    def search_jobs(self, query_params: Dict[str, Any]) -> Dict[str, Any]:
        """
        Active Jobs DB Parameters:
        - limit: Number of results
        - advanced_title_filter: Boolean operators (AND, OR, NOT, etc.)
        - location_filter: Location with OR operator
        - description_type: text or html
        - date_filter: Date posted
        """
        try:
            if not self.api_key:
                logger.warning("Active Jobs DB API key not found")
                return {"data": [], "count": 0, "source": self.api_name}

            # Build API URL
            params = {
                "limit": str(query_params.get("limit", 10)),
                "advanced_title_filter": query_params.get("advanced_title_filter", ""),
                "location_filter": query_params.get("location_filter", ""),
                "description_type": "text",
                "date_filter": query_params.get("date_filter", "")
            }

            # Remove empty parameters
            params = {k: v for k, v in params.items() if v}

            # Build query string
            query_string = '&'.join([f"{k}={urllib.parse.quote(str(v))}" for k, v in params.items()])

            conn = http.client.HTTPSConnection(self.host)
            headers = {
                'x-rapidapi-key': self.api_key,
                'x-rapidapi-host': self.host
            }

            logger.info(f"Active Jobs DB: Searching with params: {params}")
            conn.request("GET", f"/active-ats-7d?{query_string}", headers=headers)

            res = conn.getresponse()
            data = res.read()
            response = json.loads(data.decode("utf-8"))

            if isinstance(response, dict) and 'data' in response:
                jobs = response['data']
            elif isinstance(response, list):
                jobs = response
            else:
                jobs = []

            logger.info(f"Active Jobs DB: Found {len(jobs)} jobs")

            # Normalize all jobs
            normalized_jobs = [self.normalize_job(job) for job in jobs]

            return {
                "data": normalized_jobs,
                "count": len(normalized_jobs),
                "source": self.api_name
            }

        except Exception as e:
            logger.error(f"Active Jobs DB API error: {e}")
            return {"data": [], "count": 0, "source": self.api_name}

    def normalize_job(self, raw_job: Dict[str, Any]) -> Dict[str, Any]:
        """Normalize Active Jobs DB data"""
        return {
            "title": raw_job.get("title", ""),
            "company": raw_job.get("company", ""),
            "location": raw_job.get("location", ""),
            "salary": raw_job.get("salary", ""),
            "description": raw_job.get("description", ""),
            "requirements": raw_job.get("requirements", ""),
            "job_url": raw_job.get("url", "") or raw_job.get("job_url", ""),
            "posted_date": raw_job.get("date_posted", ""),
            "job_type": self._infer_job_type(raw_job.get("title", "")),
            "experience_level": self._infer_experience_level(raw_job.get("title", "")),
            "is_remote": "remote" in raw_job.get("location", "").lower() or "remote" in raw_job.get("title", "").lower(),
            "salary_min": None,
            "salary_max": None,
            "salary_currency": "USD"
        }

    def _infer_job_type(self, title: str) -> str:
        """Infer job type from title"""
        title_lower = title.lower()
        if "intern" in title_lower:
            return "internship"
        elif "contract" in title_lower or "contractor" in title_lower:
            return "contract"
        elif "part-time" in title_lower or "part time" in title_lower:
            return "part-time"
        else:
            return "full-time"

    def _infer_experience_level(self, title: str) -> str:
        """Infer experience level from title"""
        title_lower = title.lower()

        if any(word in title_lower for word in ["senior", "sr.", "lead", "principal", "staff"]):
            return "senior"
        elif any(word in title_lower for word in ["junior", "jr.", "entry", "associate"]):
            return "entry"
        elif any(word in title_lower for word in ["intern", "internship"]):
            return "internship"
        elif any(word in title_lower for word in ["director", "vp", "head of", "chief"]):
            return "executive"
        else:
            return "mid"


class GoogleJobsAdapter(JobAPIAdapter):
    """Google Jobs API Adapter (via SerpAPI)"""

    def __init__(self):
        super().__init__()
        self.api_name = "GoogleJobs"
        self.api_key = os.getenv('SERPAPI_KEY')
        self.base_url = "https://serpapi.com/search"

    def search_jobs(self, query_params: Dict[str, Any]) -> Dict[str, Any]:
        """
        Google Jobs (SerpAPI) Parameters:
        - q: Search query
        - location: Location
        - hl: Language (default: en)
        - gl: Country (default: us)
        - chips: Filters (date_posted:today, employment_type:FULLTIME, etc.)
        """
        try:
            if not self.api_key:
                logger.warning("SerpAPI key not found")
                return {"data": [], "count": 0, "source": self.api_name}

            params = {
                "engine": "google_jobs",
                "q": query_params.get("keywords", "software engineer"),
                "api_key": self.api_key,
                "hl": "en",
                "gl": "us"
            }

            if query_params.get("location"):
                params["location"] = query_params["location"]

            # Build chips filter
            chips = []
            if query_params.get("date_posted"):
                chips.append(f"date_posted:{query_params['date_posted']}")
            if query_params.get("employment_type"):
                chips.append(f"employment_type:{query_params['employment_type']}")

            if chips:
                params["chips"] = ",".join(chips)

            logger.info(f"Google Jobs: Searching with params: {params}")
            response = requests.get(self.base_url, params=params, timeout=30)
            response.raise_for_status()

            data = response.json()
            jobs = data.get("jobs_results", [])
            logger.info(f"Google Jobs: Found {len(jobs)} jobs")

            # Normalize all jobs
            normalized_jobs = [self.normalize_job(job) for job in jobs]

            return {
                "data": normalized_jobs,
                "count": len(normalized_jobs),
                "source": self.api_name
            }

        except Exception as e:
            logger.error(f"Google Jobs API error: {e}")
            return {"data": [], "count": 0, "source": self.api_name}

    def normalize_job(self, raw_job: Dict[str, Any]) -> Dict[str, Any]:
        """Normalize Google Jobs data"""
        return {
            "title": raw_job.get("title", ""),
            "company": raw_job.get("company_name", ""),
            "location": raw_job.get("location", ""),
            "salary": self._format_salary(raw_job),
            "description": raw_job.get("description", ""),
            "requirements": "",
            "job_url": raw_job.get("apply_options", [{}])[0].get("link", "") if raw_job.get("apply_options") else "",
            "posted_date": raw_job.get("detected_extensions", {}).get("posted_at", ""),
            "job_type": self._map_employment_type(raw_job.get("detected_extensions", {}).get("schedule_type", "")),
            "experience_level": self._infer_experience_level(raw_job.get("title", "")),
            "is_remote": "remote" in raw_job.get("location", "").lower(),
            "salary_min": None,
            "salary_max": None,
            "salary_currency": "USD"
        }

    def _format_salary(self, job: Dict[str, Any]) -> str:
        """Format salary from detected extensions"""
        detected = job.get("detected_extensions", {})
        salary = detected.get("salary", "")
        return salary

    def _map_employment_type(self, schedule_type: str) -> str:
        """Map schedule type to standard format"""
        st = schedule_type.lower()
        if "full" in st:
            return "full-time"
        elif "part" in st:
            return "part-time"
        elif "contract" in st:
            return "contract"
        return "full-time"

    def _infer_experience_level(self, title: str) -> str:
        """Infer experience level from title"""
        title_lower = title.lower()

        if any(word in title_lower for word in ["senior", "sr.", "lead", "principal"]):
            return "senior"
        elif any(word in title_lower for word in ["junior", "jr.", "entry", "graduate"]):
            return "entry"
        elif any(word in title_lower for word in ["director", "vp", "chief"]):
            return "executive"
        else:
            return "mid"


class TheMuseAdapter(JobAPIAdapter):
    """The Muse API Adapter - Focus on company culture and detailed job descriptions"""

    def __init__(self):
        super().__init__()
        self.api_name = "TheMuse"
        self.api_key = os.getenv('THEMUSE_API_KEY')
        self.app_label = os.getenv('THEMUSE_APP_LABEL')
        self.base_url = "https://www.themuse.com/api/public/jobs"

    def search_jobs(self, query_params: Dict[str, Any]) -> Dict[str, Any]:
        """
        The Muse API Parameters:
        - page: The page number to load (required, default: 1)
        - descending: Whether to show descending results (true/false, default: false)
        - company: Only get jobs for these companies
        - category: The job category (e.g., "Data Science", "Software Engineering", etc.)
        - level: Experience level (Entry Level, Mid Level, Senior Level, management, Internship)
        - locations: Array of job locations (can include flexible/remote jobs)
        """
        try:
            if not self.api_key:
                logger.warning("The Muse API key not found")
                return {"data": [], "count": 0, "source": self.api_name}

            # Build base parameters
            params = {
                "api_key": self.api_key,
                "page": query_params.get("page", 1)
            }

            # Optional parameters
            if query_params.get("descending"):
                params["descending"] = "true" if query_params["descending"] else "false"

            if query_params.get("company"):
                params["company"] = query_params["company"]

            # Category (from Gemini or mapped from keywords)
            if query_params.get("category"):
                params["category"] = query_params["category"]
            else:
                # Fallback: Map keywords to category
                category = self._map_keywords_to_category(query_params.get("keywords", ""))
                if category:
                    params["category"] = category

            # Experience level (from Gemini or mapped)
            if query_params.get("level"):
                params["level"] = query_params["level"]
            else:
                # Fallback: Map experience level
                level = self._map_experience_level(query_params.get("experience_level", ""))
                if level:
                    params["level"] = level

            # Handle multiple locations
            # The Muse API accepts repeated 'location' parameters: ?location=A&location=B&location=C
            locations = query_params.get("locations", [])
            if not locations and query_params.get("location"):
                # Fallback to single location
                locations = [query_params["location"]]

            # Build URL with repeated location parameters
            url_parts = [f"{self.base_url}?"]

            # Add base params
            for key, value in params.items():
                url_parts.append(f"{key}={urllib.parse.quote(str(value), safe='')}&")

            # Add multiple locations
            # IMPORTANT: Use safe='' to encode ALL special characters including '/'
            # "Flexible / Remote" must become "Flexible%20%2F%20Remote"
            for location in locations:
                url_parts.append(f"location={urllib.parse.quote(location, safe='')}&")

            # Remove trailing &
            url = "".join(url_parts).rstrip("&")

            logger.info(f"The Muse: Searching with URL: {url}")
            response = requests.get(url, timeout=30)
            response.raise_for_status()

            data = response.json()

            # Debug: Log the full response to see what's happening
            logger.info(f"The Muse: API Response - page: {data.get('page')}, page_count: {data.get('page_count')}, items_per_page: {data.get('items_per_page')}, total: {data.get('total')}")

            jobs = data.get("results", [])
            total = data.get("total", 0)
            page_count = data.get("page_count", 0)

            logger.info(f"The Muse: Found {len(jobs)} jobs (Total: {total}, Pages: {page_count})")

            # Normalize all jobs
            normalized_jobs = [self.normalize_job(job) for job in jobs]

            return {
                "data": normalized_jobs,
                "count": len(normalized_jobs),
                "source": self.api_name,
                "total": total,
                "page_count": page_count
            }

        except requests.exceptions.HTTPError as e:
            logger.error(f"The Muse HTTP error: {e}")
            if hasattr(e.response, 'text'):
                logger.error(f"Response: {e.response.text[:500]}")
            return {"data": [], "count": 0, "source": self.api_name}
        except Exception as e:
            logger.error(f"The Muse API error: {e}")
            return {"data": [], "count": 0, "source": self.api_name}

    def normalize_job(self, raw_job: Dict[str, Any]) -> Dict[str, Any]:
        """Normalize The Muse job data"""
        # The Muse provides detailed company and job information
        company_info = raw_job.get("company", {})
        locations = raw_job.get("locations", [])
        location_str = locations[0].get("name", "") if locations else ""

        # Get job URL from refs.landing_page (the actual application page)
        # This is the proper URL format: https://www.themuse.com/jobs/coinbase/product-designer-ii-consumer-aa9c3b
        job_url = raw_job.get("refs", {}).get("landing_page", "")

        return {
            "title": raw_job.get("name", ""),
            "company": company_info.get("name", ""),
            "location": location_str,
            "salary": "",  # The Muse doesn't always provide salary info
            "description": raw_job.get("contents", ""),
            "requirements": "",  # The Muse includes this in the main description
            "job_url": job_url,
            "posted_date": raw_job.get("publication_date", ""),
            "job_type": self._map_job_type(raw_job.get("type", "")),
            "experience_level": self._map_level_to_standard(raw_job.get("levels", [])),
            "is_remote": self._check_remote(locations),
            "salary_min": None,
            "salary_max": None,
            "salary_currency": "USD"
        }

    def _map_keywords_to_category(self, keywords: str) -> str:
        """Map search keywords to The Muse categories with priority-based matching"""
        keywords_lower = keywords.lower()

        # Category mapping with priority (more specific keywords first)
        # Each entry is (keyword, category, priority) - higher priority = more specific
        category_mappings = [
            # High priority - very specific matches
            ("data science", "Data Science", 10),
            ("data scientist", "Data Science", 10),
            ("machine learning", "Data Science", 10),
            ("software engineer", "Software Engineering", 10),
            ("software developer", "Software Engineering", 10),
            ("full stack", "Software Engineering", 10),
            ("backend", "Software Engineering", 10),
            ("frontend", "Software Engineering", 10),
            ("product manager", "Product Management", 10),
            ("product design", "Design and UX", 10),
            ("ux designer", "Design and UX", 10),
            ("ui designer", "Design and UX", 10),
            ("project manager", "Project Management", 10),
            ("data analyst", "Data and Analytics", 10),
            ("business analyst", "Data and Analytics", 10),

            # Medium priority - moderately specific
            ("software", "Software Engineering", 5),
            ("developer", "Software Engineering", 5),
            ("programmer", "Software Engineering", 5),
            ("engineer", "Software Engineering", 5),
            ("data", "Data and Analytics", 5),
            ("analytics", "Data and Analytics", 5),
            ("product", "Product Management", 5),
            ("design", "Design and UX", 5),
            ("ux", "Design and UX", 5),
            ("ui", "Design and UX", 5),

            # Lower priority - generic matches
            ("marketing", "Marketing", 3),
            ("sales", "Sales", 3),
            ("hr", "Human Resources and Recruiting", 3),
            ("finance", "Accounting and Finance", 3),
            ("accounting", "Accounting and Finance", 3),
            ("customer", "Customer Service", 3),
            ("writer", "Writing and Editing", 3),
            ("content", "Writing and Editing", 3),
            ("project", "Project Management", 3),
            ("manager", "Management", 3),
            ("analyst", "Data and Analytics", 3),
            ("nurse", "Nurses", 3),
            ("healthcare", "Healthcare", 3),
        ]

        # Find all matching categories with their priorities
        matches = []
        for keyword, category, priority in category_mappings:
            if keyword in keywords_lower:
                matches.append((category, priority))

        if not matches:
            return ""  # Return empty if no match

        # Sort by priority (highest first) and return the best match
        matches.sort(key=lambda x: x[1], reverse=True)
        best_category = matches[0][0]

        logger.info(f"The Muse: Mapped keywords '{keywords}' to category '{best_category}'")
        return best_category

    def _map_experience_level(self, level: str) -> str:
        """Map generic experience level to The Muse format"""
        level_lower = level.lower()

        muse_levels = {
            "entry": "Entry Level",
            "junior": "Entry Level",
            "mid": "Mid Level",
            "senior": "Senior Level",
            "lead": "Senior Level",
            "principal": "Senior Level",
            "manager": "management",
            "management": "management",
            "director": "management",
            "executive": "management",
            "intern": "Internship",
            "internship": "Internship"
        }

        for key, muse_level in muse_levels.items():
            if key in level_lower:
                return muse_level

        return ""

    def _map_level_to_standard(self, levels: List[Dict[str, Any]]) -> str:
        """Map The Muse level to standard experience level"""
        if not levels:
            return "mid"

        # Get the first level name
        level_name = levels[0].get("name", "").lower()

        if "entry" in level_name:
            return "entry"
        elif "senior" in level_name:
            return "senior"
        elif "intern" in level_name:
            return "internship"
        elif "management" in level_name or "manager" in level_name:
            return "executive"
        else:
            return "mid"

    def _map_job_type(self, job_type: str) -> str:
        """Map The Muse job type to standard format"""
        jt = job_type.lower()

        if "intern" in jt:
            return "internship"
        elif "contract" in jt:
            return "contract"
        elif "part" in jt:
            return "part-time"
        else:
            return "full-time"

    def _check_remote(self, locations: List[Dict[str, Any]]) -> bool:
        """Check if job is remote based on location data"""
        if not locations:
            return False

        for location in locations:
            location_name = location.get("name", "").lower()
            if "remote" in location_name or "flexible" in location_name:
                return True

        return False


class TheirStackAdapter(JobAPIAdapter):
    """TheirStack API Adapter - Largest database of jobs and technographics"""

    def __init__(self):
        super().__init__()
        self.api_name = "TheirStack"
        self.api_key = os.getenv('THEIRSTACK_API_KEY')
        self.base_url = "https://api.theirstack.com/v1/jobs/search"

    def search_jobs(self, query_params: Dict[str, Any]) -> Dict[str, Any]:
        """
        TheirStack API Parameters (POST request):
        - posted_at_max_age_days: Max age in days (required filter)
        - job_title_pattern_or: Regex patterns for job titles
        - job_country_code_or: ISO2 country codes
        - remote: Boolean for remote jobs
        - min_salary_usd: Minimum annual salary
        - max_salary_usd: Maximum annual salary
        - job_technology_slug_or: Technology slugs
        - company_name_or: Company names
        - limit: Number of results (default: 25)
        - offset: Pagination offset
        """
        try:
            if not self.api_key:
                logger.warning("TheirStack API key not found")
                return {"data": [], "count": 0, "source": self.api_name}

            # Build request body
            body = {
                "limit": query_params.get("limit", 25),
                "offset": query_params.get("offset", 0)
            }

            # Required: At least one of these filters must be present
            if query_params.get("posted_at_max_age_days") is not None:
                body["posted_at_max_age_days"] = query_params["posted_at_max_age_days"]
            else:
                # Default to last 30 days if not specified
                body["posted_at_max_age_days"] = 30

            # Job title patterns (from Gemini or keywords)
            if query_params.get("job_title_pattern_or"):
                body["job_title_pattern_or"] = query_params["job_title_pattern_or"]

            # Country codes
            if query_params.get("job_country_code_or"):
                body["job_country_code_or"] = query_params["job_country_code_or"]

            # Remote filter
            if query_params.get("remote") is not None:
                body["remote"] = query_params["remote"]

            # Salary range
            if query_params.get("min_salary_usd"):
                body["min_salary_usd"] = query_params["min_salary_usd"]
            if query_params.get("max_salary_usd"):
                body["max_salary_usd"] = query_params["max_salary_usd"]

            # Technologies
            if query_params.get("job_technology_slug_or"):
                body["job_technology_slug_or"] = query_params["job_technology_slug_or"]

            # Company filters
            if query_params.get("company_name_or"):
                body["company_name_or"] = query_params["company_name_or"]
            if query_params.get("company_domain_or"):
                body["company_domain_or"] = query_params["company_domain_or"]

            # Employee count range
            if query_params.get("min_employee_count"):
                body["min_employee_count"] = query_params["min_employee_count"]
            if query_params.get("max_employee_count"):
                body["max_employee_count"] = query_params["max_employee_count"]

            # Employment status
            if query_params.get("employment_statuses_or"):
                body["employment_statuses_or"] = query_params["employment_statuses_or"]

            logger.info(f"TheirStack: Searching with body: {json.dumps(body, indent=2)}")

            # Make POST request with Bearer token
            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json"
            }

            response = requests.post(self.base_url, json=body, headers=headers, timeout=30)
            response.raise_for_status()

            data = response.json()
            jobs = data.get("data", [])
            total = data.get("total_results", len(jobs))

            logger.info(f"TheirStack: Found {len(jobs)} jobs (Total: {total})")

            # Normalize all jobs
            normalized_jobs = [self.normalize_job(job) for job in jobs]

            return {
                "data": normalized_jobs,
                "count": len(normalized_jobs),
                "source": self.api_name,
                "total": total
            }

        except requests.exceptions.HTTPError as e:
            logger.error(f"TheirStack HTTP error: {e}")
            if hasattr(e.response, 'text'):
                logger.error(f"Response: {e.response.text[:500]}")
            return {"data": [], "count": 0, "source": self.api_name}
        except Exception as e:
            logger.error(f"TheirStack API error: {e}")
            return {"data": [], "count": 0, "source": self.api_name}

    def normalize_job(self, raw_job: Dict[str, Any]) -> Dict[str, Any]:
        """Normalize TheirStack job data"""
        # Extract company information
        company_obj = raw_job.get("company_object", {}) or {}

        # Get locations
        locations = raw_job.get("locations", [])
        location_str = ", ".join([loc.get("city", "") or loc.get("state", "") or loc.get("country", "")
                                   for loc in locations if loc]) if locations else raw_job.get("location", "")

        # Get employment status
        employment_statuses = raw_job.get("employment_statuses", [])
        job_type = self._map_employment_status(employment_statuses[0] if employment_statuses else "full_time")

        # Get salary info
        salary_str = ""
        min_salary = raw_job.get("min_salary_usd")
        max_salary = raw_job.get("max_salary_usd")
        if min_salary and max_salary:
            salary_str = f"${min_salary:,} - ${max_salary:,}"
        elif min_salary:
            salary_str = f"${min_salary:,}+"
        elif max_salary:
            salary_str = f"Up to ${max_salary:,}"

        return {
            "title": raw_job.get("title", ""),
            "company": raw_job.get("company", "") or company_obj.get("name", ""),
            "location": location_str,
            "salary": salary_str,
            "description": raw_job.get("description", ""),
            "requirements": "",  # TheirStack doesn't separate requirements
            "job_url": raw_job.get("url", "") or raw_job.get("final_url", ""),
            "posted_date": raw_job.get("date_posted", ""),
            "job_type": job_type,
            "experience_level": self._infer_seniority(raw_job.get("seniority")),
            "is_remote": raw_job.get("is_remote", False),
            "salary_min": min_salary,
            "salary_max": max_salary,
            "salary_currency": "USD"
        }

    def _map_employment_status(self, status: str) -> str:
        """Map TheirStack employment status to standard format"""
        mapping = {
            "full_time": "full-time",
            "part_time": "part-time",
            "contract": "contract",
            "internship": "internship",
            "temporary": "contract"
        }
        return mapping.get(status, "full-time")

    def _infer_seniority(self, seniority: Optional[str]) -> str:
        """Map TheirStack seniority to standard experience level"""
        if not seniority:
            return "mid"

        mapping = {
            "junior": "entry",
            "mid_level": "mid",
            "senior": "senior",
            "staff": "senior",
            "c_level": "executive"
        }
        return mapping.get(seniority, "mid")


# Adapter Factory
class JobAPIFactory:
    """Factory to create and manage job API adapters"""

    @staticmethod
    def get_all_adapters() -> List[JobAPIAdapter]:
        """Get all available adapters"""
        return [
            JSearchAdapter(),
            AdzunaAdapter(),
            ActiveJobsDBAdapter(),
            GoogleJobsAdapter(),
            TheMuseAdapter(),
            TheirStackAdapter()
        ]

    @staticmethod
    def get_adapter(api_name: str) -> Optional[JobAPIAdapter]:
        """Get a specific adapter by name"""
        adapters = {
            "jsearch": JSearchAdapter(),
            "adzuna": AdzunaAdapter(),
            "activejobsdb": ActiveJobsDBAdapter(),
            "googlejobs": GoogleJobsAdapter(),
            "themuse": TheMuseAdapter(),
            "theirstack": TheirStackAdapter()
        }
        return adapters.get(api_name.lower())
