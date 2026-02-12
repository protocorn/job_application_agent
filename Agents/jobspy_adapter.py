"""
JobSpy Adapter for Job Application Agent
Integrates JobSpy library to scrape jobs from multiple sources concurrently
"""

import logging
from typing import Dict, Any, List, Optional
from jobspy import scrape_jobs
import pandas as pd

logger = logging.getLogger(__name__)


class JobSpyAdapter:
    """
    Adapter for JobSpy library - scrapes jobs from multiple job boards concurrently
    
    Supported sites: LinkedIn, Indeed, Glassdoor, Google, ZipRecruiter, Bayt, Naukri, BDJobs
    """
    
    def __init__(self, proxy_manager=None):
        self.api_name = "JobSpy"
        self.requires_api_key = False
        self.proxy_manager = proxy_manager
        
        # Default sites to search (best performers)
        self.default_sites = ["indeed", "linkedin", "zip_recruiter", "google"]
        
        if self.proxy_manager:
            logger.info(f"JobSpy adapter initialized with {len(self.proxy_manager.get_proxy_list())} proxies")
        
    def search_jobs(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """
        Search for jobs using JobSpy
        
        Args:
            params: Search parameters including:
                - keywords: Job search keywords
                - location: Job location
                - remote: Remote jobs only
                - results_wanted: Number of results per site
                - job_type: fulltime, parttime, internship, contract
                - hours_old: Filter by posting age
                - easy_apply: Filter for easy apply jobs
                - distance: Search radius in miles
                - country_indeed: Country code for Indeed/Glassdoor
                - linkedin_fetch_description: Fetch full descriptions
                
        Returns:
            Dictionary with job data and metadata
        """
        try:
            # Extract parameters
            search_term = params.get("keywords", "")
            location = params.get("location", "")
            results_wanted = params.get("results_wanted", 20)
            is_remote = params.get("remote", False)
            job_type = params.get("job_type", None)  # fulltime, parttime, internship, contract
            hours_old = params.get("hours_old", None)
            easy_apply = params.get("easy_apply", False)
            distance = params.get("distance", 50)
            country_indeed = params.get("country_indeed", "USA")
            linkedin_fetch_description = params.get("linkedin_fetch_description", False)
            
            # Use proxy manager if available, otherwise use provided proxies
            proxies = None
            if self.proxy_manager:
                proxy_list = self.proxy_manager.get_proxy_list()
                if proxy_list:
                    proxies = proxy_list
                    logger.info(f"Using {len(proxies)} proxies for job scraping")
            else:
                proxies = params.get("proxies", None)
            
            # Custom sites or use defaults
            site_name = params.get("sites", self.default_sites)
            
            # Build Google search term if using Google (or use explicit override)
            google_search_term = params.get("google_search_term", None)
            if "google" in site_name and search_term and location:
                if not google_search_term:
                    google_search_term = f"{search_term} jobs near {location}"
                    if hours_old:
                        if hours_old <= 24:
                            google_search_term += " since yesterday"
                        elif hours_old <= 168:  # 7 days
                            google_search_term += " since last week"
            
            logger.info(f"JobSpy searching: {search_term} in {location} (sites: {site_name})")
            
            # Call JobSpy
            jobs_df = scrape_jobs(
                site_name=site_name,
                search_term=search_term,
                google_search_term=google_search_term,
                location=location,
                results_wanted=results_wanted,
                hours_old=hours_old,
                country_indeed=country_indeed,
                is_remote=is_remote,
                job_type=job_type,
                easy_apply=easy_apply,
                distance=distance,
                linkedin_fetch_description=linkedin_fetch_description,
                proxies=proxies,
                verbose=1  # Only errors and warnings
            )
            
            if jobs_df is None or jobs_df.empty:
                logger.warning("JobSpy returned no jobs")
                return {
                    "success": True,
                    "data": [],
                    "total": 0,
                    "source": "JobSpy",
                    "sites_searched": site_name
                }
            
            # Convert DataFrame to list of dictionaries
            jobs_list = self._convert_dataframe_to_jobs(jobs_df)
            
            logger.info(f"JobSpy found {len(jobs_list)} jobs from {site_name}")
            
            return {
                "success": True,
                "data": jobs_list,
                "total": len(jobs_list),
                "source": "JobSpy",
                "sites_searched": site_name
            }
            
        except Exception as e:
            logger.error(f"JobSpy error: {e}", exc_info=True)
            return {
                "success": False,
                "data": [],
                "total": 0,
                "error": str(e),
                "source": "JobSpy"
            }
    
    def _convert_dataframe_to_jobs(self, df: pd.DataFrame) -> List[Dict[str, Any]]:
        """Convert JobSpy DataFrame to standardized job dictionary format"""
        jobs = []
        
        for _, row in df.iterrows():
            try:
                # Build salary string
                salary = None
                if pd.notna(row.get('min_amount')) or pd.notna(row.get('max_amount')):
                    min_amt = row.get('min_amount', '')
                    max_amt = row.get('max_amount', '')
                    interval = row.get('interval', 'yearly')
                    
                    parts = []
                    if pd.notna(min_amt) and min_amt:
                        parts.append(f"${int(min_amt):,}")
                    if pd.notna(max_amt) and max_amt:
                        parts.append(f"${int(max_amt):,}")
                    
                    if parts:
                        salary = " - ".join(parts)
                        if interval:
                            salary += f" per {interval}"
                
                # Get location (already formatted by JobSpy)
                location = str(row.get('location', 'Not specified')) if pd.notna(row.get('location')) else 'Not specified'
                
                # Get job URL (prefer direct URL if available)
                job_url = str(row.get('job_url_direct', '')) if pd.notna(row.get('job_url_direct')) else str(row.get('job_url', ''))
                
                # Build job dictionary
                job = {
                    "title": str(row.get('title', 'Unknown Title')) if pd.notna(row.get('title')) else 'Unknown Title',
                    "company": str(row.get('company', 'Unknown Company')) if pd.notna(row.get('company')) else 'Unknown Company',
                    "location": location,
                    "job_url": job_url,
                    "url": job_url,  # Alias for compatibility
                    "description": str(row.get('description', '')) if pd.notna(row.get('description')) else '',
                    "salary": salary,
                    "job_type": str(row.get('job_type', '')) if pd.notna(row.get('job_type')) else None,
                    "source": str(row.get('site', 'JobSpy')) if pd.notna(row.get('site')) else 'JobSpy',
                    "date_posted": str(row.get('date_posted', '')) if pd.notna(row.get('date_posted')) else None,
                    "is_remote": bool(row.get('is_remote', False)) if pd.notna(row.get('is_remote')) else False,
                    
                    # Additional fields
                    "min_amount": int(row.get('min_amount')) if pd.notna(row.get('min_amount')) else None,
                    "max_amount": int(row.get('max_amount')) if pd.notna(row.get('max_amount')) else None,
                    "interval": str(row.get('interval', '')) if pd.notna(row.get('interval')) else None,
                    "job_level": str(row.get('job_level', '')) if pd.notna(row.get('job_level')) else None,
                    "company_url": str(row.get('company_url', '')) if pd.notna(row.get('company_url')) else None,
                    "skills": row.get('skills', []) if pd.notna(row.get('skills')) else [],
                    
                    # For compatibility with existing system
                    "apply_links": {
                        "primary": job_url,
                        str(row.get('site', 'jobspy')).lower() if pd.notna(row.get('site')) else 'jobspy': job_url
                    }
                }
                
                jobs.append(job)
                
            except Exception as e:
                logger.warning(f"Error converting job row: {e}")
                continue
        
        return jobs
    
    def get_supported_sites(self) -> List[str]:
        """Get list of supported job sites"""
        return [
            "indeed",
            "linkedin", 
            "zip_recruiter",
            "google",
            "glassdoor",
            "bayt",
            "naukri",
            "bdjobs"
        ]
    
    def get_supported_countries(self) -> Dict[str, List[str]]:
        """Get list of supported countries by site"""
        return {
            "indeed": [
                "USA", "UK", "Canada", "Australia", "Germany", "France", "India",
                "Brazil", "Mexico", "Spain", "Italy", "Netherlands", "Japan",
                # ... (see full list in JobSpy docs)
            ],
            "glassdoor": [
                "USA", "UK", "Canada", "Australia", "Germany", "France", "India",
                "Brazil", "Mexico", "Spain", "Italy"
            ],
            "linkedin": ["Global"],
            "zip_recruiter": ["USA", "Canada"],
            "google": ["Global"],
            "bayt": ["International"],
            "naukri": ["India"],
            "bdjobs": ["Bangladesh"]
        }


# Convenience function for quick searches
def search_jobs_quick(keywords: str, location: str = "", results_wanted: int = 20,
                     sites: Optional[List[str]] = None, **kwargs) -> List[Dict[str, Any]]:
    """
    Quick job search function
    
    Example:
        jobs = search_jobs_quick("software engineer", "San Francisco, CA", 20)
    """
    adapter = JobSpyAdapter()
    
    params = {
        "keywords": keywords,
        "location": location,
        "results_wanted": results_wanted,
        "sites": sites or ["indeed", "linkedin", "zip_recruiter"],
        **kwargs
    }
    
    result = adapter.search_jobs(params)
    return result.get("data", [])


if __name__ == "__main__":
    # Test the adapter
    logging.basicConfig(level=logging.INFO)
    
    print("Testing JobSpy Adapter...")
    adapter = JobSpyAdapter()
    
    result = adapter.search_jobs({
        "keywords": "software engineer",
        "location": "San Francisco, CA",
        "results_wanted": 10,
        "hours_old": 72
    })
    
    print(f"\nSuccess: {result['success']}")
    print(f"Total jobs: {result['total']}")
    print(f"Sites searched: {result.get('sites_searched', [])}")
    
    if result['data']:
        print("\nFirst job:")
        job = result['data'][0]
        print(f"  Title: {job['title']}")
        print(f"  Company: {job['company']}")
        print(f"  Location: {job['location']}")
        print(f"  URL: {job['job_url']}")
        print(f"  Source: {job['source']}")
