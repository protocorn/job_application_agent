import logging
from typing import Any, Dict, List, Optional
from playwright.async_api import Page, Locator, Error

logger = logging.getLogger(__name__)

class PopupDetector:
    """Detects and prioritizes popups on a page for handling."""

    # Configuration is a class constant for better organization.
    # Selectors are split into finding the popup 'container' and the 'action' button.
    POPUP_PATTERNS: Dict[str, Any] = {
        "cookie-consent": {
            "container_selectors": [
                '[id*="cookie"]', '[class*="consent"]', '[id*="gdpr"]',
                '[data-testid*="cookie-banner"]'
            ],
            "action_selectors": [
                'button:has-text("Accept all")', 'button:has-text("Allow All")',
                'button:has-text("Accept")', 'button:has-text("Allow")', 
                'button:has-text("I agree")', 'button:has-text("Got it")'
            ],
            "context_terms": ['cookie', 'consent', 'privacy', 'gdpr'],
            "priority": 1  # Highest priority
        },
        "newsletter-popup": {
            "container_selectors": [
                '[id*="newsletter"]', '[class*="subscribe"]', 'form[id*="signup"]'
            ],
            "action_selectors": [
                '[aria-label*="close" i]', 'button:has-text("Close")',
                'button:has-text("No thanks")', '[class*="popup-close"]'
            ],
            "context_terms": ['newsletter', 'subscribe', 'offer', 'discount', 'email'],
            "priority": 2
        },
        "modal-popup": {
            "container_selectors": [
                '[role="dialog"]', '[role="modal"]', '[aria-modal="true"]'
            ],
            "action_selectors": [
                '[aria-label*="close" i]', 'button:has-text("Close")'
            ],
            "context_terms": ['popup', 'dialog', 'alert'],
            "priority": 3  # Lowest priority
        }
    }

    def __init__(self, page: Page):
        self.page = page

    async def detect(self) -> Optional[Dict[str, Locator]]:
        """
        Detects the highest-priority popup that is currently visible.

        It searches for popups in order of priority and returns the first valid one,
        including both the popup container and the specific action button to click.

        Returns:
            A dictionary with 'type', 'container', and 'action_button' locators, or None.
        """
        # Sort patterns by priority to check the most important ones first.
        sorted_patterns = sorted(
            self.POPUP_PATTERNS.items(), key=lambda item: item[1]['priority']
        )

        for popup_type, config in sorted_patterns:
            # Efficiently find the first visible container matching this popup type.
            container_selector = ", ".join(config['container_selectors'])
            container = self.page.locator(container_selector).and_(self.page.locator(':visible')).first
            
            try:
                # Use a short timeout to see if a container exists on the page.
                await container.wait_for(state='visible', timeout=1000)
                
                # After finding a container, confirm it has the right context.
                if not await self._has_context(container, config['context_terms']):
                    continue  # Context doesn't match, check the next popup type.

                # Now, find the actionable button *within* the confirmed container.
                action_selector = ", ".join(config['action_selectors'])
                action_button = container.locator(action_selector).and_(self.page.locator(':visible')).first
                
                if await action_button.is_visible():
                    logger.info(f"✅ Detected '{popup_type}' popup.")
                    return {
                        'type': popup_type,
                        'container': container,
                        'action_button': action_button
                    }
            except Error:
                # No visible container found for this type, so we continue to the next.
                continue
        
        return None

    async def _has_context(self, element: Locator, terms: List[str]) -> bool:
        """Checks if the element's own text content contains any of the context terms."""
        try:
            # This JS function is efficient as it runs entirely in the browser.
            return bool(await element.evaluate("""(el, terms) => {
                const content = (el.textContent || "").toLowerCase();
                return terms.some(term => content.includes(term));
            }""", terms))
        except Error:
            return False