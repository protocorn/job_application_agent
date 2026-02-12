"""
Project Relevance Engine

Analyzes and scores projects based on job requirements.
Recommends which projects to include/exclude from resume.
"""

import re
import logging
from typing import Dict, List, Optional, Tuple
from datetime import datetime
import google.generativeai as genai

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

    def calculate_keyword_overlap(self, project: Dict, job_keywords: List[str]) -> float:
        """
        Calculate keyword overlap between project and job requirements.

        Args:
            project: Project dict with name, description, technologies
            job_keywords: List of keywords from job description

        Returns:
            Score 0-100 based on keyword matches
        """
        project_text = " ".join([
            project.get('name', ''),
            project.get('description', ''),
            " ".join(project.get('technologies', [])),
            " ".join(project.get('features', [])),
            " ".join(project.get('detailed_bullets', []))
        ]).lower()

        # Count unique keyword matches
        matches = 0
        total_keywords = len(job_keywords)

        for keyword in job_keywords:
            keyword_lower = keyword.lower()

            # Full keyword match
            if keyword_lower in project_text:
                matches += 1
            # Partial match (if keyword is multi-word, check for any word)
            elif len(keyword_lower.split()) > 1:
                words = keyword_lower.split()
                if any(word in project_text for word in words if len(word) > 3):
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
        project_techs = [t.lower() for t in project.get('technologies', [])]
        required_techs = [t.lower() for t in required_technologies]

        if not required_techs:
            return 50.0  # Neutral score if no specific requirements

        matches = sum(1 for req in required_techs if any(req in proj for proj in project_techs))
        match_rate = matches / len(required_techs)

        return match_rate * 100

    def calculate_domain_relevance(
        self,
        project: Dict,
        job_domain: str
    ) -> float:
        """
        Calculate domain/industry relevance.

        Args:
            project: Project dict
            job_domain: Domain/industry (e.g., "web development", "machine learning")

        Returns:
            Score 0-100
        """
        # Domain keywords mapping
        domain_keywords = {
            'web development': ['web', 'frontend', 'backend', 'fullstack', 'api', 'rest', 'http', 'server', 'client'],
            'machine learning': ['ml', 'ai', 'model', 'neural', 'deep learning', 'nlp', 'computer vision', 'tensorflow', 'pytorch'],
            'mobile': ['mobile', 'ios', 'android', 'react native', 'flutter', 'app'],
            'data': ['data', 'analytics', 'pipeline', 'etl', 'database', 'sql', 'big data', 'warehouse'],
            'devops': ['devops', 'ci/cd', 'docker', 'kubernetes', 'aws', 'cloud', 'infrastructure'],
            'security': ['security', 'authentication', 'encryption', 'penetration', 'vulnerability'],
        }

        job_domain_lower = job_domain.lower()
        relevant_keywords = []

        # Find matching domain keywords
        for domain, keywords in domain_keywords.items():
            if domain in job_domain_lower:
                relevant_keywords.extend(keywords)

        if not relevant_keywords:
            return 50.0  # Neutral if can't determine domain

        # Check project for domain keywords
        project_text = " ".join([
            project.get('name', ''),
            project.get('description', ''),
            " ".join(project.get('technologies', []))
        ]).lower()

        matches = sum(1 for kw in relevant_keywords if kw in project_text)
        return min(100, (matches / len(relevant_keywords)) * 200)  # Boost and cap

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

        except:
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
        job_domain: Optional[str] = None,
        weights: Optional[Dict[str, float]] = None
    ) -> Dict[str, float]:
        """
        Calculate overall relevance score with breakdown.

        Args:
            project: Project dict
            job_keywords: Keywords from job description
            required_technologies: List of required technologies
            job_domain: Domain/industry of the job
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
        domain_score = self.calculate_domain_relevance(
            project,
            job_domain or 'general'
        )
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
            'recency': round(recency_score, 2),
            'complexity': round(complexity_score, 2),
            'weights_used': weights
        }

    def rank_projects(
        self,
        projects: List[Dict],
        job_keywords: List[str],
        required_technologies: Optional[List[str]] = None,
        job_domain: Optional[str] = None,
        top_n: Optional[int] = None
    ) -> List[Tuple[Dict, Dict[str, float]]]:
        """
        Rank all projects by relevance.

        Args:
            projects: List of project dicts
            job_keywords: Keywords from job description
            required_technologies: List of required technologies
            job_domain: Domain/industry
            top_n: Return only top N projects (None = all)

        Returns:
            List of (project, scores) tuples, sorted by relevance (highest first)
        """
        scored_projects = []

        for project in projects:
            scores = self.calculate_overall_relevance(
                project,
                job_keywords,
                required_technologies,
                job_domain
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
        job_domain: Optional[str] = None,
        min_improvement_threshold: float = 15.0
    ) -> List[Dict]:
        """
        Recommend which projects to swap for better relevance.

        Args:
            current_projects: Projects currently on resume
            all_projects: All available projects
            job_keywords: Keywords from job description
            required_technologies: List of required technologies
            job_domain: Domain/industry
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
        # Score all current projects
        current_scored = []
        for proj in current_projects:
            scores = self.calculate_overall_relevance(
                proj,
                job_keywords,
                required_technologies,
                job_domain
            )
            current_scored.append((proj, scores['overall_score']))

        # Sort current projects (lowest score first - these are candidates for removal)
        current_scored.sort(key=lambda x: x[1])

        # Score all alternative projects (not currently on resume)
        current_ids = {p.get('id') for p in current_projects}
        alternatives = [p for p in all_projects if p.get('id') not in current_ids]

        alternative_scored = []
        for proj in alternatives:
            scores = self.calculate_overall_relevance(
                proj,
                job_keywords,
                required_technologies,
                job_domain
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
                        matching_techs = [t for t in new_techs if any(rt.lower() in t.lower() for rt in required_technologies)]
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
        job_domain: Optional[str] = None
    ) -> Tuple[List[Dict], float]:
        """
        Suggest the optimal set of N projects for this job.

        Args:
            all_projects: All available projects
            job_keywords: Keywords from job description
            target_count: Number of projects to include
            required_technologies: List of required technologies
            job_domain: Domain/industry

        Returns:
            Tuple of (optimal_projects, total_score)
        """
        # Rank all projects
        ranked = self.rank_projects(
            all_projects,
            job_keywords,
            required_technologies,
            job_domain,
            top_n=target_count * 2  # Get more candidates for optimization
        )

        # Start with top N projects
        selected = ranked[:target_count]
        selected_projects = [proj for proj, scores in selected]
        total_score = sum(scores['overall_score'] for proj, scores in selected)

        return selected_projects, total_score
