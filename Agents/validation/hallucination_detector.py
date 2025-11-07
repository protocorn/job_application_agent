"""
Hallucination Detector

Detects when AI-generated resume content adds claims not supported by source data.
Prevents fabrication of achievements, skills, or experience.
"""

import re
import logging
from typing import Dict, List, Optional
import google.generativeai as genai

# Set up logging
logger = logging.getLogger(__name__)


class HallucinationDetector:
    """Detects unsupported claims in AI-generated content"""

    def __init__(self, gemini_api_key: str, model_name: str = "gemini-2.0-flash-exp"):
        """
        Initialize the hallucination detector.

        Args:
            gemini_api_key: Google Gemini API key
            model_name: Gemini model to use for detection
        """
        genai.configure(api_key=gemini_api_key)
        self.model = genai.GenerativeModel(model_name)
        self.logger = logger

    def detect_hallucinations(
        self,
        generated_content: str,
        source_data: str,
        original_content: Optional[str] = None
    ) -> Dict[str, any]:
        """
        Detect unsupported claims in generated content.

        Args:
            generated_content: The AI-generated text to validate
            source_data: The source data (Mimikree responses, resume context, etc.)
            original_content: Optional original text being modified

        Returns:
            Dict with:
                - has_hallucinations: Boolean
                - unsupported_claims: List of claims not backed by source data
                - supported_claims: List of claims that are backed
                - confidence_score: 0.0-1.0 (higher = more confident no hallucinations)
                - recommendation: 'ACCEPT', 'REVIEW', or 'REJECT'
        """
        try:
            prompt = f"""Analyze the generated content for factual accuracy against the source data.

SOURCE DATA (ground truth):
{source_data}

{f"ORIGINAL CONTENT (before modification):\\n{original_content}\\n" if original_content else ""}
GENERATED CONTENT (to validate):
{generated_content}

TASK: Identify every factual claim in the generated content and categorize each as:
1. SUPPORTED: Clearly stated or implied in source data or original content
2. INFERRED: Reasonable inference from source data (low risk)
3. UNSUPPORTED: Not backed by any evidence (hallucination - high risk)
4. EXAGGERATED: Based on truth but overstated (medium risk)

For each claim, explain your reasoning.

CRITICAL: Numbers, percentages, achievement metrics, specific technologies, company names, dates, and role titles must be EXACTLY from source data or original content. Any deviation is a hallucination.

Respond in this exact format:

CLAIM 1: [exact claim text from generated content]
CATEGORY: [SUPPORTED / INFERRED / UNSUPPORTED / EXAGGERATED]
REASONING: [why you categorized it this way]

CLAIM 2: [exact claim text]
CATEGORY: [SUPPORTED / INFERRED / UNSUPPORTED / EXAGGERATED]
REASONING: [explanation]

...

OVERALL_ASSESSMENT: [SAFE / NEEDS_REVIEW / DANGEROUS]
CONFIDENCE: [0-100]
RECOMMENDATION: [ACCEPT / REVIEW / REJECT]
SUMMARY: [brief explanation]"""

            response = self.model.generate_content(prompt)
            analysis = response.text.strip()

            # Parse response
            unsupported_claims = []
            exaggerated_claims = []
            supported_claims = []
            inferred_claims = []

            # Extract claims
            claim_blocks = re.split(r'\n\n+', analysis)
            for block in claim_blocks:
                # Look for CLAIM pattern
                claim_match = re.search(r'CLAIM \d+:\s*(.+?)(?=\nCATEGORY:|$)', block, re.DOTALL)
                category_match = re.search(r'CATEGORY:\s*(SUPPORTED|INFERRED|UNSUPPORTED|EXAGGERATED)', block)
                reasoning_match = re.search(r'REASONING:\s*(.+?)(?=\n\n|$)', block, re.DOTALL)

                if claim_match and category_match:
                    claim_text = claim_match.group(1).strip()
                    category = category_match.group(1)
                    reasoning = reasoning_match.group(1).strip() if reasoning_match else "No reasoning provided"

                    claim_info = {
                        'claim': claim_text,
                        'reasoning': reasoning
                    }

                    if category == 'UNSUPPORTED':
                        unsupported_claims.append(claim_info)
                    elif category == 'EXAGGERATED':
                        exaggerated_claims.append(claim_info)
                    elif category == 'INFERRED':
                        inferred_claims.append(claim_info)
                    elif category == 'SUPPORTED':
                        supported_claims.append(claim_info)

            # Extract overall assessment
            assessment_match = re.search(r'OVERALL_ASSESSMENT:\s*(SAFE|NEEDS_REVIEW|DANGEROUS)', analysis)
            assessment = assessment_match.group(1) if assessment_match else 'NEEDS_REVIEW'

            confidence_match = re.search(r'CONFIDENCE:\s*(\d+)', analysis)
            confidence = int(confidence_match.group(1)) / 100 if confidence_match else 0.5

            recommendation_match = re.search(r'RECOMMENDATION:\s*(ACCEPT|REVIEW|REJECT)', analysis)
            recommendation = recommendation_match.group(1) if recommendation_match else 'REVIEW'

            summary_match = re.search(r'SUMMARY:\s*(.+?)(?=\n\n|$)', analysis, re.DOTALL)
            summary = summary_match.group(1).strip() if summary_match else "Analysis complete"

            # Calculate risk score
            total_claims = len(supported_claims) + len(inferred_claims) + len(unsupported_claims) + len(exaggerated_claims)
            if total_claims > 0:
                risk_score = (len(unsupported_claims) * 1.0 + len(exaggerated_claims) * 0.5) / total_claims
            else:
                risk_score = 0.0

            has_hallucinations = len(unsupported_claims) > 0 or len(exaggerated_claims) > 1

            return {
                'has_hallucinations': has_hallucinations,
                'unsupported_claims': unsupported_claims,
                'exaggerated_claims': exaggerated_claims,
                'inferred_claims': inferred_claims,
                'supported_claims': supported_claims,
                'confidence_score': confidence,
                'risk_score': risk_score,
                'assessment': assessment,
                'recommendation': recommendation,
                'summary': summary,
                'full_analysis': analysis
            }

        except Exception as e:
            self.logger.error(f"Hallucination detection failed: {e}")
            # Conservative fallback: flag for review
            return {
                'has_hallucinations': True,
                'unsupported_claims': [{'claim': 'Could not validate - requires manual review', 'reasoning': str(e)}],
                'exaggerated_claims': [],
                'inferred_claims': [],
                'supported_claims': [],
                'confidence_score': 0.0,
                'risk_score': 1.0,
                'assessment': 'NEEDS_REVIEW',
                'recommendation': 'REVIEW',
                'summary': f'Detection error: {str(e)}',
                'full_analysis': ''
            }

    def validate_bullet_against_experience(
        self,
        bullet_text: str,
        mimikree_data: str,
        job_context: Optional[str] = None
    ) -> Dict[str, any]:
        """
        Validate a resume bullet against user's actual experience (Mimikree data).

        Args:
            bullet_text: The bullet point to validate
            mimikree_data: User's experience data from Mimikree
            job_context: Optional job description context

        Returns:
            Dict with validation results
        """
        source_data = mimikree_data
        if job_context:
            source_data += f"\n\nJOB CONTEXT (for keyword context only - not as source of claims):\n{job_context}"

        return self.detect_hallucinations(
            generated_content=bullet_text,
            source_data=source_data,
            original_content=None
        )

    def batch_validate_bullets(
        self,
        bullets: List[str],
        mimikree_data: str
    ) -> Dict[str, any]:
        """
        Validate multiple bullets in a single batch.

        Args:
            bullets: List of bullet texts to validate
            mimikree_data: User's experience data

        Returns:
            Dict with:
                - results: List of validation results per bullet
                - overall_safe: Boolean indicating if all bullets pass
                - flagged_bullets: List of bullet indices that need review
        """
        results = []
        flagged_indices = []

        for i, bullet in enumerate(bullets):
            result = self.validate_bullet_against_experience(
                bullet_text=bullet,
                mimikree_data=mimikree_data
            )
            results.append(result)

            if result['recommendation'] in ['REVIEW', 'REJECT']:
                flagged_indices.append(i)

        overall_safe = len(flagged_indices) == 0

        return {
            'results': results,
            'overall_safe': overall_safe,
            'flagged_bullets': flagged_indices,
            'total_unsupported_claims': sum(len(r['unsupported_claims']) for r in results),
            'total_exaggerated_claims': sum(len(r['exaggerated_claims']) for r in results)
        }

    def validate_project_description(
        self,
        project_title: str,
        project_bullets: List[str],
        mimikree_data: str,
        existing_projects: Optional[List[Dict[str, str]]] = None
    ) -> Dict[str, any]:
        """
        Validate an entire project section against user's experience.

        Args:
            project_title: The project name
            project_bullets: List of bullet points for this project
            mimikree_data: User's experience data
            existing_projects: Optional list of other projects for consistency checking

        Returns:
            Dict with validation results for the entire project
        """
        # Combine project into single text
        project_text = f"PROJECT: {project_title}\n" + "\n".join([f"- {b}" for b in project_bullets])

        # Add existing projects as additional context (for consistency)
        source_data = mimikree_data
        if existing_projects:
            existing_context = "\n\nEXISTING PROJECTS (for consistency checking):\n"
            for proj in existing_projects:
                if proj.get('name') != project_title:
                    existing_context += f"- {proj.get('name', 'Unknown')}: {proj.get('description', '')[:100]}\n"
            source_data += existing_context

        result = self.detect_hallucinations(
            generated_content=project_text,
            source_data=source_data
        )

        # Add project-specific fields
        result['project_title'] = project_title
        result['bullets_validated'] = len(project_bullets)

        return result
