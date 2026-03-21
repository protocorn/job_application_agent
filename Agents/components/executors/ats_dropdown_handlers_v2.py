"""
Enhanced V2 Dropdown Handlers with Iframe Support.
Handles Greenhouse, Workday, Lever, and others with correct frame context.
"""
import asyncio
from typing import Any, Dict, List, Optional
from playwright.async_api import Locator
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
            elif ats_type == 'ashby':
                return await self._fill_ashby(element, value, field_label)
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
            role        = await element.get_attribute('role')
            aria_popup  = await element.get_attribute('aria-haspopup')
            automation_id = await element.get_attribute('data-automation-id') or ''
            class_attr  = await element.get_attribute('class') or ''

            # Greenhouse: combobox with aria-haspopup="true" (React Select)
            if role == 'combobox' and aria_popup == 'true':
                return 'greenhouse'

            # Ashby: combobox with aria-haspopup="listbox" (not "true") OR
            # Ashby-specific class/placeholder pattern
            if role == 'combobox' and aria_popup == 'listbox':
                return 'ashby'
            if '_input_v5ami_' in class_attr or 'ashby' in class_attr.lower():
                return 'ashby'
            try:
                placeholder = await element.get_attribute('placeholder') or ''
                parent_class = await element.evaluate(
                    'el => el.closest("[class*=\\"_inputContainer_\\"]")?.className || ""'
                )
                if placeholder.lower() == 'start typing...' or '_inputContainer_v5ami_' in parent_class:
                    return 'ashby'
            except Exception:
                pass

            # Workday: data-automation-id present, OR Workday CSS-in-JS class pattern
            # Workday elements carry 'data-automation-id' or are inside a Workday frame
            if automation_id:
                return 'workday'

            # Also detect by common Workday CSS class patterns (css-XXXXXXX format)
            # or by the fact that the page URL contains workday
            try:
                page_url = element.page.url.lower()
                if 'workday' in page_url or 'wd1.myworkdayjobs' in page_url or 'wd5.myworkdayjobs' in page_url:
                    return 'workday'
            except Exception:
                pass

            # Lever
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
            
            # ── Helpers ──────────────────────────────────────────────────────────
            #
            # NOTE: _scan_options / _best_match / _ask_ai_from_options are defined
            # here as closures so they share the surrounding page/field_label context.

            _menu_selectors = [
                '[class*="select__menu"]',
                '[id*="react-select"][id*="listbox"]',
                'div[class*="MenuList"]',
                '[class*="menu"][class*="select"]',
                '[role="listbox"]',
                'div[class*="option"]',
            ]
            _option_selectors = [
                '[class*="select__option"]',
                'div[class*="option"]:not([class*="placeholder"]):not([class*="input"])',
                '[role="option"]',
                '[id*="react-select"][id*="option"]',
                'div[class*="Option"]',
                'li[role="option"]',
            ]

            async def _ask_ai_from_options(target: str, options: list) -> str:
                """
                Ask gemini-2.0-flash-lite which option best matches `target`.
                Returns the exact option text chosen, or empty string on failure.
                """
                option_texts = [t for _, t in options]
                logger.info(f"  🤖 Asking AI to pick from {len(option_texts)} options for '{target}': {option_texts}")
                try:
                    from google import genai as _genai
                    import os as _os
                    _client = _genai.Client(api_key=_os.getenv('GOOGLE_API_KEY'))
                    prompt = (
                        f'You are filling a job application form dropdown.\n'
                        f'Field: "{field_label}"\n'
                        f'Desired value: "{target}"\n\n'
                        f'Available options (you MUST pick exactly one from this list):\n'
                        + "\n".join(f"- {t}" for t in option_texts)
                        + '\n\nRules:\n'
                        '- Reply with ONLY the exact option text as listed above.\n'
                        '- For race/ethnicity fields, if the profile says Indian/South Asian/Asian select the closest broader term.\n'
                        '- For disability fields, select the option that best reflects the profile value.\n'
                        '- If truly nothing matches, reply with NO_MATCH.'
                    )
                    resp = _client.models.generate_content(
                        model="gemini-2.0-flash-lite",
                        contents=prompt
                    )
                    chosen = resp.text.strip().strip('"').strip("'")
                    logger.info(f"  AI chose: '{chosen}'")
                    if chosen == "NO_MATCH":
                        return ""
                    return chosen
                except Exception as ai_err:
                    logger.warning(f"  AI option-pick error: {ai_err}")
                    return ""

            async def _click_ai_chosen(chosen: str, options: list) -> bool:
                """Find and click the AI-chosen option from the options list."""
                for loc, text in options:
                    if text.strip().lower() == chosen.lower():
                        logger.info(f"✅ AI-selected exact option: '{text}'")
                        if await self._click_greenhouse_option(loc, text):
                            await asyncio.sleep(0.1)
                            return True
                # Closest-match fallback
                best_loc, best_text, _ = _best_match(chosen, options, threshold=0.5)
                if best_loc:
                    logger.info(f"✅ AI-guided closest match: '{best_text}'")
                    if await self._click_greenhouse_option(best_loc, best_text):
                        await asyncio.sleep(0.1)
                        return True
                return False

            async def _scan_options():
                """Return list of (locator, text) for all currently visible options."""
                found = []
                menu_loc = None
                for sel in _menu_selectors:
                    try:
                        m = page.locator(sel).first
                        if await m.count() > 0 and await m.is_visible(timeout=300):
                            menu_loc = m
                            break
                    except Exception:
                        pass

                search_ctxs = []
                if menu_loc:
                    search_ctxs.append(menu_loc)
                search_ctxs.append(page.locator('body'))

                for ctx in search_ctxs:
                    for sel in _option_selectors:
                        try:
                            opts = ctx.locator(sel)
                            cnt = await opts.count()
                            if cnt > 0:
                                for i in range(min(cnt, 30)):
                                    try:
                                        opt = opts.nth(i)
                                        if await opt.is_visible(timeout=150):
                                            text = await opt.text_content(timeout=150)
                                            if text and text.strip() and text.strip().lower() != 'no options':
                                                found.append((opt, text.strip()))
                                    except Exception:
                                        continue
                                if found:
                                    return found
                        except Exception:
                            continue
                    if found:
                        break
                return found

            def _best_match(target: str, options: list, threshold: float = 0.6):
                """Return (locator, text, score) of best fuzzy match or (None, None, 0)."""
                stop_words = {"of", "the", "in", "a", "an", "degree", "(", ")", ".", ","}

                def normalize(t: str) -> str:
                    t = t.lower().strip()
                    for old, new in {
                        "master of science": "master's", "master of arts": "master's",
                        "master of business administration": "master's", "master of engineering": "master's",
                        "bachelor of science": "bachelor's", "bachelor of arts": "bachelor's",
                        "bachelor of engineering": "bachelor's", "doctor of philosophy": "doctorate",
                        "ph.d.": "doctorate", "phd": "doctorate", "m.s.": "master's",
                        "m.a.": "master's", "m.b.a.": "master's", "b.s.": "bachelor's", "b.a.": "bachelor's",
                    }.items():
                        if old in t:
                            t = t.replace(old, new)
                    return t

                def token_overlap(t1: str, t2: str) -> float:
                    toks1 = {w for w in t1.lower().split() if w not in stop_words and len(w) > 1}
                    toks2 = {w for w in t2.lower().split() if w not in stop_words and len(w) > 1}
                    if not toks1 or not toks2:
                        return 0.0
                    return len(toks1 & toks2) / len(toks1 | toks2)

                target_lower = target.lower()
                best_loc, best_text, best_score = None, None, 0.0

                for loc, text in options:
                    text_lower = text.lower()
                    if text_lower == target_lower:
                        return loc, text, 1.0  # Exact match – stop immediately

                    score = token_overlap(target_lower, text_lower)
                    norm_val = normalize(target_lower)
                    norm_txt = normalize(text_lower)
                    if norm_val in norm_txt or norm_txt in norm_val:
                        score = max(score, 0.9)
                    if target_lower in text_lower:
                        score = max(score, 0.7)
                    elif text_lower in target_lower:
                        score = max(score, len(text_lower) / max(len(target_lower), 1))

                    if score > best_score:
                        best_score = score
                        best_loc, best_text = loc, text

                if best_loc and best_score >= threshold:
                    return best_loc, best_text, best_score
                return None, None, best_score

            words = value.split()
            selected = False

            # ── Step 4: Short-list fast path (no typing needed) ──────────────────
            # If the dropdown already exposes ≤ 15 options on open, ask AI directly.
            initial_options = await _scan_options()
            if initial_options and 0 < len(initial_options) <= 15:
                logger.info(
                    f"📋 Short dropdown ({len(initial_options)} options) for '{field_label}' - "
                    f"asking AI directly"
                )
                chosen = await _ask_ai_from_options(value, initial_options)
                if chosen:
                    selected = await _click_ai_chosen(chosen, initial_options)

            # ── Step 5: Word-by-word progressive typing ───────────────────────────
            if not selected:
                for word_idx, word in enumerate(words):
                    cumulative = " ".join(words[: word_idx + 1])
                    logger.debug(f"  Typing word {word_idx + 1}/{len(words)}: '{cumulative}'")

                    if word_idx == 0:
                        await element.type(cumulative, delay=30)
                    else:
                        await element.type(" " + word, delay=30)

                    await asyncio.sleep(0.4)

                    options_found = await _scan_options()
                    if not options_found:
                        logger.debug(f"  No options visible after '{cumulative}', continuing...")
                        continue

                    # Short list after partial typing → AI picks directly
                    if len(options_found) <= 15:
                        logger.info(
                            f"  Short list ({len(options_found)} options) after '{cumulative}' - "
                            f"asking AI"
                        )
                        chosen = await _ask_ai_from_options(value, options_found)
                        if chosen:
                            selected = await _click_ai_chosen(chosen, options_found)
                            if selected:
                                break
                        continue

                    # Longer list → fuzzy match
                    best_loc, best_text, best_score = _best_match(value, options_found, threshold=0.6)
                    if best_loc:
                        logger.info(f"✅ Match after '{cumulative}': '{best_text}' (score: {best_score:.2f})")
                        if await self._click_greenhouse_option(best_loc, best_text):
                            await asyncio.sleep(0.1)
                            selected = True
                            break
                    else:
                        logger.debug(
                            f"  Best score {best_score:.2f} below threshold after '{cumulative}', "
                            f"{'trying next word...' if word_idx < len(words) - 1 else 'no more words.'}"
                        )

            # ── Step 6: AI fallback – clear, type first word, collect options ─────
            if not selected:
                logger.info(f"🤖 AI fallback: clearing and typing first word to surface options for '{value}'")

                await element.press('Control+A')
                await asyncio.sleep(0.05)
                await element.press('Backspace')
                await asyncio.sleep(0.2)

                await element.click(timeout=1200)
                await asyncio.sleep(0.2)
                first_word = words[0] if words else value[:4]
                await element.type(first_word, delay=30)
                await asyncio.sleep(0.5)

                all_options = await _scan_options()
                if all_options:
                    chosen = await _ask_ai_from_options(value, all_options)
                    if chosen:
                        selected = await _click_ai_chosen(chosen, all_options)

            # ── Step 7: Enter key last resort ─────────────────────────────────────
            if not selected:
                logger.warning(f"⚠️ All strategies exhausted for '{value}', trying Enter key...")
                await element.press('Enter')
                await asyncio.sleep(0.3)
                try:
                    final_val = await element.input_value(timeout=1000)
                    if final_val and final_val.strip():
                        logger.info(f"✅ Selected via Enter: '{final_val}'")
                        return True
                except Exception:
                    pass
                return False

            return True

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
        """
        Workday dropdown strategy.

        Workday renders its listbox as a portal <div> at z-index 30 attached to the
        page root (not inside the element's own DOM subtree). Structure:

            <div data-behavior-click-outside-close="topmost" style="z-index:30; ...">
              <div visibility="opened" ...>
                <ul role="listbox" ...>
                  <li role="option" data-value="<guid>" id="<guid>" class="...">
                    <div>Option Text</div>
                  </li>
                  ...
                </ul>
              </div>
            </div>

        Strategy:
        1. Click the trigger to open the listbox.
        2. Wait for [role="listbox"] to become visible (search at page root, not element frame).
        3. Collect all non-disabled [role="option"] items and their text.
        4. If ≤ 15 options → ask AI to pick the best match (exact label/semantic match).
        5. If > 15 options → fuzzy/exact match in Python.
        6. Click the chosen option by its locator.
        7. Fallback: type into a search input if visible inside the listbox.
        """
        try:
            page = element.page

            # ── Step 1: Open the dropdown ────────────────────────────────────
            try:
                await element.scroll_into_view_if_needed(timeout=2000)
            except Exception:
                pass
            await element.click(timeout=3000)
            await asyncio.sleep(0.4)

            # ── Step 2: Find the listbox (portal at page root) ───────────────
            # Workday portals are divs with data-behavior-click-outside-close="topmost"
            # containing a ul[role="listbox"].  Search at the page (frame) root.
            listbox_selectors = [
                '[data-behavior-click-outside-close="topmost"] [role="listbox"]',
                '[role="listbox"][aria-required]',
                '[role="listbox"]',
            ]
            listbox = None
            for sel in listbox_selectors:
                try:
                    loc = page.locator(sel).first
                    if await loc.count() > 0 and await loc.is_visible(timeout=1500):
                        listbox = loc
                        logger.debug(f"  ✓ Workday listbox found: {sel}")
                        break
                except Exception:
                    continue

            if listbox is None:
                logger.warning(f"  ⚠ Workday: listbox not visible after click for '{field_label}'")
                # Try typing-based search fallback before giving up
                return await self._workday_type_search(element, page, value, field_label)

            # ── Step 3: Collect non-disabled options ──────────────────────────
            # Options: <li role="option"> (exclude aria-disabled="true")
            option_locs = page.locator(
                '[data-behavior-click-outside-close="topmost"] [role="option"]:not([aria-disabled="true"])'
            )
            # Fallback if portal selector doesn't work
            if await option_locs.count() == 0:
                option_locs = page.locator('[role="option"]:not([aria-disabled="true"])')

            count = await option_locs.count()
            options_list: List[tuple] = []  # (locator, text)
            for i in range(min(count, 100)):
                try:
                    opt = option_locs.nth(i)
                    if not await opt.is_visible(timeout=200):
                        continue
                    # Text is inside the inner <div>
                    text = (await opt.text_content(timeout=300) or "").strip()
                    if text:
                        options_list.append((opt, text))
                except Exception:
                    continue

            logger.debug(f"  Workday: {len(options_list)} options for '{field_label}'")

            if not options_list:
                logger.warning(f"  ⚠ Workday: no options collected for '{field_label}'")
                return await self._workday_type_search(element, page, value, field_label)

            # ── Step 4/5: Pick the best option ───────────────────────────────
            chosen_loc, chosen_text = await self._workday_pick_option(
                value, options_list, field_label
            )

            if chosen_loc is None:
                logger.warning(f"  ⚠ Workday: no match found for '{value}' in '{field_label}'")
                # Close listbox
                try:
                    await element.press('Escape')
                except Exception:
                    pass
                return False

            # ── Step 6: Click the chosen option ──────────────────────────────
            logger.info(f"✅ Workday selecting: '{chosen_text}' for '{field_label}'")
            try:
                await chosen_loc.scroll_into_view_if_needed(timeout=1000)
            except Exception:
                pass
            await chosen_loc.click(timeout=3000)
            await asyncio.sleep(0.3)
            return True

        except Exception as e:
            logger.error(f"❌ Workday dropdown error for '{field_label}': {e}")
            return False

    async def _workday_pick_option(
        self,
        target: str,
        options_list: List[tuple],
        field_label: str,
    ):
        """
        Choose the best option from the Workday options list.
        ≤ 15 options → ask AI (gemini-2.0-flash-lite).
        > 15 options → fuzzy/exact Python match.
        Returns (locator, text) or (None, None).
        """
        # Always try exact match first (O(n), cheap)
        target_lower = target.lower().strip()
        for loc, text in options_list:
            if text.lower().strip() == target_lower:
                return loc, text

        # Short list → AI picks
        if len(options_list) <= 15:
            option_texts = [t for _, t in options_list]
            logger.info(f"  🤖 Workday AI pick from {len(option_texts)} options for '{field_label}': {option_texts}")
            try:
                from google import genai as _genai
                import os as _os
                _client = _genai.Client(api_key=_os.getenv('GOOGLE_API_KEY'))
                prompt = (
                    f'You are filling a job application form dropdown.\n'
                    f'Field: "{field_label}"\n'
                    f'Desired value: "{target}"\n\n'
                    f'Available options (pick exactly one):\n'
                    + "\n".join(f"- {t}" for t in option_texts)
                    + '\n\nReply with ONLY the exact option text as listed. '
                    'If truly nothing matches, reply NO_MATCH.'
                )
                resp = _client.models.generate_content(model="gemini-2.0-flash-lite", contents=prompt)
                ai_choice = resp.text.strip().strip('"').strip("'")
                logger.info(f"  AI chose: '{ai_choice}'")
                if ai_choice != "NO_MATCH":
                    # Exact match against returned text
                    for loc, text in options_list:
                        if text.strip().lower() == ai_choice.lower():
                            return loc, text
                    # Closest fallback
                    best_loc, best_text, best_s = self._fuzzy_pick(ai_choice, options_list)
                    if best_s >= 0.5:
                        return best_loc, best_text
            except Exception as ai_err:
                logger.warning(f"  Workday AI pick error: {ai_err}")

        # Long list → fuzzy match in Python
        best_loc, best_text, best_score = self._fuzzy_pick(target, options_list, threshold=0.55)
        if best_loc:
            logger.info(f"  Workday fuzzy match: '{best_text}' (score {best_score:.2f})")
            return best_loc, best_text

        return None, None

    @staticmethod
    def _fuzzy_pick(target: str, options_list: List[tuple], threshold: float = 0.5):
        """Simple token-overlap fuzzy picker. Returns (loc, text, score)."""
        stop = {"of", "the", "in", "a", "an", "(", ")", ".", ",", "-"}
        def toks(s): return {w for w in s.lower().split() if w not in stop and len(w) > 1}

        t_toks = toks(target)
        best_loc, best_text, best_score = None, None, 0.0
        for loc, text in options_list:
            o_toks = toks(text)
            if not t_toks or not o_toks:
                continue
            score = len(t_toks & o_toks) / len(t_toks | o_toks)
            # Substring bonus
            tl, ol = target.lower(), text.lower()
            if tl in ol:
                score = max(score, 0.7)
            elif ol in tl:
                score = max(score, len(ol) / max(len(tl), 1))
            if score > best_score:
                best_score = score
                best_loc, best_text = loc, text
        if best_loc and best_score >= threshold:
            return best_loc, best_text, best_score
        return None, None, best_score

    async def _workday_type_search(
        self, element: Locator, page, value: str, field_label: str
    ) -> bool:
        """
        Fallback for Workday: type value into any visible search input inside the
        open listbox portal and then click the first matching option.
        """
        try:
            # Look for a text input inside the portal overlay
            search_input = page.locator(
                '[data-behavior-click-outside-close="topmost"] input[type="text"]'
            ).first
            if await search_input.count() == 0:
                search_input = page.locator('input[placeholder*="Search"]').first

            if await search_input.count() > 0 and await search_input.is_visible(timeout=1000):
                logger.debug(f"  Workday type-search: typing '{value[:20]}'")
                await search_input.fill(value[:20])
                await asyncio.sleep(0.5)

                # Collect filtered options
                opts = page.locator('[role="option"]:not([aria-disabled="true"])')
                count = await opts.count()
                options_list = []
                for i in range(min(count, 30)):
                    try:
                        opt = opts.nth(i)
                        if await opt.is_visible(timeout=200):
                            text = (await opt.text_content(timeout=300) or "").strip()
                            if text:
                                options_list.append((opt, text))
                    except Exception:
                        continue

                if options_list:
                    chosen_loc, chosen_text = await self._workday_pick_option(
                        value, options_list, field_label
                    )
                    if chosen_loc:
                        logger.info(f"✅ Workday type-search selected: '{chosen_text}'")
                        await chosen_loc.click(timeout=2000)
                        await asyncio.sleep(0.3)
                        return True

        except Exception as e:
            logger.debug(f"  Workday type-search failed: {e}")
        return False

    async def _fill_lever(self, element: Locator, value: str, field_label: str) -> bool:
        """
        Lever / native <select> strategy with fuzzy fallback.
        Also handles Lever custom dropdowns (divs with role="option").
        """
        # 1. Native <select> - try exact label, then fuzzy across all <option> texts
        try:
            tag = await element.evaluate('el => el.tagName.toLowerCase()')
            if tag == 'select':
                # Exact first
                try:
                    await element.select_option(label=value)
                    return True
                except Exception:
                    pass

                # Collect all option texts and fuzzy-pick
                opts = element.locator('option')
                count = await opts.count()
                options_list = []
                for i in range(count):
                    text = (await opts.nth(i).text_content() or "").strip()
                    if text and text.lower() not in ('', 'select', 'select one', '--'):
                        options_list.append((i, text))

                best_idx, best_text, best_score = None, None, 0.0
                target_lower = value.lower().strip()
                for idx, text in options_list:
                    tl = text.lower().strip()
                    if tl == target_lower:
                        best_idx, best_text, best_score = idx, text, 1.0
                        break
                    score = self._token_overlap(target_lower, tl)
                    if target_lower in tl:
                        score = max(score, 0.7)
                    elif tl in target_lower:
                        score = max(score, len(tl) / max(len(target_lower), 1))
                    if score > best_score:
                        best_score, best_idx, best_text = score, idx, text

                if best_idx is not None and best_score >= 0.5:
                    await element.select_option(index=best_idx)
                    logger.info(f"✅ Lever <select> fuzzy: '{best_text}' (score {best_score:.2f})")
                    return True
                return False
        except Exception:
            pass

        # 2. Custom Lever dropdown (click-to-open with role="option" items)
        return await self._fill_generic(element, value, field_label)

    async def _fill_ashby(self, element: Locator, value: str, field_label: str) -> bool:
        """
        Ashby dropdown strategy.

        Ashby renders its combobox as:
            <div class="_inputContainer_v5ami_28">
                <input class="_input_v5ami_28" role="combobox"
                       aria-haspopup="listbox" aria-autocomplete="list"
                       placeholder="Start typing...">
                <button class="_toggleButton_v5ami_32">▼</button>   ← opens list
            </div>

        Options appear as [role="option"] items in a listbox portal.

        Strategy:
        1. Click the input to open (Ashby opens on focus).
        2. If no options appear → click the sibling toggle button.
        3. Type a search prefix to filter (Ashby does live search).
        4. Re-collect options, pick best match (AI ≤ 15, fuzzy otherwise).
        5. Click the match.
        """
        page = element.page
        try:
            # ── Step 1: Click the input ──────────────────────────────────────
            try:
                await element.scroll_into_view_if_needed(timeout=1500)
            except Exception:
                pass
            await element.click(timeout=2000)
            await asyncio.sleep(0.3)

            # ── Step 2: If empty, try the toggle button ──────────────────────
            option_locs = page.locator('[role="option"]:not([aria-disabled="true"])')
            if await option_locs.count() == 0:
                try:
                    toggle = await element.evaluate_handle(
                        'el => el.closest("[class*=\\"_inputContainer_\\"]")'
                        '    ?.querySelector("[class*=\\"_toggleButton_\\"]") || null'
                    )
                    toggle_loc = page.locator('[class*="_toggleButton_"]').first
                    if await toggle_loc.count() > 0 and await toggle_loc.is_visible(timeout=500):
                        await toggle_loc.click(timeout=1500)
                        await asyncio.sleep(0.3)
                except Exception:
                    pass

            # ── Step 3: Type to filter ───────────────────────────────────────
            await element.fill(value[:20], timeout=2000)
            await asyncio.sleep(0.4)

            # ── Step 4: Collect options ──────────────────────────────────────
            count = await option_locs.count()
            options_list: List[tuple] = []
            for i in range(min(count, 60)):
                try:
                    opt = option_locs.nth(i)
                    if not await opt.is_visible(timeout=150):
                        continue
                    text = (await opt.text_content(timeout=200) or '').strip()
                    if text:
                        options_list.append((opt, text))
                except Exception:
                    continue

            if not options_list:
                # Last resort: press Enter on whatever is typed
                await element.press('Enter')
                logger.debug(f"  Ashby: no options found for '{field_label}', pressed Enter")
                return True

            # ── Step 5: Pick best option ─────────────────────────────────────
            chosen_loc, chosen_text = await self._workday_pick_option(
                value, options_list, field_label
            )

            if chosen_loc:
                try:
                    await chosen_loc.scroll_into_view_if_needed(timeout=800)
                except Exception:
                    pass
                await chosen_loc.click(timeout=2000)
                await asyncio.sleep(0.2)
                logger.info(f"✅ Ashby dropdown: '{chosen_text}' for '{field_label}'")
                return True

            logger.warning(f"  ⚠ Ashby: no match for '{value}' in '{field_label}'")
            try:
                await element.press('Escape')
            except Exception:
                pass
            return False

        except Exception as e:
            logger.error(f"❌ Ashby dropdown error for '{field_label}': {e}")
            return False

    async def _fill_generic(self, element: Locator, value: str, field_label: str) -> bool:
        """
        Universal fallback for any ATS not covered above
        (Taleo, iCIMS, SmartRecruiters, BambooHR, JazzHR, etc.).

        Strategy:
        1. If <select> → fuzzy select_option.
        2. Click to open. Scan for [role="option"], [role="listbox"] li, or
           [class*="option"] in the page root (portal pattern).
        3. ≤ 15 options → AI picks.  > 15 → fuzzy token-overlap.
        4. Type-and-Enter last resort.
        """
        page = element.page
        try:
            # ── 1. Native <select> ──────────────────────────────────────────
            try:
                tag = await element.evaluate('el => el.tagName.toLowerCase()')
                if tag == 'select':
                    try:
                        await element.select_option(label=value)
                        return True
                    except Exception:
                        pass
                    opts = element.locator('option')
                    count = await opts.count()
                    options_list = [(i, (await opts.nth(i).text_content() or "").strip())
                                    for i in range(count)]
                    options_list = [(i, t) for i, t in options_list
                                    if t and t.lower() not in ('', 'select', 'select one', '--')]
                    loc, text, score = None, None, 0.0
                    for idx, t in options_list:
                        s = self._token_overlap(value.lower(), t.lower())
                        if value.lower() in t.lower():
                            s = max(s, 0.7)
                        if s > score:
                            score, loc, text = s, idx, t
                    if loc is not None and score >= 0.5:
                        await element.select_option(index=loc)
                        logger.info(f"✅ Generic <select> fuzzy: '{text}' ({score:.2f})")
                        return True
                    return False
            except Exception:
                pass

            # ── 2. Click to open ────────────────────────────────────────────
            try:
                await element.scroll_into_view_if_needed(timeout=1500)
            except Exception:
                pass
            await element.click(timeout=2000)
            await asyncio.sleep(0.4)

            # ── 3. Scan for open option list (portal or inline) ─────────────
            # Cast a wide net: role-based + common CSS patterns
            option_selectors = [
                '[role="option"]:not([aria-disabled="true"])',
                '[role="listbox"] li:not([aria-disabled="true"])',
                'ul[role="listbox"] li',
                '[class*="option"]:not([class*="placeholder"]):not([class*="input"])',
                '[class*="Option"]:not([class*="placeholder"])',
                'li[data-value]',          # Workday-style items outside of Workday detection
                '.dropdown-item',          # Bootstrap
                '.select-option',          # generic
                '[class*="menu-item"]',    # various
                '[class*="item"]:not([class*="disabled"])',
            ]

            options_found: List[tuple] = []
            for sel in option_selectors:
                try:
                    locs = page.locator(sel)
                    count = await locs.count()
                    if count == 0:
                        continue
                    batch = []
                    for i in range(min(count, 60)):
                        try:
                            loc = locs.nth(i)
                            if not await loc.is_visible(timeout=150):
                                continue
                            text = (await loc.text_content(timeout=200) or "").strip()
                            if text and len(text) > 0:
                                batch.append((loc, text))
                        except Exception:
                            continue
                    if batch:
                        options_found = batch
                        logger.debug(f"  Generic: {len(batch)} options via '{sel}'")
                        break
                except Exception:
                    continue

            if not options_found:
                # ── 4a. Type + Enter last resort ────────────────────────────
                logger.debug(f"  Generic: no options found, trying type+Enter for '{field_label}'")
                try:
                    await element.fill(value, timeout=2000)
                    await asyncio.sleep(0.3)
                    await element.press('Enter')
                    return True
                except Exception:
                    try:
                        await element.type(value, delay=20)
                        await asyncio.sleep(0.4)
                        await element.press('Enter')
                        return True
                    except Exception:
                        return False

            # ── 4b. Pick best option ─────────────────────────────────────────
            # Short list → AI, long list → fuzzy
            if len(options_found) <= 15:
                try:
                    from google import genai as _genai
                    import os as _os
                    _client = _genai.Client(api_key=_os.getenv('GOOGLE_API_KEY'))
                    option_texts = [t for _, t in options_found]
                    logger.info(f"  🤖 Generic AI pick from {len(option_texts)} options: {option_texts}")
                    prompt = (
                        f'Job application form dropdown.\nField: "{field_label}"\n'
                        f'Desired value: "{value}"\n\nAvailable options:\n'
                        + "\n".join(f"- {t}" for t in option_texts)
                        + '\n\nReply with ONLY the exact option text. If nothing matches, reply NO_MATCH.'
                    )
                    resp = _client.models.generate_content(model="gemini-2.0-flash-lite", contents=prompt)
                    ai_choice = resp.text.strip().strip('"').strip("'")
                    logger.info(f"  AI chose: '{ai_choice}'")
                    if ai_choice != "NO_MATCH":
                        for loc, text in options_found:
                            if text.strip().lower() == ai_choice.lower():
                                await loc.click(timeout=2000)
                                await asyncio.sleep(0.2)
                                logger.info(f"✅ Generic AI selected: '{text}'")
                                return True
                        # Closest fuzzy fallback
                        best_loc, best_text, best_s = self._fuzzy_pick(ai_choice, options_found, 0.5)
                        if best_loc:
                            await best_loc.click(timeout=2000)
                            await asyncio.sleep(0.2)
                            logger.info(f"✅ Generic AI fuzzy: '{best_text}'")
                            return True
                except Exception as ai_err:
                    logger.warning(f"  Generic AI pick error: {ai_err}")

            # Fuzzy pick for long lists (or AI failed)
            best_loc, best_text, best_score = self._fuzzy_pick(value, options_found, threshold=0.5)
            if best_loc:
                await best_loc.click(timeout=2000)
                await asyncio.sleep(0.2)
                logger.info(f"✅ Generic fuzzy selected: '{best_text}' (score {best_score:.2f})")
                return True

            # Nothing matched - press Enter on the element as last resort
            await element.press('Enter')
            await asyncio.sleep(0.2)
            return False

        except Exception as e:
            logger.error(f"❌ Generic dropdown error for '{field_label}': {e}")
            return False

    @staticmethod
    def _token_overlap(t1: str, t2: str) -> float:
        """Token-overlap Jaccard score, ignoring common stop words."""
        stop = {"of", "the", "in", "a", "an", "(", ")", ".", ",", "-"}
        toks1 = {w for w in t1.lower().split() if w not in stop and len(w) > 1}
        toks2 = {w for w in t2.lower().split() if w not in stop and len(w) > 1}
        if not toks1 or not toks2:
            return 0.0
        return len(toks1 & toks2) / len(toks1 | toks2)

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
