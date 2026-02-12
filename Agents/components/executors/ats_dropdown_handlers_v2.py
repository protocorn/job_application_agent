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

    async def fill_multiselect(
        self,
        element: Locator,
        value: str,
        field_label: str,
        is_last: bool = False
    ) -> bool:
        """
        Fill a multi-select dropdown (Greenhouse) with one value.
        
        For multi-select dropdowns, this method should be called multiple times,
        once for each value to select.
        
        Args:
            element: The input element for the multi-select dropdown
            value: Single value to select
            field_label: Label for logging
            is_last: Whether this is the last value (to close the dropdown)
        
        Returns:
            True if successful, False otherwise
        """
        try:
            logger.debug(f"🔢 Selecting '{value}' in multi-select '{field_label}'")
            
            # Get the page/frame context
            page_or_frame = element.page
            
            # Step 1: Focus and click the input to open dropdown (if not already open)
            await element.focus(timeout=1500)
            await asyncio.sleep(0.1)
            
            # Check if menu is already visible
            try:
                menu_visible = await page_or_frame.locator('[class*="select__menu"]').first.is_visible(timeout=500)
            except:
                menu_visible = False
            
            if not menu_visible:
                await element.click(timeout=1500)
                await asyncio.sleep(0.2)
            
            # Step 2: Type the value to filter options
            await element.fill('', timeout=1000)  # Clear any existing text
            await asyncio.sleep(0.05)
            await element.type(value, delay=30)
            logger.debug(f"  Typing: '{value}'")
            
            # Step 3: Wait for menu to appear with options (longer wait for dynamic loading)
            await asyncio.sleep(0.6)  # Increased wait for options to load
            
            # Step 4: Find the menu
            try:
                menu = await page_or_frame.locator('[class*="select__menu"]').first.wait_for(state='visible', timeout=2000)
                logger.debug(f"  ✓ Found menu with selector: [class*=\"select__menu\"]")
            except Exception as e:
                logger.warning(f"  ✗ Menu not found: {e}")
                return False
            
            # Step 5: Find matching option
            logger.debug(f"  Searching for options in menu context...")
            option_selector = '[class*="select__option"]'
            
            try:
                option_elements = await page_or_frame.locator(option_selector).all()
                logger.debug(f"    Found {len(option_elements)} elements with selector: {option_selector}")
                
                if not option_elements:
                    logger.warning(f"  ✗ No options found")
                    return False
                
                # Collect visible options
                visible_options = []
                for opt in option_elements:
                    try:
                        if await opt.is_visible(timeout=300):
                            opt_text = await opt.inner_text(timeout=300)
                            visible_options.append((opt, opt_text.strip()))
                            logger.debug(f"      Option: '{opt_text.strip()}'")
                    except:
                        continue
                
                if not visible_options:
                    logger.warning(f"  ✗ No visible options")
                    return False
                
                logger.debug(f"    ✓ Collected {len(visible_options)} visible options")
                
                # Step 6: Find best match
                best_match = None
                best_score = 0
                
                for opt_element, opt_text in visible_options:
                    # Exact match
                    if opt_text.lower() == value.lower():
                        best_match = (opt_element, opt_text, 1.0)
                        break
                    
                    # Partial match
                    if value.lower() in opt_text.lower() or opt_text.lower() in value.lower():
                        score = len(value) / max(len(opt_text), len(value))
                        if score > best_score:
                            best_score = score
                            best_match = (opt_element, opt_text, score)
                
                if best_match:
                    match_element, match_text, score = best_match
                    if score == 1.0:
                        logger.info(f"  ✅ Exact match found: '{match_text}'")
                    else:
                        logger.info(f"  ✅ Partial match found (score: {score:.2f}): '{match_text}'")
                    
                    # Click the option
                    await match_element.click(timeout=1500)
                    await asyncio.sleep(0.2)
                    
                    # If this is the last value, close the dropdown by clicking away
                    if is_last:
                        # Press Escape to close the dropdown
                        await element.press('Escape', timeout=1000)
                        await asyncio.sleep(0.1)
                    
                    return True
                else:
                    logger.warning(f"  ✗ No match found for '{value}'")
                    return False
                    
            except Exception as e:
                logger.warning(f"  ✗ Error finding option: {e}")
                return False
            
        except Exception as e:
            logger.warning(f"❌ Multi-select fill failed for '{field_label}': {e}")
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
        Get the root context for finding dropdown options.
        Greenhouse often renders options in a portal outside the iframe!
        """
        # First, try to get the page object (works for both iframe and main page)
        try:
            # For elements in iframes, we need to search in the PAGE context, not frame
            # because Greenhouse renders dropdowns in portals attached to the main page body
            page = element.page
            # Return a locator that searches the entire page
            return page.locator('body')
        except Exception:
            # Fallback to frame-local search
            return element.locator('xpath=/html/body')

    async def _fill_greenhouse(self, element: Locator, value: str, field_label: str) -> bool:
        """
        Enhanced Greenhouse/React Select strategy:
        1. Click to open dropdown
        2. Type to filter
        3. Find menu container (React Select renders in portal)
        4. Find and click matching option
        """
        logger.debug(f"⚡ Fast fill Greenhouse dropdown: '{field_label}' = '{value}'")

        try:
            # Get page context (React Select often renders menus in portals at page level)
            page = element.page
            
            # Step 0: Close any already-open dropdowns (to avoid conflicts)
            try:
                # Check if dropdown is already expanded
                is_expanded = await element.get_attribute('aria-expanded')
                if is_expanded == 'true':
                    logger.debug(f"  Dropdown already open, closing first...")
                    await element.press('Escape')
                    await asyncio.sleep(0.2)
            except:
                pass
            
            # Wait for DOM to stabilize before interacting
            await asyncio.sleep(0.2)
            
            # Step 1: Focus the input first
            await element.focus(timeout=1500)
            await asyncio.sleep(0.1)
            
            # Step 2: Clear any existing value using keyboard shortcuts
            # Select all and delete (works better for React Select than fill(''))
            await element.press('Control+A')
            await asyncio.sleep(0.05)
            await element.press('Backspace')
            await asyncio.sleep(0.1)
            
            # Step 3: Wait for element to be stable, then click to ensure menu opens
            try:
                # Wait for element to stop changing (become stable)
                await element.wait_for(state='attached', timeout=1000)
                await asyncio.sleep(0.1)
            except:
                pass
            
            await element.click(timeout=1200)  # Increased timeout for stability
            await asyncio.sleep(0.2)  # Increased wait for menu animation
            
            # Step 4: Type the value to filter options
            logger.debug(f"  Typing: '{value}'")
            await element.type(value, delay=20)  # Faster typing
            await asyncio.sleep(0.4)  # Reduced wait for filtering and menu to render
            
            # Step 5: Find the React Select menu container
            # React Select uses div with class="select__menu" or similar
            # Try multiple selectors for the menu container
            menu_selectors = [
                '[class*="select__menu"]',  # React Select menu
                '[id*="react-select"][id*="listbox"]',  # React Select listbox
                'div[class*="MenuList"]',  # React Select MenuList
                '[class*="menu"][class*="select"]',  # Generic React Select
                '[role="listbox"]',  # ARIA listbox
                'div[class*="option"]'  # Fallback to options container
            ]
            
            menu_found = False
            menu_locator = None
            
            for selector in menu_selectors:
                try:
                    menu_locator = page.locator(selector).first
                    count = await menu_locator.count()
                    if count > 0:
                        is_visible = await menu_locator.is_visible(timeout=300)  # Faster menu detection
                        if is_visible:
                            logger.debug(f"  ✓ Found menu with selector: {selector}")
                            menu_found = True
                            break
                        else:
                            logger.debug(f"  ✗ Menu found but not visible: {selector}")
                    else:
                        logger.debug(f"  ✗ No menu found with: {selector}")
                except Exception as e:
                    logger.debug(f"  ✗ Error checking menu selector {selector}: {e}")
                    continue
            
            # Step 6: Find options within the menu (or globally if menu not found)
            # Try multiple option selectors
            option_selectors = [
                '[class*="select__option"]',  # React Select options (most common)
                'div[class*="option"]:not([class*="placeholder"]):not([class*="input"])',  # Generic options
                '[role="option"]',  # ARIA options
                '[id*="react-select"][id*="option"]',  # React Select ID pattern
                'div[class*="Option"]',  # Capitalized Option
                'li[role="option"]',  # List item options
            ]
            
            options_found = []
            options_locator = None
            
            # Search within menu first if found, then globally as fallback
            search_contexts = []
            if menu_found:
                search_contexts.append(("menu context", menu_locator))
            search_contexts.append(("page context", page.locator('body')))
            
            for context_name, context in search_contexts:
                logger.debug(f"  Searching for options in {context_name}...")
                for selector in option_selectors:
                    try:
                        opts = context.locator(selector)
                        count = await opts.count()
                        
                        if count > 0:
                            logger.debug(f"    Found {count} elements with selector: {selector}")
                            options_locator = opts
                            
                            # Collect visible options
                            visible_count = 0
                            for i in range(min(count, 20)):  # Check first 20
                                try:
                                    opt = opts.nth(i)
                                    if await opt.is_visible(timeout=150):  # Faster visibility check
                                        visible_count += 1
                                        text = await opt.text_content(timeout=150)  # Faster text extraction
                                        if text and text.strip():
                                            options_found.append((opt, text.strip()))
                                            logger.debug(f"      Option {visible_count}: '{text.strip()}'")
                                except Exception as e:
                                    continue
                            
                            if options_found:
                                logger.debug(f"    ✓ Collected {len(options_found)} visible options")
                                break
                            else:
                                logger.debug(f"    ✗ No visible options among {count} elements")
                    except Exception as e:
                        logger.debug(f"    ✗ Error with selector {selector}: {e}")
                        continue
                
                if options_found:
                    break
            
            if not options_found:
                logger.warning(f"⚠️ No visible options found after typing '{value}'")
                logger.debug(f"  Attempting fallback strategy: clear input and browse unfiltered options...")
                
                # Fallback Strategy: Clear input and try to find options without filtering
                try:
                    # Clear the typed value
                    await element.press('Control+A')
                    await asyncio.sleep(0.05)
                    await element.press('Backspace')
                    await asyncio.sleep(0.4)  # Wait for options to reload without filtering
                    
                    # Try to find unfiltered options
                    for selector in option_selectors:
                        try:
                            opts = page.locator(selector)
                            count = await opts.count()
                            
                            if count > 0:
                                logger.debug(f"    Checking {count} unfiltered options with: {selector}")
                                # Collect visible options
                                for i in range(min(count, 30)):  # Check more options
                                    try:
                                        opt = opts.nth(i)
                                        if await opt.is_visible(timeout=200):
                                            text = await opt.text_content(timeout=200)
                                            if text and text.strip() and text.strip().lower() != 'no options':
                                                options_found.append((opt, text.strip()))
                                                if len(options_found) <= 10:  # Log first 10
                                                    logger.debug(f"      Unfiltered option {len(options_found)}: '{text.strip()}'")
                                    except:
                                        continue
                                
                                if options_found:
                                    logger.debug(f"    ✓ Found {len(options_found)} unfiltered options!")
                                    break
                        except:
                            continue
                except Exception as e:
                    logger.debug(f"  ✗ Fallback strategy failed: {e}")
                
                # If still no options, try pressing Enter as last resort
                if not options_found:
                    logger.warning(f"⚠️ Still no options found, trying Enter key...")
                    await element.press('Enter')
                    await asyncio.sleep(0.3)
                    
                    # Verify if something was selected
                    try:
                        final_val = await element.input_value(timeout=1000)
                        if final_val and final_val.strip() and len(final_val.strip()) > 0:
                            logger.info(f"✅ Selected via Enter: '{final_val}'")
                            return True
                    except Exception:
                        pass
                    
                    return False
            
            # Step 6: Find best matching option
            logger.debug(f"  Checking {len(options_found)} visible options for match...")
            
            value_lower = value.lower()
            best_match = None
            best_score = 0
            
            # Helper function to normalize degree names for matching
            def normalize_degree(text: str) -> str:
                """Normalize degree names for better matching"""
                text = text.lower().strip()
                # Normalize common degree patterns
                replacements = {
                    "master of science": "master's",
                    "master of arts": "master's",
                    "master of business administration": "master's",
                    "master of engineering": "master's",
                    "bachelor of science": "bachelor's",
                    "bachelor of arts": "bachelor's",
                    "bachelor of engineering": "bachelor's",
                    "doctor of philosophy": "doctorate",
                    "ph.d.": "doctorate",
                    "phd": "doctorate",
                    "m.s.": "master's",
                    "m.a.": "master's",
                    "m.b.a.": "master's",
                    "b.s.": "bachelor's",
                    "b.a.": "bachelor's",
                }
                for old, new in replacements.items():
                    if old in text:
                        text = text.replace(old, new)
                return text
            
            # Helper function to calculate token overlap score
            def token_overlap_score(text1: str, text2: str) -> float:
                """Calculate overlap between significant tokens"""
                # Tokenize and remove common stop words
                stop_words = {"of", "the", "in", "a", "an", "degree", "(", ")", ".", ","}
                tokens1 = {t for t in text1.lower().split() if t not in stop_words and len(t) > 1}
                tokens2 = {t for t in text2.lower().split() if t not in stop_words and len(t) > 1}
                
                if not tokens1 or not tokens2:
                    return 0.0
                
                intersection = tokens1.intersection(tokens2)
                union = tokens1.union(tokens2)
                return len(intersection) / len(union) if union else 0.0
            
            for opt, text in options_found:
                text_lower = text.lower()
                
                # Exact match (highest priority)
                if text_lower == value_lower:
                    logger.info(f"✅ Exact match found: '{text}'")
                    success = await self._click_greenhouse_option(opt, text)
                    if success:
                        await asyncio.sleep(0.1)  # Brief wait for selection to register
                        return True
                
                # Normalized match (for degrees)
                normalized_value = normalize_degree(value_lower)
                normalized_text = normalize_degree(text_lower)
                if normalized_value in normalized_text or normalized_text in normalized_value:
                    score = 0.9  # High score for normalized match
                    if score > best_score:
                        best_score = score
                        best_match = (opt, text)
                        logger.debug(f"    Normalized match: '{value}' → '{text}' (score: {score:.2f})")
                
                # Token overlap match (for semantic similarity)
                token_score = token_overlap_score(value_lower, text_lower)
                if token_score > 0.5 and token_score > best_score:
                    best_score = token_score
                    best_match = (opt, text)
                    logger.debug(f"    Token overlap: '{text}' (score: {token_score:.2f})")
                
                # Contains match (check both ways)
                if value_lower in text_lower:
                    score = len(value_lower) / len(text_lower)  # Prefer shorter matches
                    if score > best_score:
                        best_score = score
                        best_match = (opt, text)
                elif text_lower in value_lower:
                    score = len(text_lower) / len(value_lower)
                    if score > best_score:
                        best_score = score
                        best_match = (opt, text)
            
            # Click best match if found (with reasonable threshold)
            if best_match and best_score >= 0.3:
                opt, text = best_match
                logger.info(f"✅ Match found (score: {best_score:.2f}): '{text}' for '{value}'")
                success = await self._click_greenhouse_option(opt, text)
                if success:
                    await asyncio.sleep(0.1)  # Brief wait for selection to register
                    return True
            
            # No match found - try Enter as last resort
            logger.warning(f"⚠️ No matching option found, trying Enter")
            await element.press('Enter')
            await asyncio.sleep(0.3)
            
            # Verify if something was selected
            try:
                final_val = await element.input_value(timeout=1000)
                if final_val and final_val.strip() and len(final_val.strip()) > 0:
                    logger.info(f"✅ Selected via Enter: '{final_val}'")
                    return True
            except Exception:
                pass
            
            return False

        except Exception as e:
            logger.error(f"❌ Greenhouse dropdown fill error for '{field_label}': {e}")
            return False

    async def _click_greenhouse_option(self, option: Locator, option_text: str) -> bool:
        """
        Robust click for Greenhouse/React Select options.
        Greenhouse uses React which requires proper event dispatching.
        
        Strategy:
        1. Try standard click (works most of the time)
        2. Try JavaScript click (bypasses React event issues)
        3. Try mousedown + mouseup events (React's synthetic events)
        4. Try force click (last resort)
        """
        try:
            # Strategy 1: Standard click
            try:
                await option.click(timeout=1000)
                await asyncio.sleep(0.1)
                logger.debug(f"  ✓ Standard click succeeded for: '{option_text}'")
                return True
            except Exception as e:
                logger.debug(f"  ✗ Standard click failed: {e}")
            
            # Strategy 2: JavaScript click (dispatch click event)
            try:
                await option.evaluate('el => el.click()')
                await asyncio.sleep(0.1)
                logger.debug(f"  ✓ JS click succeeded for: '{option_text}'")
                return True
            except Exception as e:
                logger.debug(f"  ✗ JS click failed: {e}")
            
            # Strategy 3: Mouse events (React synthetic events)
            try:
                await option.evaluate('''el => {
                    el.dispatchEvent(new MouseEvent('mousedown', { bubbles: true }));
                    el.dispatchEvent(new MouseEvent('mouseup', { bubbles: true }));
                    el.dispatchEvent(new MouseEvent('click', { bubbles: true }));
                }''')
                await asyncio.sleep(0.1)
                logger.debug(f"  ✓ Mouse events succeeded for: '{option_text}'")
                return True
            except Exception as e:
                logger.debug(f"  ✗ Mouse events failed: {e}")
            
            # Strategy 4: Force click (last resort)
            try:
                await option.click(force=True, timeout=1000)
                await asyncio.sleep(0.1)
                logger.debug(f"  ✓ Force click succeeded for: '{option_text}'")
                return True
            except Exception as e:
                logger.debug(f"  ✗ Force click failed: {e}")
            
            logger.warning(f"⚠️ All click strategies failed for option: '{option_text}'")
            return False
            
        except Exception as e:
            logger.error(f"❌ Error clicking Greenhouse option '{option_text}': {e}")
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
            await element.click(force=True)
            await asyncio.sleep(0.5)
            
            # Try multiple selectors including broader match
            options = frame_root.locator('[role="option"], .select__option, .dropdown-option, [class*="option"]')
            count = await options.count()
            if count == 0:
                 logger.warning("  ⚠️ No options visible even after opening dropdown")
                 # Check if options are in a different frame or portal (sometimes they are attached to body)
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
