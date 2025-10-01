import re
from typing import List, Dict, Any, Optional
from playwright.async_api import Page, Locator, Error
from loguru import logger

class SensitiveFieldDetector:
    """Detects sensitive, empty input fields that require human intervention."""

    # --- Centralized Configuration ---
    # A single, compiled regex for all keywords is more efficient for searching.
    SENSITIVE_KEYWORDS_REGEX = re.compile(
        r'(password|passwort|contraseña|otp|one-time|verification code|mfa code|confirm password|re-enter password)',
        re.IGNORECASE
    )

    def __init__(self, page: Page):
        self.page = page

    async def detect(self) -> List[Dict[str, Any]]:
        """
        Finds all visible, empty, sensitive fields on the page.

        Returns:
            A list of dictionaries, each representing a sensitive field found.
        """
        logger.info("🛡️ Detecting sensitive fields...")
        sensitive_fields_found = []

        # --- Strategy 1: Direct Hit (Fastest and Most Reliable) ---
        # Directly find all visible inputs with type="password". This is the most common case.
        password_inputs = self.page.locator('input[type="password"]:visible')
        for element in await password_inputs.all():
            if await self._is_empty(element):
                sensitive_fields_found.append(self._create_field_dict(element, 'password'))
                logger.warning(" sensitive field detected: An input with type='password'.")


        # --- Strategy 2: Pattern Matching for other inputs (e.g., OTP fields) ---
        # Find all text-like inputs that are NOT password fields (we already have those).
        other_inputs = self.page.locator('input[type="text"]:visible, input[type="tel"]:visible, input:not([type]):visible')
        
        for element in await other_inputs.all():
            # Check if the element has already been identified
            if any(e['element'] == element for e in sensitive_fields_found):
                continue

            label_text = await self._get_associated_text(element)
            if self.SENSITIVE_KEYWORDS_REGEX.search(label_text):
                 if await self._is_empty(element):
                    field_type = self._determine_field_type(label_text)
                    sensitive_fields_found.append(self._create_field_dict(element, field_type, label_text))
                    logger.warning(f" sensitive field detected: A '{field_type}' field with label '{label_text}'.")

        if sensitive_fields_found:
            logger.warning(f"🚨 Found {len(sensitive_fields_found)} sensitive field(s) requiring human intervention.")
        
        return sensitive_fields_found

    async def _is_empty(self, element: Locator) -> bool:
        """Checks if an input element is empty."""
        try:
            return await element.input_value() == ""
        except Error:
            return True # Assume empty if value cannot be read

    async def _get_associated_text(self, element: Locator) -> str:
        """Gathers all text associated with an input element for context."""
        texts = []
        try:
            # 1. Associated <label>
            element_id = await element.get_attribute('id')
            if element_id:
                label = self.page.locator(f'label[for="{element_id}"]').first
                if await label.is_visible(timeout=50):
                    texts.append(await label.inner_text())

            # 2. Key attributes
            for attr in ['aria-label', 'placeholder', 'name', 'id']:
                attr_value = await element.get_attribute(attr)
                if attr_value:
                    texts.append(attr_value)
        except Error:
            pass # Ignore errors on stale elements
        
        return " ".join(texts)
    
    def _determine_field_type(self, label_text: str) -> str:
        """Determines the specific type of sensitive field from its label."""
        text_lower = label_text.lower()
        if any(keyword in text_lower for keyword in ['otp', 'one-time', 'verification', 'mfa']):
            return 'otp'
        if 'confirm' in text_lower or 're-enter' in text_lower:
            return 'confirm_password'
        return 'password' # Default to password

    def _create_field_dict(self, element: Locator, field_type: str, label: Optional[str] = None) -> Dict[str, Any]:
        """Creates a standardized dictionary for a found sensitive field."""
        return {
            'type': field_type,
            'element': element,
            'label': label or f"Input with type='{field_type}'"
        }