"""
Specialized dropdown handlers for different ATS (Applicant Tracking Systems).
Each ATS has unique dropdown implementations that require specific interaction patterns.
"""
import asyncio
from typing import Optional, Dict, Any, List
from playwright.async_api import Page, Frame, Locator, ElementHandle
from loguru import logger
from abc import ABC, abstractmethod

from components.exceptions.field_exceptions import (
    DropdownInteractionError,
    TimeoutExceededError,
    VerificationFailedError,
    FieldInteractionStrategy
)


class ATSDropdownHandler(ABC):
    """Base class for ATS-specific dropdown handlers."""

    def __init__(self, page: Page | Frame):
        self.page = page
        self.strategy_timeout = 5000  # 5 seconds per strategy

    @abstractmethod
    async def can_handle(self, element: Locator) -> bool:
        """Check if this handler can process the given element."""
        pass

    @abstractmethod
    async def fill(self, element: Locator, value: str, field_label: str) -> bool:
        """Fill the dropdown with the specified value."""
        pass

    async def verify_selection(self, element: Locator, expected_value: str) -> bool:
        """Verify that the dropdown value was set correctly."""
        try:
            actual_value = await element.input_value()
            return expected_value.lower() in actual_value.lower()
        except Exception as e:
            logger.debug(f"Verification check failed: {e}")
            return False


class GreenhouseDropdownHandler(ATSDropdownHandler):
    """Handler for Greenhouse ATS dropdowns (role='combobox', aria-haspopup='true')."""

    async def can_handle(self, element: Locator) -> bool:
        """Greenhouse dropdowns use role='combobox' with aria-haspopup='true'."""
        try:
            role = await element.get_attribute('role')
            aria_popup = await element.get_attribute('aria-haspopup')
            return role == 'combobox' and aria_popup == 'true'
        except Exception:
            return False

    async def fill(self, element: Locator, value: str, field_label: str, available_options: List[str] = None) -> bool:
        """
        FAST Greenhouse dropdown filling (market-leading strategy):
        1. Type value immediately → Filter options
        2. Get top visible options
        3. Fuzzy match and select best option
        4. VERIFY selection was successful
        5. Return False if failed (for AI batch fallback)
        
        No slow pre-extraction! Fill immediately, verify, and fallback if needed.
        """
        try:
            logger.debug(f"⚡ Greenhouse dropdown '{field_label}' - fast fill mode")
            
            # Step 0: Scroll element into view (critical for subsequent dropdowns)
            try:
                await element.scroll_into_view_if_needed(timeout=1000)
                await asyncio.sleep(0.15)
            except Exception as e:
                logger.debug(f"  Scroll failed: {e}")
            
            # Step 1: Open dropdown with Greenhouse-specific pattern
            await element.focus(timeout=self.strategy_timeout)
            await asyncio.sleep(0.15)
            
            # CRITICAL: ArrowDown to open Greenhouse dropdown
            await element.press('ArrowDown', timeout=self.strategy_timeout)
            await asyncio.sleep(0.4)  # Let dropdown open
            
            # Clear any existing value
            await element.press('Control+A')
            await element.press('Backspace')
            await asyncio.sleep(0.15)
            
            # WAIT for options - SHORT timeout if we have pre-extracted options as fallback
            wait_timeout = 1.0 if options_as_strings else 2.5
            options_ready = await self._wait_for_options_to_load(element, timeout_sec=wait_timeout)
            
            if not options_ready:
                # If options didn't load but we have pre-extracted, use those for AI
                if options_as_strings:
                    logger.info(f"  Options didn't load in {wait_timeout}s, but have {len(options_as_strings)} pre-extracted options")
                    # Don't type, go straight to AI fallback with pre-extracted options
                    logger.debug(f"  Skipping typing, using AI with pre-extracted options")
                    # Create a fake "top_options" to skip typing loop
                    await element.press('Escape')
                    # Jump to AI fallback section at the end
                    typing_succeeded = False
                else:
                    logger.warning(f"⚠️ Options didn't load and no pre-extracted options available")
                    await element.press('Escape')
                    return False
            else:
                typing_succeeded = True
            
            # Step 2: Type gradually word-by-word (only if options loaded)
            if typing_succeeded:
                words = value.split()
                for word_idx, word in enumerate(words):
                    # Type this word
                    logger.debug(f"  Typing word {word_idx + 1}/{len(words)}: '{word}'")
                    await element.type(word, delay=50)  # Faster to avoid timeout
                    
                    # Add space if not last word
                    if word_idx < len(words) - 1:
                        await element.type(" ", delay=50)
                    
                    # Wait for dropdown to filter
                    await asyncio.sleep(0.3)
                    
                    # Check top option
                    top_options = await self._get_top_visible_options(element, count=3)
                    if not top_options:
                        logger.debug(f"  No options visible after typing '{word}'")
                        continue
                    
                    logger.debug(f"  Top 3 options: {top_options}")
                    
                    # Check if top option is a good match
                    top_option = top_options[0]
                    score = self._fuzzy_similarity(value, top_option)
                    logger.debug(f"  Top option '{top_option}' score: {score:.2f}")
                    
                    # THRESHOLD 1: Excellent match (>= 0.75) → Select immediately
                    if score >= 0.75:
                        logger.info(f"✅ Excellent match (score {score:.2f}): selecting '{top_option}'")
                        # Use Greenhouse-specific click selection
                        selected = await self._select_greenhouse_option_by_click(element, top_option, index=0)
                        if selected:
                            actual = await element.input_value()
                            logger.info(f"✅ Greenhouse dropdown '{field_label}' = '{actual}'")
                            return True
                        else:
                            # Selection failed - try AI fallback
                            logger.warning(f"⚠️ Failed to select excellent match, trying AI fallback")
                            break  # Exit typing loop to try AI fallback
                    
                    # If we typed all words and score is moderate, continue to AI fallback
                    if word_idx == len(words) - 1:
                        break
            
            # Step 3: Check final fuzzy match score after typing (or use pre-extracted)
            if typing_succeeded:
                top_options = await self._get_top_visible_options(element, count=3)
                if not top_options:
                    # Try using pre-extracted options if available
                    if options_as_strings:
                        logger.info(f"  No visible options, using {len(options_as_strings)} pre-extracted options")
                        top_options = options_as_strings[:3]
                    else:
                        logger.warning(f"⚠️ No options available for '{field_label}'")
                        await element.press('Escape')
                        return False
            else:
                # Typing was skipped, use pre-extracted options
                if options_as_strings:
                    logger.info(f"  Using {len(options_as_strings)} pre-extracted options (typing skipped)")
                    top_options = options_as_strings[:10]  # Use more options for AI
                else:
                    logger.warning(f"⚠️ No options to work with")
                    return False
            
            top_score = self._fuzzy_similarity(value, top_options[0])
            logger.debug(f"  Final top option: '{top_options[0]}' (score: {top_score:.2f})")
            
            # THRESHOLD 2: Good match (0.60-0.75) OR poor match → Ask Gemini
            if 0.60 <= top_score < 0.75 or (top_score < 0.60 and options_as_strings):
                if top_score < 0.60:
                    logger.debug(f"  Poor fuzzy match ({top_score:.2f}) - asking Gemini for help with all available options")
                    # Use ALL available options if fuzzy matching failed badly
                    options_for_gemini = options_as_strings[:10] if options_as_strings else top_options[:3]
                else:
                    logger.debug(f"  Moderate match - asking Gemini to pick from top 3")
                    options_for_gemini = top_options[:3]
                
                selected_option = await self._ask_gemini_to_pick(value, options_for_gemini, field_label)
                
                if selected_option:
                    # If we skipped typing, need to reopen dropdown and type to find the option
                    if not typing_succeeded:
                        logger.debug(f"  Reopening dropdown to select Gemini's choice: '{selected_option}'")
                        await element.focus(timeout=1000)
                        await asyncio.sleep(0.1)
                        await element.press('ArrowDown', timeout=1000)
                        await asyncio.sleep(0.3)
                        # Type the selected option to filter
                        await element.type(selected_option.split()[0], delay=50)  # Type first word
                        await asyncio.sleep(0.4)
                    
                    # Find the index of the selected option in visible options
                    selected_index = None
                    search_options = await self._get_top_visible_options(element, count=10)
                    if not search_options and available_options:
                        search_options = available_options[:10]
                    
                    for i, opt in enumerate(search_options):
                        if selected_option.lower() in opt.lower() or opt.lower() in selected_option.lower():
                            selected_index = i
                            break
                    
                    if selected_index is not None:
                        selected = await self._select_greenhouse_option_by_click(element, selected_option, index=selected_index)
                        if selected:
                            actual = await element.input_value()
                            logger.info(f"✅ Gemini selected '{actual}' for '{field_label}'")
                            return True
            
            # THRESHOLD 3: Excellent match (>= 0.75) → Select
            elif top_score >= 0.75:
                logger.info(f"✅ Selecting top option '{top_options[0]}' (score {top_score:.2f})")
                # Use Greenhouse-specific click selection
                selected = await self._select_greenhouse_option_by_click(element, top_options[0], index=0)
                if selected:
                    actual = await element.input_value()
                    logger.info(f"✅ Greenhouse dropdown '{field_label}' = '{actual}'")
                    return True
            
            # No good match found
            logger.warning(f"⚠️ No suitable match for '{field_label}' (best score: {top_score:.2f})")
            await element.press('Escape')  # Close dropdown
            return False

        except TimeoutError as e:
            raise TimeoutExceededError(
                field_label=field_label,
                timeout_ms=self.strategy_timeout,
                strategy=FieldInteractionStrategy.GREENHOUSE_DROPDOWN
            )

    async def _type_and_select(self, element: Locator, type_value: str, expected_value: str) -> bool:
        """Helper to type value and select from dropdown."""
        # Focus the element
        await element.focus(timeout=self.strategy_timeout)
        await asyncio.sleep(0.1)

        # Open dropdown with ArrowDown
        await element.press('ArrowDown', timeout=self.strategy_timeout)
        await asyncio.sleep(0.3)

        # Clear any existing value
        await element.press('Control+A')
        await element.press('Backspace')
        await asyncio.sleep(0.1)

        # Type the value
        await element.type(type_value, delay=50)
        await asyncio.sleep(0.4)  # Give filter time to work

        # Press Enter to select first match
        await element.press('Enter')
        await asyncio.sleep(0.3)

        # Verify selection
        actual = await element.input_value()
        if actual and actual.strip():
            # Check if we got a reasonable match
            if (type_value.lower() in actual.lower() or
                actual.lower() in expected_value.lower() or
                self._fuzzy_similarity(expected_value, actual) > 0.6):
                logger.info(f"✅ Greenhouse dropdown = '{actual}' (typed: '{type_value}')")
                return True

        return False

    async def _wait_for_options_to_load(self, element: Locator, timeout_sec: float = 3.0) -> bool:
        """Wait for Greenhouse dropdown options to actually load."""
        try:
            page = element.page
            start_time = asyncio.get_event_loop().time()
            
            while (asyncio.get_event_loop().time() - start_time) < timeout_sec:
                # Check if options are visible
                options_locator = page.locator('[role="option"]')
                count = await options_locator.count()
                
                if count > 0:
                    # Found options, check if at least one is visible
                    try:
                        first_option = options_locator.first
                        if await first_option.is_visible(timeout=500):
                            logger.debug(f"✓ Options loaded ({count} options available)")
                            return True
                    except Exception:
                        pass
                
                # Wait a bit before checking again
                await asyncio.sleep(0.3)
            
            logger.debug(f"⚠️ Timeout waiting for options to load")
            return False
        except Exception as e:
            logger.debug(f"Error waiting for options: {e}")
            return False
    
    async def _select_greenhouse_option_by_click(self, element: Locator, option_text: str, index: int = 0) -> bool:
        """
        Select a Greenhouse option by clicking on it (Greenhouse-specific pattern).
        
        Args:
            element: The input element
            option_text: Text of the option to select
            index: Index of the option in the list (0-based)
        
        Returns:
            True if selection succeeded
        """
        try:
            page = element.page
            
            # CRITICAL FIX: Only check VISIBLE options (filtered list), not all options
            # When there are 488 options, checking all with timeout=500 takes 244+ seconds!
            options_locator = page.locator('[role="option"]:visible')
            visible_count = await options_locator.count()
            
            logger.debug(f"  Found {visible_count} visible options to check")
            
            # Try to find and click the matching option (check max 10 to be safe)
            for i in range(min(visible_count, 10)):
                try:
                    option = options_locator.nth(i)
                    text = await option.text_content(timeout=500)
                    if text and option_text.lower() in text.lower():
                        # GREENHOUSE SPECIAL CLICK: Click on the option element
                        logger.debug(f"  Clicking option: '{text.strip()}'")
                        await option.click(timeout=1000)
                        await asyncio.sleep(0.3)  # Let selection register
                        
                        # Verify it closed and selected
                        actual = await element.input_value()
                        if actual and actual.strip():
                            logger.debug(f"  ✓ Selected: '{actual}'")
                            return True
                except Exception as e:
                    logger.debug(f"  Failed to click option {i}: {e}")
                    continue
            
            # Fallback: Use keyboard Enter (dropdown is already filtered, so first option should be correct)
            logger.debug(f"  Fallback: Using Enter key to select first filtered option")
            await element.focus(timeout=500)
            await asyncio.sleep(0.1)
            
            # Just press Enter - the dropdown is already open and filtered
            await element.press('Enter')
            await asyncio.sleep(0.3)
            
            # Verify
            actual = await element.input_value()
            if actual and actual.strip():
                logger.debug(f"  ✓ Selected via Enter: '{actual}'")
                return True
            
            return False
            
        except Exception as e:
            logger.debug(f"Error selecting option: {e}")
            return False
    
    async def _get_top_visible_options(self, element: Locator, count: int = 3) -> List[str]:
        """Get the top N visible options from the open Greenhouse dropdown."""
        try:
            # Greenhouse shows options in role="option" elements
            page = element.page
            
            # Wait briefly for options to appear
            await asyncio.sleep(0.2)
            
            # Get all visible options
            options_locator = page.locator('[role="option"]')
            options_count = await options_locator.count()
            
            if options_count == 0:
                return []
            
            # Get text of top N options
            top_options = []
            for i in range(min(count, options_count)):
                try:
                    option = options_locator.nth(i)
                    if await option.is_visible(timeout=500):
                        text = await option.text_content()
                        if text and text.strip():
                            top_options.append(text.strip())
                except Exception:
                    continue
            
            return top_options
        except Exception as e:
            logger.debug(f"Error getting top options: {e}")
            return []
    
    async def _ask_gemini_to_pick(self, desired_value: str, available_options: List[str], field_label: str) -> Optional[str]:
        """Ask Gemini to pick the best option from the available list."""
        try:
            # Import Gemini here to avoid circular imports
            import google.generativeai as genai
            import os
            
            api_key = os.getenv('GEMINI_API_KEY')
            if not api_key:
                logger.debug("No Gemini API key available for option selection")
                return None
            
            genai.configure(api_key=api_key)
            model = genai.GenerativeModel('gemini-2.0-flash-exp')
            
            prompt = f"""You are helping fill a job application form.

Field: {field_label}
Desired value: {desired_value}

Available options in the dropdown:
{chr(10).join(f'{i+1}. {opt}' for i, opt in enumerate(available_options))}

Which option best matches the desired value? 
- If one option is a clear match, respond with EXACTLY that option text (copy it precisely)
- If no option is suitable, respond with: SKIP

Your response (option text or SKIP):"""

            response = model.generate_content(prompt)
            result = response.text.strip()
            
            logger.debug(f"  Gemini response: '{result}'")
            
            if result == "SKIP" or "skip" in result.lower():
                return None
            
            # Check if result matches one of the options
            for opt in available_options:
                if result.lower() in opt.lower() or opt.lower() in result.lower():
                    logger.info(f"🧠 Gemini selected: '{opt}'")
                    return opt
            
            return None
            
        except Exception as e:
            logger.debug(f"Gemini option selection failed: {e}")
            return None

    def _fuzzy_find_best_option(self, value: str, options: List[str]) -> tuple[str, float]:
        """Find the best matching option using fuzzy matching."""
        value_lower = value.lower().strip()
        best_match = None
        best_score = 0.0

        for option in options:
            if not option or not option.strip():
                continue

            option_lower = option.lower().strip()

            # Exact match
            if value_lower == option_lower:
                return option, 1.0

            # Calculate similarity score
            score = self._fuzzy_similarity(value_lower, option_lower)
            if score > best_score:
                best_score = score
                best_match = option

        return best_match, best_score

    def _fuzzy_similarity(self, str1: str, str2: str) -> float:
        """Calculate fuzzy similarity between two strings."""
        str1_lower = str1.lower().strip()
        str2_lower = str2.lower().strip()

        # Direct substring match
        if str1_lower in str2_lower or str2_lower in str1_lower:
            return 0.9

        # Word-based similarity
        words1 = set(str1_lower.split())
        words2 = set(str2_lower.split())

        if words1 and words2:
            intersection = words1 & words2
            union = words1 | words2
            if union:
                return len(intersection) / len(union)

        # Character overlap
        common_chars = sum(1 for c in str1_lower if c in str2_lower)
        return common_chars / max(len(str1_lower), len(str2_lower))

    def _get_shortest_typing_string(self, target: str, all_options: List[str]) -> str:
        """Get the shortest string that uniquely identifies target among options."""
        # Try progressively longer prefixes until we find one that's unique
        for length in range(3, len(target) + 1):
            prefix = target[:length]
            # Count how many options start with this prefix
            matches = sum(1 for opt in all_options if opt and opt.lower().startswith(prefix.lower()))
            if matches == 1:
                return prefix

        # Fall back to first word or first 5 chars
        first_word = target.split()[0] if ' ' in target else target[:5]
        return first_word


class WorkdayDropdownHandler(ATSDropdownHandler):
    """Handler for Workday ATS dropdowns (data-automation-id patterns)."""

    async def can_handle(self, element: Locator) -> bool:
        """Workday dropdowns use data-automation-id attributes."""
        try:
            automation_id = await element.get_attribute('data-automation-id')
            return automation_id is not None and 'dropdown' in automation_id.lower()
        except Exception:
            return False

    async def fill(self, element: Locator, value: str, field_label: str) -> bool:
        """
        Workday dropdown interaction pattern:
        1. Click to open dropdown
        2. Wait for options to load
        3. Find and click matching option
        """
        try:
            logger.debug(f"💼 Workday dropdown handler for '{field_label}'")

            # Step 1: Click to open dropdown
            await element.click(timeout=self.strategy_timeout)
            await asyncio.sleep(0.3)

            # Step 2: Find the dropdown list container
            dropdown_list = self.page.locator('[data-automation-id*="dropdown-list"]').first
            await dropdown_list.wait_for(state='visible', timeout=self.strategy_timeout)

            # Step 3: Find matching option (case-insensitive)
            option_selector = f'[data-automation-id*="dropdown-option"]:has-text("{value}")'
            option = self.page.locator(option_selector).first

            # Try exact match first
            if await option.is_visible(timeout=1000):
                await option.click()
            else:
                # Fallback: find option containing the value
                all_options = await dropdown_list.locator('[data-automation-id*="dropdown-option"]').all()
                for opt in all_options:
                    text = await opt.inner_text()
                    if value.lower() in text.lower():
                        await opt.click()
                        break
                else:
                    raise DropdownInteractionError(
                        field_label=field_label,
                        value=value,
                        dropdown_type='workday_dropdown',
                        reason=f"Option '{value}' not found in dropdown"
                    )

            await asyncio.sleep(0.2)

            # Verify selection
            if await self.verify_selection(element, value):
                logger.info(f"✅ Workday dropdown '{field_label}' = '{value}'")
                return True
            else:
                actual = await element.input_value()
                raise VerificationFailedError(
                    field_label=field_label,
                    expected=value,
                    actual=actual
                )

        except TimeoutError:
            raise TimeoutExceededError(
                field_label=field_label,
                timeout_ms=self.strategy_timeout,
                strategy=FieldInteractionStrategy.WORKDAY_DROPDOWN
            )


class LeverDropdownHandler(ATSDropdownHandler):
    """Handler for Lever ATS dropdowns (standard select elements with custom styling)."""

    async def can_handle(self, element: Locator) -> bool:
        """Lever uses standard select elements with specific class patterns."""
        try:
            tag_name = await element.evaluate('el => el.tagName.toLowerCase()')
            if tag_name == 'select':
                # Check for Lever-specific classes or attributes
                class_attr = await element.get_attribute('class') or ''
                return 'lever' in class_attr.lower() or 'application-field' in class_attr.lower()
            return False
        except Exception:
            return False

    async def fill(self, element: Locator, value: str, field_label: str) -> bool:
        """
        Lever dropdown interaction pattern:
        1. Use Playwright's select_option (standard select element)
        2. Verify selection
        """
        try:
            logger.debug(f"🎚️ Lever dropdown handler for '{field_label}'")

            # Lever uses standard HTML select elements
            # Try to select by text value
            await element.select_option(label=value, timeout=self.strategy_timeout)
            await asyncio.sleep(0.2)

            # Verify selection
            if await self.verify_selection(element, value):
                logger.info(f"✅ Lever dropdown '{field_label}' = '{value}'")
                return True
            else:
                actual = await element.input_value()
                raise VerificationFailedError(
                    field_label=field_label,
                    expected=value,
                    actual=actual
                )

        except Exception as e:
            # If select_option fails, try by value or index
            try:
                options = await element.locator('option').all()
                for idx, option in enumerate(options):
                    text = await option.inner_text()
                    if value.lower() in text.lower():
                        await element.select_option(index=idx)
                        logger.info(f"✅ Lever dropdown '{field_label}' = '{value}' (by index)")
                        return True
            except Exception:
                pass

            raise DropdownInteractionError(
                field_label=field_label,
                value=value,
                dropdown_type='lever_dropdown',
                reason=str(e)
            )


class AshbyButtonGroupHandler(ATSDropdownHandler):
    """Handler for Ashby button groups (radio-style button selections)."""

    async def can_handle(self, element: Locator) -> bool:
        """Ashby uses button groups with role='button' for selections."""
        try:
            role = await element.get_attribute('role')
            data_testid = await element.get_attribute('data-testid') or ''
            return role == 'button' and 'option' in data_testid.lower()
        except Exception:
            return False

    async def fill(self, element: Locator, value: str, field_label: str) -> bool:
        """
        Ashby button group interaction pattern:
        1. Find the button group container
        2. Find button matching the value
        3. Click it
        """
        try:
            logger.debug(f"🔘 Ashby button group handler for '{field_label}'")

            # Ashby button groups are siblings - find the group
            parent = element.locator('..')
            buttons = await parent.locator('button[role="button"]').all()

            for button in buttons:
                button_text = await button.inner_text()
                if value.lower() in button_text.lower():
                    await button.click(timeout=self.strategy_timeout)
                    await asyncio.sleep(0.2)
                    logger.info(f"✅ Ashby button group '{field_label}' = '{value}'")
                    return True

            raise DropdownInteractionError(
                field_label=field_label,
                value=value,
                dropdown_type='ashby_button_group',
                reason=f"Button with text '{value}' not found in group"
            )

        except TimeoutError:
            raise TimeoutExceededError(
                field_label=field_label,
                timeout_ms=self.strategy_timeout,
                strategy=FieldInteractionStrategy.ASHBY_BUTTON_GROUP
            )


class UniversalDropdownHandler(ATSDropdownHandler):
    """Fallback handler that tries multiple generic strategies."""

    async def can_handle(self, element: Locator) -> bool:
        """Always returns True as this is the fallback handler."""
        return True

    async def fill(self, element: Locator, value: str, field_label: str) -> bool:
        """
        Universal fallback strategies:
        1. Greenhouse-style combobox (focus + arrow + type + enter)
        2. Standard select element
        3. Click + type + enter
        4. JavaScript injection
        """
        logger.debug(f"🌐 Universal dropdown handler for '{field_label}'")

        strategies = [
            self._try_greenhouse_pattern,
            self._try_standard_select,
            self._try_click_type_enter,
            self._try_javascript_injection
        ]

        for strategy in strategies:
            try:
                if await strategy(element, value, field_label):
                    return True
            except Exception as e:
                logger.debug(f"{strategy.__name__} failed: {e}")
                continue

        return False

    async def _try_greenhouse_pattern(self, element: Locator, value: str, field_label: str) -> bool:
        """Try Greenhouse combobox pattern with intelligent typing strategies."""
        try:
            # Check if it has combobox characteristics
            role = await element.get_attribute('role')
            if role != 'combobox':
                return False

            logger.debug(f"Trying Greenhouse pattern for '{field_label}'")

            # Try multiple typing strategies
            strategies = [
                value,  # Full value
                value.split()[0] if ' ' in value else value,  # First word only (e.g., "Male" from "Male - He/Him")
                value[:10],  # First 10 characters
            ]

            for attempt, type_value in enumerate(strategies, 1):
                try:
                    logger.debug(f"  Attempt {attempt}: typing '{type_value}'")

                    # Focus the element
                    await element.focus(timeout=3000)
                    await asyncio.sleep(0.1)

                    # Open dropdown with ArrowDown
                    await element.press('ArrowDown', timeout=3000)
                    await asyncio.sleep(0.3)

                    # Clear any existing value
                    await element.press('Control+A')
                    await element.press('Backspace')
                    await asyncio.sleep(0.1)

                    # Type the value
                    await element.type(type_value, delay=50)
                    await asyncio.sleep(0.4)  # Give filter time to work

                    # Press Enter to select first match
                    await element.press('Enter')
                    await asyncio.sleep(0.3)

                    # Verify selection - check if any value was selected
                    actual = await element.input_value()
                    if actual and actual.strip():
                        # Check if the typed value is in the actual value (fuzzy match)
                        if type_value.lower() in actual.lower() or actual.lower() in value.lower():
                            logger.info(f"✅ Greenhouse pattern '{field_label}' = '{actual}' (typed: '{type_value}')")
                            return True
                        else:
                            logger.debug(f"  Mismatch: typed '{type_value}', got '{actual}'")
                            # Try next strategy
                            continue

                except Exception as e:
                    logger.debug(f"  Strategy {attempt} failed: {e}")
                    continue

            logger.debug("All Greenhouse typing strategies exhausted")

        except Exception as e:
            logger.debug(f"Greenhouse pattern failed: {e}")
            pass
        return False

    async def _try_standard_select(self, element: Locator, value: str, field_label: str) -> bool:
        """Try standard HTML select element interaction."""
        try:
            await element.select_option(label=value, timeout=3000)
            if await self.verify_selection(element, value):
                logger.info(f"✅ Standard select '{field_label}' = '{value}'")
                return True
        except Exception:
            pass
        return False

    async def _try_click_type_enter(self, element: Locator, value: str, field_label: str) -> bool:
        """Try click, type, and enter strategy."""
        try:
            await element.click(timeout=3000)
            await asyncio.sleep(0.2)
            await element.type(value, delay=30)
            await asyncio.sleep(0.2)
            await element.press('Enter')
            await asyncio.sleep(0.2)

            if await self.verify_selection(element, value):
                logger.info(f"✅ Click-type-enter '{field_label}' = '{value}'")
                return True
        except Exception:
            pass
        return False

    async def _try_javascript_injection(self, element: Locator, value: str, field_label: str) -> bool:
        """Try JavaScript injection as last resort."""
        try:
            await element.evaluate(
                """(el, val) => {
                    el.value = val;
                    el.dispatchEvent(new Event('input', { bubbles: true }));
                    el.dispatchEvent(new Event('change', { bubbles: true }));
                }""",
                value
            )
            await asyncio.sleep(0.2)

            if await self.verify_selection(element, value):
                logger.info(f"✅ JavaScript injection '{field_label}' = '{value}'")
                return True
        except Exception:
            pass
        return False


class ATSDropdownFactory:
    """Factory to get the appropriate dropdown handler for an element."""

    def __init__(self, page: Page | Frame):
        self.page = page
        self.handlers = [
            GreenhouseDropdownHandler(page),
            WorkdayDropdownHandler(page),
            LeverDropdownHandler(page),
            AshbyButtonGroupHandler(page),
            UniversalDropdownHandler(page)  # Fallback - must be last
        ]

    async def get_handler(self, element: Locator) -> ATSDropdownHandler:
        """Get the appropriate handler for the given element."""
        for handler in self.handlers:
            if await handler.can_handle(element):
                logger.debug(f"Selected handler: {handler.__class__.__name__}")
                return handler

        # Should never reach here as UniversalDropdownHandler always returns True
        return self.handlers[-1]

    async def fill_dropdown(self, element: Locator, value: str, field_label: str, available_options: List[str] = None) -> bool:
        """Automatically select and use the appropriate handler."""
        handler = await self.get_handler(element)
        
        # Pass available_options to Greenhouse handler, others don't need it
        if isinstance(handler, GreenhouseDropdownHandler):
            return await handler.fill(element, value, field_label, available_options)
        else:
            return await handler.fill(element, value, field_label)
