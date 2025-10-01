import re
from typing import Dict, List, Optional, Any
from playwright.async_api import Page, Locator, Error
from loguru import logger

class ApplicationFormDetector:
    """Detects if a page is a job application form based on a confidence score."""

    # --- Centralized Configuration ---
    # This structure is clean and easy to update.
    CONFIDENCE_THRESHOLD = 0.6  # Slightly increased for higher certainty
    FORM_INDICATORS: Dict[str, Any] = {
        'url': {
            'patterns': [r'/apply', r'/application', r'/candidate', r'/job-application'],
            'weight': 0.3
        },
        'page_text': {
            'patterns': [
                r'job application', r'application form', r'apply for this position',
                r'candidate information', r'submit your application'
            ],
            'weight': 0.4
        },
        'field_labels': {
            'patterns': [
                r'first name', r'last name', r'email', r'phone', r'resume', r'cv',
                r'linkedin', r'cover letter', r'work authorization'
            ],
            'weight': 0.1, # Each match contributes a small amount
        },
        'navigation': {
            'patterns': [r'step \d+ of \d+', r'application progress', r'next step', r'continue to next'],
            'weight': 0.3
        },
        'structure': {
            'min_fields': 4, # A form with fewer than 4 fields is less likely to be the main application
            'weight': 0.2
        }
    }

    def __init__(self, page: Page):
        self.page = page

    async def detect(self) -> Optional[Dict[str, Any]]:
        """
        Analyzes the current page to determine if it's a job application form.

        Returns:
            A dictionary with detection results, or None if confidence is below the threshold."""
        logger.info("🔍 Analyzing page for application form indicators...")
        
        # --- Step 1: Gather all necessary page data in one go for efficiency ---
        try:
            page_url = self.page.url.lower()
            body_text = await self.page.locator('body').inner_text(timeout=5000)
            form_elements = await self.page.locator('input:visible, select:visible, textarea:visible').all()
        except Error as e:
            logger.error(f"Could not gather page data for analysis: {e}")
            return None

        # --- Step 2: Calculate confidence score from various indicators ---
        confidence_score = 0.0
        detection_reasons = []

        # Indicator: URL
        if self._check_patterns(page_url, self.FORM_INDICATORS['url']['patterns']):
            score = self.FORM_INDICATORS['url']['weight']
            confidence_score += score
            detection_reasons.append(f"URL matched (score: +{score})")

        # Indicator: Page Text
        if self._check_patterns(body_text, self.FORM_INDICATORS['page_text']['patterns']):
            score = self.FORM_INDICATORS['page_text']['weight']
            confidence_score += score
            detection_reasons.append(f"Page text matched (score: +{score})")

        # Indicator: Form Field Labels
        field_match_score = await self._calculate_field_score(form_elements)
        if field_match_score > 0:
            confidence_score += field_match_score
            detection_reasons.append(f"Found typical application fields (total score: +{field_match_score:.2f})")

        # Indicator: Navigation Text (e.g., "Step 2 of 4")
        if self._check_patterns(body_text, self.FORM_INDICATORS['navigation']['patterns']):
            score = self.FORM_INDICATORS['navigation']['weight']
            confidence_score += score
            detection_reasons.append(f"Multi-step navigation detected (score: +{score})")
            
        # Indicator: Form Structure (number of fields)
        if len(form_elements) >= self.FORM_INDICATORS['structure']['min_fields']:
            score = self.FORM_INDICATORS['structure']['weight']
            confidence_score += score
            detection_reasons.append(f"{len(form_elements)} fields found (score: +{score})")

        # --- Step 3: Make final decision based on the confidence threshold ---
        if confidence_score >= self.CONFIDENCE_THRESHOLD:
            logger.success(f"✅ Application form detected with confidence {confidence_score:.2f}")
            logger.info(f"Reasons: {', '.join(detection_reasons)}")
            return {
                'is_application_form': True,
                'confidence': confidence_score,
                'reasons': detection_reasons
            }
        else:
            logger.info(f"❌ Not an application form (confidence: {confidence_score:.2f} < {self.CONFIDENCE_THRESHOLD})")
            return None

    def _check_patterns(self, text_to_search: str, patterns: List[str]) -> bool:
        """A generic helper to check if any pattern exists in the given text."""
        text_lower = text_to_search.lower()
        # Using 'any' is an efficient way to stop searching after the first match.
        return any(re.search(pattern, text_lower) for pattern in patterns)

    async def _calculate_field_score(self, elements: List[Locator]) -> float:
        """Calculates a confidence score based on the labels of visible form fields."""
        total_score = 0.0
        patterns = self.FORM_INDICATORS['field_labels']['patterns']
        weight_per_match = self.FORM_INDICATORS['field_labels']['weight']

        # We can check a limited number of fields for speed
        for element in elements[:15]: 
            try:
                # Reuse the robust label finding logic from FieldInteractor
                label = await self._get_field_label(element)
                if self._check_patterns(label, patterns):
                    total_score += weight_per_match
            except Error:
                continue
        # Cap the maximum score from this indicator to prevent it from dominating
        return min(total_score, 0.5)

    async def _get_field_label(self, element: Locator) -> str:
        """Gets the most likely human-readable label for a form element."""
        # This is a simplified version of the label logic for quick checks.
        element_id = await element.get_attribute('id')
        if element_id:
            label = self.page.locator(f'label[for="{element_id}"]').first
            if await label.count() > 0: return await label.inner_text()
        
        aria_label = await element.get_attribute('aria-label')
        if aria_label: return aria_label
        
        placeholder = await element.get_attribute('placeholder')
        if placeholder: return placeholder
        
        return ""