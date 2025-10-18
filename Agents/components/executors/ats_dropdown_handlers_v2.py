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
    
    async def fill(self, element: Locator, value: str, field_label: str, profile_context: dict = None, 
                   all_options: List[str] = None) -> bool:
        """
        FAST fill: Type → Get options → Context-aware fuzzy match → Select → Verify.
        Returns False if failed (so it goes to AI batch fallback).
        
        Args:
            profile_context: Optional dict with 'city', 'state' for context-aware matching
            all_options: If provided, skip typing and use these options directly (for AI fallback)
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
            
            # Step 3: If all_options provided (AI fallback mode), use them directly
            if all_options:
                logger.debug(f"  Using pre-extracted {len(all_options)} options for AI-guided selection")
                top_options = all_options[:10]  # Use top 10
            else:
                # Step 3a: Clear and type value IMMEDIATELY
                await element.press('Control+A')
                await element.press('Backspace')
                await asyncio.sleep(0.1)
                
                logger.debug(f"  Typing: '{value}'")
                await element.type(value, delay=30)  # Fast typing
                await asyncio.sleep(0.5)  # Wait for options to filter
                
                # Step 4: Get top visible options (filtered by typing)
                top_options = await self._get_top_visible_options(element, count=10)  # Increased for context matching
            
            if not top_options:
                logger.warning(f"⚠️ No options appeared after typing - trying full option extraction")
                await element.press('Escape')
                await asyncio.sleep(0.3)
                
                # FALLBACK: Extract ALL options and try fuzzy matching
                all_extracted_options = await self.extract_all_options(element)
                if not all_extracted_options:
                    logger.error(f"⚠️ Could not extract any options - giving up")
                    return False
                
                # Try filling again with extracted options
                logger.debug(f"  🔄 Retrying with {len(all_extracted_options)} extracted options")
                return await self.fill(element, value, field_label, profile_context, all_options=all_extracted_options)
            
            logger.debug(f"  Top {len(top_options)} options: {top_options[:5]}")
            
            # Step 5a: Try intelligent date matching first (for graduation dates, etc.)
            date_match = self._smart_date_match(value, top_options, field_label)
            if date_match:
                best_match, best_score = date_match, 0.95  # High confidence for date matching
                logger.info(f"📅 Smart date match: '{value}' → '{best_match}' (closest valid option)")
            else:
                # Step 5b: Fall back to context-aware fuzzy match
                best_match, best_score = self._fuzzy_find_best_option_with_context(
                    value, top_options, field_label, profile_context
                )
                logger.debug(f"  Best match: '{best_match}' (score: {best_score:.2f})")
            
            # Step 6: Select if score is good enough (>= 0.50, lowered threshold)
            if best_score >= 0.50:
                logger.info(f"✅ Good match ({best_score:.2f}): selecting '{best_match}'")
                
                # CRITICAL: Re-type the EXACT matched option to filter to only that option
                # This ensures Enter selects the correct option, not just the first visible one
                await element.press('Control+A')
                await element.press('Backspace')
                await asyncio.sleep(0.1)
                
                logger.debug(f"  🎯 Re-typing exact match: '{best_match}'")
                await element.type(best_match, delay=30)
                await asyncio.sleep(0.5)  # Wait for dropdown to filter to exact match
                
                # Now press Enter to select the (hopefully only) filtered option
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
                # Score too low (< 0.50) - return False for AI batch
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
    
    async def extract_all_options(self, element: Locator) -> List[str]:
        """
        Extract ALL options from dropdown by scrolling through the list.
        Used when typing returns no results (e.g., "United States of America" doesn't filter to "United States").
        """
        try:
            logger.debug(f"  🔍 Extracting ALL options (fallback mode)")
            
            # Open dropdown
            await element.scroll_into_view_if_needed(timeout=1000)
            await element.focus(timeout=2000)
            await asyncio.sleep(0.1)
            await element.press('ArrowDown', timeout=2000)
            await asyncio.sleep(0.5)  # Wait for dropdown to fully open
            
            page = element.page
            options_locator = page.locator('[role="option"]:visible')
            
            # Wait for options
            try:
                await options_locator.first.wait_for(state='visible', timeout=2000)
            except:
                logger.warning(f"  ⚠️ No options visible even after opening dropdown")
                await element.press('Escape')
                return []
            
            # Get ALL visible options (limit to reasonable number)
            visible_count = await options_locator.count()
            max_options = min(visible_count, 50)  # Limit to 50 for performance
            
            logger.debug(f"  Found {visible_count} options, extracting top {max_options}")
            
            all_options = []
            for i in range(max_options):
                try:
                    text = await options_locator.nth(i).text_content(timeout=300)
                    if text and text.strip():
                        all_options.append(text.strip())
                except:
                    continue
            
            logger.debug(f"  ✅ Extracted {len(all_options)} options")
            await element.press('Escape')  # Close dropdown
            await asyncio.sleep(0.2)
            
            return all_options
            
        except Exception as e:
            logger.error(f"  ❌ Error extracting all options: {e}")
            try:
                await element.press('Escape')
            except:
                pass
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
    
    def _smart_date_match(self, desired_value: str, options: List[str], field_label: str) -> Optional[str]:
        """
        Intelligent date matching for graduation date fields.
        
        When exact match isn't available, finds the closest valid date option.
        
        Examples:
        - Desired: "May 2025", Options: ["Spring 2026", "Fall 2026"] → "Spring 2026"
        - Desired: "2025", Options: ["Spring 2025", "Fall 2025", "Spring 2026"] → "Spring 2025"
        """
        from datetime import datetime
        
        # Only apply to date-related fields
        date_keywords = ['graduation', 'graduate', 'enrolled', 'completion', 'finish', 'expected']
        if not any(keyword in field_label.lower() for keyword in date_keywords):
            return None
        
        try:
            # Parse the desired date
            desired_date = None
            import re
            
            # Try extracting year and month
            year_match = re.search(r'\b(20\d{2})\b', desired_value)
            if not year_match:
                return None
            
            year = int(year_match.group(1))
            
            # Determine month from desired value
            desired_lower = desired_value.lower()
            if any(m in desired_lower for m in ['jan', 'feb', 'mar', 'apr', 'spring', 'winter']):
                month = 5  # Spring
            elif any(m in desired_lower for m in ['may', 'jun', 'jul', 'summer']):
                month = 8  # Summer
            elif any(m in desired_lower for m in ['aug', 'sep', 'oct', 'nov', 'dec', 'fall', 'autumn']):
                month = 12  # Fall
            else:
                month = 6  # Default mid-year
            
            desired_date = datetime(year, month, 1)
            
            # Parse all options and find closest valid one
            option_dates = []
            for option in options:
                try:
                    option_lower = option.lower()
                    
                    # Extract year
                    opt_year_match = re.search(r'\b(20\d{2})\b', option)
                    if not opt_year_match:
                        continue
                    
                    opt_year = int(opt_year_match.group(1))
                    
                    # Determine month based on season/term
                    if 'spring' in option_lower or 'winter' in option_lower:
                        opt_month = 5  # May
                    elif 'summer' in option_lower:
                        opt_month = 8  # August
                    elif 'fall' in option_lower or 'autumn' in option_lower:
                        opt_month = 12  # December
                    else:
                        opt_month = 6  # Default to mid-year
                    
                    option_date = datetime(opt_year, opt_month, 1)
                    option_dates.append((option, option_date))
                except:
                    continue
            
            if not option_dates:
                return None
            
            # Find the closest date that is >= desired date (next available option)
            future_options = [(opt, dt) for opt, dt in option_dates if dt >= desired_date]
            
            if future_options:
                # Pick the earliest future option (closest to desired date)
                closest = min(future_options, key=lambda x: abs((x[1] - desired_date).days))
                logger.info(f"📅 Date matching: '{desired_value}' ({desired_date.strftime('%b %Y')}) → '{closest[0]}' ({closest[1].strftime('%b %Y')})")
                return closest[0]
            else:
                # All options are in the past, pick the latest one
                latest = max(option_dates, key=lambda x: x[1])
                logger.warning(f"⚠️ All options are before '{desired_value}', selecting latest: '{latest[0]}'")
                return latest[0]
                
        except Exception as e:
            logger.debug(f"Date matching failed: {e}")
            return None
    
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
                # Give CUMULATIVE boost for multiple matches (more specific = higher score)
                for i, (option, base_score, option_lower) in enumerate(high_scorers):
                    location_boost = 0.0
                    matches_found = []
                    
                    for keyword in location_keywords:
                        if keyword in option_lower:
                            # More specific keywords (city) get higher boost
                            if len(keyword.split()) > 1 or keyword == location_keywords[0]:  # City (more specific)
                                location_boost += 0.20  # Higher boost for city match
                            else:  # State (less specific)
                                location_boost += 0.10  # Lower boost for state match
                            matches_found.append(keyword)
                    
                    if matches_found:
                        logger.debug(f"  📍 Location match: '{option}' contains {matches_found} (boost: +{location_boost:.2f})")
                    
                    # Update score with CUMULATIVE location boost
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

