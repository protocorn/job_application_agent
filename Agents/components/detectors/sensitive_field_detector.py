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
    
    # Keywords that indicate account creation (passwords should be auto-generated)
    ACCOUNT_CREATION_KEYWORDS = [
        'create account',
        'create your account',
        'register',
        'sign up',
        'new account',
        'candidate home',
        'verify new password'
    ]

    def __init__(self, page: Page, skip_account_creation: bool = True):
        """
        Initialize the detector
        
        Args:
            page: Playwright page object
            skip_account_creation: If True, don't mark account creation passwords as sensitive
        """
        self.page = page
        self.skip_account_creation = skip_account_creation

    async def detect(self) -> List[Dict[str, Any]]:
        """
        Finds all visible, empty, sensitive fields on the page.

        Returns:
            A list of dictionaries, each representing a sensitive field found.
        """
        logger.info("🛡️ Detecting sensitive fields...")
        sensitive_fields_found = []
        
        # Check if this is an account creation page (skip password detection if so)
        if self.skip_account_creation:
            is_account_creation = await self._is_account_creation_page()
            if is_account_creation:
                logger.info("ℹ️ Account creation page detected - password fields will be auto-filled")
                # Still detect OTP and other sensitive fields, but not passwords
                return await self._detect_non_password_sensitive_fields()

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
    
    async def _is_account_creation_page(self) -> bool:
        """
        Check if the current page is an account creation page
        
        Returns:
            True if this is an account creation page, False otherwise
        """
        try:
            page_text = await self.page.text_content('body')
            if not page_text:
                return False
            
            page_text_lower = page_text.lower()
            
            # Check for account creation keywords
            for keyword in self.ACCOUNT_CREATION_KEYWORDS:
                if keyword in page_text_lower:
                    logger.debug(f"Found account creation keyword: {keyword}")
                    return True
            
            # Additional check: Look for password confirmation field
            confirm_password = await self.page.locator(
                'input[type="password"][data-automation-id*="verify"], '
                'input[type="password"][name*="confirm"], '
                'input[type="password"][id*="confirm"]'
            ).count()
            
            if confirm_password > 0:
                logger.debug("Found password confirmation field")
                return True
            
            return False
        
        except Exception as e:
            logger.debug(f"Error checking if account creation page: {e}")
            return False
    
    async def _detect_non_password_sensitive_fields(self) -> List[Dict[str, Any]]:
        """
        Detect only non-password sensitive fields (like OTP)
        Used for account creation pages where passwords are auto-filled
        
        Returns:
            List of sensitive field dicts (excluding password fields)
        """
        sensitive_fields_found = []
        
        # Only check text-like inputs for OTP and verification codes
        other_inputs = self.page.locator('input[type="text"]:visible, input[type="tel"]:visible, input:not([type]):visible')
        
        # OTP-specific keywords (not password-related)
        otp_regex = re.compile(
            r'(otp|one-time|verification code|mfa code)',
            re.IGNORECASE
        )
        
        for element in await other_inputs.all():
            label_text = await self._get_associated_text(element)
            if otp_regex.search(label_text):
                if await self._is_empty(element):
                    field_type = 'otp'
                    sensitive_fields_found.append(self._create_field_dict(element, field_type, label_text))
                    logger.warning(f"⚠️ Sensitive field detected: A '{field_type}' field with label '{label_text}'.")
        
        return sensitive_fields_found