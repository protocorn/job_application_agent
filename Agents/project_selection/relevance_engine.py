"""
Project Relevance Engine

Analyzes and scores projects based on job requirements.
Recommends which projects to include/exclude from resume.
"""

import json
import hashlib
import re
import logging
from typing import Any, Dict, List, Optional, Set, Tuple
from datetime import datetime
from gemini_compat import genai

# Set up logging
logger = logging.getLogger(__name__)


class ProjectRelevanceEngine:
    """Scores and ranks projects based on job relevance"""

    def __init__(self, gemini_api_key: Optional[str] = None):
        """
        Initialize the relevance engine.

        Args:
            gemini_api_key: Optional Gemini API key for AI-powered relevance scoring
        """
        if gemini_api_key:
            genai.configure(api_key=gemini_api_key)
            self.model = genai.GenerativeModel("gemini-2.5-flash")
        else:
            self.model = None
        self.logger = logger
        self._llm_score_cache: Dict[str, Dict[str, float]] = {}

    @staticmethod
    def _normalize_text(text: str) -> str:
        """Lowercase and normalize whitespace for consistent matching."""
        return re.sub(r"\s+", " ", (text or "").lower()).strip()

    @staticmethod
    def _tokenize_text(text: str) -> Set[str]:
        """
        Tokenize text into alphanumeric-ish tokens.

        Keeps common tech punctuation in tokens (e.g., c++, c#, node.js).
        """
        return set(re.findall(r"[a-z0-9][a-z0-9+#.\-]*", text.lower()))

    @staticmethod
    def _contains_phrase(normalized_text: str, phrase: str) -> bool:
        """Match phrase with token boundaries to avoid substring false positives."""
        normalized_phrase = re.sub(r"\s+", " ", phrase.lower()).strip()
        if not normalized_phrase:
            return False

        tokens = [re.escape(token) for token in normalized_phrase.split()]
        pattern = r"(?<![a-z0-9])" + r"\s+".join(tokens) + r"(?![a-z0-9])"
        return re.search(pattern, normalized_text) is not None

    @classmethod
    def _term_matches(cls, candidate: str, reference: str) -> bool:
        """
        Return True when candidate and reference tech terms align.

        Examples:
        - "react" matches "react native"
        - "node.js" matches "node"
        """
        cand = cls._normalize_text(candidate)
        ref = cls._normalize_text(reference)
        if not cand or not ref:
            return False

        if cand == ref:
            return True

        return cls._contains_phrase(cand, ref) or cls._contains_phrase(ref, cand)

    @staticmethod
    def _clamp_score(score: float) -> float:
        """Clamp score to the 0-100 range."""
        return max(0.0, min(100.0, float(score)))

    @staticmethod
    def _project_key(project: Dict, index: int) -> str:
        """Stable key used to map LLM scores back to projects."""
        project_id = project.get("id")
        if project_id is not None:
            return str(project_id)

        fingerprint_source = json.dumps(
            {
                "name": str(project.get("name", "")).strip().lower(),
                "description": str(project.get("description", "")).strip().lower(),
                "technologies": sorted(str(t).strip().lower() for t in project.get("technologies", [])),
                "features": sorted(str(f).strip().lower() for f in project.get("features", [])),
            },
            sort_keys=True,
            ensure_ascii=True,
        )
        return f"fp_{hashlib.sha1(fingerprint_source.encode('utf-8')).hexdigest()[:20]}"

    @staticmethod
    def _extract_json_payload(text: str) -> Dict[str, Any]:
        """Extract a JSON object from raw model output."""
        if not text:
            return {}

        cleaned = text.strip()
        if cleaned.startswith("```"):
            cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
            cleaned = re.sub(r"\s*```$", "", cleaned)

        try:
            parsed = json.loads(cleaned)
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            pass

        match = re.search(r"\{.*\}", text, flags=re.DOTALL)
        if not match:
            return {}

        try:
            parsed = json.loads(match.group(0))
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}

    def calculate_llm_relevance_batch(
        self,
        projects: List[Dict],
        job_description: str,
        job_keywords: List[str],
        required_technologies: Optional[List[str]] = None,
    ) -> Dict[str, float]:
        """
        Score project-job relevance in one lightweight LLM call.

        Returns:
            Mapping of project_key -> score (0-100)
        """
        if not self.model or not projects or not job_description:
            return {}

        normalized_job = self._normalize_text(job_description)
        if not normalized_job:
            return {}
        prompt_job_description = normalized_job[:2500]

        required_technologies = required_technologies or []
        compact_projects = []

        for idx, project in enumerate(projects):
            compact_projects.append({
                "project_key": self._project_key(project, idx),
                "name": project.get("name", ""),
                "description": project.get("description", "")[:500],
                "technologies": project.get("technologies", [])[:20],
                "features": project.get("features", [])[:12],
            })

        cache_material = {
            "job_description": prompt_job_description,
            "job_keywords": [self._normalize_text(k) for k in (job_keywords or [])][:20],
            "required_technologies": [self._normalize_text(t) for t in required_technologies][:20],
            "projects": compact_projects,
        }
        cache_key = json.dumps(cache_material, sort_keys=True)
        if cache_key in self._llm_score_cache:
            return self._llm_score_cache[cache_key]

        prompt = f"""
You are scoring resume projects for job relevance.

Return ONLY valid JSON in this exact schema:
{{
  "scores": [
    {{
      "project_key": "string",
      "score": 0-100
    }}
  ]
}}

Scoring guidance:
- 90-100: Strong direct match to role scope and responsibilities
- 70-89: Good match with clear overlap
- 40-69: Partial relevance
- 0-39: Weak match

Job description:
{prompt_job_description}

Priority keywords:
{", ".join((job_keywords or [])[:25])}

Required technologies:
{", ".join((required_technologies or [])[:25])}

Projects:
{json.dumps(compact_projects, ensure_ascii=True)}
""".strip()

        try:
            response = self.model.generate_content(prompt)
            payload = self._extract_json_payload(getattr(response, "text", ""))
            raw_scores = payload.get("scores", [])

            score_map: Dict[str, float] = {}
            if isinstance(raw_scores, list):
                for item in raw_scores:
                    if not isinstance(item, dict):
                        continue
                    project_key = str(item.get("project_key", "")).strip()
                    score_value = item.get("score")
                    if not project_key or score_value is None:
                        continue
                    try:
                        score_map[project_key] = self._clamp_score(float(score_value))
                    except (TypeError, ValueError):
                        continue

            self._llm_score_cache[cache_key] = score_map
            return score_map
        except Exception as exc:
            self.logger.warning("LLM relevance scoring failed: %s", exc)
            return {}

    def calculate_keyword_overlap(self, project: Dict, job_keywords: List[str]) -> float:
        """
        Calculate keyword overlap between project and job requirements.

        Args:
            project: Project dict with name, description, technologies
            job_keywords: List of keywords from job description

        Returns:
            Score 0-100 based on keyword matches
        """
        project_text = self._normalize_text(" ".join([
            project.get('name', ''),
            project.get('description', ''),
            " ".join(project.get('technologies', [])),
            " ".join(project.get('features', [])),
            " ".join(project.get('detailed_bullets', []))
        ]))
        project_tokens = self._tokenize_text(project_text)

        # Count unique keyword matches
        matches = 0
        normalized_keywords = []
        seen_keywords = set()
        for keyword in job_keywords:
            normalized = self._normalize_text(keyword)
            if normalized and normalized not in seen_keywords:
                seen_keywords.add(normalized)
                normalized_keywords.append(normalized)

        total_keywords = len(normalized_keywords)

        for keyword_lower in normalized_keywords:
            keyword_tokens = keyword_lower.split()

            # Full keyword match
            if len(keyword_tokens) == 1 and keyword_tokens[0] in project_tokens:
                matches += 1
            elif self._contains_phrase(project_text, keyword_lower):
                matches += 1
            # Partial match (if keyword is multi-word, check for any word)
            elif len(keyword_tokens) > 1:
                if any(word in project_tokens for word in keyword_tokens if len(word) > 3):
                    matches += 0.5

        # Convert to 0-100 score (weight keyword coverage heavily)
        if total_keywords == 0:
            return 0.0

        coverage = (matches / total_keywords) * 100
        return min(100, coverage * 1.5)  # Boost scores, cap at 100

    def calculate_technology_match(
        self,
        project: Dict,
        required_technologies: List[str]
    ) -> float:
        """
        Calculate how well project technologies match job requirements.

        Args:
            project: Project dict
            required_technologies: List of required/preferred technologies

        Returns:
            Score 0-100
        """
        project_techs = [
            self._normalize_text(t)
            for t in project.get('technologies', [])
            if self._normalize_text(t)
        ]
        required_techs = []
        seen_required = set()
        for tech in required_technologies:
            normalized = self._normalize_text(tech)
            if normalized and normalized not in seen_required:
                seen_required.add(normalized)
                required_techs.append(normalized)

        if not required_techs:
            return 50.0  # Neutral score if no specific requirements

        matches = sum(
            1 for req in required_techs
            if any(self._term_matches(proj, req) for proj in project_techs)
        )
        match_rate = matches / len(required_techs)

        return match_rate * 100

    def calculate_recency_score(self, project: Dict) -> float:
        """
        Calculate recency bonus (prefer recent projects).

        Args:
            project: Project dict with end_date

        Returns:
            Score 0-100 (100 = very recent, 0 = very old)
        """
        end_date_str = project.get('end_date', '')

        # If ongoing project ("Present")
        if end_date_str and end_date_str.lower() in ['present', 'current', 'ongoing']:
            return 100.0

        # Try to parse date
        try:
            # Handle various formats: "2024", "Jan 2024", "2024-01"
            if len(end_date_str) == 4 and end_date_str.isdigit():
                year = int(end_date_str)
            elif re.match(r'\w+ \d{4}', end_date_str):
                year = int(end_date_str.split()[-1])
            elif '-' in end_date_str:
                year = int(end_date_str.split('-')[0])
            else:
                return 50.0  # Can't determine

            current_year = datetime.now().year
            years_ago = current_year - year

            # Scoring: 100 for this year, decreasing by 15 per year
            score = max(0, 100 - (years_ago * 15))
            return score

        except (ValueError, TypeError):
            return 50.0  # Default if can't parse

    def calculate_complexity_score(self, project: Dict) -> float:
        """
        Calculate project complexity/impressiveness.

        More complex projects are more valuable on resume.

        Args:
            project: Project dict

        Returns:
            Score 0-100
        """
        score = 50.0  # Base score

        # Team size indicator
        team_size = project.get('team_size', 1)
        if team_size > 1:
            score += min(15, team_size * 3)  # Up to +15 for team projects

        # Number of features
        features = project.get('features', [])
        score += min(15, len(features) * 3)  # Up to +15 for feature-rich projects

        # Number of technologies used
        technologies = project.get('technologies', [])
        score += min(10, len(technologies) * 2)  # Up to +10 for tech diversity

        # Has live deployment
        if project.get('live_url'):
            score += 10

        # Has GitHub repo
        if project.get('github_url'):
            score += 5

        # Description length (longer = more detailed = more substantial)
        description = project.get('description', '')
        if len(description) > 200:
            score += 10
        elif len(description) > 100:
            score += 5

        return min(100, score)

    def calculate_overall_relevance(
        self,
        project: Dict,
        job_keywords: List[str],
        required_technologies: Optional[List[str]] = None,
        llm_relevance_score: Optional[float] = None,
        weights: Optional[Dict[str, float]] = None
    ) -> Dict[str, float]:
        """
        Calculate overall relevance score with breakdown.

        Args:
            project: Project dict
            job_keywords: Keywords from job description
            required_technologies: List of required technologies
            llm_relevance_score: Optional LLM relevance score (0-100)
            weights: Optional custom weights for scoring components

        Returns:
            Dict with overall score and component breakdowns
        """
        # Default weights
        if weights is None:
            weights = {
                'keyword_overlap': 0.40,
                'technology_match': 0.25,
                'domain_relevance': 0.15,
                'recency': 0.10,
                'complexity': 0.10
            }

        # Calculate component scores
        keyword_score = self.calculate_keyword_overlap(project, job_keywords)
        tech_score = self.calculate_technology_match(
            project,
            required_technologies or []
        )
        if llm_relevance_score is not None:
            domain_score = self._clamp_score(llm_relevance_score)
        else:
            domain_score = 50.0
        recency_score = self.calculate_recency_score(project)
        complexity_score = self.calculate_complexity_score(project)

        # Weighted overall score
        overall_score = (
            keyword_score * weights['keyword_overlap'] +
            tech_score * weights['technology_match'] +
            domain_score * weights['domain_relevance'] +
            recency_score * weights['recency'] +
            complexity_score * weights['complexity']
        )

        return {
            'overall_score': round(overall_score, 2),
            'keyword_overlap': round(keyword_score, 2),
            'technology_match': round(tech_score, 2),
            'domain_relevance': round(domain_score, 2),
            'llm_relevance': round(domain_score, 2) if llm_relevance_score is not None else None,
            'recency': round(recency_score, 2),
            'complexity': round(complexity_score, 2),
            'weights_used': weights
        }

    def rank_projects(
        self,
        projects: List[Dict],
        job_keywords: List[str],
        required_technologies: Optional[List[str]] = None,
        job_description: Optional[str] = None,
        top_n: Optional[int] = None
    ) -> List[Tuple[Dict, Dict[str, float]]]:
        """
        Rank all projects by relevance.

        Args:
            projects: List of project dicts
            job_keywords: Keywords from job description
            required_technologies: List of required technologies
            job_description: Full job description for optional LLM relevance scoring
            top_n: Return only top N projects (None = all)

        Returns:
            List of (project, scores) tuples, sorted by relevance (highest first)
        """
        scored_projects = []

        llm_scores = self.calculate_llm_relevance_batch(
            projects=projects,
            job_description=job_description or "",
            job_keywords=job_keywords,
            required_technologies=required_technologies or [],
        )

        for idx, project in enumerate(projects):
            scores = self.calculate_overall_relevance(
                project,
                job_keywords,
                required_technologies,
                llm_relevance_score=llm_scores.get(self._project_key(project, idx))
            )
            scored_projects.append((project, scores))

        # Sort by overall score (descending)
        scored_projects.sort(key=lambda x: x[1]['overall_score'], reverse=True)

        if top_n:
            return scored_projects[:top_n]

        return scored_projects

    def recommend_project_swaps(
        self,
        current_projects: List[Dict],
        all_projects: List[Dict],
        job_keywords: List[str],
        required_technologies: Optional[List[str]] = None,
        job_description: Optional[str] = None,
        min_improvement_threshold: float = 15.0
    ) -> List[Dict]:
        """
        Recommend which projects to swap for better relevance.

        Args:
            current_projects: Projects currently on resume
            all_projects: All available projects
            job_keywords: Keywords from job description
            required_technologies: List of required technologies
            job_description: Full job description for optional LLM relevance scoring
            min_improvement_threshold: Minimum score improvement to recommend swap

        Returns:
            List of swap recommendations with format:
            {
                'remove': project_dict,
                'add': project_dict,
                'score_delta': float,
                'remove_score': float,
                'add_score': float,
                'reason': str
            }
        """
        llm_scores = self.calculate_llm_relevance_batch(
            projects=all_projects,
            job_description=job_description or "",
            job_keywords=job_keywords,
            required_technologies=required_technologies or [],
        )

        # Score all current projects
        current_scored = []
        for idx, proj in enumerate(current_projects):
            scores = self.calculate_overall_relevance(
                proj,
                job_keywords,
                required_technologies,
                llm_relevance_score=llm_scores.get(self._project_key(proj, idx))
            )
            current_scored.append((proj, scores['overall_score']))

        # Sort current projects (lowest score first - these are candidates for removal)
        current_scored.sort(key=lambda x: x[1])

        # Score all alternative projects (not currently on resume)
        current_ids = {p.get('id') for p in current_projects}
        alternatives = [p for p in all_projects if p.get('id') not in current_ids]

        alternative_scored = []
        for idx, proj in enumerate(alternatives):
            scores = self.calculate_overall_relevance(
                proj,
                job_keywords,
                required_technologies,
                llm_relevance_score=llm_scores.get(self._project_key(proj, idx))
            )
            alternative_scored.append((proj, scores['overall_score']))

        # Sort alternatives (highest score first)
        alternative_scored.sort(key=lambda x: x[1], reverse=True)

        # Generate swap recommendations
        recommendations = []

        for remove_proj, remove_score in current_scored:
            for add_proj, add_score in alternative_scored:
                score_delta = add_score - remove_score

                if score_delta >= min_improvement_threshold:
                    # Generate human-readable reason
                    reason = f"Higher relevance (+{score_delta:.0f} points)"

                    # Add specific reasons
                    add_techs = set(add_proj.get('technologies', []))
                    remove_techs = set(remove_proj.get('technologies', []))
                    new_techs = add_techs - remove_techs

                    if new_techs and required_technologies:
                        matching_techs = [
                            t for t in new_techs
                            if any(self._term_matches(t, rt) for rt in required_technologies)
                        ]
                        if matching_techs:
                            reason += f"; adds required tech: {', '.join(matching_techs[:2])}"

                    recommendations.append({
                        'remove': remove_proj,
                        'add': add_proj,
                        'score_delta': round(score_delta, 2),
                        'remove_score': round(remove_score, 2),
                        'add_score': round(add_score, 2),
                        'reason': reason
                    })

                    # Once we find a good replacement, move to next project to remove
                    break

        return recommendations

    def suggest_optimal_project_set(
        self,
        all_projects: List[Dict],
        job_keywords: List[str],
        target_count: int = 3,
        required_technologies: Optional[List[str]] = None,
        job_description: Optional[str] = None,
    ) -> Tuple[List[Dict], float]:
        """
        Suggest the optimal set of N projects for this job.

        Args:
            all_projects: All available projects
            job_keywords: Keywords from job description
            target_count: Number of projects to include
            required_technologies: List of required technologies
            job_description: Full job description for optional LLM relevance scoring

        Returns:
            Tuple of (optimal_projects, total_score)
        """
        # Rank all projects
        ranked = self.rank_projects(
            all_projects,
            job_keywords,
            required_technologies,
            job_description,
            top_n=target_count * 2  # Get more candidates for optimization
        )

        # Start with top N projects
        selected = ranked[:target_count]
        selected_projects = [proj for proj, scores in selected]
        total_score = sum(scores['overall_score'] for proj, scores in selected)

        return selected_projects, total_score

