"""
Job Relevance Scoring System
Scores jobs based on profile match without using expensive LLM calls
Uses keyword matching, experience level, salary, location, and other factors
"""

import re
import logging
from typing import Dict, List, Any, Set
from datetime import datetime

logger = logging.getLogger(__name__)


class JobRelevanceScorer:
    """Calculate relevance score for jobs based on user profile"""

    def __init__(self, profile: Dict[str, Any]):
        """
        Initialize scorer with user profile

        Profile should contain:
        - skills: {technical, programming_languages, frameworks, tools, soft_skills}
        - work_experience: [{title, company, description, achievements}]
        - years_of_experience: int
        - minimum_salary: int
        - maximum_salary: int (optional)
        - salary_currency: str
        - desired_job_types: [str]
        - desired_experience_levels: [str]
        - open_to_remote: bool
        - open_to_anywhere: bool
        - preferred_cities: [str]
        - preferred_states: [str]
        - education: [{degree, institution}]
        """
        self.profile = profile
        self.user_keywords = self._extract_user_keywords()

    def _extract_user_keywords(self) -> Set[str]:
        """Extract all relevant keywords from user profile"""
        keywords = set()

        # Extract from skills
        skills = self.profile.get("skills", {})
        for skill_category in ["technical", "programming_languages", "frameworks", "tools"]:
            skill_list = skills.get(skill_category, [])
            if isinstance(skill_list, list):
                keywords.update([s.lower().strip() for s in skill_list if s])

        # Extract from work experience titles
        work_exp = self.profile.get("work_experience", [])
        for exp in work_exp:
            if isinstance(exp, dict):
                title = exp.get("title", "")
                if title:
                    keywords.update(self._tokenize(title))

        # Extract from education
        education = self.profile.get("education", [])
        for edu in education:
            if isinstance(edu, dict):
                degree = edu.get("degree", "")
                if degree:
                    keywords.update(self._tokenize(degree))

        # Extract from projects
        projects = self.profile.get("projects", [])
        for project in projects:
            if isinstance(project, dict):
                techs = project.get("technologies", [])
                if isinstance(techs, list):
                    keywords.update([t.lower().strip() for t in techs if t])

        logger.info(f"Extracted {len(keywords)} keywords from user profile")
        return keywords

    def _tokenize(self, text: str) -> Set[str]:
        """Tokenize text into keywords"""
        if not text:
            return set()

        # Convert to lowercase and split
        tokens = re.findall(r'\b\w+\b', text.lower())

        # Remove common stop words
        stop_words = {'the', 'a', 'an', 'and', 'or', 'but', 'in', 'on', 'at', 'to', 'for', 'of', 'with', 'by', 'from', 'as', 'is', 'was', 'are', 'were', 'been', 'be', 'have', 'has', 'had', 'do', 'does', 'did', 'will', 'would', 'should', 'could', 'may', 'might', 'must', 'can'}

        return set([t for t in tokens if t not in stop_words and len(t) > 2])

    def calculate_score(self, job: Dict[str, Any]) -> int:
        """
        Calculate relevance score (0-100) for a job

        Scoring breakdown:
        - Keyword match (title): 0-25 points
        - Keyword match (description): 0-20 points
        - Experience level match: 0-15 points
        - Salary match: 0-15 points
        - Location match: 0-10 points
        - Job type match: 0-10 points
        - Recency bonus: 0-5 points

        Total: 100 points
        """
        try:
            score = 0

            # 1. Title keyword match (0-25 points)
            score += self._score_title_match(job.get("title", ""))

            # 2. Description keyword match (0-20 points)
            score += self._score_description_match(job.get("description", ""), job.get("requirements", ""))

            # 3. Experience level match (0-15 points)
            score += self._score_experience_match(job.get("experience_level", ""))

            # 4. Salary match (0-15 points)
            score += self._score_salary_match(
                job.get("salary_min"),
                job.get("salary_max"),
                job.get("salary_currency", "USD")
            )

            # 5. Location match (0-10 points)
            score += self._score_location_match(job.get("location", ""), job.get("is_remote", False))

            # 6. Job type match (0-10 points)
            score += self._score_job_type_match(job.get("job_type", ""))

            # 7. Recency bonus (0-5 points)
            score += self._score_recency(job.get("posted_date", ""))

            # Ensure score is between 0 and 100
            score = max(0, min(100, score))

            logger.debug(f"Job '{job.get('title', 'Unknown')}' scored {score}/100")
            return score

        except Exception as e:
            logger.error(f"Error calculating score for job: {e}")
            return 0

    def _score_title_match(self, title: str) -> int:
        """Score based on title keyword match (0-25 points)"""
        if not title:
            return 0

        title_keywords = self._tokenize(title)

        # Calculate match percentage
        if not self.user_keywords:
            return 5  # Default score if no user keywords

        matching_keywords = title_keywords.intersection(self.user_keywords)
        match_percentage = len(matching_keywords) / max(len(title_keywords), 1)

        # Convert to score (0-25)
        score = int(match_percentage * 25)

        # Bonus: Check for exact title matches from work experience
        work_exp = self.profile.get("work_experience", [])
        for exp in work_exp:
            if isinstance(exp, dict):
                exp_title = exp.get("title", "").lower()
                if exp_title and exp_title in title.lower():
                    score = min(score + 5, 25)  # Bonus 5 points
                    break

        return score

    def _score_description_match(self, description: str, requirements: str) -> int:
        """Score based on description/requirements keyword match (0-20 points)"""
        if not description and not requirements:
            return 0

        # Combine description and requirements
        combined_text = f"{description} {requirements}"
        job_keywords = self._tokenize(combined_text)

        if not self.user_keywords:
            return 5  # Default score

        matching_keywords = job_keywords.intersection(self.user_keywords)

        # Weight by number of matches (more matches = higher score)
        num_matches = len(matching_keywords)

        if num_matches == 0:
            return 0
        elif num_matches <= 3:
            return 5
        elif num_matches <= 7:
            return 10
        elif num_matches <= 12:
            return 15
        else:
            return 20

    def _score_experience_match(self, job_experience_level: str) -> int:
        """Score based on experience level match (0-15 points)"""
        if not job_experience_level:
            return 7  # Default score if no level specified

        user_years = self.profile.get("years_of_experience", 0)
        desired_levels = self.profile.get("desired_experience_levels", [])

        # Map years to experience level
        user_level = self._years_to_level(user_years)

        # If job matches desired levels, perfect score
        if desired_levels and job_experience_level.lower() in [l.lower() for l in desired_levels]:
            return 15

        # If job matches user's actual level, high score
        if job_experience_level.lower() == user_level.lower():
            return 15

        # If job is one level below user (e.g., user is senior, job is mid), still good
        level_order = ["internship", "entry", "mid", "senior", "lead", "executive"]
        try:
            user_idx = level_order.index(user_level.lower())
            job_idx = level_order.index(job_experience_level.lower())

            diff = abs(user_idx - job_idx)
            if diff == 0:
                return 15
            elif diff == 1:
                return 10
            elif diff == 2:
                return 5
            else:
                return 0
        except ValueError:
            return 7  # Default if level not in order

    def _years_to_level(self, years: int) -> str:
        """Convert years of experience to level"""
        if years < 1:
            return "entry"
        elif years < 3:
            return "entry"
        elif years < 5:
            return "mid"
        elif years < 8:
            return "senior"
        elif years < 12:
            return "lead"
        else:
            return "executive"

    def _score_salary_match(self, job_min: int, job_max: int, job_currency: str) -> int:
        """Score based on salary match (0-15 points)"""
        user_min = self.profile.get("minimum_salary")
        user_max = self.profile.get("maximum_salary")
        user_currency = self.profile.get("salary_currency", "USD")

        # If no salary info from either side, return default
        if not job_min and not job_max:
            return 7  # Neutral score

        if not user_min:
            return 7  # Neutral score

        # Currency conversion is complex, so only compare if same currency
        if job_currency != user_currency:
            return 7  # Neutral score

        # Check if job meets minimum salary
        if job_min and job_min >= user_min:
            score = 15  # Perfect match
        elif job_max and job_max >= user_min:
            score = 12  # Maximum meets expectation
        elif job_max and job_max >= (user_min * 0.8):
            score = 8  # Within 80% of expectation
        else:
            score = 0  # Below expectation

        # If user has maximum and job exceeds it significantly, slightly reduce score
        if user_max and job_min and job_min > user_max * 1.5:
            score = max(score - 3, 0)  # Might be overqualified

        return score

    def _score_location_match(self, location: str, is_remote: bool) -> int:
        """Score based on location match (0-10 points)"""
        open_to_remote = self.profile.get("open_to_remote", False)
        open_to_anywhere = self.profile.get("open_to_anywhere", False)
        preferred_cities = self.profile.get("preferred_cities", [])
        preferred_states = self.profile.get("preferred_states", [])

        # If user is open to anywhere, perfect score
        if open_to_anywhere:
            return 10

        # If job is remote and user is open to remote, perfect score
        if is_remote and open_to_remote:
            return 10

        # If no location preferences set, neutral score
        if not preferred_cities and not preferred_states and not open_to_remote:
            return 5

        # Check if location matches preferred cities or states
        if location:
            location_lower = location.lower()

            # Check cities
            if preferred_cities:
                for city in preferred_cities:
                    if city.lower() in location_lower:
                        return 10

            # Check states
            if preferred_states:
                for state in preferred_states:
                    if state.lower() in location_lower:
                        return 8

        # If remote is mentioned in location and user is open to remote
        if "remote" in location.lower() and open_to_remote:
            return 10

        # No match
        return 0

    def _score_job_type_match(self, job_type: str) -> int:
        """Score based on job type match (0-10 points)"""
        desired_types = self.profile.get("desired_job_types", [])

        # If no preference, neutral score
        if not desired_types:
            return 5

        # If job type matches desired types, perfect score
        if job_type.lower() in [t.lower() for t in desired_types]:
            return 10

        # Partial matches
        if job_type.lower() == "full-time" and not desired_types:
            return 8  # Full-time is default

        return 0

    def _score_recency(self, posted_date: str) -> int:
        """Score based on how recent the job posting is (0-5 points)"""
        if not posted_date:
            return 2  # Default score

        try:
            # Try to parse date
            from dateutil import parser
            posted = parser.parse(posted_date)
            now = datetime.utcnow()
            days_old = (now - posted).days

            if days_old <= 1:
                return 5  # Posted today or yesterday
            elif days_old <= 7:
                return 4  # Within a week
            elif days_old <= 14:
                return 3  # Within 2 weeks
            elif days_old <= 30:
                return 2  # Within a month
            else:
                return 1  # Older than a month

        except Exception:
            return 2  # Default if can't parse date


def rank_jobs(jobs: List[Dict[str, Any]], profile: Dict[str, Any], min_score: int = 0) -> List[Dict[str, Any]]:
    """
    Rank jobs by relevance score and filter by minimum score

    Args:
        jobs: List of job dictionaries
        profile: User profile dictionary
        min_score: Minimum score to include (default: 0)

    Returns:
        List of jobs with relevance_score added, sorted by score (highest first)
    """
    scorer = JobRelevanceScorer(profile)

    # Calculate scores for all jobs
    for job in jobs:
        job["relevance_score"] = scorer.calculate_score(job)

    # Filter by minimum score
    filtered_jobs = [job for job in jobs if job["relevance_score"] >= min_score]

    # Sort by score (highest first)
    filtered_jobs.sort(key=lambda x: x["relevance_score"], reverse=True)

    logger.info(f"Ranked {len(filtered_jobs)} jobs (filtered from {len(jobs)}) with min_score={min_score}")

    return filtered_jobs
