import re
from typing import Dict, List, Optional, Any
from playwright.async_api import Page
from loguru import logger

class AuthenticationPageDetector:
    """Detects authentication pages (signup/signin) and determines the appropriate action."""

    # --- Configuration Constants ---
    # Centralizing configuration makes the detector easier to tune.
    CONFIDENCE_THRESHOLD = 0.8

    AUTH_PATTERNS: Dict[str, Any] = {
        'signup': {
            'text_patterns': [
                r'sign up', r'signup', r'register', r'create account', r'join us',
                r'new account', r'get started', r'sign up now', r'create profile'
            ],
            'confidence': 0.3
        },
        'signin': {
            'text_patterns': [
                r'sign in', r'signin', r'login', r'log in', r'welcome back',
                r'returning user', r'already have an account'
            ],
            'confidence': 0.2
        },
        'password_field': {
            'selectors': ['input[type="password"]'],
            'confidence': 0.4  # High confidence for a password field
        },
        'otp_field': {
            'selectors': ['input[type="text"][placeholder*="code" i]', 'input[placeholder*="otp" i]'],
            'confidence': 0.5  # Very high confidence for an OTP field
        },
    }

    FORM_FIELD_SELECTORS: List[str] = [
        'input[type="text"]', 'input[type="email"]', 'input[type="tel"]',
        'input[type="password"]', 'input[type="number"]', 'input[type="url"]',
        'textarea', 'select'
    ]

    def __init__(self, page: Page):
        """Initializes the detector with a Playwright page object."""
        self.page = page

    async def detect(self) -> Optional[Dict[str, Any]]:
        """
        Detects if the current page is an authentication page and determines the action.
        
        Returns:
            A dictionary with detection details ('type', 'confidence', 'action', etc.)
            or None if not considered an authentication page.
        """
        logger.info("🔍 Analyzing page for authentication indicators...")
        
        scores = {'signup': 0.0, 'signin': 0.0}
        sensitive_fields = {}

        # 1. Check for high-confidence selectors (password, OTP)
        for field_type in ['password_field', 'otp_field']:
            config = self.AUTH_PATTERNS[field_type]
            is_present = await self._are_elements_visible(config['selectors'])
            sensitive_fields[field_type] = is_present
            if is_present:
                # A password/OTP field strongly implies both signin and signup possibilities
                scores['signup'] += config['confidence']
                scores['signin'] += config['confidence']

        # 2. Check for text-based indicators
        try:
            page_text = (await self.page.inner_text('body')).lower()
            for auth_type in ['signup', 'signin']:
                config = self.AUTH_PATTERNS[auth_type]
                # Combine patterns into a single regex for efficiency
                combined_pattern = re.compile("|".join(config['text_patterns']))
                if combined_pattern.search(page_text):
                    scores[auth_type] += config['confidence']
        except Exception as e:
            logger.warning(f"Could not read page text for auth detection: {e}")

        # 3. Determine auth type and decide if it meets the confidence threshold
        max_confidence = max(scores.values())

        if max_confidence < self.CONFIDENCE_THRESHOLD:
            logger.info("No authentication page detected (confidence below threshold).")
            return None

        auth_type = 'signup' if scores['signup'] > scores['signin'] else 'signin'
        
        # 4. Determine the recommended action based on field presence
        has_form_fields = await self._are_elements_visible(self.FORM_FIELD_SELECTORS)

        if sensitive_fields.get('password_field') or sensitive_fields.get('otp_field'):
            action = 'human_intervention'
            reason = f"Sensitive fields detected on {auth_type} page. Manual completion required."
        elif has_form_fields:
            action = 'fill_form'
            reason = f"{auth_type.capitalize()} page detected with form fields. Proceeding to fill."
        else:
            action = 'skip'
            reason = f"{auth_type.capitalize()} page detected, but no interactive form fields found. Skipping."
        
        result = {
            'type': auth_type,
            'confidence': max_confidence,
            'action': action,
            'reason': reason,
            'password_detected': sensitive_fields.get('password_field', False),
            'otp_detected': sensitive_fields.get('otp_field', False),
        }

        logger.info(f"🔐 Auth page detected: {auth_type.upper()} (Confidence: {max_confidence:.2f})")
        logger.info(f"Action: {action} - {reason}")

        return result

    async def _are_elements_visible(self, selectors: List[str]) -> bool:
        """A generic helper to check if any element matching the given selectors is visible."""
        for selector in selectors:
            try:
                # Efficiently check if any of the matched elements are visible
                visible_elements = self.page.locator(selector).and_(self.page.locator(':visible'))
                if await visible_elements.count() > 0:
                    return True
            except Exception as e:
                logger.debug(f"Error checking visibility for selector '{selector}': {e}")
        return False
