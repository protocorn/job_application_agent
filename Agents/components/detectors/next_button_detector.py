import re
from typing import Optional, List
from playwright.async_api import Page, Frame, Locator, Error
from loguru import logger

from components.brains.gemini_button_brain import GeminiButtonBrain

class NextButtonDetector:
    """Detects the primary 'Next' or 'Continue' button on a multi-page form."""

    # --- Centralized Configuration ---
    # Patterns are anchored (^) to match the beginning of the text for accuracy.
    # The `|` character in the regex acts as an "OR".
    NEXT_PATTERNS_REGEX = re.compile(
        r'^(next|continue|save and continue|next step)$', 
        re.IGNORECASE
    )

    def __init__(self, page: Page | Frame):
        self.page = page
        self.ai_brain = GeminiButtonBrain()

    async def detect(self) -> Optional[Locator]:
        """
        Finds the most likely 'next step' button using patterns, with an AI fallback.

        Returns:
            The Locator for the next button if found, otherwise None.
        """
        logger.info("🕵️‍♂️ Searching for the 'Next' or 'Continue' button...")

        # --- Strategy 1: Pattern Matching (Fast and Reliable) ---
        try:
            # This single locator is more efficient than looping through each pattern.
            button = self.page.get_by_role("button", name=self.NEXT_PATTERNS_REGEX).first
            
            # Check if the found button is visible and enabled before returning it.
            if await button.is_visible(timeout=1500) and await button.is_enabled():
                button_text = await button.inner_text()
                logger.success(f"✅ Found enabled 'Next' button via pattern matching: '{button_text}'")
                return button
        except Error:
            logger.debug("No enabled button found matching the primary patterns.")

        # --- Strategy 2: AI Fallback (For non-standard buttons) ---
        logger.warning("Pattern matching failed. Attempting AI fallback.")
        ai_button = await self._find_button_with_ai()
        if ai_button:
            return ai_button

        logger.error("❌ No actionable 'Next' or 'Continue' button found by any method.")
        return None

    async def _find_button_with_ai(self) -> Optional[Locator]:
        """Uses the GeminiButtonBrain to find the button as a fallback."""
        try:
            page_content = await self.page.content()
            context = "Find the primary button to proceed to the next step in a multi-page application form. It might be labeled 'Next', 'Continue', 'Proceed', or something similar."
            
            ai_result = await self.ai_brain.find_next_button(page_content, context)
            
            if not (ai_result and ai_result.get('found')):
                logger.info("AI analysis did not find a suitable 'Next' button.")
                return None

            selector = ai_result.get('selector')
            if not selector:
                logger.warning("AI found a button but did not provide a CSS selector.")
                return None

            element = self.page.locator(selector).first
            if await element.is_visible() and await element.is_enabled():
                logger.info(f"🧠 AI successfully located an enabled button: '{ai_result.get('text', 'Unknown')}'")
                return element
            else:
                logger.warning(f"AI-suggested element is not visible or is disabled: '{selector}'")
                return None
        except Exception as e:
            logger.error(f"AI fallback process failed with an exception: {e}")
            return None