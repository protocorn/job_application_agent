"""
Mimikree Project Discovery

Uses intelligent questioning to discover additional projects from user's Mimikree profile
that aren't currently listed on their resume.
"""

import re
import json
import logging
from typing import Dict, List, Optional, Tuple
import google.generativeai as genai

# Set up logging
logger = logging.getLogger(__name__)

# Handle imports whether called from Agents/ or parent directory
try:
    from Agents.mimikree_integration import MimikreeClient
except ImportError:
    from mimikree_integration import MimikreeClient


class MimikreeProjectDiscovery:
    """Discovers additional projects from Mimikree based on job requirements"""

    def __init__(self, gemini_api_key: str):
        """
        Initialize project discovery.

        Args:
            gemini_api_key: Gemini API key for question generation and parsing
        """
        genai.configure(api_key=gemini_api_key)
        self.model = genai.GenerativeModel("gemini-2.5-flash")
        self.logger = logger

    def generate_discovery_questions(
        self,
        job_keywords: List[str],
        job_description: str,
        current_projects: List[Dict],
        max_questions: int = 10
    ) -> List[str]:
        """
        Generate intelligent questions to discover relevant projects.

        Args:
            job_keywords: Keywords from job description
            job_description: Full job description
            current_projects: Projects already on resume
            max_questions: Maximum number of questions to generate

        Returns:
            List of questions to ask Mimikree
        """
        # Extract current project technologies and domains
        current_techs = set()
        current_domains = set()

        for proj in current_projects:
            current_techs.update([t.lower() for t in proj.get('technologies', [])])
            proj_name = proj.get('name', '').lower()

            # Infer domain from project name/description
            if any(word in proj_name for word in ['web', 'website', 'app']):
                current_domains.add('web development')
            if any(word in proj_name for word in ['ml', 'ai', 'model', 'neural']):
                current_domains.add('machine learning')
            if any(word in proj_name for word in ['mobile', 'ios', 'android']):
                current_domains.add('mobile development')

        try:
            prompt = f"""Generate questions to discover relevant projects from a user's portfolio.

JOB DESCRIPTION:
{job_description[:1000]}

KEY JOB REQUIREMENTS:
{', '.join(job_keywords[:15])}

CURRENT PROJECTS ON RESUME:
{chr(10).join([f"- {p.get('name', 'Unknown')}: {', '.join(p.get('technologies', []))}" for p in current_projects[:5]])}

TASK: Generate {max_questions} targeted questions to find OTHER projects (not listed above) that would be relevant for this job.

Focus on:
1. Technologies mentioned in job but missing from current projects
2. Domains/industries relevant to the job
3. Specific types of projects (e.g., "APIs", "mobile apps", "ML models")
4. Academic/personal/side projects that demonstrate relevant skills

CRITICAL FORMAT RULES:
- Each question on a new line starting with "Q:"
- Questions should be open-ended to get detailed responses
- Ask about PROJECTS specifically (not just experience)
- Don't repeat technologies already well-covered in current projects

Example format:
Q: Do you have any projects involving machine learning or AI?
Q: Have you built any REST APIs or backend services?
Q: What mobile app projects have you developed?

Generate exactly {max_questions} questions now:"""

            response = self.model.generate_content(prompt)
            questions_text = response.text.strip()

            # Parse questions
            questions = []
            for line in questions_text.split('\n'):
                line = line.strip()
                if line.startswith('Q:'):
                    question = line[2:].strip()
                    if question and '?' in question:
                        questions.append(question)

            # Limit to max_questions
            return questions[:max_questions]

        except Exception as e:
            self.logger.error(f"Failed to generate discovery questions: {e}")
            # Fallback: generic questions based on job keywords
            fallback_questions = [
                f"Do you have any projects involving {kw}?" for kw in job_keywords[:5]
            ]
            fallback_questions.append("What other technical projects have you built that aren't on your resume?")
            fallback_questions.append("Do you have any academic or personal projects that showcase your skills?")
            return fallback_questions[:max_questions]

    def query_mimikree_for_projects(
        self,
        mimikree_client: MimikreeClient,
        questions: List[str]
    ) -> Dict[str, str]:
        """
        Query Mimikree with discovery questions.

        Args:
            mimikree_client: Authenticated MimikreeClient instance
            questions: List of questions to ask

        Returns:
            Dict mapping questions to answers
        """
        try:
            result = mimikree_client.ask_batch_questions(questions)
            answers = mimikree_client.extract_successful_answers(result)
            return answers

        except Exception as e:
            self.logger.error(f"Failed to query Mimikree: {e}")
            return {}

    def parse_projects_from_responses(
        self,
        responses: Dict[str, str],
        job_keywords: List[str]
    ) -> List[Dict]:
        """
        Parse project information from Mimikree responses.

        Args:
            responses: Dict of question -> answer pairs
            job_keywords: Keywords from job description (for relevance scoring)

        Returns:
            List of discovered project dicts
        """
        # Combine all responses
        combined_text = "\n\n".join([
            f"Q: {q}\nA: {a}" for q, a in responses.items()
        ])

        if not combined_text.strip():
            return []

        try:
            prompt = f"""Parse project information from these Q&A responses.

RESPONSES:
{combined_text}

JOB KEYWORDS (for context): {', '.join(job_keywords[:10])}

TASK: Extract ALL distinct projects mentioned in the responses. For each project, extract:
- Name (or create a descriptive name if not explicitly stated)
- Description (2-3 sentence summary)
- Technologies used
- Key features or accomplishments
- Any URLs mentioned

Format your response as JSON array:
[
  {{
    "name": "Project Name",
    "description": "Brief description of what the project does",
    "technologies": ["Tech1", "Tech2"],
    "features": ["Feature 1", "Feature 2"],
    "github_url": "url or null",
    "live_url": "url or null",
    "confidence": "high|medium|low"
  }}
]

CRITICAL RULES:
- Only include PROJECTS (not general experience or skills)
- Each project must have at least a name and description
- Confidence = "high" if explicitly described, "medium" if inferred, "low" if vague
- Return valid JSON array even if only one project found
- If no projects found, return empty array: []

Extract projects now:"""

            response = self.model.generate_content(prompt)
            json_text = response.text.strip()

            # Extract JSON from response (handle markdown code blocks)
            json_match = re.search(r'```(?:json)?\s*(\[[\s\S]*?\])\s*```', json_text)
            if json_match:
                json_text = json_match.group(1)
            elif not json_text.startswith('['):
                # Try to find JSON array in text
                json_match = re.search(r'\[[\s\S]*\]', json_text)
                if json_match:
                    json_text = json_match.group(0)

            # Parse JSON
            projects = json.loads(json_text)

            # Filter by confidence (only high and medium)
            filtered_projects = [
                p for p in projects
                if p.get('confidence', 'low') in ['high', 'medium']
            ]

            self.logger.info(f"Discovered {len(filtered_projects)} projects from Mimikree")
            return filtered_projects

        except json.JSONDecodeError as e:
            self.logger.error(f"Failed to parse JSON from Gemini response: {e}")
            self.logger.debug(f"Response text: {json_text[:500]}")
            return []
        except Exception as e:
            self.logger.error(f"Failed to parse projects: {e}")
            return []

    def discover_projects(
        self,
        mimikree_client: MimikreeClient,
        job_keywords: List[str],
        job_description: str,
        current_projects: List[Dict],
        max_questions: int = 10
    ) -> Tuple[List[Dict], Dict[str, str]]:
        """
        Complete project discovery workflow.

        Args:
            mimikree_client: Authenticated MimikreeClient instance
            job_keywords: Keywords from job description
            job_description: Full job description
            current_projects: Projects currently on resume
            max_questions: Maximum number of questions to ask

        Returns:
            Tuple of (discovered_projects, raw_responses)
        """
        self.logger.info("Starting Mimikree project discovery...")

        # Step 1: Generate questions
        questions = self.generate_discovery_questions(
            job_keywords,
            job_description,
            current_projects,
            max_questions
        )
        self.logger.info(f"Generated {len(questions)} discovery questions")

        # Step 2: Query Mimikree
        responses = self.query_mimikree_for_projects(mimikree_client, questions)
        self.logger.info(f"Received {len(responses)} responses from Mimikree")

        if not responses:
            self.logger.warning("No responses from Mimikree")
            return [], {}

        # Step 3: Parse projects
        discovered_projects = self.parse_projects_from_responses(responses, job_keywords)

        # Step 4: Deduplicate against current projects
        current_names = {p.get('name', '').lower() for p in current_projects}
        new_projects = []

        for proj in discovered_projects:
            proj_name = proj.get('name', '').lower()
            # Check if similar name already exists
            if not any(
                self._similarity(proj_name, existing) > 0.8
                for existing in current_names
            ):
                new_projects.append(proj)

        self.logger.info(f"Found {len(new_projects)} new projects (after deduplication)")

        return new_projects, responses

    def _similarity(self, str1: str, str2: str) -> float:
        """
        Calculate simple similarity between two strings.

        Args:
            str1: First string
            str2: Second string

        Returns:
            Similarity score 0.0-1.0
        """
        words1 = set(str1.lower().split())
        words2 = set(str2.lower().split())

        if not words1 or not words2:
            return 0.0

        intersection = words1.intersection(words2)
        union = words1.union(words2)

        return len(intersection) / len(union)

    def enrich_discovered_projects(
        self,
        projects: List[Dict],
        job_keywords: List[str]
    ) -> List[Dict]:
        """
        Enrich discovered projects with additional metadata.

        Args:
            projects: List of discovered project dicts
            job_keywords: Keywords from job description

        Returns:
            Enriched project list
        """
        for project in projects:
            # Generate tags from technologies and keywords
            tags = set()

            # Add technologies as tags
            for tech in project.get('technologies', []):
                tags.add(tech.lower())

            # Add relevant keywords as tags
            description = project.get('description', '').lower()
            for keyword in job_keywords:
                if keyword.lower() in description:
                    tags.add(keyword.lower())

            project['tags'] = list(tags)

            # Set defaults
            project.setdefault('is_on_resume', False)
            project.setdefault('detailed_bullets', [])
            project.setdefault('display_order', 0)

            # Mark as discovered (not in database yet)
            project['source'] = 'mimikree_discovery'

        return projects
