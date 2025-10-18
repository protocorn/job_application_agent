"""
ATS-specific dropdown handlers - FAST VERSION (Market-Leading Strategy)

Key improvements:
1. NO slow option pre-extraction (saves 1+ minute)
2. Immediate fill-and-verify approach
3. Robust verification of selection success
4. AI batch fallback for failed fields only
"""

import asyncio
from typing import List, Optional, Tuple
from playwright.async_api import Locator
from loguru import logger
from difflib import SequenceMatcher

class GreenhouseDropdownHandlerV2:
    """
    Fast Greenhouse dropdown handler - fills immediately without pre-extraction.
    
    Strategy:
    1. Type value → Filter options
    2. Get top visible options
    3. Fuzzy match → Select best
    4. **VERIFY** selection
    5. Return False if failed (for AI batch fallback)
    """
    
    def __init__(self, strategy_timeout: int = 10000):
        self.strategy_timeout = strategy_timeout
    
    async def can_handle(self, element: Locator) -> bool:
        """Check if this is a Greenhouse combobox."""
        try:
            role = await element.get_attribute('role')
            aria_popup = await element.get_attribute('aria-haspopup')
            return role == 'combobox' and aria_popup == 'true'
        except Exception:
            return False
    
    async def fill(self, element: Locator, value: str, field_label: str) -> bool:
        """
        FAST fill: Type → Get options → Fuzzy match → Select → Verify.
        Returns False if failed (so it goes to AI batch fallback).
        """
        try:
            logger.debug(f"⚡ Fast fill: '{field_label}'")
            
            # Step 1: Scroll and focus
            try:
                await element.scroll_into_view_if_needed(timeout=1000)
                await asyncio.sleep(0.1)
            except Exception:
                pass
            
            # Step 2: Open dropdown (Greenhouse: focus → ArrowDown)
            await element.focus(timeout=self.strategy_timeout)
            await asyncio.sleep(0.1)
            await element.press('ArrowDown', timeout=self.strategy_timeout)
            await asyncio.sleep(0.3)
            
            # Step 3: Clear and type value IMMEDIATELY
            await element.press('Control+A')
            await element.press('Backspace')
            await asyncio.sleep(0.1)
            
            logger.debug(f"  Typing: '{value}'")
            await element.type(value, delay=30)  # Fast typing
            await asyncio.sleep(0.5)  # Wait for options to filter
            
            # Step 4: Get top visible options (filtered by typing)
            top_options = await self._get_top_visible_options(element, count=5)
            
            if not top_options:
                logger.warning(f"⚠️ No options appeared after typing")
                await element.press('Escape')
                return False  # Will go to AI batch fallback
            
            logger.debug(f"  Top {len(top_options)} options: {top_options[:3]}")
            
            # Step 5: Fuzzy match to find best option
            best_match, best_score = self._fuzzy_find_best_option(value, top_options)
            logger.debug(f"  Best match: '{best_match}' (score: {best_score:.2f})")
            
            # Step 6: Select if score is good enough (>= 0.70)
            if best_score >= 0.70:
                logger.info(f"✅ Good match ({best_score:.2f}): selecting '{best_match}'")
                
                # Press Enter to select (dropdown already filtered to show this option first)
                await element.press('Enter')
                await asyncio.sleep(0.3)
                
                # Step 7: VERIFY selection was successful
                verification_passed = await self._verify_selection(element, field_label, best_match)
                
                if verification_passed:
                    logger.info(f"✅ Verified: '{field_label}' filled successfully")
                    return True
                else:
                    logger.warning(f"⚠️ Verification failed - will retry with AI")
                    return False  # Will go to AI batch fallback
            else:
                # Score too low - return False for AI batch
                logger.warning(f"⚠️ Low match score ({best_score:.2f}) - will ask AI")
                await element.press('Escape')
                return False  # Will go to AI batch fallback
                
        except Exception as e:
            logger.error(f"❌ Error filling '{field_label}': {e}")
            try:
                await element.press('Escape')
            except:
                pass
            return False
    
    async def _get_top_visible_options(self, element: Locator, count: int = 5) -> List[str]:
        """Get top N visible options from the open dropdown."""
        try:
            page = element.page
            # Greenhouse options have role="option" and are visible
            options_locator = page.locator('[role="option"]:visible')
            
            # Wait briefly for options to appear
            try:
                await options_locator.first.wait_for(state='visible', timeout=1000)
            except:
                return []
            
            visible_count = await options_locator.count()
            if visible_count == 0:
                return []
            
            # Get text from top N options
            options_text = []
            for i in range(min(count, visible_count)):
                try:
                    text = await options_locator.nth(i).text_content(timeout=500)
                    if text and text.strip():
                        options_text.append(text.strip())
                except:
                    continue
            
            return options_text
        except Exception as e:
            logger.debug(f"  Error getting options: {e}")
            return []
    
    def _fuzzy_find_best_option(self, desired: str, options: List[str]) -> Tuple[str, float]:
        """Find the best matching option using fuzzy matching."""
        if not options:
            return ("", 0.0)
        
        best_match = options[0]
        best_score = 0.0
        
        desired_lower = desired.lower().strip()
        
        for option in options:
            option_lower = option.lower().strip()
            
            # Calculate similarity
            score = SequenceMatcher(None, desired_lower, option_lower).ratio()
            
            # Boost score if desired is substring of option or vice versa
            if desired_lower in option_lower or option_lower in desired_lower:
                score = max(score, 0.75)
            
            if score > best_score:
                best_score = score
                best_match = option
        
        return (best_match, best_score)
    
    async def _verify_selection(self, element: Locator, field_label: str, expected_value: str) -> bool:
        """
        CRITICAL: Verify that the dropdown was actually filled.
        
        For Greenhouse dropdowns, check:
        1. Sibling display element (e.g., [class*="singleValue"])
        2. Input value
        3. aria-activedescendant attribute
        """
        try:
            # Method 1: Check sibling display element (most reliable for Greenhouse)
            try:
                parent = element.locator('xpath=..')
                display_elem = parent.locator('[class*="singleValue"], [class*="placeholder"]').first
                display_text = await display_elem.text_content(timeout=1000)
                if display_text and display_text.strip():
                    # Check if it matches our expected value
                    similarity = SequenceMatcher(None, expected_value.lower(), display_text.lower()).ratio()
                    if similarity > 0.6:
                        logger.debug(f"  ✓ Verified via display element: '{display_text}'")
                        return True
            except:
                pass
            
            # Method 2: Check input value
            try:
                input_val = await element.input_value(timeout=1000)
                if input_val and input_val.strip():
                    similarity = SequenceMatcher(None, expected_value.lower(), input_val.lower()).ratio()
                    if similarity > 0.6:
                        logger.debug(f"  ✓ Verified via input value: '{input_val}'")
                        return True
            except:
                pass
            
            # Method 3: Check aria-activedescendant
            try:
                active_id = await element.get_attribute('aria-activedescendant')
                if active_id:
                    logger.debug(f"  ✓ Verified via aria-activedescendant: {active_id}")
                    return True
            except:
                pass
            
            # No verification method succeeded
            logger.warning(f"  ✗ Could not verify selection for '{field_label}'")
            return False
            
        except Exception as e:
            logger.debug(f"  Verification error: {e}")
            return False


# Factory function
def get_dropdown_handler(strategy_timeout: int = 10000) -> GreenhouseDropdownHandlerV2:
    """Get the fast dropdown handler."""
    return GreenhouseDropdownHandlerV2(strategy_timeout=strategy_timeout)

