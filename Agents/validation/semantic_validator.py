"""
Semantic Preservation Validator

Validates that resume edits (condensations and expansions) preserve the original meaning
and key facts. Prevents over-aggressive condensation that loses important information.
"""

import re
import logging
from typing import Dict, List, Optional, Tuple
import google.generativeai as genai

# Set up logging
logger = logging.getLogger(__name__)


class SemanticValidator:
    """Validates semantic preservation in resume edits"""

    def __init__(self, gemini_api_key: str, model_name: str = "gemini-2.0-flash-exp"):
        """
        Initialize the semantic validator.

        Args:
            gemini_api_key: Google Gemini API key
            model_name: Gemini model to use for validation
        """
        genai.configure(api_key=gemini_api_key)
        self.model = genai.GenerativeModel(model_name)
        self.logger = logger

    def calculate_information_density(self, text: str) -> Dict[str, any]:
        """
        Calculate the information density of a text segment.

        High density text has multiple facts, numbers, technical terms.
        Low density text has filler words, vague statements.

        Args:
            text: The text to analyze

        Returns:
            Dict with:
                - density_score: 0-100 (higher = more information-dense)
                - fact_count: Number of distinct facts/claims
                - has_quantified_data: Boolean
                - technical_terms: List of technical terms found
                - filler_percentage: % of text that is filler words
        """
        # Count quantified data (numbers, percentages)
        numbers = re.findall(r'\d+[%+]?|\d+\.\d+[%+]?|\d+x|\d+k\+?', text.lower())
        has_quantified_data = len(numbers) > 0

        # Identify technical terms (capitalized acronyms, technical keywords)
        technical_terms = []

        # Common technical acronyms and terms
        tech_patterns = [
            r'\b[A-Z]{2,}\b',  # Acronyms like API, REST, SQL
            r'\b(?:Python|Java|JavaScript|React|Node|AWS|Azure|GCP|Docker|Kubernetes|TensorFlow|PyTorch|ML|AI|NLP|CI/CD)\b',
            r'\b(?:database|backend|frontend|API|endpoint|microservice|algorithm|model|framework)\b'
        ]

        for pattern in tech_patterns:
            matches = re.findall(pattern, text)
            technical_terms.extend(matches)

        technical_terms = list(set(technical_terms))

        # Count distinct facts (rough heuristic: clauses separated by commas, "and", "by")
        # Remove common conjunctions that don't indicate new facts
        clauses = re.split(r'[,;]|\s+and\s+|\s+by\s+|\s+using\s+|\s+with\s+', text)
        meaningful_clauses = [c.strip() for c in clauses if len(c.strip()) > 10]
        fact_count = len(meaningful_clauses)

        # Calculate filler word percentage
        filler_words = [
            'very', 'really', 'quite', 'just', 'basically', 'actually', 'literally',
            'the', 'a', 'an', 'of', 'to', 'in', 'for', 'on', 'with', 'as', 'by'
        ]
        words = text.lower().split()
        total_words = len(words)
        filler_count = sum(1 for word in words if word.strip('.,;:') in filler_words)
        filler_percentage = (filler_count / total_words * 100) if total_words > 0 else 0

        # Calculate density score
        density_score = 0

        # Factor 1: Facts per 10 words (up to 40 points)
        if total_words > 0:
            fact_density = (fact_count / total_words) * 10
            density_score += min(40, fact_density * 20)

        # Factor 2: Quantified data presence (20 points)
        if has_quantified_data:
            density_score += 20

        # Factor 3: Technical terms (up to 30 points)
        density_score += min(30, len(technical_terms) * 5)

        # Factor 4: Penalty for filler (subtract up to 10 points)
        if filler_percentage > 30:
            density_score -= min(10, (filler_percentage - 30) / 5)

        density_score = max(0, min(100, density_score))

        return {
            'density_score': round(density_score, 2),
            'fact_count': fact_count,
            'has_quantified_data': has_quantified_data,
            'technical_terms': technical_terms,
            'filler_percentage': round(filler_percentage, 2),
            'quantified_data': numbers
        }

    def should_condense(self, text: str, min_density_threshold: int = 50) -> Tuple[bool, str]:
        """
        Determine if a text segment is safe to condense based on information density.

        Args:
            text: The text to evaluate
            min_density_threshold: Minimum density score (0-100) to allow condensation

        Returns:
            Tuple of (should_condense: bool, reason: str)
        """
        density_info = self.calculate_information_density(text)
        density_score = density_info['density_score']

        # High density: Don't condense (too much important info)
        if density_score >= 70:
            return False, f"High information density ({density_score}/100) - condensation may lose critical facts"

        # Medium-high density: Condense with caution
        if density_score >= min_density_threshold:
            if density_info['has_quantified_data']:
                return True, f"Medium density ({density_score}/100) with metrics - condense carefully, preserve numbers"
            return True, f"Medium density ({density_score}/100) - safe to condense moderately"

        # Low density: Safe to condense aggressively
        return True, f"Low density ({density_score}/100) - can condense aggressively"

    def validate_condensation(
        self,
        original_text: str,
        condensed_text: str,
        min_retention_threshold: float = 0.70
    ) -> Dict[str, any]:
        """
        Validate that a condensed version preserves key information from the original.

        Args:
            original_text: The original text
            condensed_text: The condensed version
            min_retention_threshold: Minimum information retention rate (0.0-1.0)

        Returns:
            Dict with:
                - is_valid: Boolean indicating if condensation is acceptable
                - retention_score: 0.0-1.0 indicating information preservation
                - key_facts_preserved: Boolean indicating if critical facts remain
                - missing_elements: List of important elements that were lost
                - reason: Explanation of the validation result
        """
        # Quick checks
        if not condensed_text or len(condensed_text.strip()) < 10:
            return {
                'is_valid': False,
                'retention_score': 0.0,
                'key_facts_preserved': False,
                'missing_elements': ['Condensed text is too short or empty'],
                'reason': 'Condensed version is too short to be meaningful'
            }

        # Analyze both versions
        original_density = self.calculate_information_density(original_text)
        condensed_density = self.calculate_information_density(condensed_text)

        missing_elements = []

        # Check 1: Quantified data preservation (critical)
        original_numbers = set(original_density['quantified_data'])
        condensed_numbers = set(condensed_density['quantified_data'])
        missing_numbers = original_numbers - condensed_numbers

        if missing_numbers:
            missing_elements.append(f"Lost metrics: {', '.join(missing_numbers)}")

        # Check 2: Technical terms preservation
        original_terms = set([t.lower() for t in original_density['technical_terms']])
        condensed_terms = set([t.lower() for t in condensed_density['technical_terms']])
        missing_terms = original_terms - condensed_terms

        if len(missing_terms) > 2:  # Allow losing 1-2 minor terms
            missing_elements.append(f"Lost technical terms: {', '.join(list(missing_terms)[:3])}")

        # Check 3: Use AI to assess semantic similarity
        try:
            prompt = f"""Compare these two text versions and determine if the condensed version preserves the core meaning and key facts from the original.

Original: "{original_text}"
Condensed: "{condensed_text}"

Rate the information retention from 0-100 (100 = all key facts preserved, 0 = completely different meaning).

Respond in this exact format:
RETENTION_SCORE: [number 0-100]
KEY_FACTS_PRESERVED: [YES or NO]
MISSING_CRITICAL_INFO: [list any critical facts that were lost, or "None"]
REASONING: [brief explanation]"""

            response = self.model.generate_content(prompt)
            ai_analysis = response.text.strip()

            # Parse AI response
            retention_match = re.search(r'RETENTION_SCORE:\s*(\d+)', ai_analysis)
            retention_score = int(retention_match.group(1)) / 100 if retention_match else 0.5

            key_facts_match = re.search(r'KEY_FACTS_PRESERVED:\s*(YES|NO)', ai_analysis, re.IGNORECASE)
            key_facts_preserved = key_facts_match.group(1).upper() == 'YES' if key_facts_match else False

            missing_info_match = re.search(r'MISSING_CRITICAL_INFO:\s*(.+?)(?=\n|REASONING:|$)', ai_analysis, re.DOTALL)
            if missing_info_match:
                missing_info = missing_info_match.group(1).strip()
                if missing_info.lower() != 'none':
                    missing_elements.append(f"AI detected: {missing_info}")

        except Exception as e:
            self.logger.error(f"AI validation failed: {e}")
            # Fallback to heuristic scoring
            retention_score = condensed_density['fact_count'] / max(1, original_density['fact_count'])
            key_facts_preserved = retention_score >= 0.6

        # Final validation decision
        is_valid = (
            retention_score >= min_retention_threshold and
            key_facts_preserved and
            len(missing_numbers) == 0  # Never lose quantified data
        )

        # Generate reason
        if is_valid:
            reason = f"Condensation is acceptable ({int(retention_score*100)}% retention)"
        else:
            reasons = []
            if retention_score < min_retention_threshold:
                reasons.append(f"Low retention ({int(retention_score*100)}% < {int(min_retention_threshold*100)}%)")
            if not key_facts_preserved:
                reasons.append("Key facts not preserved")
            if missing_numbers:
                reasons.append(f"Lost metrics: {', '.join(missing_numbers)}")
            reason = "; ".join(reasons)

        return {
            'is_valid': is_valid,
            'retention_score': round(retention_score, 3),
            'key_facts_preserved': key_facts_preserved,
            'missing_elements': missing_elements,
            'reason': reason,
            'ai_analysis': ai_analysis if 'ai_analysis' in locals() else None
        }

    def validate_expansion(
        self,
        original_text: str,
        expanded_text: str,
        source_data: Optional[str] = None
    ) -> Dict[str, any]:
        """
        Validate that an expanded version doesn't add unsupported claims (hallucinations).

        Args:
            original_text: The original text
            expanded_text: The expanded version
            source_data: Optional source data (e.g., Mimikree responses) to validate against

        Returns:
            Dict with:
                - is_valid: Boolean indicating if expansion is acceptable
                - has_new_claims: Boolean indicating if new facts were added
                - unsupported_claims: List of claims not backed by source data
                - reason: Explanation of the validation result
        """
        if not expanded_text or len(expanded_text.strip()) < len(original_text.strip()):
            return {
                'is_valid': False,
                'has_new_claims': False,
                'unsupported_claims': ['Expanded text is not actually longer than original'],
                'reason': 'Expansion failed - text is not longer'
            }

        try:
            # Use AI to detect new claims
            prompt = f"""Compare the original and expanded versions. Identify any NEW factual claims or achievements added in the expanded version that were not present or implied in the original.

Original: "{original_text}"
Expanded: "{expanded_text}"
{f'Source Data (to validate claims): "{source_data}"' if source_data else ''}

List any new claims added. For each new claim, indicate if it's:
- SUPPORTED: Clearly backed by the original text or source data
- INFERRED: Reasonable inference from original
- UNSUPPORTED: Not backed by any evidence (potential hallucination)

Respond in this format:
NEW_CLAIMS: [YES or NO]
CLAIM_1: [claim text] | [SUPPORTED/INFERRED/UNSUPPORTED]
CLAIM_2: [claim text] | [SUPPORTED/INFERRED/UNSUPPORTED]
...
OVERALL_VALIDITY: [VALID or INVALID]
REASONING: [brief explanation]"""

            response = self.model.generate_content(prompt)
            ai_analysis = response.text.strip()

            # Parse response
            has_new_claims = 'NEW_CLAIMS: YES' in ai_analysis

            unsupported_claims = []
            claim_pattern = re.compile(r'CLAIM_\d+:\s*(.+?)\s*\|\s*(SUPPORTED|INFERRED|UNSUPPORTED)', re.IGNORECASE)
            for match in claim_pattern.finditer(ai_analysis):
                claim_text, status = match.groups()
                if status.upper() == 'UNSUPPORTED':
                    unsupported_claims.append(claim_text.strip())

            validity_match = re.search(r'OVERALL_VALIDITY:\s*(VALID|INVALID)', ai_analysis, re.IGNORECASE)
            is_valid = validity_match.group(1).upper() == 'VALID' if validity_match else len(unsupported_claims) == 0

            reason = "Expansion is acceptable" if is_valid else f"Contains {len(unsupported_claims)} unsupported claim(s)"

            return {
                'is_valid': is_valid,
                'has_new_claims': has_new_claims,
                'unsupported_claims': unsupported_claims,
                'reason': reason,
                'ai_analysis': ai_analysis
            }

        except Exception as e:
            self.logger.error(f"Expansion validation failed: {e}")
            # Conservative fallback: flag for manual review
            return {
                'is_valid': False,
                'has_new_claims': True,
                'unsupported_claims': ['Could not validate - requires manual review'],
                'reason': f'Validation error: {str(e)}'
            }

    def validate_cross_section_consistency(
        self,
        resume_sections: Dict[str, str],
        new_content: str,
        section_name: str
    ) -> Dict[str, any]:
        """
        Validate that new content is consistent with other resume sections.

        For example, if a project mentions "expert in React", verify the Skills
        section lists React. If a project uses technologies not in Skills, suggest adding them.

        Args:
            resume_sections: Dict mapping section names to content
            new_content: The new content being added
            section_name: Which section the new content belongs to

        Returns:
            Dict with:
                - is_consistent: Boolean
                - inconsistencies: List of detected inconsistencies
                - suggestions: List of suggested fixes
        """
        try:
            sections_text = "\n\n".join([f"{name}:\n{content}" for name, content in resume_sections.items()])

            prompt = f"""Analyze the new content for consistency with existing resume sections.

Existing Resume Sections:
{sections_text}

New Content (for {section_name} section):
"{new_content}"

Check for:
1. Skills/technologies mentioned in new content but missing from Skills section
2. Experience levels claimed that contradict other sections
3. Dates/timelines that don't align
4. Company/role names that are inconsistent

Respond in this format:
IS_CONSISTENT: [YES or NO]
INCONSISTENCY_1: [description]
INCONSISTENCY_2: [description]
...
SUGGESTION_1: [what to fix]
SUGGESTION_2: [what to fix]
...
"""

            response = self.model.generate_content(prompt)
            ai_analysis = response.text.strip()

            is_consistent = 'IS_CONSISTENT: YES' in ai_analysis

            inconsistencies = []
            inconsistency_pattern = re.compile(r'INCONSISTENCY_\d+:\s*(.+)', re.IGNORECASE)
            for match in inconsistency_pattern.finditer(ai_analysis):
                inconsistencies.append(match.group(1).strip())

            suggestions = []
            suggestion_pattern = re.compile(r'SUGGESTION_\d+:\s*(.+)', re.IGNORECASE)
            for match in suggestion_pattern.finditer(ai_analysis):
                suggestions.append(match.group(1).strip())

            return {
                'is_consistent': is_consistent,
                'inconsistencies': inconsistencies,
                'suggestions': suggestions,
                'ai_analysis': ai_analysis
            }

        except Exception as e:
            self.logger.error(f"Consistency validation failed: {e}")
            return {
                'is_consistent': True,  # Default to pass on error
                'inconsistencies': [],
                'suggestions': [],
                'error': str(e)
            }
