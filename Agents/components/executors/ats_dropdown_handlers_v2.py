"""
Enhanced V2 Dropdown Handlers with Iframe Support.
Handles Greenhouse, Workday, Lever, and others with correct frame context.
"""
import asyncio
from typing import Any, Dict, List, Optional
from playwright.async_api import Locator, Page, Frame
from loguru import logger

from components.exceptions.field_exceptions import (
    DropdownInteractionError,
    TimeoutExceededError
)

class ATSDropdownHandlerV2:
    """
    Unified V2 Handler for ATS Dropdowns.
    Detects ATS type dynamically and applies specialized strategies.
    CRITICAL: Uses frame-aware selectors to support iframes (Greenhouse).
    """

    def __init__(self):
        pass

    async def fill(
        self,
        element: Locator,
        value: str,
        field_label: str,
        profile_context: Optional[Dict[str, Any]] = None
    ) -> bool:
        """
        Fill a dropdown using the best strategy.
        Returns True if successful, False if should fallback to AI.
        """
        try:
            # 1. Detect ATS/Dropdown Type
            ats_type = await self._detect_type(element)
            logger.debug(f"🔍 Detected dropdown type for '{field_label}': {ats_type}")

            # 2. Dispatch to strategy
            if ats_type == 'greenhouse':
                return await self._fill_greenhouse(element, value, field_label)
            elif ats_type == 'workday':
                return await self._fill_workday(element, value, field_label)
            elif ats_type == 'lever':
                return await self._fill_lever(element, value, field_label)
            else:
                return await self._fill_generic(element, value, field_label)

        except Exception as e:
            logger.warning(f"Dropdown fill failed for '{field_label}': {e}")
            return False

    async def _detect_type(self, element: Locator) -> str:
        """Detect the type of dropdown."""
        try:
            # Check for Greenhouse (combobox with aria-haspopup)
            role = await element.get_attribute('role')
            aria_popup = await element.get_attribute('aria-haspopup')
            if role == 'combobox' and aria_popup == 'true':
                return 'greenhouse'
            
            # Check for Workday
            automation_id = await element.get_attribute('data-automation-id')
            if automation_id and 'dropdown' in automation_id:
                return 'workday'
                
            # Check for Lever
            class_attr = await element.get_attribute('class') or ''
            if 'lever' in class_attr.lower() or 'application-field' in class_attr.lower():
                return 'lever'
                
            return 'generic'
        except Exception:
            return 'generic'

    async def _get_frame_root(self, element: Locator) -> Locator:
        """
        Get the root (body) of the frame containing this element.
        This is critical for finding options in iframes.
        """
        # XPath / resolves to the document root of the current frame
        return element.locator('xpath=/html/body')

    async def _fill_greenhouse(self, element: Locator, value: str, field_label: str) -> bool:
        """
        Fast Greenhouse strategy:
        1. Open
        2. Type
        3. Check visible options (FRAME AWARE)
        4. Select
        """
        logger.debug(f"⚡ Fast fill: '{field_label}'")

        try:
            # Focus and Open
            await element.focus(timeout=2000)
            await element.press('ArrowDown')
            await asyncio.sleep(0.2)

            # Clear and Type
            # Try to select all text first to overwrite
            await element.press('Control+a') 
            await element.press('Backspace')
            
            logger.debug(f"  Typing: '{value}'")
            await element.type(value, delay=50)
            await asyncio.sleep(0.5) # Wait for filter

            # Find options - FRAME AWARE
            # We look for [role="option"] inside the frame's body
            frame_root = await self._get_frame_root(element)
            options_locator = frame_root.locator('[role="option"]:visible')
            
            count = await options_locator.count()
            
            if count == 0:
                logger.warning(f"⚠️ No options appeared after typing - trying full option extraction")
                # Fallback: try opening full list
                await self._extract_all_options(element, frame_root) # Just to log/debug
                return False

            # Check matches
            logger.debug(f"  Found {count} visible options")
            for i in range(min(count, 5)):
                opt = options_locator.nth(i)
                text = await opt.text_content()
                if text and value.lower() in text.lower():
                    logger.info(f"✅ Exact/Partial match found: '{text.strip()}'")
                    await opt.click()
                    await asyncio.sleep(0.2)
                    return True

            # If no text match but options exist, select first one? 
            # Riskier, but 'Enter' usually selects first filtered option
            await element.press('Enter')
            await asyncio.sleep(0.2)
            
            # Verify
            final_val = await element.input_value()
            if final_val and final_val.strip():
                 logger.info(f"✅ Selected via Enter: '{final_val}'")
                 return True

            return False

        except Exception as e:
            logger.error(f"Greenhouse fill error: {e}")
            return False

    async def _fill_workday(self, element: Locator, value: str, field_label: str) -> bool:
        """Workday specific strategy."""
        try:
            await element.click()
            await asyncio.sleep(0.5)
            
            frame_root = await self._get_frame_root(element)
            # Workday options are usually in a container with role="listbox" or similar
            # Look for options containing text
            option = frame_root.locator(f'[role="option"]:has-text("{value}")').first
            
            if await option.is_visible(timeout=3000):
                await option.click()
                return True
                
            # Fallback: Type in search box if available
            search_box = frame_root.locator('input[type="text"]').first
            if await search_box.is_visible():
                await search_box.fill(value)
                await asyncio.sleep(1)
                await option.click()
                return True
                
            return False
        except Exception:
            return False

    async def _fill_lever(self, element: Locator, value: str, field_label: str) -> bool:
        """Lever strategy (standard select)."""
        try:
            await element.select_option(label=value)
            return True
        except Exception:
            try:
                # Try by index/text match
                options = element.locator('option')
                count = await options.count()
                for i in range(count):
                    text = await options.nth(i).text_content()
                    if value.lower() in (text or '').lower():
                        await element.select_option(index=i)
                        return True
            except Exception:
                pass
            return False

    async def _fill_generic(self, element: Locator, value: str, field_label: str) -> bool:
        """Universal fallback."""
        try:
            # Try standard select
            tag = await element.evaluate('el => el.tagName.toLowerCase()')
            if tag == 'select':
                await element.select_option(label=value)
                return True
            
            # Try click-type-enter
            await element.click()
            await element.type(value)
            await asyncio.sleep(0.5)
            await element.press('Enter')
            return True
        except Exception:
            return False

    async def _extract_all_options(self, element: Locator, frame_root: Locator):
        """Debug helper to list available options."""
        logger.debug("  🔍 Extracting ALL options (fallback mode)")
        try:
            # Try to open dropdown if closed
            await element.click()
            await asyncio.sleep(0.5)
            
            options = frame_root.locator('[role="option"]')
            count = await options.count()
            if count == 0:
                 logger.warning("  ⚠️ No options visible even after opening dropdown")
                 return
            
            texts = []
            for i in range(min(count, 10)):
                texts.append(await options.nth(i).text_content())
            logger.debug(f"  Available options: {texts}")
        except Exception as e:
            logger.error(f"  Error extracting options: {e}")

# Factory function
_handler_instance = ATSDropdownHandlerV2()

def get_dropdown_handler() -> ATSDropdownHandlerV2:
    return _handler_instance
