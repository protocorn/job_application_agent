"""
Project Bullet Generator

Generates tailored resume bullets for projects based on job requirements.
"""

import re
import logging
from typing import Dict, List, Optional
import google.generativeai as genai

# Set up logging
logger = logging.getLogger(__name__)


class ProjectBulletGenerator:
    """Generates tailored project bullets for resumes"""

    def __init__(self, gemini_api_key: str, model_name: str = "gemini-2.5-flash"):
        """
        Initialize the bullet generator.

        Args:
            gemini_api_key: Gemini API key
            model_name: Gemini model to use
        """
        genai.configure(api_key=gemini_api_key)
        self.model = genai.GenerativeModel(model_name)
        self.logger = logger

    def generate_bullets(
        self,
        project: Dict,
        job_keywords: List[str],
        job_description: str,
        target_bullet_count: int = 3,
        char_limit_per_line: int = 90,
        mimikree_context: Optional[str] = None
    ) -> List[str]:
        """
        Generate tailored resume bullets for a project.

        Args:
            project: Project dict with name, description, technologies, features
            job_keywords: Keywords from job description to emphasize
            job_description: Full job description for context
            target_bullet_count: Number of bullets to generate
            char_limit_per_line: Character limit per bullet line
            mimikree_context: Optional additional context from Mimikree

        Returns:
            List of bullet strings
        """
        try:
            # Build project context
            project_context = f"""
PROJECT NAME: {project.get('name', 'Unknown Project')}

DESCRIPTION: {project.get('description', 'No description available')}

TECHNOLOGIES: {', '.join(project.get('technologies', []))}

FEATURES:
{chr(10).join([f"- {f}" for f in project.get('features', [])])}
"""

            if project.get('detailed_bullets'):
                project_context += f"\n\nEXISTING BULLETS (for reference):\n"
                project_context += "\n".join([f"- {b}" for b in project.get('detailed_bullets', [])])

            # Prepare additional context section (avoid backslash in f-string)
            additional_context = ""
            if mimikree_context:
                additional_context = f"ADDITIONAL CONTEXT:\n{mimikree_context}\n"
            
            prompt = f"""Generate {target_bullet_count} tailored resume bullet points for this project.

{project_context}

JOB DESCRIPTION EXCERPT:
{job_description[:800]}

KEY JOB REQUIREMENTS TO EMPHASIZE:
{', '.join(job_keywords[:10])}

{additional_context}

REQUIREMENTS FOR BULLETS:
1. Each bullet MUST be 2-3 lines long (approximately {char_limit_per_line * 2}-{char_limit_per_line * 3} characters)
2. Start with strong action verbs (Built, Developed, Implemented, Designed, Created)
3. Include quantified metrics where possible (users, performance, scale, etc.)
4. Naturally incorporate relevant job keywords: {', '.join(job_keywords[:5])}
5. Highlight technical skills and impact
6. Be specific about technologies and methodologies used
7. Follow the format: [Action] [What] using [Technologies] to achieve [Impact/Result]

EXAMPLE FORMAT (adapt to this project):
Built scalable REST API using Node.js and Express, handling 10K+ requests/day and reducing response time by 40% through optimized database queries and caching strategies

Implemented real-time chat feature with WebSocket protocol and Redis pub/sub, enabling instant message delivery for 5K+ concurrent users with 99.9% uptime

CRITICAL OUTPUT RULES:
- Return EXACTLY {target_bullet_count} bullets
- Each bullet on a new line starting with a dash (-)
- NO numbering, NO extra formatting
- NO explanations or metadata
- Just the clean bullet text

Generate {target_bullet_count} bullets now:"""

            response = self.model.generate_content(prompt)
            bullets_text = response.text.strip()

            # Parse bullets
            bullets = []
            for line in bullets_text.split('\n'):
                line = line.strip()
                # Remove leading dash, number, or asterisk
                line = re.sub(r'^[-•*\d.)\]]+\s*', '', line)

                if line and len(line) > 20:  # Minimum meaningful length
                    bullets.append(line)

            # Validate bullet count
            if len(bullets) < target_bullet_count:
                self.logger.warning(f"Generated only {len(bullets)} bullets, expected {target_bullet_count}")
            elif len(bullets) > target_bullet_count:
                bullets = bullets[:target_bullet_count]

            # Validate bullet length (should be 2-3 lines)
            validated_bullets = []
            for bullet in bullets:
                lines_count = len(bullet) // char_limit_per_line + (1 if len(bullet) % char_limit_per_line > 0 else 0)

                if lines_count < 2:
                    self.logger.warning(f"Bullet too short ({lines_count} lines): {bullet[:50]}...")
                elif lines_count > 4:
                    self.logger.warning(f"Bullet too long ({lines_count} lines): {bullet[:50]}...")

                validated_bullets.append(bullet)

            return validated_bullets

        except Exception as e:
            self.logger.error(f"Failed to generate bullets: {e}")
            # Fallback: create basic bullets from project info
            fallback_bullets = []

            if project.get('description'):
                fallback_bullets.append(project['description'])

            for feature in project.get('features', [])[:2]:
                if len(fallback_bullets) < target_bullet_count:
                    fallback_bullets.append(f"Implemented {feature} using {', '.join(project.get('technologies', [])[:2])}")

            return fallback_bullets[:target_bullet_count]

    def regenerate_bullet(
        self,
        original_bullet: str,
        feedback: str,
        job_keywords: List[str],
        char_limit_per_line: int = 90
    ) -> str:
        """
        Regenerate a single bullet based on user feedback.

        Args:
            original_bullet: The bullet to improve
            feedback: User feedback (e.g., "add more metrics", "too generic")
            job_keywords: Keywords to emphasize
            char_limit_per_line: Character limit per line

        Returns:
            Improved bullet string
        """
        try:
            prompt = f"""Improve this resume bullet based on user feedback.

ORIGINAL BULLET:
{original_bullet}

USER FEEDBACK:
{feedback}

JOB KEYWORDS TO EMPHASIZE: {', '.join(job_keywords[:5])}

REQUIREMENTS:
- Keep it 2-3 lines long (~{char_limit_per_line * 2}-{char_limit_per_line * 3} characters)
- Address the user's feedback
- Maintain or add quantified metrics
- Incorporate relevant keywords naturally
- Keep strong action verb at start

Return ONLY the improved bullet text, no explanations."""

            response = self.model.generate_content(prompt)
            improved = response.text.strip()

            # Clean up formatting
            improved = re.sub(r'^[-•*\d.)\]]+\s*', '', improved)

            return improved

        except Exception as e:
            self.logger.error(f"Failed to regenerate bullet: {e}")
            return original_bullet

    def generate_bullets_batch(
        self,
        projects: List[Dict],
        job_keywords: List[str],
        job_description: str,
        bullets_per_project: int = 3
    ) -> Dict[str, List[str]]:
        """
        Generate bullets for multiple projects in batch.

        Args:
            projects: List of project dicts
            job_keywords: Keywords from job description
            job_description: Full job description
            bullets_per_project: Number of bullets per project

        Returns:
            Dict mapping project IDs/names to bullet lists
        """
        results = {}

        for project in projects:
            project_id = project.get('id') or project.get('name', 'unknown')

            bullets = self.generate_bullets(
                project,
                job_keywords,
                job_description,
                target_bullet_count=bullets_per_project
            )

            results[str(project_id)] = bullets

            self.logger.info(f"Generated {len(bullets)} bullets for project: {project.get('name', 'Unknown')}")

        return results

    def enhance_existing_bullets(
        self,
        project: Dict,
        existing_bullets: List[str],
        job_keywords: List[str],
        job_description: str
    ) -> List[str]:
        """
        Enhance existing project bullets to better match job requirements.

        Args:
            project: Project dict
            existing_bullets: Current bullets
            job_keywords: Keywords from job description
            job_description: Full job description

        Returns:
            Enhanced bullet list
        """
        try:
            bullets_text = "\n".join([f"- {b}" for b in existing_bullets])

            prompt = f"""Enhance these project bullets to better match job requirements.

PROJECT: {project.get('name', 'Unknown')}
TECHNOLOGIES: {', '.join(project.get('technologies', []))}

CURRENT BULLETS:
{bullets_text}

JOB DESCRIPTION EXCERPT:
{job_description[:600]}

KEY JOB KEYWORDS: {', '.join(job_keywords[:10])}

TASK: Enhance each bullet to:
1. Better emphasize relevant keywords: {', '.join(job_keywords[:5])}
2. Add more quantified metrics where possible
3. Highlight technologies and methodologies relevant to the job
4. Maintain 2-3 lines per bullet
5. Keep all factual claims (don't fabricate metrics)

Return the enhanced bullets in the same order, one per line, starting with dash (-)."""

            response = self.model.generate_content(prompt)
            enhanced_text = response.text.strip()

            # Parse enhanced bullets
            enhanced = []
            for line in enhanced_text.split('\n'):
                line = line.strip()
                line = re.sub(r'^[-•*\d.)\]]+\s*', '', line)
                if line and len(line) > 20:
                    enhanced.append(line)

            # Ensure same count as original
            if len(enhanced) != len(existing_bullets):
                self.logger.warning(f"Enhanced bullet count mismatch: {len(enhanced)} vs {len(existing_bullets)}")
                return existing_bullets

            return enhanced

        except Exception as e:
            self.logger.error(f"Failed to enhance bullets: {e}")
            return existing_bullets

    def validate_bullet_quality(self, bullet: str) -> Dict[str, any]:
        """
        Validate the quality of a generated bullet.

        Args:
            bullet: Bullet text to validate

        Returns:
            Dict with validation results:
                - is_valid: Boolean
                - issues: List of quality issues
                - score: Quality score 0-100
        """
        issues = []
        score = 100.0

        # Check 1: Length (should be 2-3 lines, ~160-270 chars)
        if len(bullet) < 100:
            issues.append("Too short - add more detail")
            score -= 20
        elif len(bullet) > 350:
            issues.append("Too long - condense")
            score -= 15

        # Check 2: Starts with action verb
        action_verbs = [
            'built', 'developed', 'implemented', 'designed', 'created', 'engineered',
            'architected', 'deployed', 'optimized', 'improved', 'led', 'managed'
        ]
        if not any(bullet.lower().startswith(verb) for verb in action_verbs):
            issues.append("Should start with strong action verb")
            score -= 15

        # Check 3: Contains technical terms
        tech_pattern = r'\b[A-Z][a-z]+(?:\.[A-Z][a-z]+)*\b|[A-Z]{2,}'
        if not re.search(tech_pattern, bullet):
            issues.append("Should include specific technologies")
            score -= 10

        # Check 4: Contains metrics (numbers)
        if not re.search(r'\d+[%+kKmM]?', bullet):
            issues.append("Consider adding quantified metrics")
            score -= 10

        # Check 5: Not too generic
        generic_phrases = ['various', 'multiple', 'several', 'different', 'many']
        if any(phrase in bullet.lower() for phrase in generic_phrases):
            issues.append("Avoid generic phrases - be specific")
            score -= 10

        is_valid = score >= 70

        return {
            'is_valid': is_valid,
            'issues': issues,
            'score': score,
            'length': len(bullet)
        }
