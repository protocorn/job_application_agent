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
        Greenhouse dropdown interaction pattern with smart fuzzy matching:
        1. Use fuzzy matching to find best option from available options
        2. Type minimal characters needed to match that option
        3. Select the top result
        """
        try:
            logger.debug(f"🏢 Greenhouse dropdown handler for '{field_label}'")

            # Strategy 1: If we have available options, use fuzzy matching to find best match
            if available_options and len(available_options) > 0:
                best_match, best_score = self._fuzzy_find_best_option(value, available_options)
                if best_match and best_score > 0.6:  # 60% threshold
                    logger.debug(f"  Fuzzy matched '{value}' → '{best_match}' (score: {best_score:.2f})")
                    # Type the shortest unique prefix of the best match
                    type_value = self._get_shortest_typing_string(best_match, available_options)
                    logger.debug(f"  Will type '{type_value}' to select '{best_match}'")

                    success = await self._type_and_select(element, type_value, best_match)
                    if success:
                        return True

            # Strategy 2: Fall back to original multi-strategy approach
            logger.debug(f"  Trying fallback strategies...")
            strategies = [
                value.split()[0] if ' ' in value else value[:3],  # First word or first 3 chars
                value.split()[0] if ' ' in value else value,  # First word
                value[:10] if len(value) > 10 else value,  # First 10 chars
            ]

            for attempt, type_value in enumerate(strategies, 1):
                try:
                    logger.debug(f"  Attempt {attempt}: typing '{type_value}'")
                    success = await self._type_and_select(element, type_value, value)
                    if success:
                        return True
                except Exception as e:
                    logger.debug(f"  Strategy {attempt} failed: {e}")
                    continue

            # All strategies failed
            actual = await element.input_value()
            raise VerificationFailedError(
                field_label=field_label,
                expected=value,
                actual=actual
            )

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

    async def fill_dropdown(self, element: Locator, value: str, field_label: str) -> bool:
        """Automatically select and use the appropriate handler."""
        handler = await self.get_handler(element)
        return await handler.fill(element, value, field_label)
