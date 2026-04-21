import logging
import re
from typing import Optional, Union
from playwright.async_api import Page, Frame, Locator, Error

logger = logging.getLogger(__name__)

class ClickExecutor:
    """Performs a click action on a Playwright Locator with robust fallbacks.

    Click strategy order:
      1. Standard Playwright click (safest, respects actionability checks)
      2. JavaScript dispatchEvent click (bypasses pointer-event overlays)
      3. Force click (bypasses all actionability checks)
      4. Find-by-visible-text and click (when original locator is stale/unresolvable)
      5. Find-by-ARIA role + accessible name (last DOM-based resort)
    """

    def __init__(self, page: Union[Page, Frame], action_recorder=None):
        """Initializes the executor with the browsing context (Page or Frame)."""
        self.page = page
        self.action_recorder = action_recorder

    async def execute(
        self,
        element: Optional[Locator],
        action_description: str = "element",
        *,
        hint_text: Optional[str] = None,
        hint_role: Optional[str] = None,
    ) -> bool:
        """
        Click the given element using up to 5 progressive strategies.

        Args:
            element:            The Playwright Locator to click. Can be None.
            action_description: Human-readable label used in log messages.
            hint_text:          Visible text of the element (used for strategies 4-5 fallbacks).
            hint_role:          ARIA role (e.g. "button", "link") for strategy 5.

        Returns:
            True if any strategy succeeded, False if all failed.
        """
        if not element:
            logger.warning("Attempted to click a null element.")
            return False

        selector = ""
        try:
            selector = await self._get_element_selector(element)
        except Exception:
            pass

        # ── Strategy 1: Standard Playwright click ───────────────────────────
        try:
            await element.click(timeout=5000)
            logger.info(f"✅ [S1-standard] Clicked '{action_description}'.")
            self._record_click(selector, action_description, success=True)
            return True
        except Error as e:
            logger.warning(f"⚠️ [S1-standard] '{action_description}' failed: {e!s:.120}")

        # ── Strategy 2: JavaScript dispatchEvent click ───────────────────────
        try:
            await element.dispatch_event('click')
            logger.info(f"✅ [S2-js-event] Clicked '{action_description}'.")
            self._record_click(selector, action_description, success=True)
            return True
        except Error as e:
            logger.warning(f"⚠️ [S2-js-event] '{action_description}' failed: {e!s:.120}")

        # ── Strategy 3: Force click ──────────────────────────────────────────
        try:
            await element.click(force=True, timeout=5000)
            logger.info(f"✅ [S3-force] Clicked '{action_description}'.")
            self._record_click(selector, action_description, success=True)
            return True
        except Error as e:
            logger.warning(f"⚠️ [S3-force] '{action_description}' failed: {e!s:.120}")

        # ── Strategy 4: Find by visible text ────────────────────────────────
        text_to_try = hint_text or self._extract_text_from_description(action_description)
        if text_to_try:
            found = await self._find_by_text(text_to_try)
            if found:
                try:
                    await found.click(timeout=4000)
                    logger.info(f"✅ [S4-text-search] Clicked '{action_description}' via text='{text_to_try}'.")
                    self._record_click(selector, action_description, success=True)
                    return True
                except Error as e:
                    logger.warning(f"⚠️ [S4-text-search] Click on text='{text_to_try}' failed: {e!s:.120}")
            else:
                logger.warning(f"⚠️ [S4-text-search] No visible element found with text='{text_to_try}'")

        # ── Strategy 5: Find by ARIA role + accessible name ──────────────────
        role = hint_role or "button"
        name_hint = hint_text or self._extract_text_from_description(action_description)
        if name_hint:
            found = await self._find_by_role(role, name_hint)
            if found:
                try:
                    await found.click(timeout=4000)
                    logger.info(f"✅ [S5-aria-role] Clicked '{action_description}' via role={role} name='{name_hint}'.")
                    self._record_click(selector, action_description, success=True)
                    return True
                except Error as e:
                    logger.warning(f"⚠️ [S5-aria-role] Failed: {e!s:.120}")

        logger.error(f"❌ All 5 click strategies failed for '{action_description}'.")
        self._record_click(selector, action_description, success=False, error="all_strategies_failed")
        return False

    async def _find_by_text(self, text: str) -> Optional[Locator]:
        """Return the first visible clickable element whose text matches."""
        selectors = [
            f'button:has-text("{text}")',
            f'a:has-text("{text}")',
            f'[role="button"]:has-text("{text}")',
            f'input[type="submit"][value*="{text}" i]',
            f'input[type="button"][value*="{text}" i]',
            f':text-is("{text}")',
        ]
        for sel in selectors:
            try:
                loc = self.page.locator(sel).first
                if await loc.count() > 0:
                    return loc
            except Exception:
                continue
        return None

    async def _find_by_role(self, role: str, name: str) -> Optional[Locator]:
        """Return the first visible element matching ARIA role and accessible name."""
        try:
            loc = self.page.get_by_role(role, name=name).first
            if await loc.count() > 0:
                return loc
        except Exception:
            pass
        # Partial-name fallback
        try:
            loc = self.page.get_by_role(role).filter(has_text=name).first
            if await loc.count() > 0:
                return loc
        except Exception:
            pass
        return None

    def _extract_text_from_description(self, description: str) -> str:
        """Pull a short usable label from the action description string."""
        # Remove common prefixes like "Apply button", "Submit button", etc.
        cleaned = re.sub(r'\b(button|link|element|field|input)\b', '', description, flags=re.IGNORECASE).strip()
        return cleaned[:60] if cleaned else description[:60]

    def _record_click(self, selector: str, description: str, success: bool, error: str = "") -> None:
        if self.action_recorder:
            try:
                self.action_recorder.record_click(selector, description, success=success,
                                                  **({"error": error} if error else {}))
            except Exception:
                pass

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