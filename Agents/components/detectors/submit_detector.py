import re
from typing import Optional
from playwright.async_api import Page, Frame, Locator, Error
from loguru import logger

from components.brains.gemini_button_brain import GeminiButtonBrain

class SubmitDetector:
    """Detects the final 'Submit Application' button on a form."""

    # --- Centralized Configuration ---
    # A single, compiled regex is more efficient than a list of patterns.
    # The `$` anchor ensures it matches the end of the button text.
    SUBMIT_PATTERNS_REGEX = re.compile(
        r'^(submit application|submit|finish|complete application|i agree and submit)$', 
        re.IGNORECASE
    )

    def __init__(self, page: Page | Frame):
        self.page = page
        self.ai_brain = GeminiButtonBrain()

    async def detect(self) -> Optional[Locator]:
        """
        Finds the final submit button using patterns, with an AI fallback.

        Returns:
            The Locator for the submit button if found, otherwise None.
        """
        logger.info("🕵️‍♂️ Searching for the final 'Submit' button...")

        # --- Strategy 1: Pattern Matching (Fast and Reliable) ---
        try:
            # This single locator efficiently checks all patterns at once.
            button = self.page.get_by_role("button", name=self.SUBMIT_PATTERNS_REGEX).first

            if await button.is_visible(timeout=1500) and await button.is_enabled():
                button_text = await button.inner_text()
                logger.success(f"✅ Found enabled 'Submit' button via pattern: '{button_text}'")
                return button
        except Error:
            logger.debug("No enabled button found matching the primary submit patterns.")

        # --- Strategy 1.5: Greenhouse-specific patterns ---
        try:
            greenhouse_selectors = [
                'button[data-action="submit"]',
                'button#submit_app',
                'input[type="submit"][id*="submit" i]',
                'input[type="submit"][value*="Submit" i]',
                'button.button--submit:has-text("Submit")',
            ]

            for selector in greenhouse_selectors:
                try:
                    button = self.page.locator(selector).first
                    if await button.is_visible(timeout=500) and await button.is_enabled():
                        logger.success(f"✅ Found 'Submit' button via Greenhouse pattern: {selector}")
                        return button
                except:
                    continue
        except Exception as e:
            logger.debug(f"Greenhouse submit pattern matching failed: {e}")

        # --- Strategy 2: AI Fallback (For non-standard buttons) ---
        logger.warning("Pattern matching failed. Attempting AI fallback.")
        ai_button = await self._find_button_with_ai()
        if ai_button:
            return ai_button

        logger.error("❌ No actionable 'Submit' button found by any method.")
        return None

    async def _find_button_with_ai(self) -> Optional[Locator]:
        """Uses the GeminiButtonBrain to find the button as a fallback."""
        try:
            page_content = await self.page.content()
            context = "Find the final button to submit and complete the entire job application form. It is likely labeled 'Submit', 'Finish Application', or similar."
            
            ai_result = await self.ai_brain.find_submit_button(page_content, context)
            
            if not (ai_result and ai_result.get('found')):
                logger.info("AI analysis did not find a suitable 'Submit' button.")
                return None

            selector = ai_result.get('selector')
            if not selector:
                logger.warning("AI found a button but did not provide a CSS selector.")
                return None

            element = self.page.locator(selector).first
            if await element.is_visible() and await element.is_enabled():
                logger.info(f"🧠 AI successfully located an enabled submit button: '{ai_result.get('text', 'Unknown')}'")
                return element
            else:
                logger.warning(f"AI-suggested submit button is not visible or is disabled: '{selector}'")
                return None
        except Exception as e:
            logger.error(f"AI fallback process failed with an exception: {e}")
            return None