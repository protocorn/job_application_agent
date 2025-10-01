import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

class NavValidator:
    """Validates if a navigation or significant DOM change occurred."""

    def __init__(self, page: Any):
        self.page = page
        self.initial_state = {}
        self.initial_pages_count = 0

    async def capture_initial_state(self):
        """Captures the initial state of the page before an action."""
        self.initial_state = {
            'url': self.page.url,
            'title': await self.page.title(),
            'dom_structure': await self._get_dom_structure_summary()
        }
        # Capture the initial number of pages in the browser context
        try:
            self.initial_pages_count = len(self.page.context.pages)
            logger.debug(f"Initial pages count: {self.initial_pages_count}")
        except Exception as e:
            logger.warning(f"Failed to capture initial pages count: {e}")
            self.initial_pages_count = 1

    async def validate(self) -> bool:
        """Validates if a significant change has occurred since the initial state."""
        # Check for new tabs/windows first (highest priority)
        new_page = await self.detect_new_tab()
        if new_page:
            logger.info("✅ Validation successful: New tab/window detected.")
            return True
        
        # Frames may not have distinct URLs, so only check URL on Page
        try:
            current_url = getattr(self.page, 'url', None)
            if current_url and current_url != self.initial_state.get('url'):
                logger.info(f"✅ Validation successful: URL changed to {current_url}")
                return True
        except Exception:
            pass
        
        # Title is only available on Page, not Frame
        try:
            current_title = await self.page.title()
            if current_title != self.initial_state.get('title'):
                logger.info(f"✅ Validation successful: Title changed to {current_title}")
                return True
        except Exception:
            pass

        current_dom_structure = await self._get_dom_structure_summary()
        if current_dom_structure != self.initial_state.get('dom_structure'):
            logger.info("✅ Validation successful: DOM structure changed.")
            return True

        logger.warning("⚠️ Validation failed: No significant navigation or DOM change detected.")
        return False

    async def detect_new_tab(self) -> Optional[Any]:
        """
        Detects if a new tab/window was opened as a consequence of an action.
        
        Returns:
            The new page object if a new tab was detected, None otherwise.
        """
        try:
            current_pages = self.page.context.pages
            current_pages_count = len(current_pages)
            
            logger.debug(f"Current pages count: {current_pages_count}, Initial: {self.initial_pages_count}")
            
            if current_pages_count > self.initial_pages_count:
                # Find the newest page (likely the one that was just opened)
                newest_page = current_pages[-1]  # Last page in the list is usually the newest
                
                # Wait a bit for the new page to load
                try:
                    await newest_page.wait_for_load_state("domcontentloaded", timeout=10000)
                    logger.info(f"🆕 New tab detected with URL: {newest_page.url}")
                    return newest_page
                except Exception as load_error:
                    logger.warning(f"New tab detected but failed to load: {load_error}")
                    return newest_page  # Return it anyway, might still be usable
            
            return None
            
        except Exception as e:
            logger.error(f"Failed to detect new tabs: {e}")
            return None

    async def _get_dom_structure_summary(self) -> Dict[str, int]:
        """Gets a summary of the DOM structure for comparison."""
        return await self.page.evaluate("""() => ({
            inputs: document.querySelectorAll('input, textarea, select').length,
            buttons: document.querySelectorAll('button').length,
            links: document.querySelectorAll('a').length,
            h1s: document.querySelectorAll('h1').length,
        })""")
