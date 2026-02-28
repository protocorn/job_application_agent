"""
Job Relevance Scoring System

Scores jobs 0-100 based on profile match using:
  - Keyword matching against resume_keywords (Gemini-extracted) when available,
    falling back to profile-derived keywords
  - Experience level
  - Salary
  - Location
  - Job type
  - Recency

No domain-specific penalties are applied — the scorer works for any profession.

Field-name awareness: handles both "work experience" and "work_experience" so it
works whether the profile came from AgentProfileService or the Launchway API.
"""

import re
import logging
from typing import Any, Dict, List, Set
from datetime import datetime

logger = logging.getLogger(__name__)


class JobRelevanceScorer:
    """Calculate relevance score for a job based on the user's profile."""

    def __init__(self, profile: Dict[str, Any]):
        self.profile = profile
        self._augment_profile()
        self.user_keywords = self._extract_user_keywords()
        logger.debug(f"Scorer initialised — {len(self.user_keywords)} user keywords")

    # ── profile normalisation ─────────────────────────────────────────────────

    def _augment_profile(self):
        """
        Derive computed fields that the scoring logic needs but that the
        profile doesn't store directly.  Handles both naming conventions
        ("work experience" / "work_experience").
        """
        p = self.profile

        # Normalise work experience key
        if "work experience" in p and "work_experience" not in p:
            p["work_experience"] = p["work experience"]
        elif "work_experience" in p and "work experience" not in p:
            p["work experience"] = p["work_experience"]

        # Derive years_of_experience from work history entries
        if not p.get("years_of_experience"):
            work_exp = p.get("work_experience") or p.get("work experience") or []
            if work_exp:
                p["years_of_experience"] = min(len(work_exp) * 2, 20)

            # Also check resume_keywords if present
            rk = p.get("resume_keywords") or {}
            if rk.get("years_of_experience") and not p.get("years_of_experience"):
                p["years_of_experience"] = rk["years_of_experience"]

        # Derive open_to_remote from preferred location list
        if not p.get("open_to_remote"):
            locs = p.get("preferred location") or p.get("preferred_location") or []
            p["open_to_remote"] = any(
                "remote" in str(loc).lower() for loc in locs
            )

        # Derive preferred_cities / preferred_states from preferred location
        if not p.get("preferred_cities") and not p.get("preferred_states"):
            locs = p.get("preferred location") or p.get("preferred_location") or []
            cities, states = [], []
            for loc in locs:
                if not isinstance(loc, str) or "remote" in loc.lower():
                    continue
                parts = [pt.strip() for pt in loc.split(",")]
                if parts:
                    cities.append(parts[0])
                if len(parts) >= 2:
                    states.append(parts[-1].split()[0])
            p["preferred_cities"] = cities
            p["preferred_states"] = states

    # ── keyword extraction ────────────────────────────────────────────────────

    def _extract_user_keywords(self) -> Set[str]:
        """
        Build the keyword set that represents this user's professional identity.

        Priority order:
          1. resume_keywords (Gemini-extracted) — most accurate, domain-agnostic
          2. Profile fields: skills, summary, work experience titles/descriptions,
             education, projects
        """
        keywords: Set[str] = set()
        p = self.profile

        # ── 1. Gemini-extracted resume keywords (highest quality) ─────────────
        rk = p.get("resume_keywords") or {}
        if rk:
            for cat in ("skills", "job_titles", "industries", "domains", "education_fields"):
                for item in (rk.get(cat) or []):
                    keywords.update(self._tokenize(str(item)))

        # ── 2. Skills object from profile ─────────────────────────────────────
        for cat in ("technical", "programming_languages", "frameworks", "tools", "soft_skills"):
            items = (p.get("skills") or {}).get(cat, [])
            if isinstance(items, list):
                keywords.update(s.lower().strip() for s in items if s)

        # ── 3. Professional summary ───────────────────────────────────────────
        summary = p.get("summary", "")
        if summary:
            keywords.update(self._tokenize(summary))

        # ── 4. Work experience titles and descriptions ────────────────────────
        work_exp = p.get("work_experience") or p.get("work experience") or []
        for exp in work_exp:
            if isinstance(exp, dict):
                if exp.get("title"):
                    keywords.update(self._tokenize(exp["title"]))
                if exp.get("description"):
                    keywords.update(self._tokenize(exp["description"]))

        # ── 5. Education degrees ──────────────────────────────────────────────
        for edu in (p.get("education") or []):
            if isinstance(edu, dict) and edu.get("degree"):
                keywords.update(self._tokenize(edu["degree"]))

        # ── 6. Project technologies ───────────────────────────────────────────
        for proj in (p.get("projects") or []):
            if isinstance(proj, dict):
                techs = proj.get("technologies", [])
                if isinstance(techs, list):
                    keywords.update(t.lower().strip() for t in techs if t)

        logger.debug(f"Extracted {len(keywords)} keywords from profile")
        return keywords

    # ── tokeniser ─────────────────────────────────────────────────────────────

    _STOP_WORDS = {
        'the', 'a', 'an', 'and', 'or', 'but', 'in', 'on', 'at', 'to',
        'for', 'of', 'with', 'by', 'from', 'as', 'is', 'was', 'are', 'were',
        'been', 'be', 'have', 'has', 'had', 'do', 'does', 'did', 'will',
        'would', 'should', 'could', 'may', 'might', 'must', 'can',
        'its', 'our', 'their', 'this', 'that', 'also', 'other', 'using',
    }

    def _tokenize(self, text: str) -> Set[str]:
        if not text:
            return set()
        tokens = re.findall(r'\b\w+\b', text.lower())
        return {t for t in tokens if t not in self._STOP_WORDS and len(t) > 2}

    # ── main scorer ───────────────────────────────────────────────────────────

    def calculate_score(self, job: Dict[str, Any]) -> int:
        """
        Score breakdown (total: 100 points)
          Title keyword match:       0–30
          Description keyword match: 0–25
          Experience level match:    0–15
          Salary match:              0–10  (0 default — no free points if no data)
          Location match:            0–10
          Job type match:            0–5
          Recency:                   0–5
        """
        try:
            title = job.get("title", "")
            score = 0

            score += self._score_title_match(title)
            score += self._score_description_match(
                job.get("description", ""),
                job.get("requirements", ""),
            )
            score += self._score_experience_match(job.get("experience_level", ""))
            score += self._score_salary_match(
                job.get("salary_min"), job.get("salary_max"),
                job.get("salary_currency", "USD"),
            )
            score += self._score_location_match(
                job.get("location", ""), job.get("is_remote", False)
            )
            score += self._score_job_type_match(job.get("job_type", ""))
            score += self._score_recency(job.get("posted_date", ""))

            score = max(0, min(100, score))
            logger.debug(f"'{title}' -> {score}/100")
            return score

        except Exception as e:
            logger.error(f"Scoring error for '{job.get('title','?')}': {e}")
            return 0

    # ── component scorers ─────────────────────────────────────────────────────

    def _score_title_match(self, title: str) -> int:
        """Keyword overlap between job title and user keywords (0-30)."""
        if not title:
            return 0

        title_tokens = self._tokenize(title)
        if not self.user_keywords:
            return 5  # minimal score when no profile keywords available

        matching = title_tokens.intersection(self.user_keywords)
        ratio = len(matching) / max(len(title_tokens), 1)
        score = int(ratio * 25)

        # Bonus for exact past job title match
        for exp in (
            self.profile.get("work_experience")
            or self.profile.get("work experience")
            or []
        ):
            if isinstance(exp, dict):
                past_title = exp.get("title", "").lower()
                if past_title and past_title in title.lower():
                    score = min(score + 5, 30)
                    break

        # Bonus if title keywords also appear in resume_keywords job_titles
        rk_titles = (self.profile.get("resume_keywords") or {}).get("job_titles", [])
        if rk_titles:
            rk_title_tokens: Set[str] = set()
            for t in rk_titles:
                rk_title_tokens.update(self._tokenize(str(t)))
            if title_tokens.intersection(rk_title_tokens):
                score = min(score + 5, 30)

        return score

    def _score_description_match(self, description: str, requirements: str) -> int:
        """Keyword overlap against job description + requirements (0-25)."""
        combined = f"{description} {requirements}"
        if not combined.strip():
            return 0

        job_tokens = self._tokenize(combined)
        if not self.user_keywords:
            return 0

        matches = len(job_tokens.intersection(self.user_keywords))

        if matches == 0:    return 0
        if matches <= 3:    return 5
        if matches <= 7:    return 10
        if matches <= 12:   return 17
        return 25

    def _score_experience_match(self, job_experience_level: str) -> int:
        """Experience level alignment (0-15).  7 default when level unknown."""
        if not job_experience_level:
            return 7

        # Prefer resume_keywords-derived level if available
        rk = self.profile.get("resume_keywords") or {}
        rk_level = rk.get("experience_level", "")
        user_years = (
            rk.get("years_of_experience")
            or self.profile.get("years_of_experience", 0)
        )
        desired  = self.profile.get("desired_experience_levels", [])
        user_level = rk_level or self._years_to_level(user_years)

        if desired and job_experience_level.lower() in [l.lower() for l in desired]:
            return 15
        if job_experience_level.lower() == user_level.lower():
            return 15

        level_order = ["internship", "entry", "mid", "senior", "lead", "executive"]
        try:
            diff = abs(
                level_order.index(user_level.lower()) -
                level_order.index(job_experience_level.lower())
            )
            return [15, 10, 5, 0][min(diff, 3)]
        except ValueError:
            return 7

    @staticmethod
    def _years_to_level(years: int) -> str:
        if years < 2:  return "entry"
        if years < 5:  return "mid"
        if years < 8:  return "senior"
        if years < 12: return "lead"
        return "executive"

    def _score_salary_match(self, job_min, job_max, job_currency: str) -> int:
        """Salary alignment (0-10).  Returns 0 when no data — no free points."""
        user_min = self.profile.get("minimum_salary")
        if not user_min or (not job_min and not job_max):
            return 0

        if job_currency != self.profile.get("salary_currency", "USD"):
            return 0

        if job_min and job_min >= user_min:         return 10
        if job_max and job_max >= user_min:         return 8
        if job_max and job_max >= user_min * 0.8:   return 4
        return 0

    def _score_location_match(self, location: str, is_remote: bool) -> int:
        """Location alignment (0-10).  5 default when no preferences set."""
        open_anywhere = self.profile.get("open_to_anywhere", False)
        open_remote   = self.profile.get("open_to_remote", False)
        pref_cities   = self.profile.get("preferred_cities", [])
        pref_states   = self.profile.get("preferred_states", [])

        if open_anywhere:
            return 10
        if (is_remote or "remote" in location.lower()) and open_remote:
            return 10
        if not pref_cities and not pref_states and not open_remote:
            return 5  # no preferences → neutral

        loc_lower = location.lower()
        if any(c.lower() in loc_lower for c in pref_cities if c):
            return 10
        if any(s.lower() in loc_lower for s in pref_states if s):
            return 8
        return 0

    def _score_job_type_match(self, job_type: str) -> int:
        """Job type alignment (0-5)."""
        desired = self.profile.get("desired_job_types", [])
        if not desired:
            return 3  # slight positive — most jobs are full-time
        if job_type and job_type.lower() in [t.lower() for t in desired]:
            return 5
        return 0

    def _score_recency(self, posted_date: str) -> int:
        """Posting recency bonus (0-5)."""
        if not posted_date:
            return 2
        try:
            from dateutil import parser
            days = (datetime.utcnow() - parser.parse(posted_date)).days
            if days <= 1:   return 5
            if days <= 7:   return 4
            if days <= 14:  return 3
            if days <= 30:  return 2
            return 1
        except Exception:
            return 2


# ── public API ────────────────────────────────────────────────────────────────

def rank_jobs(
    jobs: List[Dict[str, Any]],
    profile: Dict[str, Any],
    min_score: int = 0,
) -> List[Dict[str, Any]]:
    """
    Score every job in `jobs` and return them sorted highest-first,
    filtered to those with relevance_score >= min_score.
    """
    scorer = JobRelevanceScorer(profile)

    for job in jobs:
        job["relevance_score"] = scorer.calculate_score(job)

    filtered = [j for j in jobs if j["relevance_score"] >= min_score]
    filtered.sort(key=lambda j: j["relevance_score"], reverse=True)

    logger.info(
        f"Ranked {len(filtered)} jobs (from {len(jobs)}) with min_score={min_score}"
    )
    return filtered
