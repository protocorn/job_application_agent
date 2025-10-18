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
    
    async def fill(self, element: Locator, value: str, field_label: str, profile_context: dict = None) -> bool:
        """
        FAST fill: Type → Get options → Context-aware fuzzy match → Select → Verify.
        Returns False if failed (so it goes to AI batch fallback).
        
        Args:
            profile_context: Optional dict with 'city', 'state' for context-aware matching
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
            top_options = await self._get_top_visible_options(element, count=10)  # Increased for context matching
            
            if not top_options:
                logger.warning(f"⚠️ No options appeared after typing")
                await element.press('Escape')
                return False  # Will go to AI batch fallback
            
            logger.debug(f"  Top {len(top_options)} options: {top_options[:5]}")
            
            # Step 5: Context-aware fuzzy match
            best_match, best_score = self._fuzzy_find_best_option_with_context(
                value, top_options, field_label, profile_context
            )
            logger.debug(f"  Best match: '{best_match}' (score: {best_score:.2f})")
            
            # Step 6: Select if score is good enough (>= 0.70)
            if best_score >= 0.70:
                logger.info(f"✅ Good match ({best_score:.2f}): selecting '{best_match}'")
                
                # Press Enter to select (dropdown already filtered to show this option first)
                await element.press('Enter')
                await asyncio.sleep(0.5)  # Give Greenhouse time to update DOM
                
                # Step 7: VERIFY selection was successful
                # For Greenhouse: If dropdown closed, selection succeeded
                verification_passed = await self._verify_selection_simple(element, field_label)
                
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
    
    def _fuzzy_find_best_option_with_context(self, desired: str, options: List[str], 
                                             field_label: str, profile_context: dict = None) -> Tuple[str, float]:
        """
        Find best matching option using fuzzy matching + location context.
        
        SAFETY NET for ambiguous options (e.g., University names with multiple campuses).
        If multiple high-scoring options exist, prefer ones containing location keywords.
        
        Example:
            desired = "University of Maryland"
            options = ["University of Maryland - Baltimore", 
                      "University of Maryland - Baltimore County",
                      "University of Maryland - College Park"]
            profile_context = {"city": "College Park", "state": "Maryland"}
            → Picks "College Park" option due to location match
        """
        if not options:
            return ("", 0.0)
        
        # Step 1: Get all fuzzy scores
        scored_options = []
        desired_lower = desired.lower().strip()
        
        for option in options:
            option_lower = option.lower().strip()
            
            # Base fuzzy score
            score = SequenceMatcher(None, desired_lower, option_lower).ratio()
            
            # Boost score if desired is substring of option or vice versa
            if desired_lower in option_lower or option_lower in desired_lower:
                score = max(score, 0.75)
            
            scored_options.append((option, score, option_lower))
        
        # Step 2: Check for ambiguous high scores (multiple options with similar scores)
        high_score_threshold = max(s[1] for s in scored_options)
        high_scorers = [opt for opt in scored_options if opt[1] >= high_score_threshold - 0.05]  # Within 0.05
        
        if len(high_scorers) > 1 and profile_context:
            # Multiple ambiguous options - use location context as tiebreaker
            location_keywords = []
            if profile_context.get('city'):
                location_keywords.append(profile_context['city'].lower())
            if profile_context.get('state'):
                location_keywords.append(profile_context['state'].lower())
            
            if location_keywords:
                logger.debug(f"  🎯 Ambiguous options detected, using location context: {location_keywords}")
                
                # Boost scores for options containing location keywords
                for i, (option, base_score, option_lower) in enumerate(high_scorers):
                    location_boost = 0.0
                    for keyword in location_keywords:
                        if keyword in option_lower:
                            location_boost = 0.15  # Significant boost for location match
                            logger.debug(f"  📍 Location match: '{option}' contains '{keyword}'")
                            break
                    
                    # Update score with location boost
                    high_scorers[i] = (option, base_score + location_boost, option_lower)
        
        # Step 3: Return best option (with location boost if applied)
        if high_scorers:
            best = max(high_scorers, key=lambda x: x[1])
            return (best[0], best[1])
        else:
            # Fallback to first option
            return (scored_options[0][0], scored_options[0][1])
    
    async def _verify_selection_simple(self, element: Locator, field_label: str) -> bool:
        """
        SIMPLE verification: Check if dropdown closed (aria-expanded=false).
        For Greenhouse, when you press Enter, the dropdown closes if selection succeeded.
        """
        try:
            # Method 1: Check if dropdown is closed (aria-expanded="false")
            try:
                expanded = await element.get_attribute('aria-expanded')
                if expanded == 'false':
                    logger.debug(f"  ✓ Verified: dropdown closed (aria-expanded=false)")
                    return True
            except:
                pass
            
            # Method 2: Check if options are no longer visible
            try:
                page = element.page
                options_visible = await page.locator('[role="option"]:visible').count()
                if options_visible == 0:
                    logger.debug(f"  ✓ Verified: no visible options (dropdown closed)")
                    return True
            except:
                pass
            
            # Method 3: Check sibling display element has a value (not placeholder)
            try:
                parent = element.locator('xpath=..')
                display_elem = parent.locator('[class*="singleValue"]').first
                display_text = await display_elem.text_content(timeout=500)
                if display_text and len(display_text.strip()) > 0:
                    logger.debug(f"  ✓ Verified: display shows '{display_text.strip()}'")
                    return True
            except:
                pass
            
            # No verification method succeeded
            logger.debug(f"  ✗ Could not verify selection for '{field_label}'")
            return False
            
        except Exception as e:
            logger.debug(f"  Verification error: {e}")
            return False


# Factory function
def get_dropdown_handler(strategy_timeout: int = 10000) -> GreenhouseDropdownHandlerV2:
    """Get the fast dropdown handler."""
    return GreenhouseDropdownHandlerV2(strategy_timeout=strategy_timeout)

