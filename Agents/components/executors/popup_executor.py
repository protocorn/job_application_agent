import logging
from typing import Dict, Any
from playwright.async_api import Page, Locator, Error

# Assuming these utilities are in the same project and can be imported
from .cmp_consent import CmpConsent
from .click_executor import ClickExecutor

logger = logging.getLogger(__name__)

class PopupExecutor:
    """Handles different types of popups with appropriate dismissal strategies."""

    def __init__(self, page: Page, action_recorder=None):
        self.page = page
        self.action_recorder = action_recorder
        self.cmp_consent = CmpConsent(page)
        self.click_executor = ClickExecutor(page, action_recorder) # Reuse robust click logic

    async def execute(self, popup_info: Dict[str, Any]) -> bool:
        """
        Executes a dismissal action for the given popup using a cascade of strategies.
        """
        popup_type = popup_info.get('type')
        container = popup_info.get('container')
        action_button = popup_info.get('action_button')

        if not popup_type or not container:
            logger.warning("Popup execution requires a 'type' and a 'container' element.")
            return False

        # --- Strategy 1: CMP API for Cookie Consents (Fastest & Most Reliable) ---
        if popup_type == 'cookie-consent':
            if await self.cmp_consent.accept_all():
                logger.info("✅ Cookie consent handled via CMP API.")
                # Record popup dismissal
                if self.action_recorder:
                    self.action_recorder.record_click("cmp-api", f"Dismissed {popup_type} via CMP API", success=True)
                return await self._wait_for_dismissal(container)
            logger.info("ℹ️ CMP API not found or failed, falling back to UI interaction.")

        # --- Strategy 2: Robust UI Click (Primary Fallback) ---
        if action_button:
            if await self.click_executor.execute(action_button, f"'{popup_type}' action button"):
                # Record popup dismissal (already recorded by click_executor)
                return await self._wait_for_dismissal(container)
        
        # --- Strategy 3: Press 'Escape' Key (Final Fallback) ---
        logger.warning(f"Click failed or no button found for '{popup_type}'. Trying 'Escape' key.")
        try:
            await self.page.keyboard.press('Escape')
            logger.info(f"✅ Pressed 'Escape' as a fallback for '{popup_type}'.")
            # Record escape key press
            if self.action_recorder:
                self.action_recorder.record_click("keyboard:Escape", f"Dismissed {popup_type} with Escape key", success=True)
            return await self._wait_for_dismissal(container)
        except Error as e:
            logger.error(f"❌ Pressing 'Escape' key also failed: {e}")
            # Record failure
            if self.action_recorder:
                self.action_recorder.record_failure("popup_dismissal", str(e), context={"popup_type": popup_type})
            return False

    async def _wait_for_dismissal(self, element: Locator) -> bool:
        """Waits for an element to become hidden, confirming its dismissal."""
        try:
            # This is a reliable, dynamic wait, far superior to a fixed timeout.
            await element.wait_for(state='hidden', timeout=3000)
            logger.info("Popup element successfully dismissed.")
            return True
        except Error:
            logger.warning("Popup element did not disappear after action.")
            return False