import logging
from typing import Optional
from playwright.async_api import Page, Frame, Locator, Error

logger = logging.getLogger(__name__)

class ClickExecutor:
    """Performs a click action on a Playwright Locator with robust fallbacks."""

    def __init__(self, page: Page | Frame, action_recorder=None):
        """Initializes the executor with the browsing context (Page or Frame)."""
        self.page = page
        self.action_recorder = action_recorder

    async def execute(self, element: Optional[Locator], action_description: str = "element") -> bool:
        """
        Attempts to click the given element using a sequence of strategies.

        This method avoids hardcoded waits like `wait_for_timeout`. The calling code
        should handle waiting for the specific outcome of the click (e.g., a new page
        to load or a new element to appear), which is a more reliable practice.

        Args:
            element: The Playwright Locator to click. Can be None.
            action_description: A human-readable name for the element for clearer logging.

        Returns:
            True if any click strategy was successful, False otherwise.
        """
        if not element:
            logger.warning("Attempted to click a null element.")
            return False

        # Try to get selector for action recording
        selector = ""
        try:
            selector = await self._get_element_selector(element)
        except Exception:
            pass

        # Strategy 1: Standard Click (Safest)
        try:
            await element.click(timeout=5000)
            logger.info(f"✅ Successfully clicked '{action_description}'.")
            # Record successful click
            if self.action_recorder:
                self.action_recorder.record_click(selector, action_description, success=True)
            return True
        except Error as e:
            logger.warning(f"⚠️ Standard click on '{action_description}' failed. Trying JS click. Error: {e}")

        # Strategy 2: JavaScript Click (Bypasses overlays)
        try:
            await element.dispatch_event('click')
            logger.info(f"✅ Successfully clicked '{action_description}' using JavaScript.")
            # Record successful click
            if self.action_recorder:
                self.action_recorder.record_click(selector, action_description, success=True)
            return True
        except Error as e:
            logger.warning(f"⚠️ JS click on '{action_description}' failed. Trying force click. Error: {e}")

        # Strategy 3: Force Click (Last Resort)
        try:
            await element.click(force=True, timeout=5000)
            logger.info(f"✅ Successfully clicked '{action_description}' using force.")
            # Record successful click
            if self.action_recorder:
                self.action_recorder.record_click(selector, action_description, success=True)
            return True
        except Error as e:
            logger.error(f"❌ All click strategies for '{action_description}' failed. Final error: {e}")
            # Record failed click
            if self.action_recorder:
                self.action_recorder.record_click(selector, action_description, success=False, error=str(e))
            return False

    async def _get_element_selector(self, element: Locator) -> str:
        """Try to get a useful selector for the element for action recording"""
        try:
            # Try to get id first
            element_id = await element.get_attribute('id')
            if element_id:
                return f"id:{element_id}"

            # Try to get name
            name = await element.get_attribute('name')
            if name:
                return f"name:{name}"

            # Try to get data-automation-id (common in job sites)
            automation_id = await element.get_attribute('data-automation-id')
            if automation_id:
                return f"[data-automation-id='{automation_id}']"

            # Fallback to basic tag with class
            tag_name = await element.evaluate('el => el.tagName.toLowerCase()')
            class_name = await element.get_attribute('class')
            if class_name:
                # Take first class for simplicity
                first_class = class_name.split()[0] if class_name else ""
                return f"{tag_name}.{first_class}" if first_class else tag_name

            return tag_name or "unknown"

        except Exception:
            return "unknown"