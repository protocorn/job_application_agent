from __future__ import annotations
import asyncio
import logging
import base64
import os
import sys
import time
from typing import Any, Dict, List, Optional, Union

from playwright.async_api import Page, Frame, async_playwright

# Add parent directory to path for logging_config import
sys.path.append(os.path.join(os.path.dirname(__file__), '..'))
from logging_config import setup_file_logging
from components.session.session_manager import SessionManager

# New refactored imports
from components.router.state_machine import StateMachine, ApplicationState
from components.detectors.popup_detector import PopupDetector
from components.detectors.apply_detector import ApplyDetector
from components.executors.popup_executor import PopupExecutor
from components.executors.click_executor import ClickExecutor
from components.validators.nav_validator import NavValidator
from components.executors.generic_form_filler import GenericFormFiller
from components.custom_exceptions import HumanInterventionRequired
from components.detectors.submit_detector import SubmitDetector
from components.detectors.next_button_detector import NextButtonDetector
from components.detectors.auth_page_detector import AuthenticationPageDetector
from components.detectors.section_detector import SectionDetector
from components.executors.section_filler import SectionFiller
from components.detectors.application_form_detector import ApplicationFormDetector
from components.executors.cmp_consent import CmpConsent
from components.brains.gemini_page_analyzer import GeminiPageAnalyzer
from components.executors.iframe_helper import IframeHelper


logger = logging.getLogger(__name__)

class RefactoredJobAgent:
    """The main class for the refactored job application agent."""
    def __init__(self, playwright, headless: bool = True, keep_open: bool = False, debug: bool = False, hold_seconds: int = 0, slow_mo_ms: int = 0, job_id: str = None, jobs_dict: dict = None, session_manager: SessionManager = None) -> None:
        self.playwright = playwright
        self.headless = headless
        self.keep_open = keep_open
        self.debug = debug
        self.hold_seconds = hold_seconds
        self.slow_mo_ms = slow_mo_ms
        self.job_id = job_id  # Store job_id for intervention notifications
        self.jobs_dict = jobs_dict  # Reference to the shared JOBS dictionary
        self.session_manager = session_manager
        self.current_session = None
        self.page: Optional[Page] = None
        self.current_context: Optional[Union[Page, Frame]] = None
        self.state_machine: Optional[StateMachine] = None
        
        # These will be initialized after the page is created
        self.popup_detector = None
        self.popup_executor = None
        self.apply_detector = None
        self.click_executor = None
        self.nav_validator = None
        self.form_filler = None
        self.submit_detector = None
        self.next_button_detector = None
        self.auth_page_detector = None
        self.application_form_detector = None
        self.page_analyzer = None
        self.iframe_helper = None

    async def _new_page(self) -> Page:
        browser = await self.playwright.chromium.launch(headless=self.headless, slow_mo=self.slow_mo_ms)
        context = await browser.new_context()
        page = await context.new_page()
        page.set_default_timeout(60000)
        return page
    
    def _initialize_components_for_context(self, context: Union[Page, Frame]):
        """Initialize all components that require a browsing context (Page or Frame)."""
        self.popup_detector = PopupDetector(context)
        # Pass action recorder to popup executor for recording popup interactions
        if hasattr(self, 'action_recorder') and self.action_recorder:
            self.popup_executor = PopupExecutor(context, self.action_recorder)
        else:
            self.popup_executor = PopupExecutor(context)
        self.apply_detector = ApplyDetector(context)
        # Pass action recorder to click executor for recording clicks
        if hasattr(self, 'action_recorder') and self.action_recorder:
            self.click_executor = ClickExecutor(context, self.action_recorder)
        else:
            self.click_executor = ClickExecutor(context)
        self.nav_validator = NavValidator(context)
        # Create form filler with action recorder if available
        if hasattr(self, 'action_recorder') and self.action_recorder:
            self.form_filler = GenericFormFiller(context, self.action_recorder)
            logger.info(f"🎬 Form filler initialized with action recorder for context: {type(context).__name__}")
        else:
            self.form_filler = GenericFormFiller(context)
            logger.warning("⚠️ Form filler initialized WITHOUT action recorder")
        self.submit_detector = SubmitDetector(context)
        self.next_button_detector = NextButtonDetector(context)
        self.auth_page_detector = AuthenticationPageDetector(context)
        self.section_detector = SectionDetector(context)
        self.section_filler = SectionFiller(context)
        self.application_form_detector = ApplicationFormDetector(context)
        self.page_analyzer = GeminiPageAnalyzer()
        self.iframe_helper = IframeHelper(self.page)

    async def _get_element_selector(self, element) -> str:
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

    def _log_to_jobs(self, level: str, message: str):
        """Log a message to the shared JOBS dictionary for frontend display."""
        if self.jobs_dict and self.job_id and self.job_id in self.jobs_dict:
            self.jobs_dict[self.job_id]['logs'].append({
                "timestamp": time.time(),
                "level": level,
                "message": message
            })
            # Also update last_updated timestamp
            self.jobs_dict[self.job_id]['last_updated'] = time.time()

    def _update_job_and_session_status(self, status: str, message: str = None):
        """Update both job status in JOBS dict and session status in SessionManager."""
        # Update job status
        if self.jobs_dict and self.job_id and self.job_id in self.jobs_dict:
            self.jobs_dict[self.job_id]['status'] = status
            self.jobs_dict[self.job_id]['last_updated'] = time.time()
            if message:
                self._log_to_jobs("info", message)

        # Update session status
        if self.session_manager and self.current_session:
            # Map job statuses to session statuses
            session_status_map = {
                "running": "in_progress",
                "completed": "completed",
                "failed": "failed",
                "intervention": "needs_attention",
                "requires_auth": "requires_authentication",
                "frozen": "frozen"
            }
            session_status = session_status_map.get(status, status)
            self.session_manager.update_session(self.current_session.session_id, status=session_status)

    def _set_context(self, context: Union[Page, Frame]):
        """Switch active context (page or iframe frame) and reinitialize components."""
        self.current_context = context
        self._initialize_components_for_context(context)
        
        # Note: State registration will happen after state machine is created

    async def _update_components_for_new_page(self, new_page: Page):
        """Update all components to work with a new page context after tab switching."""
        logger.info(f"🔄 Updating all components for new page: {new_page.url}")
        
        # Update the main page reference
        self.page = new_page
        
        # Reinitialize all components with the new page
        self._initialize_components_for_context(new_page)
        
        # Update the state machine's page reference if it exists
        if hasattr(self, 'state_machine') and self.state_machine:
            self.state_machine.page = new_page
        
        logger.info("✅ All components updated for new page")

    async def process_link(self, url: str) -> None:
        logger.info("Processing link with refactored agent: %s", url)
        self._log_to_jobs("info", f"🚀 Starting job application for: {url}")
        
        # Create session if session manager is available
        if self.session_manager:
            self.current_session = self.session_manager.create_session(url)
            logger.info(f"Created session {self.current_session.session_id} for {url}")

            # Store session_id in JOBS dictionary for mapping
            if self.jobs_dict and self.job_id and self.job_id in self.jobs_dict:
                self.jobs_dict[self.job_id]['session_id'] = self.current_session.session_id

            # Start action recording for this session
            action_recorder = self.session_manager.start_action_recording(
                self.current_session.session_id, url
            )

            # Store action recorder for later use when context is available
            self.action_recorder = action_recorder
        else:
            # No action recording available
            self.action_recorder = None

        self.page = await self._new_page()

        try:
            # Update status to running
            self._update_job_and_session_status('running', "🏃 Job application process started")

            # Initialize all components now that page is ready
            self._set_context(self.page)
            
            self._log_to_jobs("info", "🌐 Navigating to job posting...")
            await self.page.goto(url, wait_until="domcontentloaded")
            # Record the navigation action
            if self.action_recorder:
                self.action_recorder.record_navigation(url, success=True)
                logger.info(f"🎬 Recorded initial navigation to: {url}")
            # Wait a bit more for dynamic content to load
            await self.page.wait_for_timeout(3000)
            self._log_to_jobs("info", "✅ Page loaded successfully")
            
            # Initialize and run the state machine
            self.state_machine = StateMachine(initial_state='start', page=self.page)
            self._register_states()  # Register states AFTER creating the state machine
            self._log_to_jobs("info", "🤖 Starting AI-powered job application process...")
            await self.state_machine.run()
            
            # Session will be frozen in the finally block regardless of outcome
        except Exception as e:
            logger.error(f"Failed to process link '{url}': {e}", exc_info=True)
            self._log_to_jobs("error", f"❌ Failed to process job application: {str(e)}")
        finally:
            # Stop action recording and save to session (NEW ACTION-BASED APPROACH)
            if (self.session_manager and self.current_session and 
                not hasattr(self, '_session_already_frozen')):
                try:
                    # Stop action recording and save actions to session
                    success = self.session_manager.stop_action_recording(
                        self.current_session.session_id, 
                        save_to_session=True
                    )
                    if success:
                        logger.info(f"✅ Action recording stopped and saved for session {self.current_session.session_id}")
                        self._log_to_jobs("info", f"💾 Action history saved! Use dashboard to resume exactly where you left off.")
                        
                        # Optional: Still take a screenshot for visual reference (much smaller than browser state)
                        if self.page:
                            try:
                                screenshot_path = await self.session_manager.take_screenshot(
                                    self.current_session.session_id, self.page
                                )
                                if screenshot_path:
                                    self.current_session.screenshot_path = screenshot_path
                                    self.session_manager.save_sessions()
                                    logger.info(f"📷 Reference screenshot saved: {screenshot_path}")
                            except Exception as screenshot_error:
                                logger.warning(f"Failed to take reference screenshot: {screenshot_error}")
                    else:
                        logger.warning(f"Failed to save action recording for session {self.current_session.session_id}")
                        
                except Exception as recording_error:
                    logger.error(f"Critical error during action recording save: {recording_error}")
                    self._log_to_jobs("error", f"⚠️ Failed to save action history: {str(recording_error)}")
            
            # Check if browser should stay open for human intervention
            if getattr(self, 'keep_browser_open_for_human', False):
                logger.info("👤 Keeping browser open indefinitely for human intervention...")
                self._log_to_jobs("info", "👤 Browser staying open for manual completion. Close manually when done.")
                # Don't close browser - let human handle it
                return

            elif self.keep_open or self.debug:
                if self.debug:
                    logger.info("🐛 Debug mode: Keeping browser open indefinitely...")
                    self._log_to_jobs("info", "🐛 Debug mode: Browser staying open indefinitely. Close manually when done.")
                    # Don't close browser in debug mode - let user handle it
                    return
                else:
                    logger.info(f"Keeping browser open for {self.hold_seconds} seconds...")
                    self._log_to_jobs("info", f"⏳ Keeping browser open for {self.hold_seconds} seconds...")
                    await asyncio.sleep(self.hold_seconds)

            try:
                if self.page and not self.page.is_closed():
                    await self.page.context.browser.close()
                    self._log_to_jobs("info", "🔒 Browser session closed")
            except Exception:
                pass

    def _register_states(self):
        """Registers all the state handlers with the state machine."""
        if not self.state_machine:
            return
        self.state_machine.add_state('start', self._state_start)
        self.state_machine.add_state('ai_guided_navigation', self._state_ai_guided_navigation)  # New unified AI-guided state
        self.state_machine.add_state('resolve_blocker', self._state_resolve_blocker)
        self.state_machine.add_state('click_apply', self._state_click_apply)
        self.state_machine.add_state('fill_form', self._state_fill_form) 
        self.state_machine.add_state('ai_analyze_page', self._state_ai_analyze_page)  # AI page analysis state
        self.state_machine.add_state('human_intervention', self._state_human_intervention)
        self.state_machine.add_state('success', self._state_success)
        self.state_machine.add_state('fail', self._state_fail)

    # -----------------------------------------------------------------
    # State Machine Handlers
    # -----------------------------------------------------------------

    async def _state_start(self, state: ApplicationState) -> str:
        profile = _load_profile_data()

        # Try to extract job context from the page if possible
        job_context = await self._extract_job_context_from_page()
        if job_context:
            profile['job_context'] = job_context
            logger.info(f"📋 Extracted job context: {job_context.get('company', 'Unknown')} - {job_context.get('title', 'Unknown')}")

        state.update_context({'url': self.page.url, 'profile': profile})
        return 'ai_guided_navigation'

    async def _extract_job_context_from_page(self) -> Dict[str, str]:
        """Extract job context (company, title, description) from the current page."""
        try:
            job_context = {}

            # Get page text content
            page_content = await self.page.content()

            # Extract company name from page title or content
            page_title = await self.page.title()
            if page_title:
                job_context['page_title'] = page_title

            # Look for common job posting elements
            try:
                # Try to find company name
                company_selectors = [
                    'h1', 'h2', '[data-test="company-name"]', '.company-name',
                    '[class*="company"]', '[id*="company"]'
                ]
                for selector in company_selectors:
                    try:
                        elements = await self.page.locator(selector).all()
                        for element in elements[:3]:  # Check first 3 matches
                            if await element.is_visible():
                                text = await element.inner_text()
                                if text and len(text.strip()) < 100:  # Reasonable company name length
                                    job_context['company'] = text.strip()
                                    break
                        if 'company' in job_context:
                            break
                    except:
                        continue

                # Try to find job title
                title_selectors = [
                    'h1', 'h2', '[data-test="job-title"]', '.job-title',
                    '[class*="title"]', '[id*="title"]'
                ]
                for selector in title_selectors:
                    try:
                        elements = await self.page.locator(selector).all()
                        for element in elements[:3]:
                            if await element.is_visible():
                                text = await element.inner_text()
                                if text and len(text.strip()) < 150:  # Reasonable job title length
                                    # Avoid duplicate company name
                                    if job_context.get('company', '') not in text:
                                        job_context['title'] = text.strip()
                                        break
                        if 'title' in job_context:
                            break
                    except:
                        continue

                # Try to find job description
                description_selectors = [
                    '[class*="description"]', '[id*="description"]',
                    '[class*="job-description"]', '.job-details',
                    '[data-test="job-description"]'
                ]
                for selector in description_selectors:
                    try:
                        element = await self.page.locator(selector).first
                        if await element.is_visible():
                            text = await element.inner_text()
                            if text and len(text.strip()) > 50:  # Substantial description
                                job_context['description'] = text.strip()[:1000]  # Limit length
                                break
                    except:
                        continue

            except Exception as e:
                logger.debug(f"Error extracting job details from page elements: {e}")

            # If we didn't get much, try URL analysis
            if not job_context.get('company'):
                url = self.page.url
                # Extract from common job board URLs
                import re
                if 'linkedin.com' in url:
                    match = re.search(r'/company/([^/]+)', url)
                    if match:
                        job_context['company'] = match.group(1).replace('-', ' ').title()
                elif 'indeed.com' in url or 'glassdoor.com' in url:
                    # These often have company names in the URL parameters
                    pass

            logger.info(f"📋 Extracted job context: {list(job_context.keys())}")
            return job_context

        except Exception as e:
            logger.warning(f"Failed to extract job context: {e}")
            return {}

    async def _state_ai_guided_navigation(self, state: ApplicationState) -> Optional[str]:
        """AI-guided navigation that analyzes the current page and determines the next best action."""
        logger.info(">>> State: AI_GUIDED_NAVIGATION")
        
        # UNIVERSAL CHECK 1: Always check for popups first - they can appear at ANY time
        logger.info("🔍 Universal Check 1: Detecting popups...")
        popup_result = await self.popup_detector.detect()
        if popup_result:
            logger.info(f"🚨 Popup detected: {popup_result}")
            state.context['blocker'] = popup_result
            return 'resolve_blocker'
        
        # UNIVERSAL CHECK 2: Check for authentication pages
        logger.info("🔍 Universal Check 2: Checking for authentication pages...")
        auth_result = await self.auth_page_detector.detect()
        if auth_result and auth_result['action'] == 'human_intervention':
            logger.info(f"🔐 Authentication page requires human intervention: {auth_result['reason']}")
            state.context['human_intervention_reason'] = auth_result['reason']
            return 'human_intervention'
        
        # UNIVERSAL CHECK 3: Check for CMP/Cookie consent
        logger.info("🔍 Universal Check 3: Checking for cookie consent...")
        try:
            cmp_consent = CmpConsent(self.page)
            if await cmp_consent.detect_and_handle():
                logger.info("✅ Handled cookie consent, re-analyzing page...")
                return 'ai_guided_navigation'  # Re-analyze after handling consent
        except Exception as e:
            logger.debug(f"CMP consent check failed: {e}")
        
        # PATTERN-BASED DETECTION: Only check for apply button if we haven't started the application process
        has_clicked_apply = state.context.get('has_clicked_apply', False)
        
        if not has_clicked_apply:
            logger.info("🔍 Pattern Check: Looking for apply button before AI analysis...")
            try:
                apply_button_result = await self.apply_detector.detect()
                if apply_button_result:
                    logger.info("✅ Apply button found via pattern matching - proceeding to click")
                    state.context['apply_button'] = apply_button_result
                    return 'click_apply'
            except Exception as e:
                logger.debug(f"Apply button pattern detection failed: {e}")
        else:
            logger.info("🔍 Skipping apply button check - already in application process")
        
        # AI ANALYSIS: Only if no apply button found, let AI determine the page state and next action
        logger.info("🧠 AI Analysis: Determining page state and next action...")
        try:
            page_analysis = await self._comprehensive_page_analysis(state)
            logger.info(f"🤖 AI Analysis Result: {page_analysis}")
            
            # Execute the AI-recommended action
            if page_analysis['action'] == 'find_apply_button':
                logger.info("🎯 AI: Page is a job listing - looking for apply button")
                return await self._handle_find_apply_button(state)
                
            elif page_analysis['action'] == 'fill_form':
                logger.info("📝 AI: Form detected - proceeding to fill")
                state.context['has_clicked_apply'] = True
                return 'fill_form'
                
            elif page_analysis['action'] == 'handle_iframe':
                logger.info("🖼️ AI: Iframe detected - switching context")
                return await self._handle_iframe_switch(state)
                
            elif page_analysis['action'] == 'submit_form':
                logger.info("📤 AI: Form ready for submission")
                return await self._handle_form_submission_intelligent(state)
                
            elif page_analysis['action'] == 'application_complete':
                logger.info("✅ AI: Application appears complete")
                # Double-check for explicit success indicators before declaring success
                if await self._verify_application_success(state):
                    return 'success'
                else:
                    logger.warning("⚠️ AI suggested completion but verification failed - asking for human confirmation")
                    state.context['human_intervention_reason'] = "AI believes application is complete, but no clear success indicators found. Please verify if the application was successfully submitted."
                    return 'human_intervention'
                
            elif page_analysis['action'] == 'need_human_intervention':
                logger.info("👤 AI: Requires human intervention")
                state.context['human_intervention_reason'] = page_analysis['reason']
                return 'human_intervention'
                
            elif page_analysis['action'] == 'navigate_to_next_page':
                logger.info("➡️ AI: Navigating to next page")
                return await self._handle_navigation(state, page_analysis)
                
            else:
                logger.warning(f"⚠️ AI returned unknown action: {page_analysis['action']}")
                state.context['human_intervention_reason'] = f"AI could not determine next action: {page_analysis.get('reason', 'Unknown reason')}"
                return 'human_intervention'
                
        except Exception as e:
            logger.error(f"❌ AI page analysis failed: {e}")
            state.context['human_intervention_reason'] = f"AI page analysis failed: {str(e)}. Please review the page and determine next steps."
            return 'human_intervention'

    async def _comprehensive_page_analysis(self, state: ApplicationState) -> Dict[str, Any]:
        """Uses AI to comprehensively analyze the current page and determine the best next action."""
        try:
            # Take screenshot for AI analysis
            screenshot = await self.page.screenshot()

            # Get page context
            url = self.page.url
            page_title = await self.page.title()
            has_clicked_apply = state.context.get('has_clicked_apply', False)
            came_from_intervention = state.context.get('came_from_human_intervention', False)

            # Record page state for replay
            if self.action_recorder:
                self.action_recorder.record_page_state(
                    url,
                    page_title,
                    page_type="ai_analysis_checkpoint",
                    metadata={"has_clicked_apply": has_clicked_apply}
                )
            
            # Create comprehensive prompt for AI
            prompt = f"""
You are analyzing a webpage during a job application process. Based on the screenshot and context, determine the SINGLE BEST next action.

CONTEXT:
- URL: {url}
- Page Title: {page_title}
- Has clicked Apply button: {has_clicked_apply}
- Coming from human intervention: {came_from_intervention}

IMPORTANT CONTEXT RULES:
- If coming from human intervention AND has_clicked_apply is False, you're likely back on the job listing after authentication
- If coming from human intervention AND has_clicked_apply is True, you're likely in the middle of an application form
- Look for signs of successful authentication (user profiles, personalized content)
- NEVER choose "application_complete" unless you see explicit success messages like "Application submitted", "Thank you", etc.

POSSIBLE ACTIONS (choose exactly ONE):

1. "find_apply_button" - If this is a job listing page and you need to find/click an Apply button (especially after authentication)
2. "fill_form" - ONLY if there are actual form fields (text inputs, dropdowns, etc.) that need to be filled
3. "handle_iframe" - If there's an iframe that contains the application form
4. "submit_form" - If form is filled and ready for submission (Next/Submit button visible)
5. "application_complete" - ONLY if you see explicit success confirmation messages ("Application submitted", "Thank you for applying", etc.)
6. "navigate_to_next_page" - If you see application start options like "Autofill with Resume", "Apply Manually", or need to click buttons to proceed
7. "need_human_intervention" - If the page requires human attention (captcha, file upload, complex forms) - DO NOT use this for simple chatbots or help widgets

ANALYSIS CRITERIA:
- DISTINGUISH CAREFULLY: "Application start page" vs "Actual form page"
  * Application start page: Shows options like "Autofill with Resume", "Apply Manually", "Use Last Application" → use "navigate_to_next_page"
  * Actual form page: Shows text inputs, dropdowns, checkboxes that need filling → use "fill_form"
- After authentication, you should typically return to the job listing to find the Apply button
- Look for job application forms, apply buttons, user profiles indicating successful login
- Check for popups, overlays, or blocking elements
- Identify if this is a job listing, application form, or confirmation page
- Be VERY conservative about declaring "application_complete" - only if explicit success indicators
- Consider if forms need filling or if submission is ready
- IMPORTANT: Chatbots, help widgets, or AI assistants (like "Electra") are NOT blocking elements - ignore them and focus on the main content
- Only use "need_human_intervention" for actual blockers like CAPTCHAs, broken pages, or authentication failures

Return ONLY a JSON object:
{{
    "action": "one_of_the_above_actions",
    "confidence": 0.0-1.0,
    "reason": "Brief explanation of why this action was chosen",
    "page_type": "job_listing|application_form|auth_page|success_page|other",
    "elements_detected": ["list", "of", "key", "elements", "seen"]
}}
"""
            
            # Use Gemini to analyze the page
            response = await self._analyze_page_with_ai(screenshot, prompt)
            
            return response
            
        except Exception as e:
            logger.error(f"Comprehensive page analysis failed: {e}")
            return {
                "action": "need_human_intervention",
                "confidence": 0.0,
                "reason": f"Page analysis failed: {str(e)}",
                "page_type": "unknown",
                "elements_detected": []
            }

    async def _handle_find_apply_button(self, state: ApplicationState) -> str:
        """Handle finding and clicking apply button."""
        apply_button = await self.apply_detector.detect()
        if apply_button:
            state.context['apply_button'] = apply_button
            return 'click_apply'
        else:
            state.context['human_intervention_reason'] = "Could not find Apply button on job listing page. Please locate and click the Apply button manually."
            return 'human_intervention'

    async def _handle_iframe_switch(self, state: ApplicationState) -> str:
        """Handle switching to iframe context."""
        try:
            frame = await self.iframe_helper.find_actionable_frame()
            if frame:
                logger.info("🧭 Switching context to detected iframe")
                state.context['in_iframe'] = True
                self._set_context(frame)
                return 'ai_guided_navigation'  # Re-analyze in new context
            else:
                state.context['in_iframe'] = False
                self._set_context(self.page)
                return 'ai_guided_navigation'  # Continue with main page
        except Exception as e:
            logger.error(f"Iframe handling failed: {e}")
            return 'ai_guided_navigation'  # Continue anyway

    async def _handle_form_submission_intelligent(self, state: ApplicationState) -> str:
        """Intelligently handle form submission."""
        try:
            # Check for Next or Submit buttons
            next_button = await self.next_button_detector.detect()
            submit_button = await self.submit_detector.detect()
            
            if next_button:
                await next_button.click()
                logger.info("✅ Clicked Next button")
                # Record next button click
                if self.action_recorder:
                    selector = await self._get_element_selector(next_button)
                    self.action_recorder.record_click(selector, "Next button", success=True)
                await self.page.wait_for_load_state("domcontentloaded", timeout=10000)
                # Record navigation after next button
                if self.action_recorder:
                    self.action_recorder.record_navigation(self.page.url, success=True)
                    self.action_recorder.record_wait(2000, "Wait for page load after Next button")
                return 'ai_guided_navigation'  # Re-analyze after navigation

            elif submit_button:
                await submit_button.click()
                logger.info("✅ Clicked Submit button")
                # Record submit button click
                if self.action_recorder:
                    selector = await self._get_element_selector(submit_button)
                    self.action_recorder.record_click(selector, "Submit button", success=True)
                await self.page.wait_for_load_state("domcontentloaded", timeout=10000)
                # Record navigation after submit
                if self.action_recorder:
                    self.action_recorder.record_navigation(self.page.url, success=True)
                    self.action_recorder.record_wait(2000, "Wait for page load after Submit button")
                return 'ai_guided_navigation'  # Re-analyze to check if complete
                
            else:
                state.context['human_intervention_reason'] = "Form appears ready for submission but no Next/Submit button found. Please submit the form manually."
                return 'human_intervention'
                
        except Exception as e:
            logger.error(f"Form submission failed: {e}")
            state.context['human_intervention_reason'] = f"Form submission error: {str(e)}. Please submit manually."
            return 'human_intervention'

    async def _handle_navigation(self, state: ApplicationState, page_analysis: Dict[str, Any]) -> str:
        """Handle navigation to next page based on AI analysis."""
        try:
            reason = page_analysis.get('reason', '').lower()
            
            # Handle application start pages - AVOID AUTOFILL, PREFER MANUAL
            if any(keyword in reason for keyword in ['autofill', 'apply manually', 'start application', 'application start']):
                logger.info("🎯 Detected application start page - prioritizing manual application (avoiding autofill)")
                
                # Strategy 1: PRIORITIZE Manual Application (best for accuracy)
                manual_selectors = [
                    '[data-automation-id*="manual"]', 
                    'button[aria-label*="manual"]', 
                    'text=Apply Manually',
                    'text=Manual Application',
                    'text=Start Application'
                ]
                for selector in manual_selectors:
                    try:
                        element = await self.page.wait_for_selector(selector, timeout=2000)
                        if element and await element.is_visible():
                            logger.info(f"✅ Clicking manual application option (avoiding autofill): {selector}")
                            await element.click()
                            # Record navigation action
                            if self.action_recorder:
                                self.action_recorder.record_click(selector, "Manual application button", success=True)
                            await self.page.wait_for_load_state("domcontentloaded", timeout=10000)
                            # Record page transition
                            if self.action_recorder:
                                self.action_recorder.record_navigation(self.page.url, success=True)
                                self.action_recorder.record_wait(2000, "Wait for page load after manual application")
                            return 'ai_guided_navigation'
                    except:
                        continue
                
                # Strategy 2: If no manual option, upload resume directly and continue
                profile = state.context.get('profile', {})
                resume_path = profile.get('resume_path')
                if resume_path:
                    logger.info("🎯 No manual option found - attempting direct resume upload...")
                    from components.executors.field_interactor import FieldInteractor
                    interactor = FieldInteractor(self.page, self.action_recorder)
                    if await interactor.upload_resume_if_present(resume_path):
                        logger.info("✅ Resume uploaded directly (avoiding autofill)")
                        await self.page.wait_for_timeout(2000)  # Wait for processing
                        
                        # Look for continue/next button
                        continue_selectors = [
                            'button[data-automation-id="pageFooterNextButton"]',  # Workday Continue
                            'text=Continue', 
                            'text=Next', 
                            'button[data-automation-id*="continue"]'
                        ]
                        for continue_selector in continue_selectors:
                            try:
                                continue_btn = await self.page.wait_for_selector(continue_selector, timeout=3000)
                                if continue_btn and await continue_btn.is_visible():
                                    logger.info(f"✅ Clicking continue after direct upload: {continue_selector}")
                                    await continue_btn.click()
                                    await self.page.wait_for_load_state("domcontentloaded", timeout=10000)
                                    return 'ai_guided_navigation'
                            except:
                                continue
                        
                        return 'ai_guided_navigation'
                
                # Strategy 3: LAST RESORT - Only if absolutely no other option exists
                logger.warning("⚠️ No manual application option found - checking if autofill is the only choice")
                autofill_selectors = ['[data-automation-id*="autofill"]', 'button[aria-label*="autofill"]', 'text=Autofill with Resume']
                autofill_found = False
                for selector in autofill_selectors:
                    try:
                        element = await self.page.wait_for_selector(selector, timeout=1000)
                        if element and await element.is_visible():
                            autofill_found = True
                            logger.warning(f"⚠️ Only autofill option available: {selector} - will skip and continue manually")
                            # Just upload resume and continue - don't click autofill
                            break
                    except:
                        continue
                
                if autofill_found:
                    logger.info("🎯 Skipping autofill button - uploading resume manually")
                    if resume_path:
                        from components.executors.field_interactor import FieldInteractor
                        interactor = FieldInteractor(self.page, self.action_recorder)
                        if await interactor.upload_resume_if_present(resume_path):
                            logger.info("✅ Resume uploaded manually (autofill button ignored)")
                        
                    # Look for continue button
                    continue_selectors = ['button[data-automation-id="pageFooterNextButton"]', 'text=Continue', 'text=Next']
                    for continue_selector in continue_selectors:
                        try:
                            continue_btn = await self.page.wait_for_selector(continue_selector, timeout=3000)
                            if continue_btn and await continue_btn.is_visible():
                                logger.info(f"✅ Clicking continue (autofill avoided): {continue_selector}")
                                await continue_btn.click()
                                await self.page.wait_for_load_state("domcontentloaded", timeout=10000)
                                return 'ai_guided_navigation'
                        except:
                            continue
                    
                    return 'ai_guided_navigation'
            
            # Handle direct resume upload if we see upload elements but no autofill button worked
            if any(keyword in reason for keyword in ['upload', 'resume', 'file', 'drop']):
                profile = state.context.get('profile', {})
                resume_path = profile.get('resume_path')
                if resume_path:
                    logger.info("🎯 Attempting direct resume upload...")
                    from components.executors.field_interactor import FieldInteractor
                    interactor = FieldInteractor(self.page, self.action_recorder)
                    if await interactor.upload_resume_if_present(resume_path):
                        logger.info("✅ Resume uploaded directly")
                        await self.page.wait_for_timeout(2000)  # Wait for processing
                        
                        # Look for continue button after upload
                        continue_selectors = [
                            'button[data-automation-id="pageFooterNextButton"]',
                            'text=Continue', 
                            'text=Next'
                        ]
                        for continue_selector in continue_selectors:
                            try:
                                continue_btn = await self.page.wait_for_selector(continue_selector, timeout=3000)
                                if continue_btn and await continue_btn.is_visible():
                                    logger.info(f"✅ Clicking continue after direct upload: {continue_selector}")
                                    await continue_btn.click()
                                    await self.page.wait_for_load_state("domcontentloaded", timeout=10000)
                                    return 'ai_guided_navigation'
                            except:
                                continue
                        
                        return 'ai_guided_navigation'

            # Handle standard next/continue buttons
            if 'next' in reason or 'continue' in reason:
                # Try Workday continue button first
                workday_continue = await self.page.wait_for_selector('button[data-automation-id="pageFooterNextButton"]', timeout=2000)
                if workday_continue and await workday_continue.is_visible():
                    logger.info("✅ Clicking Workday continue button")
                    await workday_continue.click()
                    await self.page.wait_for_load_state("domcontentloaded", timeout=10000)
                    return 'ai_guided_navigation'
                
                # Fallback to generic next button
                next_button = await self.next_button_detector.detect()
                if next_button:
                    await next_button.click()
                    await self.page.wait_for_load_state("domcontentloaded", timeout=10000)
                    return 'ai_guided_navigation'
            
            # If no specific action found, ask for human help
            state.context['human_intervention_reason'] = f"AI suggests navigation but unclear how to proceed: {reason}"
            return 'human_intervention'
            
        except Exception as e:
            logger.error(f"Navigation handling failed: {e}")
            state.context['human_intervention_reason'] = f"Navigation error: {str(e)}. Please proceed manually."
            return 'human_intervention'

    async def _verify_application_success(self, state: ApplicationState) -> bool:
        """Verify that the application was actually successfully submitted."""
        try:
            # Check page content for explicit success indicators
            page_content = await self.page.content()
            page_text = page_content.lower()
            
            # Look for strong success indicators
            strong_success_indicators = [
                "application submitted", "thank you for applying", "application received",
                "we have received your application", "application complete", "successfully submitted",
                "thank you for your interest", "application confirmation", "your application has been received",
                "application sent", "submission successful", "applied successfully"
            ]
            
            success_found = any(indicator in page_text for indicator in strong_success_indicators)
            
            if success_found:
                logger.info("✅ Strong success indicators found in page content")
                return True
            
            # Check URL for success patterns
            url = self.page.url
            success_url_patterns = ['success', 'complete', 'submitted', 'thank', 'confirmation', 'done']
            url_success = any(pattern in url.lower() for pattern in success_url_patterns)
            
            if url_success:
                logger.info("✅ Success patterns found in URL")
                return True
            
            # Check for confirmation numbers or application IDs
            confirmation_patterns = [
                r'application\s*(?:id|number|reference):\s*\w+',
                r'confirmation\s*(?:id|number|code):\s*\w+',
                r'reference\s*(?:id|number):\s*\w+',
                r'tracking\s*(?:id|number):\s*\w+'
            ]
            
            import re
            for pattern in confirmation_patterns:
                if re.search(pattern, page_text):
                    logger.info("✅ Confirmation/tracking number found")
                    return True
            
            logger.warning("⚠️ No strong success indicators found")
            return False
            
        except Exception as e:
            logger.error(f"Error verifying application success: {e}")
            return False

    async def _analyze_page_with_ai(self, screenshot_bytes: bytes, prompt: str) -> Dict[str, Any]:
        """Use AI to analyze page screenshot with custom prompt."""
        try:
            import base64
            import json
            from PIL import Image
            from io import BytesIO
            
            # Convert screenshot to image
            image = Image.open(BytesIO(screenshot_bytes))
            
            # Use the page analyzer's model
            if not self.page_analyzer.model:
                return {
                    "action": "need_human_intervention",
                    "confidence": 0.0,
                    "reason": "AI model not available",
                    "page_type": "unknown",
                    "elements_detected": []
                }
            
            # Generate content with image and prompt
            response = self.page_analyzer.model.generate_content([image, prompt])
            
            # Parse JSON response
            response_text = response.text.strip()
            
            # Clean up the response to extract JSON
            if response_text.startswith('```json'):
                response_text = response_text[7:-3]
            elif response_text.startswith('```'):
                response_text = response_text[3:-3]
            
            try:
                result = json.loads(response_text)
                return result
            except json.JSONDecodeError:
                # Try to find JSON object in the response
                import re
                json_match = re.search(r'\{.*\}', response_text, re.DOTALL)
                if json_match:
                    result = json.loads(json_match.group())
                    return result
                else:
                    raise ValueError("No valid JSON found in response")
                    
        except Exception as e:
            logger.error(f"AI page analysis error: {e}")
            return {
                "action": "need_human_intervention",
                "confidence": 0.0,
                "reason": f"AI analysis failed: {str(e)}",
                "page_type": "unknown",
                "elements_detected": []
            }

    async def _state_detect_blocker(self, state: ApplicationState) -> Optional[str]:
        logger.info(">>> State: DETECT_BLOCKER")
        popup_result = await self.popup_detector.detect()
        if popup_result:
            state.context['blocker'] = popup_result
            return 'resolve_blocker'
        
        # If we have already clicked apply, we are in the form-filling flow.
        if state.context.get('has_clicked_apply'):
            return 'handle_iframe'
            
        return 'find_apply'

    async def _state_resolve_blocker(self, state: ApplicationState) -> str:
        logger.info(">>> State: RESOLVE_BLOCKER")
        blocker_result = state.context.get('blocker')
        if blocker_result:
            success = await PopupExecutor(self.page).execute(blocker_result)
            logger.info(f"Popup resolution {'successful' if success else 'failed'}")
            
            # If automated popup resolution failed, try AI vision fallback
            if not success:
                logger.info("🤖 Automated popup resolution failed. Trying AI vision fallback...")
                try:
                    # Take screenshot and analyze with AI
                    screenshot = await self.page.screenshot()
                    ai_decision = await self._analyze_popup_with_ai(screenshot)
                    
                    if ai_decision and ai_decision.get('action') == 'click_element':
                        # AI suggested clicking a specific element
                        selector = ai_decision.get('selector')
                        if selector:
                            logger.info(f"🎯 AI suggests clicking element: {selector}")
                            try:
                                await self.page.click(selector)
                                success = True
                                logger.info("✅ AI-guided popup resolution successful")
                            except Exception as e:
                                logger.error(f"❌ AI-guided click failed: {e}")
                    
                    elif ai_decision and ai_decision.get('action') == 'human_intervention':
                        logger.warning("🤔 AI cannot resolve popup - requesting human intervention")
                        reason = ai_decision.get('reason', 'Complex popup detected that requires human attention')
                        state.context['human_intervention_reason'] = f"Popup blocking progress: {reason}. Please close the popup and click continue."
                        return 'human_intervention'
                        
                except Exception as e:
                    logger.error(f"❌ AI popup analysis failed: {e}")
            
            # If still unsuccessful after AI attempt, request human help
            if not success:
                logger.warning("⚠️ Could not resolve popup automatically or with AI - requesting human intervention")
                state.context['human_intervention_reason'] = "A popup is blocking progress and cannot be closed automatically. Please close the popup manually and click continue."
                return 'human_intervention'
        else:
            logger.info("No blocker to resolve.")
        
        # After resolving, check where we should go next
        if state.context.get('post_apply_popup'):
            # This was a popup after clicking Apply, continue with validation
            state.context.pop('post_apply_popup', None)  # Remove the flag
            return 'validate_apply'
        else:
            # Regular popup detection flow  
            return 'ai_guided_navigation'

    async def _state_find_apply(self, state: ApplicationState) -> str:
        logger.info(">>> State: FIND_APPLY")
        
        # Priority 1: Look for apply button
        logger.info("🔍 Priority 1: Looking for apply button...")
        detector = ApplyDetector(self.page)
        apply_button = await detector.detect()
        if apply_button:
            state.update_context({'apply_button': apply_button})
            return 'click_apply'
        
        logger.info("❌ No apply button found.")
        
        # Priority 2: Check if this is an authentication page
        logger.info("🔍 Priority 2: Checking for authentication page...")
        auth_result = await self.auth_page_detector.detect()
        if auth_result:
            logger.info(f"🔐 Authentication page detected: {auth_result['type']} (confidence: {auth_result['confidence']:.2f})")
            if auth_result['action'] == 'human_intervention':
                state.context['human_intervention_reason'] = auth_result['reason']
                return 'human_intervention'
            elif auth_result['action'] == 'fill_form':
                # Set flag to indicate we're in an application flow
                state.context['has_clicked_apply'] = True
                return 'handle_iframe'
            elif auth_result['action'] == 'skip':
                logger.info(f"⏭️ {auth_result['reason']}")
                # Continue to next priority (check if already in application form)
                pass
        
        logger.info("❌ No authentication page detected.")
        
        # Priority 3: Check if we're already in a job application form
        logger.info("🔍 Priority 3: Checking if we're already in a job application form...")
        form_result = await self.application_form_detector.detect()
        if form_result:
            logger.info(f"📝 Job application form detected (confidence: {form_result['confidence']:.2f})")
            logger.info(f"Indicators: {', '.join(form_result['indicators'])}")
            # Set flag to indicate we're in an application flow
            state.context['has_clicked_apply'] = True
            return 'handle_iframe'
        
        logger.info("❌ No job application form detected.")
        
        # AI Fallback: Analyze the page when all manual detection fails
        logger.info("🧠 All manual detection failed. Using AI to analyze page type...")
        try:
            ai_analysis = await self.page_analyzer.analyze_page(self.page)
            logger.info(f"🤖 AI Analysis: {ai_analysis['page_type']} (confidence: {ai_analysis['confidence']:.2f})")
            logger.info(f"📋 AI Reason: {ai_analysis['reason']}")
            
            # Handle different page types based on AI analysis
            if ai_analysis['page_type'] == 'JOB_LISTING':
                if ai_analysis.get('apply_button_selector'):
                    logger.info(f"🎯 AI found Apply button selector: {ai_analysis['apply_button_selector']}")
                    # Try to click the AI-suggested button
                    try:
                        button = self.page.locator(ai_analysis['apply_button_selector'])
                        if await button.is_visible():
                            await button.click()
                            logger.info("✅ Successfully clicked AI-suggested Apply button")
                            state.context['has_clicked_apply'] = True
                            return 'validate_apply'
                    except Exception as e:
                        logger.warning(f"⚠️ Failed to click AI-suggested button: {e}")
                
                logger.info("🔄 AI suggests this is a job listing page, but no Apply button found. Proceeding to form filling.")
                return 'handle_iframe'
                
            elif ai_analysis['page_type'] == 'AUTHENTICATION':
                logger.info("🔐 AI detected authentication page. Proceeding with form filling.")
                state.context['has_clicked_apply'] = True
                return 'handle_iframe'
                
            elif ai_analysis['page_type'] == 'APPLICATION_FORM':
                logger.info("📝 AI detected application form. Proceeding with form filling.")
                state.context['has_clicked_apply'] = True
                return 'handle_iframe'
                
            elif ai_analysis['page_type'] == 'ERROR_PAGE':
                logger.error("❌ AI detected error page. Stopping.")
                return 'fail'
                
            elif ai_analysis['page_type'] == 'LOADING_PAGE':
                logger.info("⏳ AI detected loading page. Waiting and retrying...")
                await self.page.wait_for_timeout(3000)
                return 'find_apply'  # Retry
                
            else:
                logger.warning(f"❓ AI detected unknown page type: {ai_analysis['page_type']}")
                return 'fail'
                
        except Exception as e:
            logger.error(f"❌ AI page analysis failed: {e}")
            return 'fail'

    async def _state_click_apply(self, state: ApplicationState) -> Optional[str]:
        logger.info(">>> State: CLICK_APPLY")
        
        # Track action sequence
        if 'action_sequence' not in state.context:
            state.context['action_sequence'] = []
        state.context['action_sequence'].append('click_apply')
        
        apply_button_result = state.context.get('apply_button')
        if not apply_button_result:
            return 'fail'

        await self.nav_validator.capture_initial_state()
        success = await self.click_executor.execute(apply_button_result['element'])
        
        if success:
            # Wait for page to load completely after click
            await self.page.wait_for_load_state("domcontentloaded", timeout=15000)
            await self.page.wait_for_timeout(2000)
            
            # CRITICAL: Check for new tabs first - this is a consequence of the click action
            logger.info("🔍 Checking for new tabs opened by Apply button click...")
            new_page = await self.nav_validator.detect_new_tab()
            if new_page:
                logger.info(f"🆕 Apply button opened new tab. Switching to: {new_page.url}")
                # Switch to the new tab and update our working context
                self.page = new_page
                # Update all components to use the new page
                await self._update_components_for_new_page(new_page)
                logger.info("✅ Successfully switched to new tab for form filling")
            
            # Check for popups that might have appeared after clicking Apply
            logger.info("🔍 Checking for popups after Apply button click...")
            popup_result = await self.popup_detector.detect()
            if popup_result:
                logger.info(f"🚨 Popup detected after Apply click: {popup_result}")
                state.context['blocker'] = popup_result
                state.context['post_apply_popup'] = True  # Flag to know this popup came after Apply
                return 'resolve_blocker'
            
            # Record navigation if URL changed after apply click
            if self.action_recorder:
                current_url = self.page.url
                self.action_recorder.record_navigation(current_url, success=True)
                logger.info(f"🎬 Recorded navigation after apply click: {current_url}")

            # Set the flag to remember we're inside the application now.
            state.context['has_clicked_apply'] = True
            return 'ai_guided_navigation'  # Let AI analyze what to do next
        return 'fail'

    async def _state_validate_apply(self, state: ApplicationState) -> Optional[str]:
        logger.info(">>> State: VALIDATE_APPLY")
        navigated = await NavValidator(self.page).validate()
        if navigated:
            logger.info("✅ Apply click successfully navigated to a new page or state.")
            # Before filling, check for iframe and switch context if needed
            return 'handle_iframe'
        else:
            logger.warning("⚠️ Apply click did not result in a navigation. Failing.")
            # Here we could add logic to try the next best apply button if available
            return 'fail'

    async def _state_handle_iframe(self, state: ApplicationState) -> Optional[str]:
        logger.info(">>> State: HANDLE_IFRAME")
        try:
            # Always try to detect an actionable iframe; it might appear at any step
            frame = await self.iframe_helper.find_actionable_frame()
            if frame:
                logger.info("🧭 Switching context to detected iframe for subsequent actions")
                state.context['in_iframe'] = True
                self._set_context(frame)
            else:
                # Ensure we are on the main page context
                state.context['in_iframe'] = False
                self._set_context(self.page)
        except Exception as e:
            logger.debug(f"Iframe handling encountered an issue: {e}")
            # Fall back to main page context
            state.context['in_iframe'] = False
            self._set_context(self.page)
        return 'fill_form'

    async def _state_fill_form(self, state: ApplicationState) -> Optional[str]:
        logger.info(">>> State: ANALYZE_AND_FILL_FORM")
        
        # CRITICAL: Always check for popups FIRST - they can appear at any stage
        logger.info("🔍 Checking for popups before form filling...")
        popup_result = await self.popup_detector.detect()
        if popup_result:
            logger.info(f"🚨 Popup detected during form filling: {popup_result}")
            state.context['blocker'] = popup_result
            return 'resolve_blocker'
        
        # Track action sequence for pattern detection
        if 'action_sequence' not in state.context:
            state.context['action_sequence'] = []
        state.context['action_sequence'].append('fill_form')
        
        # Enhanced loop protection with completion tracking
        if 'fill_form_count' not in state.context:
            state.context['fill_form_count'] = 0
        state.context['fill_form_count'] += 1
        
        # Get current page URL for tracking
        current_url = self.current_context.url
        
        # Check if we have a completion tracker in the form filler
        if hasattr(self.form_filler, 'completion_tracker'):
            tracker = self.form_filler.completion_tracker
            tracker.set_current_page(current_url)
            
            # Log current progress
            summary = tracker.get_completion_summary()
            logger.info(f"📊 Form Progress - Iteration {state.context['fill_form_count']}: "
                       f"{summary['completed_fields']} fields completed, "
                       f"{summary['successful_attempts']} successful attempts")
            
            # Enhanced loop detection: if we have many iterations but no new completions
            if state.context['fill_form_count'] > 2:
                if summary['completed_fields'] == 0:
                    logger.warning(f"⚠️ No fields completed after {state.context['fill_form_count']} iterations")
                    if state.context['fill_form_count'] > 3:
                        logger.error("🔄 No progress detected! Possible infinite loop. Stopping.")
                        return 'fail'
                elif summary['completed_fields'] > 5 and state.context['fill_form_count'] > 2:
                    logger.info(f"✅ {summary['completed_fields']} fields completed. Form may be done - continuing to next state.")
                    return 'ai_guided_navigation'
        
        if state.context['fill_form_count'] > 10:  # Max 10 iterations (fallback)
            logger.error("🔄 Maximum iterations reached! Too many fill_form attempts. Stopping.")
            return 'fail'
        
        profile = _load_profile_data()
        
        try:
            # Step 1: Check if there are any form fields to fill
            form_fields = await self.form_filler._get_all_form_fields()
            if not form_fields:
                logger.info("📝 No form fields found on the page.")
                # Check if we're coming back from human intervention
                if state.context.get('came_from_human_intervention'):
                    logger.info("🔄 Resuming after human intervention - checking page state before proceeding")
                    state.context.pop('came_from_human_intervention', None)  # Clear the flag
                    return 'ai_analyze_page'  # Let AI analyze what to do next
                else:
                    logger.info("⏭️ Proceeding to form submission logic")
                    return await self._handle_form_submission_with_error_recovery(state, profile)
            
            # Step 2: Attempt to fill fields. This will raise an error for sensitive fields.
            await self.form_filler.fill_form(profile)
        except HumanInterventionRequired as e:
            logger.warning(f"⏸️ Human intervention required: {e}")
            state.context['human_intervention_reason'] = str(e)
            return 'human_intervention'
        except Exception as e:
            logger.error(f"An error occurred during form filling: {e}")
            return 'fail'

        # Step 2.5: Check if form is complete and ready for human review
        if hasattr(self.form_filler, 'completion_tracker'):
            tracker = self.form_filler.completion_tracker
            summary = tracker.get_completion_summary()
            
            # If we've filled a significant number of fields and this is a repeat iteration,
            # the form is likely complete and ready for human submission
            if (summary['completed_fields'] >= 5 and 
                state.context.get('fill_form_count', 0) > 1):
                
                logger.info(f"🎯 Form appears complete: {summary['completed_fields']} fields filled")
                logger.info("👤 Ready for human review and submission")
                
                # Set positive context for human intervention
                state.context['human_intervention_reason'] = f"✅ Form filling complete! Successfully filled {summary['completed_fields']} fields. Please review the form and submit when ready."
                state.context['form_completion_status'] = 'ready_for_submission'
                state.context['keep_browser_open'] = True  # Important: don't close browser
                self.keep_browser_open_for_human = True  # Set class flag for process_link
                return 'human_intervention'

        # Step 3: Check for and fill education/work experience sections
        await self._fill_sections_if_needed(profile)
        
        # Step 4: After form filling, let AI analyze what to do next
        return 'ai_guided_navigation'

    async def _fill_sections_if_needed(self, profile: Dict[str, Any]) -> None:
        """Check for and fill education/work experience sections before proceeding."""
        logger.info("🔍 Checking for education and work experience sections...")
        
        # Check for education section
        education_section = await self.section_detector.detect('education')
        if education_section:
            logger.info(f"🎓 Education section detected: {education_section}")
            education_data = profile.get('education', [])
            if education_data:
                logger.info(f"🎓 Found education section, attempting to fill with {len(education_data)} entries...")
                logger.info(f"🔍 Education data: {education_data}")
                success = await self.section_filler.fill_education_section(education_data, education_section)
                if success:
                    logger.info("✅ Education section filled successfully")
                else:
                    logger.warning("⚠️ Failed to fill education section")
            else:
                logger.warning("⚠️ Education section found but no education data available")
        else:
            logger.info("📝 No education section detected")
        
        # Check for work experience section
        work_section = await self.section_detector.detect('work_experience')
        if work_section:
            logger.info(f"💼 Work experience section detected: {work_section}")
            work_data = profile.get('work_experience', [])
            if work_data:
                logger.info(f"💼 Found work experience section, attempting to fill with {len(work_data)} entries...")
                logger.info(f"🔍 Work data: {work_data}")
                success = await self.section_filler.fill_work_experience_section(work_data, work_section)
                if success:
                    logger.info("✅ Work experience section filled successfully")
                else:
                    logger.warning("⚠️ Failed to fill work experience section")
            else:
                logger.warning("⚠️ Work experience section found but no work data available")
        else:
            logger.info("📝 No work experience section detected")

    async def _handle_form_submission_with_error_recovery(self, state: ApplicationState, profile: Dict[str, Any]) -> Optional[str]:
        """
        Smart form submission with error recovery:
        1. Try to click Next/Submit
        2. If errors appear, use AI to fill missing fields
        3. If still errors, ask human to intervene
        4. Resume after human intervention
        """
        logger.info("🔄 Attempting form submission with error recovery...")
        
        # Step 1: Check for Next or Submit button
        next_button = await self.next_button_detector.detect()
        submit_button = await self.submit_detector.detect()
        
        if not next_button and not submit_button:
            # No buttons found, check for auth page
            auth_result = await self.auth_page_detector.detect()
            if auth_result:
                if auth_result['action'] == 'human_intervention':
                    logger.warning(f"🔐 Authentication page detected: {auth_result['reason']}")
                    state.context['human_intervention_reason'] = auth_result['reason']
                    return 'human_intervention'
                elif auth_result['action'] == 'fill_form':
                    logger.info(f"🔐 Authentication page detected: {auth_result['reason']}")
                    return 'fill_form'
                elif auth_result['action'] == 'skip':
                    logger.info(f"⏭️ {auth_result['reason']}")
                    # No forms to fill, escalate to AI analysis
                    return 'ai_analyze_page'
            
            # No clear action, escalate to AI
            logger.info("No clear next action found. Escalating to AI analysis.")
            return 'ai_analyze_page'
        
        # Step 2: Try to click the button
        button_to_click = next_button or submit_button
        button_type = "Next" if next_button else "Submit"
        
        logger.info(f"🖱️ Clicking {button_type} button...")
        await button_to_click.click()
        
        # Wait for page to load
        await self.page.wait_for_load_state("domcontentloaded", timeout=15000)
        await self.page.wait_for_timeout(2000)
        
        # Step 3: Check for errors on the page
        errors = await self._detect_form_errors()
        
        # Check for repeating action patterns instead of URL changes
        current_action = f"click_{button_type.lower()}"
        if 'action_sequence' not in state.context:
            state.context['action_sequence'] = []
        
        state.context['action_sequence'].append(current_action)
        
        # Keep only last 6 actions for pattern detection
        if len(state.context['action_sequence']) > 6:
            state.context['action_sequence'] = state.context['action_sequence'][-6:]
        
        # Check for repeating patterns
        action_seq = state.context['action_sequence']
        logger.info(f"📊 Current action sequence: {action_seq}")
        
        if len(action_seq) >= 4:
            # Check if last 3 actions are the same
            if len(set(action_seq[-3:])) == 1:
                logger.error(f"🔄 Repeating action pattern detected: {action_seq[-3:]}")
                state.context['human_intervention_reason'] = f"Agent is stuck in a repeating pattern: {action_seq[-3:]}. Likely form validation errors preventing progress. Please review and fix the form manually."
                return 'human_intervention'
            
            # Check for alternating patterns (e.g., click_next -> fill_form -> click_next -> fill_form)
            if len(action_seq) >= 4 and action_seq[-4:] == action_seq[-2:] * 2:
                logger.error(f"🔄 Alternating action pattern detected: {action_seq[-4:]}")
                state.context['human_intervention_reason'] = f"Agent is stuck in an alternating pattern: {action_seq[-4:]}. Likely form validation errors preventing progress. Please review and fix the form manually."
                return 'human_intervention'
            
            # Check for longer repeating cycles (e.g., A->B->C->A->B->C)
            if len(action_seq) >= 6:
                # Check if last 6 actions form a repeating 3-action cycle
                cycle = action_seq[-6:]
                if cycle[:3] == cycle[3:]:
                    logger.error(f"🔄 3-action cycle detected: {cycle}")
                    state.context['human_intervention_reason'] = f"Agent is stuck in a 3-action cycle: {cycle}. Likely form validation errors preventing progress. Please review and fix the form manually."
                    return 'human_intervention'
        
        if errors:
            logger.warning(f"⚠️ Form errors detected: {errors}")
            
            # Try to fill missing fields with AI
            logger.info("🤖 Attempting to fill missing fields with AI...")
            ai_success = await self._fill_missing_fields_with_ai(profile, errors)
            
            if ai_success:
                # Try clicking again
                logger.info("🔄 Retrying form submission after AI fill...")
                next_button = await self.next_button_detector.detect()
                submit_button = await self.submit_detector.detect()
                
                if next_button or submit_button:
                    button_to_click = next_button or submit_button
                    await button_to_click.click()
                    await self.page.wait_for_load_state("domcontentloaded", timeout=15000)
                    await self.page.wait_for_timeout(2000)
                    
                    # Check for errors again
                    errors = await self._detect_form_errors()
                    if errors:
                        logger.warning(f"⚠️ Still errors after AI fill: {errors}")
                        state.context['human_intervention_reason'] = f"Form has errors that AI couldn't resolve: {errors}. Please fill the missing details and click continue."
                        return 'human_intervention'
            
            if not ai_success:
                # AI couldn't help, ask human
                state.context['human_intervention_reason'] = f"Form has errors that need human attention: {errors}. Please fill the missing details and click continue."
                return 'human_intervention'
        
        # Step 4: Determine next action based on button type
        if next_button:
            logger.info("✅ Successfully proceeded to next page.")
            return 'fill_form'  # Continue to next page
        else:
            logger.info("✅ Successfully clicked submit button - analyzing page to determine if application is complete.")
            # Don't assume success - check if we're actually done
            return 'ai_analyze_page'

    async def _detect_form_errors(self) -> List[str]:
        """Detect form validation errors on the page."""
        errors = []
        try:
            # Look for common error indicators
            error_selectors = [
                '.error', '.field-error', '.validation-error', '.invalid',
                '[class*="error"]', '[class*="invalid"]', '.alert-danger',
                '.text-danger', '.error-message', '.form-error', '.input-error',
                '[aria-invalid="true"]', '.has-error', '.is-invalid',
                # Workday specific error selectors
                '[data-automation-id*="error"]', '.css-1hyfx7x[aria-invalid="true"]',
                '.css-1hyfx7x .css-1hyfx7x', '.css-1hyfx7x[class*="error"]'
            ]
            
            for selector in error_selectors:
                error_elements = await self.page.locator(selector).all()
                for element in error_elements:
                    if await element.is_visible():
                        error_text = await element.inner_text()
                        if error_text and error_text.strip():
                            errors.append(error_text.strip())
            
            # Also check for required field indicators that might indicate missing data
            required_fields = await self.page.locator('input[required], select[required], textarea[required]').all()
            for field in required_fields:
                if await field.is_visible():
                    value = await field.input_value()
                    if not value or value.strip() == '':
                        # Get the label or placeholder to identify the field
                        try:
                            field_id = await field.get_attribute('id')
                            if field_id:
                                label = await self.page.locator(f'label[for="{field_id}"]').first.inner_text()
                                if label:
                                    errors.append(f"Required field '{label.strip()}' is empty")
                        except:
                            pass
            
            # Check for any red text or error styling
            red_elements = await self.page.locator('*').all()
            for element in red_elements[:50]:  # Limit to first 50 elements for performance
                try:
                    if await element.is_visible():
                        color = await element.evaluate('el => getComputedStyle(el).color')
                        if 'rgb(220, 53, 69)' in color or 'rgb(255, 0, 0)' in color:  # Red colors
                            text = await element.inner_text()
                            if text and len(text.strip()) > 0 and len(text.strip()) < 100:
                                errors.append(f"Error text: {text.strip()}")
                except:
                    continue
                    
        except Exception as e:
            logger.debug(f"Error detecting form errors: {e}")
        
        # Remove duplicates and filter out empty errors
        unique_errors = list(set([e for e in errors if e and len(e.strip()) > 0]))
        if unique_errors:
            logger.warning(f"🔍 Detected {len(unique_errors)} form errors: {unique_errors}")
        
        return unique_errors

    async def _fill_missing_fields_with_ai(self, profile: Dict[str, Any], errors: List[str]) -> bool:
        """Use AI to fill missing fields based on detected errors."""
        try:
            # This would use the Gemini form brain to analyze errors and fill fields
            # For now, return False to indicate AI couldn't help
            logger.info("AI field filling not yet implemented.")
            return False
        except Exception as e:
            logger.error(f"AI field filling failed: {e}")
            return False

    async def _analyze_popup_with_ai(self, screenshot_bytes: bytes) -> Optional[Dict[str, Any]]:
        """Use AI to analyze popup and suggest resolution"""
        try:
            import base64
            import json
            from google import genai
            
            # Convert screenshot to base64
            screenshot_b64 = base64.b64encode(screenshot_bytes).decode('utf-8')
            
            model = genai.GenerativeModel("gemini-2.0-flash")
            
            prompt = """
            You are analyzing a screenshot of a webpage that has a popup blocking the job application process.
            
            Your task is to determine how to handle this popup. Look for:
            1. Close buttons (X, Close, Cancel, etc.)
            2. Accept/OK buttons if it's a cookie/privacy notice
            3. Skip buttons
            4. Any other way to dismiss the popup
            
            Return a JSON response with one of these actions:
            
            Option 1 - If you can identify a way to close the popup:
            {
                "action": "click_element",
                "selector": "CSS selector or description of element to click",
                "reason": "Brief explanation of what you found"
            }
            
            Option 2 - If the popup is too complex or unclear:
            {
                "action": "human_intervention",
                "reason": "Explanation of why human help is needed"
            }
            
            Be specific about selectors when possible (e.g., 'button[aria-label="Close"]', '.close-btn', etc.).
            """
            
            response = model.generate_content([
                prompt,
                {
                    "mime_type": "image/png", 
                    "data": screenshot_b64
                }
            ])
            
            response_text = response.text.strip()
            if response_text.startswith('```json'):
                response_text = response_text[7:-3]
            elif response_text.startswith('```'):
                response_text = response_text[3:-3]
            
            return json.loads(response_text)
            
        except Exception as e:
            logger.error(f"AI popup analysis failed: {e}")
            return {"action": "human_intervention", "reason": "AI analysis failed"}

    async def _state_ai_analyze_page(self, state: ApplicationState) -> Optional[str]:
        logger.info(">>> State: AI_ANALYZE_PAGE")
        
        # CRITICAL: Always check for popups FIRST - they can appear at any stage
        logger.info("🔍 Step 1: Checking for popups that might be blocking the page...")
        popup_result = await self.popup_detector.detect()
        if popup_result:
            logger.info(f"🚨 Popup detected during page analysis: {popup_result}")
            state.context['blocker'] = popup_result
            return 'resolve_blocker'
        
        # Check if we're on a success/confirmation page
        browsing = self.current_context or self.page
        page_content = await browsing.content()
        url = (self.page.url if self.page else "")
        
        # Look for success indicators
        success_indicators = [
            "application submitted", "thank you for applying", "application received",
            "we have received your application", "application complete", "successfully submitted",
            "thank you for your interest", "application confirmation", "your application has been received"
        ]
        
        page_text = page_content.lower()
        success_found = any(indicator in page_text for indicator in success_indicators)
        
        if success_found:
            logger.info("✅ Found success indicators on the page - application appears complete")
            return 'success'
        
        # Check for more forms or steps
        form_fields = await self.form_filler._get_all_form_fields()
        
        # Count how many fields actually need filling
        empty_fields = []
        for field in form_fields:
            try:
                # Check if field has any value
                element = field.get('element')
                if element and await element.is_visible():
                    value = await element.input_value()
                    if not value or value.strip() == '':
                        empty_fields.append(field)
            except:
                # If we can't check the field, assume it needs filling
                empty_fields.append(field)
        
        if empty_fields:
            logger.info(f"📝 Found {len(empty_fields)} empty form fields - continuing application process")
            return 'fill_form'
        elif form_fields:
            logger.info(f"📝 Found {len(form_fields)} form fields but all appear filled - checking for next step")
            # All fields are filled, look for submit buttons
            next_button = await self.next_button_detector.detect()
            submit_button = await self.submit_detector.detect()
            
            if next_button or submit_button:
                logger.info("🔄 Found next/submit button - continuing to form submission")
                return 'fill_form'
        
        # Check for file upload areas - but only intervene if we don't have resume data
        file_inputs = await self.page.query_selector_all('input[type="file"]')
        if file_inputs:
            profile = state.context.get('profile', {})
            resume_path = profile.get('resume_path')
            if resume_path and os.path.exists(resume_path):
                logger.info("📄 Found file upload fields but we have resume - proceeding to fill form")
                return 'fill_form'
            else:
                logger.info("📄 Found file upload fields but no resume available - requesting human intervention")
                state.context['human_intervention_reason'] = "File upload detected but no resume file available. Please upload your resume/documents and continue."
            return 'human_intervention'
        
        # Check for any buttons that might indicate more steps
        next_button = await self.next_button_detector.detect()
        submit_button = await self.submit_detector.detect()
        
        if next_button or submit_button:
            logger.info("🔄 Found more buttons - continuing process")
            return 'fill_form'
        
        # If no clear indicators, be more conservative about declaring success
        logger.warning("⚠️ Cannot determine page state clearly")
        
        # Check if this might be a success page by URL patterns
        success_url_patterns = ['success', 'complete', 'submitted', 'thank', 'confirmation']
        if any(pattern in url.lower() for pattern in success_url_patterns):
            logger.info("✅ URL suggests successful completion - declaring success")
            return 'success'
        
        # If we can't determine the state, ask for human verification
        logger.warning("🤔 Requesting human verification of application status")
        state.context['human_intervention_reason'] = f"Agent cannot determine if application is complete. Current URL: {url}. Please verify if the application was successfully submitted or if more steps are needed."
        return 'human_intervention'

    async def _state_human_intervention(self, state: ApplicationState) -> Optional[str]:
        """Pauses execution and waits for human input."""
        reason = state.context.get('human_intervention_reason', 'No reason provided.')
        action_seq = state.context.get('action_sequence', [])
        
        logger.critical("="*80)
        print("\n" + "="*50)
        print("📋 HUMAN INTERVENTION REQUIRED")
        print(f"   Reason: {reason}")
        print(f"   Action sequence: {' -> '.join(action_seq[-10:])}")  # Show last 10 actions
        print("   Please complete the required action in the browser.")
        print("   Press Enter in this terminal when you are ready to continue.")
        print("="*50 + "\n")
        
        # Update job status and log the intervention need
        self._update_job_and_session_status('intervention', f"🚨 Human intervention required: {reason}")
        
        try:
            # SAVE ACTION RECORDER before human intervention
            if self.session_manager and self.current_session and self.action_recorder:
                try:
                    logger.info("🎬 Saving action recorder before human intervention...")
                    self.session_manager.stop_action_recording(
                        self.current_session.session_id,
                        save_to_session=True
                    )
                    logger.info("✅ Action history saved successfully")
                except Exception as recorder_error:
                    logger.error(f"Failed to save action recorder: {recorder_error}")

            # FREEZE SESSION before waiting for human intervention
            if self.session_manager and self.current_session:
                try:
                    completion_tracker = None
                    if hasattr(self.form_filler, 'completion_tracker'):
                        completion_tracker = self.form_filler.completion_tracker

                    success = await self.session_manager.freeze_session(
                        self.current_session.session_id,
                        self.page,
                        completion_tracker
                    )
                    if success:
                        logger.info(f"Session {self.current_session.session_id} frozen before human intervention")
                        self._log_to_jobs("info", f"💾 Session saved before human review!")
                        # Mark that session was already frozen to avoid double-freezing
                        self._session_already_frozen = True
                    else:
                        logger.warning(f"Failed to freeze session {self.current_session.session_id} before intervention")
                except Exception as freeze_error:
                    logger.error(f"Error freezing session before intervention: {freeze_error}")
            
            # Notify the frontend about the intervention need
            await self._notify_intervention_needed(reason, action_seq)
            
            # Keep browser open for human intervention
            logger.info("💾 Session state saved! Browser will stay open for manual completion.")
            self._log_to_jobs("info", "✅ Session saved! Browser is open for you to complete the application manually.")

            logger.info("👤 Browser staying open for human intervention")
            self._log_to_jobs("info", "👤 Please complete the application manually. The browser will stay open.")

            # Set flag to keep browser open
            self.keep_browser_open_for_human = True

            # Mark session as ready for manual completion
            if self.session_manager and self.current_session:
                self.session_manager.update_session(
                    self.current_session.session_id, 
                    status="needs_attention"
                )
            
            # Check if we should wait for user input in debug mode
            if self.debug:
                logger.info("🔍 --debug flag detected: waiting for user to complete manual steps...")
                self._log_to_jobs("info", "🐛 Debug mode: Complete manual steps, then press Enter to continue")

                # Wait for user input in debug mode
                try:
                    await asyncio.get_event_loop().run_in_executor(
                        None,
                        input,
                        "Press Enter when you have completed the manual steps and want to continue..."
                    )
                    logger.info("👤 User indicated manual completion finished")
                    self._log_to_jobs("info", "✅ User confirmed manual completion - continuing...")
                    return 'success'  # Continue to success state
                except KeyboardInterrupt:
                    logger.info("👤 User interrupted - ending process")
                    return None
            else:
                # Don't continue with state machine - let user resume manually
                return None  # This will end the state machine
        except Exception as e:
            logger.error(f"Error in human intervention state: {e}")
            logger.warning("Continuing automatically after intervention error...")
            # Continue with the next state instead of failing completely
            return 'ai_guided_navigation'
    
    async def _notify_intervention_needed(self, reason: str, action_sequence: list):
        """Notify the frontend that human intervention is needed"""
        try:
            # Get job_id from context if available
            job_id = getattr(self, 'job_id', None)
            if not job_id:
                logger.warning("No job_id available for intervention notification")
                return
            
            logger.info(f"🔔 Creating intervention notification for job {job_id}")
            logger.info(f"🔔 Reason: {reason}")
            
            # Take a screenshot for context
            try:
                screenshot = await self.page.screenshot()
                screenshot_b64 = base64.b64encode(screenshot).decode('utf-8')
                logger.info(f"📸 Screenshot captured: {len(screenshot_b64)} characters")
            except Exception as screenshot_error:
                logger.error(f"Failed to capture screenshot: {screenshot_error}")
                screenshot_b64 = None
            
            # Store intervention in the global INTERVENTIONS store
            # This will be imported from api_server
            try:
                from api_server import INTERVENTIONS
                
                intervention_data = {
                    "status": "pending",
                    "type": "manual_action_required",
                    "payload": {
                        "message": reason,
                        "action_sequence": action_sequence[-10:],  # Last 10 actions
                        "screenshot": screenshot_b64,
                        "current_url": self.page.url,
                        "timestamp": time.time()
                    }
                }
                
                INTERVENTIONS[job_id] = intervention_data
                
                logger.info(f"📡 Intervention stored in INTERVENTIONS for job {job_id}")
                logger.info(f"📡 INTERVENTIONS keys: {list(INTERVENTIONS.keys())}")
                logger.info(f"📡 Intervention data type: {intervention_data['type']}")
                
            except ImportError as import_error:
                logger.error(f"Failed to import INTERVENTIONS: {import_error}")
            except Exception as store_error:
                logger.error(f"Failed to store intervention: {store_error}")
            
        except Exception as e:
            logger.error(f"Failed to notify intervention: {e}")
            import traceback
            logger.error(f"Traceback: {traceback.format_exc()}")
    
    async def _wait_for_intervention_resolution(self):
        """Wait for intervention to be resolved (either terminal input or frontend)"""
        import asyncio
        import sys
        
        job_id = getattr(self, 'job_id', None)
        
        async def wait_for_terminal():
            """Wait for terminal input with proper error handling"""
            try:
                # Check if stdin is available
                if sys.stdin.isatty():
                    await asyncio.get_event_loop().run_in_executor(None, input)
                    return "terminal"
                else:
                    # If stdin is not available, wait for a reasonable time then timeout
                    logger.warning("Terminal input not available, waiting for frontend resolution only")
                    await asyncio.sleep(300)  # Wait 5 minutes max
                    return "timeout"
            except (EOFError, OSError) as e:
                logger.warning(f"Terminal input not available: {e}")
                await asyncio.sleep(300)  # Wait 5 minutes max
                return "timeout"
        
        async def wait_for_frontend():
            """Wait for frontend resolution"""
            if not job_id or not self.jobs_dict:
                await asyncio.sleep(86400)  # Wait forever if no job_id or jobs_dict
                return None
            
            while True:
                job_data = self.jobs_dict.get(job_id)
                if job_data and job_data.get("status") == "running":
                    # Job was resumed by frontend
                    self._log_to_jobs("info", "✅ Intervention resolved via frontend - resuming job")
                    return "frontend"
                await asyncio.sleep(1)  # Check every second
        
        try:
            # Race between terminal input and frontend resolution
            if job_id:
                done, pending = await asyncio.wait([
                    asyncio.create_task(wait_for_terminal()),
                    asyncio.create_task(wait_for_frontend())
                ], return_when=asyncio.FIRST_COMPLETED)
                
                # Cancel the other task
                for task in pending:
                    task.cancel()
                
                # Get the result
                result = list(done)[0].result()
                logger.info(f"Intervention resolved via: {result}")
                
                # If terminal timed out, continue with frontend only
                if result == "timeout":
                    logger.info("Terminal input timed out, continuing with frontend resolution only")
                    await wait_for_frontend()
            else:
                # No job_id, only wait for terminal
                result = await wait_for_terminal()
                if result == "timeout":
                    logger.warning("No job_id and terminal input timed out, continuing anyway")
                else:
                    logger.info("Intervention resolved via terminal")
                
        except Exception as e:
            logger.error(f"Error waiting for intervention resolution: {e}")
            # If all else fails, just wait a bit and continue
            logger.warning("Falling back to automatic continuation after 30 seconds")
            await asyncio.sleep(30)

    async def _state_success(self, state: ApplicationState) -> Optional[str]:
        logger.info("✅ Application process finished successfully.")
        self._update_job_and_session_status('completed', "🎉 Job application completed successfully!")
        return None

    async def _state_fail(self, state: ApplicationState) -> Optional[str]:
        logger.warning("❌ Application process failed.")
        self._update_job_and_session_status('failed', "❌ Job application failed - unable to complete the process")
        return None

# Load profile data from PostgreSQL database
def _load_profile_data(user_id=None):
    import os
    from agent_profile_service import AgentProfileService

    try:
        # Load from PostgreSQL database
        if user_id:
            profile_data = AgentProfileService.get_profile_by_user_id(user_id)
        else:
            # For backward compatibility, get the latest user's profile
            profile_data = AgentProfileService.get_latest_user_profile()

        if not profile_data:
            logger.error("❌ No profile data found in database")
            logger.warning("🔄 Using fallback profile data...")
            return fallback_profile
        
        # Get current directory for resume path
        current_dir = os.path.dirname(os.path.abspath(__file__))
        project_root = os.path.dirname(current_dir)  # Go up one level from Agents/

        # Map the profile data to our expected format
        mapped_profile = {
            # Personal Information
            'first_name': profile_data.get('first name', ''),
            'last_name': profile_data.get('last name', ''),
            'email': profile_data.get('email', ''),
            'phone': profile_data.get('phone', ''),
            'address': profile_data.get('address', ''),
            'city': profile_data.get('city', ''),
            'state': profile_data.get('state', ''),
            'state_code': profile_data.get('state_code', ''),
            'zip_code': profile_data.get('zip', ''),
            'country': profile_data.get('country', ''),
            'country_code': profile_data.get('country_code', '+1' if profile_data.get('country_code') == 'US' else ''),
            'linkedin': profile_data.get('linkedin', ''),
            'github': profile_data.get('github', ''),
            'resume_path': os.path.join(project_root, 'Resumes', 'Sahil-Chordia-Resume.pdf'),  # Actual resume path

            # Demographic and EEO Information (using actual data when available)
            'gender': profile_data.get('gender', 'Prefer not to say'),
            'nationality': profile_data.get('nationality', ''),
            'date_of_birth': profile_data.get('date of birth', ''),
            'race_ethnicity': profile_data.get('race_ethnicity', 'Prefer not to say'),
            'veteran_status': profile_data.get('veteran status', ''),
            'disability_status': profile_data.get('disabilities', []),
            'preferred_language': profile_data.get('preferred language', ''),

            # Work Authorization (using actual data)
            'work_authorization': profile_data.get('work_authorization', ''),
            'visa_status': profile_data.get('visa status', 'F-1'),
            'require_sponsorship': 'Yes' if profile_data.get('visa sponsorship') == 'Required' else 'No',

            # Additional Information
            'cover_letter': profile_data.get('cover_letter', ''),
            'summary': profile_data.get('summary', ''),
            'salary_expectation': profile_data.get('salary_expectation', ''),
            'availability': profile_data.get('availability', 'Immediately'),
            'willing_to_relocate': profile_data.get('willing to relocate', 'Yes'),
            'preferred_locations': profile_data.get('preferred location', []),
            'other_links': profile_data.get('other links', []),
            'resume_url': profile_data.get('resume_url', ''),

            # Professional Details (derived from work experience if available)
            'years_experience': profile_data.get('years_experience', '2'),
            'current_title': profile_data.get('work experience', [{}])[0].get('title', '') if profile_data.get('work experience') else '',
            'current_company': profile_data.get('work experience', [{}])[0].get('company', '') if profile_data.get('work experience') else '',

            # Skills and Technical Information
            'skills': profile_data.get('skills', {}),
            'programming_languages': profile_data.get('skills', {}).get('programming_languages', []),
            'frameworks': profile_data.get('skills', {}).get('frameworks', []),
            'tools': profile_data.get('skills', {}).get('tools', []),
            'technical_skills': profile_data.get('skills', {}).get('technical', []),

            # Arrays
            'education': profile_data.get('education', []),
            'work_experience': profile_data.get('work experience', []),
            'projects': profile_data.get('projects', [])
        }
        
        logger.info(f"✅ Successfully loaded profile for: {mapped_profile['first_name']} {mapped_profile['last_name']}")
        logger.info(f"📧 Email: {mapped_profile['email']}")
        logger.info(f"📱 Phone: {mapped_profile['phone']}")
        logger.info(f"📄 Resume: {mapped_profile['resume_path']}")
        logger.info(f"🔗 LinkedIn: {mapped_profile.get('linkedin', 'Not provided')}")
        logger.info(f"💻 GitHub: {mapped_profile.get('github', 'Not provided')}")
        
        # Log education data
        education_count = len(mapped_profile.get('education', []))
        logger.info(f"🎓 Education entries: {education_count}")
        for i, edu in enumerate(mapped_profile.get('education', [])[:2]):  # Show first 2
            logger.info(f"   {i+1}. {edu.get('degree', 'N/A')} at {edu.get('institution', 'N/A')}")
        
        # Log work experience data
        work_count = len(mapped_profile.get('work_experience', []))
        logger.info(f"💼 Work experience entries: {work_count}")
        for i, work in enumerate(mapped_profile.get('work_experience', [])[:2]):  # Show first 2
            logger.info(f"   {i+1}. {work.get('title', 'N/A')} at {work.get('company', 'N/A')}")
        
        return mapped_profile
        
    except Exception as e:
        logger.error(f"❌ Failed to load profile data: {e}")
        logger.warning("🔄 Using fallback profile data...")
        return fallback_profile

async def run_links_with_refactored_agent(links: list[str], headless: bool, keep_open: bool, debug: bool, hold_seconds: int, slow_mo_ms: int, job_id: str = None, jobs_dict: dict = None, session_manager: SessionManager = None):
    p = await async_playwright().start()
    try:
        agent = RefactoredJobAgent(p, headless=headless, keep_open=keep_open, debug=debug, hold_seconds=hold_seconds, slow_mo_ms=slow_mo_ms, job_id=job_id, jobs_dict=jobs_dict, session_manager=session_manager)
        for link in links:
            await agent.process_link(link)
    finally:
        # Only stop Playwright if browser is not being kept open for human intervention
        if not hasattr(agent, 'keep_browser_open_for_human') or not agent.keep_browser_open_for_human:
            await p.stop()
        else:
            logger.info("🔒 Keeping Playwright instance alive for human intervention - browser will stay open")

def main():
    import argparse
    
    # Set up file logging for job application agent with DEBUG level to capture everything
    log_file = setup_file_logging(log_level=logging.DEBUG, console_logging=True)
    logger.info(f"Job application agent starting. Logs will be saved to: {log_file}")

    parser = argparse.ArgumentParser(description="Refactored Job Application Agent")
    parser.add_argument("--links", type=str, required=True, help="File path or comma-separated URLs")
    parser.add_argument("--headful", action="store_true", help="Run in headed mode")
    parser.add_argument("--keep-open", action="store_true", help="Keep browser open after run")
    parser.add_argument("--debug", action="store_true", help="Debug mode: wait for Enter during human intervention and keep browser open indefinitely")
    parser.add_argument("--hold-seconds", type=int, default=10, help="Seconds to keep browser open")
    parser.add_argument("--slowmo", type=int, default=0, help="Slow motion in milliseconds")
    args = parser.parse_args()

    links = args.links.split(',') if ',' in args.links else [args.links]

    asyncio.run(run_links_with_refactored_agent(
        links=links,
        headless=not args.headful,
        keep_open=args.keep_open,
        debug=args.debug,
        hold_seconds=args.hold_seconds,
        slow_mo_ms=args.slowmo
    ))

if __name__ == "__main__":
    main()
