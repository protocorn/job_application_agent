"""
Gemini-Powered Search Query Optimizer
Uses Gemini AI to generate optimized job search queries
"""

import logging
import os
import json
import re
from typing import Dict, Any, List, Optional
import google.generativeai as genai

logger = logging.getLogger(__name__)


class GeminiQueryOptimizer:
    """Uses Gemini AI to optimize job search queries for better results"""
    
    def __init__(self):
        self.model = None
        self._initialize_gemini()
    
    def _initialize_gemini(self):
        """Initialize Gemini API"""
        try:
            api_key = os.getenv("GEMINI_API_KEY")
            if not api_key:
                logger.warning("GEMINI_API_KEY not found in environment")
                return
            
            genai.configure(api_key=api_key)
            self.model = genai.GenerativeModel('gemini-2.5-flash')
            logger.info("Gemini Query Optimizer initialized")
            
        except Exception as e:
            logger.error(f"Failed to initialize Gemini: {e}")
            self.model = None
    
    def optimize_search_query(self, 
                             user_keywords: str, 
                             location: str = "",
                             profile_data: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        Generate optimized search queries using Gemini AI
        
        Args:
            user_keywords: User's original search keywords
            location: Job location
            profile_data: User's profile data (skills, experience, etc.)
        
        Returns:
            Dictionary with optimized queries and metadata
        """
        if not self.model:
            # Fallback to rule-based optimization if Gemini not available
            logger.info("Using rule-based query optimization (no Gemini)")
            return self._rule_based_optimization(user_keywords, location, profile_data)
        
        try:
            # Build prompt for Gemini
            prompt = self._build_optimization_prompt(user_keywords, location, profile_data)
            
            # Generate optimized queries
            response = self.model.generate_content(prompt)
            result_text = response.text.strip()
            
            # Parse Gemini's response
            optimized = self._parse_gemini_response(result_text, user_keywords, location)
            
            logger.info(f"Query optimized: '{user_keywords}' → '{optimized['primary_query']}'")
            
            return optimized
            
        except Exception as e:
            logger.error(f"Query optimization error: {e}", exc_info=True)
            # Fallback to original
            return {
                "success": False,
                "primary_query": user_keywords,
                "variations": [user_keywords],
                "google_query": f"{user_keywords} jobs" + (f" near {location}" if location else ""),
                "error": str(e)
            }

    def enrich_jobspy_parameters(
        self,
        user_keywords: str,
        location: str = "",
        profile_data: Optional[Dict[str, Any]] = None,
        remote: Optional[bool] = None,
        user_easy_apply: Optional[bool] = None,
        user_hours_old: Optional[int] = None
    ) -> Dict[str, Any]:
        """
        Generate structured JobSpy parameters using Gemini.
        User-provided easy_apply/hours_old are treated as hard constraints.
        """
        defaults = self._build_default_jobspy_params(
            user_keywords=user_keywords,
            location=location,
            remote=remote,
            user_easy_apply=user_easy_apply,
            user_hours_old=user_hours_old
        )

        if not self.model:
            return {
                "success": True,
                "method": "rule_based",
                "params": defaults
            }

        try:
            experience_level = self._determine_experience_level(profile_data)
            prompt = self._build_jobspy_param_prompt(
                user_keywords=user_keywords,
                location=location,
                profile_data=profile_data,
                experience_level=experience_level,
                remote=remote,
                user_easy_apply=user_easy_apply,
                user_hours_old=user_hours_old
            )

            response = self.model.generate_content(prompt)
            result_text = (response.text or "").strip()
            parsed = self._parse_json_from_text(result_text)
            if not isinstance(parsed, dict):
                raise ValueError("Gemini did not return a valid JSON object")

            merged = self._merge_and_validate_jobspy_params(defaults, parsed)
            return {
                "success": True,
                "method": "gemini_ai",
                "params": merged
            }

        except Exception as e:
            logger.error(f"JobSpy parameter enrichment error: {e}", exc_info=True)
            return {
                "success": False,
                "method": "fallback",
                "params": defaults,
                "error": str(e)
            }
    
    def _determine_experience_level(self, profile_data: Optional[Dict[str, Any]]) -> str:
        """Determine user's experience level from profile"""
        if not profile_data:
            return "mid level"
        
        work_exp = profile_data.get('work_experience', [])
        if not work_exp:
            return "entry level"
        
        # Count years of experience
        total_years = 0
        for exp in work_exp:
            # Simple heuristic: each job = ~2 years if not specified
            total_years += 2
        
        # Check for senior/lead titles
        for exp in work_exp:
            title = exp.get('title', '').lower()
            if any(keyword in title for keyword in ['senior', 'lead', 'principal', 'staff', 'architect']):
                return "senior"
            if any(keyword in title for keyword in ['junior', 'associate', 'intern']):
                return "entry level"
        
        # Based on years
        if total_years < 2:
            return "entry level"
        elif total_years < 5:
            return "mid level"
        else:
            return "senior"
    
    def _build_optimization_prompt(self, 
                                   keywords: str, 
                                   location: str,
                                   profile_data: Optional[Dict[str, Any]]) -> str:
        """Build prompt for Gemini to optimize the search query"""
        
        # Determine experience level
        experience_level = self._determine_experience_level(profile_data)
        
        prompt = f"""You are a job search expert. Create highly effective job search queries.

USER'S SEARCH:
- Keywords: "{keywords}"
- Location: "{location or 'Any location'}"
- Experience Level: {experience_level}
"""
        
        # Add profile context if available
        if profile_data:
            skills = profile_data.get('skills', {})
            work_exp = profile_data.get('work_experience', [])
            
            if skills:
                all_skills = []
                for category, skill_list in skills.items():
                    if skill_list:
                        all_skills.extend(skill_list[:5])  # Top 5 per category
                if all_skills:
                    prompt += f"\n- Key Skills: {', '.join(all_skills[:8])}"
            
            if work_exp and len(work_exp) > 0:
                recent_title = work_exp[0].get('title', '')
                if recent_title:
                    prompt += f"\n- Current/Recent Role: {recent_title}"
        
        prompt += f"""

YOUR TASK: Generate optimized job search queries using this format:
"{experience_level} [job type] jobs for [role/keywords] in [location]"

REQUIREMENTS:
1. PRIMARY: Best query with experience level, key skills, location
2. ALT1: Alternative with different job title variation
3. ALT2: Alternative emphasizing different skills
4. ALT3: Broader alternative (in case specific searches fail)
5. GOOGLE: Google Jobs format with "since yesterday" or "since last week"

FORMATTING RULES:
✓ Include experience level: "entry level", "mid level", "senior", etc.
✓ Use natural language: "jobs for X in Y" not just "X"
✓ Add relevant skills/tools when appropriate
✓ For Indeed searches, can use Boolean: (python OR java) -sales
✓ Keep it readable and natural
✓ Location should be included naturally

RESPOND IN EXACTLY THIS FORMAT (no markdown, no extra text):
PRIMARY: [query]
ALT1: [query]
ALT2: [query]
ALT3: [query]
GOOGLE: [query]

EXAMPLES:

Example 1 - Software Engineer:
PRIMARY: mid level software engineer jobs for python AWS in San Francisco, CA
ALT1: mid level backend developer jobs with python docker in San Francisco, CA
ALT2: software engineer full time python cloud in San Francisco Bay Area
ALT3: software development jobs mid level in San Francisco, CA
GOOGLE: mid level software engineer python jobs near San Francisco, CA since last week

Example 2 - AI Engineer (senior):
PRIMARY: senior AI engineer jobs machine learning python in New York, NY
ALT1: senior machine learning engineer jobs deep learning in New York, NY
ALT2: AI/ML engineer senior full time in New York City
ALT3: artificial intelligence engineer jobs senior in New York
GOOGLE: senior AI engineer machine learning jobs near New York, NY since yesterday

Example 3 - Data Analyst (entry level):
PRIMARY: entry level data analyst jobs SQL python in Chicago, IL
ALT1: junior data analyst jobs excel SQL in Chicago, IL
ALT2: data analyst entry level full time in Chicago area
ALT3: analyst jobs entry level data in Chicago, IL
GOOGLE: entry level data analyst jobs near Chicago, IL since last week

NOW CREATE QUERIES FOR THE USER'S SEARCH ABOVE:
"""
        
        return prompt
    
    def _rule_based_optimization(self,
                                keywords: str,
                                location: str,
                                profile_data: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Generate optimized queries using rule-based logic (no AI needed)
        Fallback when Gemini is not available
        """
        # Determine experience level
        exp_level = self._determine_experience_level(profile_data)
        
        # Extract key skills if available
        top_skills = []
        if profile_data:
            skills = profile_data.get('skills', {})
            for category, skill_list in skills.items():
                if skill_list:
                    top_skills.extend(skill_list[:3])
        
        # Build location phrase
        loc_phrase = f"in {location}" if location else ""
        
        # Build queries
        primary = f"{exp_level} {keywords} jobs {loc_phrase}".strip()
        
        # Add top skill if available
        if top_skills:
            primary = f"{exp_level} {keywords} jobs {top_skills[0]} {loc_phrase}".strip()
        
        # Alternative variations
        variations = [
            f"{exp_level} {keywords} full time {loc_phrase}".strip(),
            f"{keywords} {exp_level} jobs {loc_phrase}".strip(),
            f"{keywords} jobs {loc_phrase}".strip()
        ]
        
        # Google query
        google_query = f"{exp_level} {keywords} jobs"
        if location:
            google_query += f" near {location}"
        google_query += " since last week"
        
        return {
            "success": True,
            "primary_query": primary,
            "variations": variations,
            "google_query": google_query,
            "original_query": keywords,
            "method": "rule_based"
        }
    
    def _parse_gemini_response(self, 
                               response_text: str, 
                               fallback_keywords: str,
                               location: str) -> Dict[str, Any]:
        """Parse Gemini's response into structured data"""
        
        lines = response_text.strip().split('\n')
        
        primary = fallback_keywords
        variations = []
        google_query = f"{fallback_keywords} jobs" + (f" near {location}" if location else "")
        
        for line in lines:
            line = line.strip()
            if line.startswith('PRIMARY:'):
                primary = line.replace('PRIMARY:', '').strip()
            elif line.startswith('ALT1:'):
                variations.append(line.replace('ALT1:', '').strip())
            elif line.startswith('ALT2:'):
                variations.append(line.replace('ALT2:', '').strip())
            elif line.startswith('ALT3:'):
                variations.append(line.replace('ALT3:', '').strip())
            elif line.startswith('GOOGLE:'):
                google_query = line.replace('GOOGLE:', '').strip()
        
        # Ensure we have at least one variation (the primary)
        if not variations:
            variations = [primary]
        
        return {
            "success": True,
            "primary_query": primary,
            "variations": variations,
            "google_query": google_query,
            "original_query": fallback_keywords,
            "method": "gemini_ai"
        }

    def _build_default_jobspy_params(
        self,
        user_keywords: str,
        location: str,
        remote: Optional[bool],
        user_easy_apply: Optional[bool],
        user_hours_old: Optional[int]
    ) -> Dict[str, Any]:
        """Build conservative defaults compatible with JobSpyAdapter."""
        return {
            "keywords": user_keywords,
            "location": location or "",
            "remote": bool(remote) if remote is not None else False,
            "job_type": None,
            "hours_old": user_hours_old if user_hours_old is not None else None,
            "easy_apply": bool(user_easy_apply) if user_easy_apply is not None else False,
            "distance": 50,
            "country_indeed": "USA",
            "linkedin_fetch_description": False,
            "google_search_term": None,
            "sites": ["indeed", "linkedin", "zip_recruiter", "google"]
        }

    def _build_jobspy_param_prompt(
        self,
        user_keywords: str,
        location: str,
        profile_data: Optional[Dict[str, Any]],
        experience_level: str,
        remote: Optional[bool],
        user_easy_apply: Optional[bool],
        user_hours_old: Optional[int]
    ) -> str:
        """Prompt Gemini to return structured JobSpy params."""
        profile_context = json.dumps(profile_data or {}, indent=2)
        remote_text = "null" if remote is None else str(bool(remote)).lower()
        easy_text = "null" if user_easy_apply is None else str(bool(user_easy_apply)).lower()
        hours_text = "null" if user_hours_old is None else str(int(user_hours_old))

        return f"""You are a job search parameter optimizer.
Return ONLY a JSON object, no markdown, no comments.

User input:
- keywords: "{user_keywords}"
- location: "{location or ''}"
- experience_level: "{experience_level}"
- remote_preference: {remote_text}
- user_easy_apply_override: {easy_text}
- user_hours_old_override: {hours_text}

Profile context:
{profile_context}

Output JSON schema:
{{
  "keywords": "string",
  "location": "string",
  "remote": true,
  "job_type": "fulltime|parttime|internship|contract|null",
  "hours_old": "integer|null",
  "easy_apply": true,
  "distance": 50,
  "country_indeed": "string",
  "linkedin_fetch_description": false,
  "google_search_term": "string|null",
  "sites": ["indeed","linkedin","zip_recruiter","google"]
}}

Rules:
1) If user_easy_apply_override is not null, easy_apply MUST equal it.
2) If user_hours_old_override is not null, hours_old MUST equal it.
3) Keep sites to supported JobSpy sources only: indeed, linkedin, zip_recruiter, google, glassdoor, bayt, naukri, bdjobs.
4) Prefer broad high-recall search. Keep keywords clear and role-focused.
5) If location is unknown, use empty string.
6) country_indeed should be a valid country name like USA, UK, Canada, India.
7) distance must be an integer from 5 to 100.
8) google_search_term should be natural and can include recency phrases.
"""

    def _parse_json_from_text(self, text: str) -> Dict[str, Any]:
        """Parse JSON from model response with small cleanup fallback."""
        try:
            return json.loads(text)
        except Exception:
            match = re.search(r"\{[\s\S]*\}", text)
            if not match:
                raise
            return json.loads(match.group(0))

    def _merge_and_validate_jobspy_params(
        self,
        defaults: Dict[str, Any],
        model_params: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Merge model output onto defaults and sanitize expected fields."""
        merged = defaults.copy()
        merged.update({k: v for k, v in model_params.items() if v is not None})

        # Normalize supported enum values
        valid_job_types = {"fulltime", "parttime", "internship", "contract"}
        jt = merged.get("job_type")
        if isinstance(jt, str):
            jt = jt.strip().lower()
            merged["job_type"] = jt if jt in valid_job_types else None
        else:
            merged["job_type"] = None

        # Normalize sites
        valid_sites = {"indeed", "linkedin", "zip_recruiter", "google", "glassdoor", "bayt", "naukri", "bdjobs"}
        sites = merged.get("sites")
        if isinstance(sites, list):
            normalized_sites = [str(s).strip().lower() for s in sites if str(s).strip().lower() in valid_sites]
            merged["sites"] = normalized_sites or defaults["sites"]
        else:
            merged["sites"] = defaults["sites"]

        # Normalize booleans
        merged["remote"] = bool(merged.get("remote", defaults["remote"]))
        merged["easy_apply"] = bool(merged.get("easy_apply", defaults["easy_apply"]))
        merged["linkedin_fetch_description"] = bool(
            merged.get("linkedin_fetch_description", defaults["linkedin_fetch_description"])
        )

        # Normalize numeric fields
        try:
            distance = int(merged.get("distance", defaults["distance"]))
            merged["distance"] = max(5, min(distance, 100))
        except Exception:
            merged["distance"] = defaults["distance"]

        hours_old = merged.get("hours_old")
        if hours_old is not None:
            try:
                hours_val = int(hours_old)
                merged["hours_old"] = max(1, hours_val)
            except Exception:
                merged["hours_old"] = defaults["hours_old"]

        # Normalize strings
        merged["keywords"] = str(merged.get("keywords") or defaults["keywords"]).strip()
        merged["location"] = str(merged.get("location") or defaults["location"]).strip()
        merged["country_indeed"] = str(merged.get("country_indeed") or defaults["country_indeed"]).strip()
        google_term = merged.get("google_search_term")
        merged["google_search_term"] = str(google_term).strip() if isinstance(google_term, str) and google_term.strip() else None

        return merged
    
    def generate_query_variations(self, 
                                 keywords: str, 
                                 num_variations: int = 3) -> List[str]:
        """
        Generate multiple search query variations
        Useful for trying different search strategies
        """
        result = self.optimize_search_query(keywords)
        
        if result['success']:
            # Return primary + variations (up to num_variations)
            queries = [result['primary_query']] + result.get('variations', [])
            return queries[:num_variations]
        else:
            # Fallback: return original
            return [keywords]


def optimize_query(keywords: str, 
                  location: str = "", 
                  profile_data: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """
    Convenience function to optimize a search query
    
    Example:
        optimized = optimize_query("software engineer", "San Francisco, CA")
        print(optimized['primary_query'])  # Use this for JobSpy search
    """
    optimizer = GeminiQueryOptimizer()
    return optimizer.optimize_search_query(keywords, location, profile_data)


if __name__ == "__main__":
    # Test the optimizer
    logging.basicConfig(level=logging.INFO)
    
    print("Testing Gemini Query Optimizer...\n")
    
    optimizer = GeminiQueryOptimizer()
    
    # Test 1: Simple query
    result = optimizer.optimize_search_query("AI engineer", "New York, NY")
    
    print(f"Original: AI engineer")
    print(f"Optimized Primary: {result['primary_query']}")
    print(f"Google Query: {result['google_query']}")
    print(f"\nAlternative queries:")
    for i, var in enumerate(result.get('variations', []), 1):
        print(f"  {i}. {var}")
    
    # Test 2: With profile data
    print("\n" + "="*60 + "\n")
    
    profile = {
        "skills": {
            "programming_languages": ["Python", "JavaScript", "Go"],
            "tools": ["Docker", "Kubernetes", "AWS"]
        },
        "work_experience": [
            {"title": "Senior Backend Engineer"}
        ]
    }
    
    result2 = optimizer.optimize_search_query("software engineer", "San Francisco, CA", profile)
    
    print(f"Original: software engineer")
    print(f"Optimized Primary: {result2['primary_query']}")
    print(f"Google Query: {result2['google_query']}")
    print(f"\nAlternative queries:")
    for i, var in enumerate(result2.get('variations', []), 1):
        print(f"  {i}. {var}")
