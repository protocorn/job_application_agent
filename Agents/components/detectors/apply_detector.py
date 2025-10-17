from typing import Any, Dict, List, Optional
from playwright.async_api import Page, Frame, Locator
from loguru import logger

from components.brains.gemini_button_brain import GeminiButtonBrain

class ApplyDetector:
    """Detects and ranks 'Apply' buttons on a page using patterns and an AI fallback."""

    # --- Configuration Constants ---
    # Patterns are now more concise, have no duplicates, and are easier to manage.
    # Tiers are ordered from most to least specific.
    APPLY_PATTERNS: Dict[str, Any] = {
        'primary': {
            'selectors': [
                'a[data-automation-id="adventureButton"]',  # Specific to Workday
                'button:text-is("Apply Now")', # Exact, case-sensitive text match
                'a:text-is("Apply Now")',
            ],
            'confidence': 0.95
        },
        'secondary': {
            'selectors': [
                'button:text-is("Apply")',
                'a:text-is("Apply")',
                'button[aria-label~="Apply" i]', # Contains whole word "Apply", case-insensitive
                'a[aria-label~="Apply" i]',
            ],
            'confidence': 0.8
        },
        'tertiary': {
            'selectors': [
                '[role="button"]:has-text("Apply")',
                'button[class*="apply"]',
                'a[class*="apply"]',
                'input[type="submit"][value*="Apply" i]',
            ],
            'confidence': 0.6
        }
    }

    def __init__(self, page: Page | Frame):
        """Initializes the detector with a Playwright page and AI brain."""
        self.page = page
        self.ai_brain = GeminiButtonBrain()

    async def detect(self) -> Optional[Dict[str, Any]]:
        """
        Detects the best apply button using a tiered pattern search with an AI fallback.

        Returns:
            A dictionary containing the button element and detection details, or None.
        """
        logger.info("🕵️‍♂️ Detecting apply button...")

        # Check for Adzuna-specific flow
        current_url = self.page.url
        if "adzuna.com" in current_url:
            if "/details/" in current_url:
                logger.info("🔍 Detected Adzuna details page - handling popup and 'Apply for this job' button")
                return await self._handle_adzuna_details_page()
            elif "/land/ad/" in current_url:
                logger.info("🔍 Detected Adzuna land page - clicking 'Apply Now' button")
                return await self._handle_adzuna_land_page()
        
        # Check for DeJobs intermediate page (comes after Adzuna or standalone)
        if "dejobs.org" in current_url and "/job/" in current_url:
            logger.info("🔍 Detected DeJobs intermediate page - clicking 'Apply Now' button")
            return await self._handle_dejobs_page()

        # A single, efficient wait for any potential button to appear.
        all_selectors = [s for tier in self.APPLY_PATTERNS.values() for s in tier['selectors']]
        try:
            await self.page.locator(", ".join(all_selectors)).first.wait_for(state='visible', timeout=10000)
            logger.info("Potential apply button(s) are visible. Searching by tier.")
        except Exception:
            logger.warning("No standard apply button appeared within timeout.")
        
        # 1. Attempt to find the button using reliable, tiered patterns first.
        pattern_candidate = await self._find_best_candidate_by_pattern()
        if pattern_candidate:
            logger.info(f"✅ Found apply button via pattern matching. Reason: {pattern_candidate['reason']}")
            return pattern_candidate

        # 2. If pattern matching fails, fall back to the AI model.
        logger.warning("Pattern matching failed. Attempting AI fallback.")
        ai_candidate = await self._find_candidate_by_ai()
        if ai_candidate:
             logger.info(f"✅ Found apply button via AI fallback. Reason: {ai_candidate['reason']}")
             return ai_candidate

        logger.error("❌ No apply button found by any method.")
        return None

    async def _find_best_candidate_by_pattern(self) -> Optional[Dict[str, Any]]:
        """Searches for a button tier by tier and returns the first visible match."""
        for tier_name, config in self.APPLY_PATTERNS.items():
            try:
                # Find the first visible element that matches any selector in the current tier.
                tier_locator = self.page.locator(", ".join(config['selectors']))
                visible_element = tier_locator.and_(self.page.locator(':visible')).first
                
                # A short timeout confirms the element is ready; the main wait did the heavy lifting.
                await visible_element.wait_for(state='visible', timeout=500)
                
                return {
                    'element': visible_element,
                    'confidence': config['confidence'],
                    'reason': f"Matched first visible element in '{tier_name}' tier.",
                    'method': 'pattern_match'
                }
            except Exception:
                # No visible element found in this tier, so we continue to the next.
                continue
        return None

    async def _find_candidate_by_ai(self) -> Optional[Dict[str, Any]]:
        """Uses an AI model to find the apply button as a fallback."""
        try:
            page_content = await self.page.content()
            context = "Find the primary call-to-action button to start a job application."
            ai_result = await self.ai_brain.find_apply_button(page_content, context)

            if not (ai_result and ai_result.get('found')):
                logger.info("🧠 AI analysis complete: No apply button found.")
                return None

            selector = ai_result.get('selector')
            if not selector:
                logger.warning("AI found a button but did not provide a CSS selector.")
                return None

            element = self.page.locator(selector).first
            if await element.is_visible(timeout=3000):
                return {
                    'element': element,
                    'confidence': ai_result.get('confidence', 0.5),
                    'reason': f"AI fallback: {ai_result.get('reason', 'No reason provided')}",
                    'method': 'ai_fallback'
                }
            else:
                logger.warning(f"AI-suggested element is not visible: '{selector}'")
                return None
        except Exception as e:
            logger.error(f"AI fallback process failed with an exception: {e}")
            return None

    async def _handle_adzuna_details_page(self) -> Optional[Dict[str, Any]]:
        """Handle Adzuna details page: close popup then click 'Apply for this job' button."""
        import asyncio

        logger.info("📧 Waiting for Adzuna email popup...")
        # Wait for popup to appear
        await asyncio.sleep(2)

        # Try to close the popup - click "No, thanks"
        popup_selectors = [
            'a.ea_close:has-text("No, thanks")',
            'div.mfp-content a.ea_close:has-text("No, thanks")',
            'a[class*="ea_close"]:has-text("No, thanks")',
            '.ea_close'
        ]

        popup_closed = False
        for selector in popup_selectors:
            try:
                popup_close = self.page.locator(selector)
                if await popup_close.is_visible(timeout=2000):
                    logger.info(f"📧 Found Adzuna popup 'No, thanks' button, closing it")
                    await popup_close.click()
                    await asyncio.sleep(1)
                    popup_closed = True
                    break
            except Exception:
                continue

        if not popup_closed:
            logger.warning("⚠️ Could not find Adzuna popup close button, continuing anyway")

        # Find the "Apply for this job" button
        apply_selectors = [
            'a[data-js="apply"]:has-text("Apply for this job")',
            'a[data-js="apply"]',
            'a.bg-adzuna-green-500:has-text("Apply for this job")',
            'a:has-text("Apply for this job")'
        ]

        for selector in apply_selectors:
            try:
                apply_btn = self.page.locator(selector).first
                await apply_btn.wait_for(state='visible', timeout=5000)
                logger.info(f"✅ Found Adzuna 'Apply for this job' button")
                return {
                    'element': apply_btn,
                    'confidence': 0.95,
                    'reason': 'Adzuna details page - Apply for this job button',
                    'method': 'adzuna_details_page'
                }
            except Exception:
                continue

        logger.warning(f"⚠️ Could not find Adzuna apply button")
        return None

    async def _handle_adzuna_land_page(self) -> Optional[Dict[str, Any]]:
        """Handle Adzuna /land/ad/ intermediate page: click 'Apply Now' button that leads to DeJobs."""
        import asyncio

        logger.info("🔄 On Adzuna land page, looking for 'Apply Now' button...")
        await asyncio.sleep(1)

        # Find the "Apply Now" button that links to jobsyn.org (DeJobs)
        apply_selectors = [
            'a[href*="rr.jobsyn.org"]:has-text("Apply Now")',
            'a.bg-button:has-text("Apply Now")',
            'a[class*="bg-button"]:has-text("Apply Now")',
            'a:has-text("Apply Now")'
        ]

        for selector in apply_selectors:
            try:
                apply_btn = self.page.locator(selector).first
                await apply_btn.wait_for(state='visible', timeout=5000)
                logger.info(f"✅ Found Adzuna land page 'Apply Now' button")
                return {
                    'element': apply_btn,
                    'confidence': 0.95,
                    'reason': 'Adzuna land page - Apply Now button to DeJobs',
                    'method': 'adzuna_land_page'
                }
            except Exception:
                continue

        logger.warning(f"⚠️ Could not find Adzuna land page apply button")
        return None

    async def _handle_adzuna_page(self) -> Optional[Dict[str, Any]]:
        """DEPRECATED: Old combined Adzuna handler. Use _handle_adzuna_details_page instead."""
        logger.warning("Using deprecated _handle_adzuna_page method")
        return await self._handle_adzuna_details_page()

    async def _handle_dejobs_page(self) -> Optional[Dict[str, Any]]:
        """Handle DeJobs intermediate page: find the 'Apply Now' link to actual application."""
        import asyncio
        
        logger.info("🔄 On DeJobs page, looking for 'Apply Now' button...")
        await asyncio.sleep(1)  # Let page settle
        
        # Try multiple selectors in priority order
        apply_selectors = [
            'a[href*="rr.jobsyn.org"]:has-text("Apply Now")',  # Most specific
            'a[href*="rr.jobsyn.org"]',  # By redirect URL
            'a.bg-button:has-text("Apply Now")',  # By class and text
            'a[class*="bg-button"]:has-text("Apply Now")',  # Flexible class match
            'a:has-text("Apply Now")'  # Generic fallback
        ]
        
        for selector in apply_selectors:
            try:
                apply_link = self.page.locator(selector).first
                await apply_link.wait_for(state='visible', timeout=5000)
                logger.info(f"✅ Found DeJobs 'Apply Now' button")
                return {
                    'element': apply_link,
                    'confidence': 0.95,
                    'reason': 'DeJobs Apply Now link to actual application',
                    'method': 'dejobs_specific'
                }
            except Exception:
                continue
        
        logger.warning(f"⚠️ Could not find DeJobs apply button with any selector")
        return None