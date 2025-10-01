"""
Session Manager for Job Application Agent
Handles session persistence, freezing, and resumption
"""

import json
import time
import uuid
import os
import base64
from datetime import datetime
from typing import Dict, List, Any, Optional
from playwright.async_api import Page, BrowserContext
from loguru import logger
from ..action_recorder import ActionRecorder, ActionReplay


class ApplicationSession:
    """Represents a single job application session"""
    
    def __init__(self, job_url: str, job_title: str = "", company: str = ""):
        self.session_id = str(uuid.uuid4())
        self.job_url = job_url
        self.job_title = job_title
        self.company = company
        self.status = "in_progress"  # in_progress, completed, needs_attention, failed, frozen
        self.created_at = time.time()
        self.last_updated = time.time()
        self.completed_fields = {}
        self.missing_fields = []
        self.errors = []
        self.completion_percentage = 0
        self.screenshot_path = None
        
        # Action-based session management (NEW)
        self.action_history = []  # Store recorded actions instead of browser state
        self.last_successful_step = None
        self.failure_point = None
        
        # Legacy fields (will be phased out)
        self.browser_state = {}
        self.form_data = {}
        
    def to_dict(self) -> Dict[str, Any]:
        """Convert session to dictionary for JSON serialization"""
        return {
            "session_id": self.session_id,
            "job_url": self.job_url,
            "job_title": self.job_title,
            "company": self.company,
            "status": self.status,
            "created_at": self.created_at,
            "last_updated": self.last_updated,
            "completed_fields": self.completed_fields,
            "missing_fields": self.missing_fields,
            "errors": self.errors,
            "completion_percentage": self.completion_percentage,
            "screenshot_path": self.screenshot_path,
            
            # Action-based fields (NEW)
            "action_history": self.action_history,
            "last_successful_step": self.last_successful_step,
            "failure_point": self.failure_point,
            
            # Legacy fields (for backward compatibility)
            "browser_state": self.browser_state,
            "form_data": self.form_data
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'ApplicationSession':
        """Create session from dictionary"""
        session = cls(data["job_url"], data.get("job_title", ""), data.get("company", ""))
        session.session_id = data["session_id"]
        session.status = data.get("status", "in_progress")
        session.created_at = data.get("created_at", time.time())
        session.last_updated = data.get("last_updated", time.time())
        session.completed_fields = data.get("completed_fields", {})
        session.missing_fields = data.get("missing_fields", [])
        session.errors = data.get("errors", [])
        session.completion_percentage = data.get("completion_percentage", 0)
        session.screenshot_path = data.get("screenshot_path")
        
        # Action-based fields (NEW)
        session.action_history = data.get("action_history", [])
        session.last_successful_step = data.get("last_successful_step")
        session.failure_point = data.get("failure_point")
        
        # Legacy fields (for backward compatibility)
        session.browser_state = data.get("browser_state", {})
        session.form_data = data.get("form_data", {})
        return session


class SessionManager:
    """Manages job application sessions with action recording"""
    
    def __init__(self, storage_dir: str = "sessions"):
        self.storage_dir = storage_dir
        self.sessions_file = os.path.join(storage_dir, "sessions.json")
        self.screenshots_dir = os.path.join(storage_dir, "screenshots")
        self.browser_states_dir = os.path.join(storage_dir, "browser_states")
        self.action_logs_dir = os.path.join(storage_dir, "action_logs")  # NEW: Action logs directory
        
        # Action recording
        self.current_action_recorder: Optional[ActionRecorder] = None
        
        # Create directories with full error handling
        try:
            os.makedirs(storage_dir, exist_ok=True)
            os.makedirs(self.screenshots_dir, exist_ok=True)
            os.makedirs(self.browser_states_dir, exist_ok=True)
            os.makedirs(self.action_logs_dir, exist_ok=True)  # NEW: Action logs directory
            logger.info(f"✅ Session storage directories created at: {os.path.abspath(storage_dir)}")
        except Exception as e:
            logger.error(f"❌ Failed to create session directories: {e}")
            raise
        
        self.sessions: Dict[str, ApplicationSession] = {}
        self.load_sessions()
    
    def load_sessions(self):
        """Load sessions from storage"""
        try:
            if os.path.exists(self.sessions_file):
                with open(self.sessions_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    for session_data in data:
                        session = ApplicationSession.from_dict(session_data)
                        self.sessions[session.session_id] = session
                logger.info(f"Loaded {len(self.sessions)} sessions from storage")
        except Exception as e:
            logger.error(f"Failed to load sessions: {e}")
            self.sessions = {}
    
    def save_sessions(self):
        """Save sessions to storage"""
        try:
            data = [session.to_dict() for session in self.sessions.values()]
            with open(self.sessions_file, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            logger.debug(f"Saved {len(self.sessions)} sessions to storage")
        except Exception as e:
            logger.error(f"Failed to save sessions: {e}")
    
    # NEW: Action Recording Methods
    def start_action_recording(self, session_id: str, initial_url: str) -> ActionRecorder:
        """Start recording actions for a session"""
        self.current_action_recorder = ActionRecorder()
        self.current_action_recorder.start_recording(session_id, initial_url)
        logger.info(f"Started action recording for session {session_id}")
        return self.current_action_recorder
    
    def get_action_recorder(self) -> Optional[ActionRecorder]:
        """Get the current action recorder"""
        return self.current_action_recorder
    
    def stop_action_recording(self, session_id: str, save_to_session: bool = True) -> bool:
        """Stop recording and optionally save actions to session"""
        if not self.current_action_recorder:
            logger.warning("No active action recorder to stop")
            return False
        
        try:
            # Ensure action logs directory exists
            os.makedirs(self.action_logs_dir, exist_ok=True)
            
            # Save action log to file
            action_log_file = os.path.join(self.action_logs_dir, f"actions_{session_id}.json")
            action_data = self.current_action_recorder.to_dict()
            
            with open(action_log_file, 'w', encoding='utf-8') as f:
                json.dump(action_data, f, indent=2, ensure_ascii=False)
            
            # Save to session if requested
            if save_to_session and session_id in self.sessions:
                session = self.sessions[session_id]
                session.action_history = action_data["actions"]
                last_successful = self.current_action_recorder.get_last_successful_step()
                session.last_successful_step = last_successful.to_dict() if last_successful else None

                failure_point = self.current_action_recorder.get_failure_point()
                session.failure_point = failure_point.to_dict() if failure_point else None
                session.last_updated = time.time()

                # Debug logging
                logger.info(f"📝 Saved {len(session.action_history)} actions to session {session_id}")
                successful_actions = [a for a in session.action_history if a.get('success', False)]
                logger.info(f"📝 {len(successful_actions)} successful actions saved")

                # Save sessions
                self.save_sessions()
            
            logger.info(f"Stopped action recording for session {session_id}. Actions saved to {action_log_file}")
            self.current_action_recorder = None
            return True
            
        except Exception as e:
            logger.error(f"Failed to stop action recording: {e}")
            return False
    
    async def resume_session_with_actions(self, session_id: str, page: Page) -> bool:
        """Resume a session by replaying recorded actions"""
        try:
            session = self.get_session(session_id)
            if not session:
                logger.error(f"Session {session_id} not found")
                return False
            
            if not session.action_history:
                logger.warning(f"No action history found for session {session_id}")
                logger.info("Checking for saved action log file...")
                # Try to load from action log file as fallback
                action_log_file = os.path.join(self.action_logs_dir, f"actions_{session_id}.json")
                if os.path.exists(action_log_file):
                    logger.info(f"Found action log file: {action_log_file}")
                    try:
                        with open(action_log_file, 'r', encoding='utf-8') as f:
                            action_data = json.load(f)
                        session.action_history = action_data.get("actions", [])
                        logger.info(f"Loaded {len(session.action_history)} actions from log file")
                    except Exception as e:
                        logger.error(f"Failed to load action log file: {e}")

                if not session.action_history:
                    # Fall back to old method if no actions recorded
                    return await self.resume_session(session_id, page)

            logger.info(f"🎬 Resuming session {session_id} using action replay with {len(session.action_history)} actions")
            
            # Convert action history to ActionStep objects
            from ..action_recorder import ActionStep
            actions = [ActionStep.from_dict(action_data) for action_data in session.action_history]
            
            # Create action replay instance
            action_replay = ActionReplay(page)
            
            # Debug: Show action types and success status
            logger.info("🔍 Action history breakdown:")
            action_types = {}
            for action in actions:
                action_type = action.type
                success_status = "✓" if action.success else "✗"
                action_types[action_type] = action_types.get(action_type, 0) + 1
                logger.debug(f"  {success_status} {action_type}: {action.field_label or action.selector or action.url}")

            logger.info(f"Action types: {action_types}")

            # Replay only successful actions up to the failure point
            successful_actions = [action for action in actions if action.success]

            logger.info(f"🎬 Replaying {len(successful_actions)} successful actions (out of {len(actions)} total)")

            # Define progress callback for user feedback
            def progress_callback(current, total, action_type, description):
                logger.info(f"🎬 Replaying action {current}/{total}: {action_type} - {description}")

            success = await action_replay.replay_actions(successful_actions, stop_at_failure=False, progress_callback=progress_callback)
            
            if success:
                logger.info(f"✅ Action replay completed successfully for session {session_id}")
                session.status = "in_progress"
                session.last_updated = time.time()
                self.save_sessions()
                return True
            else:
                logger.error(f"❌ Action replay failed for session {session_id}")
                return False
                
        except Exception as e:
            logger.error(f"Error during action replay for session {session_id}: {e}")
            return False
    
    async def take_screenshot(self, session_id: str, page: Page) -> Optional[str]:
        """Take a screenshot for reference purposes"""
        try:
            screenshot_filename = f"session_{session_id}_{int(time.time())}.png"
            screenshot_path = os.path.join(self.screenshots_dir, screenshot_filename)
            await page.screenshot(path=screenshot_path, full_page=True)
            logger.info(f"Reference screenshot saved: {screenshot_path}")
            return screenshot_path
        except Exception as e:
            logger.error(f"Failed to take screenshot: {e}")
            return None
    
    def create_session(self, job_url: str, job_title: str = "", company: str = "") -> ApplicationSession:
        """Create a new application session"""
        session = ApplicationSession(job_url, job_title, company)
        self.sessions[session.session_id] = session
        self.save_sessions()
        logger.info(f"Created new session {session.session_id} for {job_url}")
        return session
    
    def get_session(self, session_id: str) -> Optional[ApplicationSession]:
        """Get session by ID"""
        return self.sessions.get(session_id)
    
    def update_session(self, session_id: str, **kwargs):
        """Update session data"""
        if session_id in self.sessions:
            session = self.sessions[session_id]
            for key, value in kwargs.items():
                if hasattr(session, key):
                    setattr(session, key, value)
            session.last_updated = time.time()
            self.save_sessions()
            logger.debug(f"Updated session {session_id}")
    
    def get_all_sessions(self) -> List[ApplicationSession]:
        """Get all sessions sorted by creation time (newest first)"""
        return sorted(self.sessions.values(), key=lambda s: s.created_at, reverse=True)
    
    def get_sessions_by_status(self, status: str) -> List[ApplicationSession]:
        """Get sessions filtered by status"""
        return [s for s in self.sessions.values() if s.status == status]
    
    async def freeze_session(self, session_id: str, page: Page, completion_tracker=None) -> bool:
        """Freeze a session by saving its current state"""
        try:
            session = self.get_session(session_id)
            if not session:
                logger.error(f"Session {session_id} not found")
                return False
            
            # Take screenshot
            screenshot_filename = f"session_{session_id}_{int(time.time())}.png"
            screenshot_path = os.path.join(self.screenshots_dir, screenshot_filename)
            await page.screenshot(path=screenshot_path, full_page=True)
            session.screenshot_path = screenshot_path
            logger.info(f"Screenshot saved: {screenshot_path}")
            
            # Save browser state
            try:
                # Get cookies
                cookies = await page.context.cookies()
                
                # Get local storage and session storage
                local_storage = await page.evaluate("() => JSON.stringify(localStorage)")
                session_storage = await page.evaluate("() => JSON.stringify(sessionStorage)")
                
                # Get current URL and page state
                current_url = page.url
                page_content = await page.content()
                
                browser_state = {
                    "cookies": cookies,
                    "local_storage": local_storage,
                    "session_storage": session_storage,
                    "current_url": current_url,
                    "page_content": page_content,
                    "viewport": page.viewport_size,
                    "user_agent": await page.evaluate("() => navigator.userAgent")
                }
                
                # Save browser state to file
                state_filename = f"state_{session_id}.json"
                state_path = os.path.join(self.browser_states_dir, state_filename)
                with open(state_path, 'w', encoding='utf-8') as f:
                    json.dump(browser_state, f, indent=2, ensure_ascii=False)
                
                session.browser_state = {"state_file": state_path}
                logger.info(f"Browser state saved: {state_path}")
                
            except Exception as e:
                logger.warning(f"Failed to save browser state: {e}")
            
            # Get completion data from tracker
            if completion_tracker:
                summary = completion_tracker.get_completion_summary()
                session.completed_fields = summary.get('completed_field_details', {})
                
                # Calculate completion percentage more accurately
                total_attempted = summary.get('successful_attempts', 0)
                total_completed = len(session.completed_fields)
                
                # Consider both AI-filled and pattern-filled fields
                if total_attempted > 0:
                    session.completion_percentage = min(100, (total_completed / max(1, total_attempted)) * 100)
                else:
                    session.completion_percentage = 0
                
                logger.debug(f"Completion calculation: {total_completed} completed / {total_attempted} attempted = {session.completion_percentage}%")
            
            # Update session status based on page context and completion
            current_url = page.url
            page_title = await page.title()
            
            # Check if we're on authentication/login page
            auth_indicators = ['login', 'signin', 'sign-in', 'auth', 'password', 'credentials']
            is_auth_page = any(indicator in current_url.lower() or indicator in page_title.lower() 
                             for indicator in auth_indicators)
            
            # Check if we're still on job listing page (not application form)
            job_listing_indicators = ['jobs', 'careers', 'apply', 'posting']
            form_indicators = ['application', 'form', 'greenhouse', 'workday', 'lever']
            
            is_job_listing = any(indicator in current_url.lower() for indicator in job_listing_indicators)
            is_application_form = any(indicator in current_url.lower() for indicator in form_indicators)
            
            if is_auth_page:
                session.status = "requires_authentication"
                session.completion_percentage = 0  # Don't count auth fields as completion
                logger.info(f"Session {session.session_id} requires authentication")
            elif not is_application_form and is_job_listing:
                session.status = "navigation_required"
                session.completion_percentage = 0  # Haven't reached the form yet
                logger.info(f"Session {session.session_id} needs navigation to application form")
            elif session.completion_percentage >= 90:
                # Don't override if already set to needs_attention (human intervention required)
                if session.status != "needs_attention":
                    session.status = "completed"
            elif session.completion_percentage >= 50:
                session.status = "needs_attention"
            elif session.completion_percentage > 0:
                session.status = "partially_completed"
            else:
                session.status = "frozen"
            
            session.last_updated = time.time()
            self.save_sessions()
            
            logger.info(f"Session {session_id} frozen successfully with {session.completion_percentage}% completion")
            return True
            
        except Exception as e:
            logger.error(f"Failed to freeze session {session_id}: {e}")
            return False
    
    async def resume_session(self, session_id: str, page: Page) -> bool:
        """Resume a frozen session"""
        try:
            session = self.get_session(session_id)
            if not session:
                logger.error(f"Session {session_id} not found")
                return False
            
            # Load browser state
            state_file = session.browser_state.get("state_file")
            if state_file and os.path.exists(state_file):
                with open(state_file, 'r', encoding='utf-8') as f:
                    browser_state = json.load(f)
                
                # Enhanced authentication restoration process
                logger.info("Starting enhanced authentication restoration...")

                # Step 1: Restore cookies BEFORE navigation
                if browser_state.get("cookies"):
                    logger.info(f"Restoring {len(browser_state['cookies'])} cookies")
                    try:
                        await page.context.add_cookies(browser_state["cookies"])
                        logger.info("✓ Cookies restored successfully")
                    except Exception as e:
                        logger.warning(f"Failed to restore cookies: {e}")

                # Step 2: Navigate to the saved URL FIRST (must be on correct origin for storage)
                if browser_state.get("current_url"):
                    url = browser_state["current_url"]
                    logger.info(f"Navigating to saved URL: {url}")

                    try:
                        # Navigate with authentication-friendly options
                        await page.goto(url, wait_until="domcontentloaded", timeout=30000)
                        logger.info("✓ Page navigation completed")

                        # Wait for page to be ready for storage operations
                        await page.wait_for_load_state("domcontentloaded")
                        await page.wait_for_timeout(1000)

                        # Step 3: Restore storage AFTER navigation (now on correct origin)
                        if browser_state.get("local_storage"):
                            logger.info("Restoring localStorage")
                            try:
                                await page.evaluate(f"() => {{ localStorage.clear(); Object.assign(localStorage, {browser_state['local_storage']}); }}")
                                logger.info("✓ localStorage restored")
                            except Exception as e:
                                logger.warning(f"Failed to restore localStorage: {e}")

                        if browser_state.get("session_storage"):
                            logger.info("Restoring sessionStorage")
                            try:
                                await page.evaluate(f"() => {{ sessionStorage.clear(); Object.assign(sessionStorage, {browser_state['session_storage']}); }}")
                                logger.info("✓ sessionStorage restored")
                            except Exception as e:
                                logger.warning(f"Failed to restore sessionStorage: {e}")

                        # Wait for any authentication checks to complete
                        await page.wait_for_timeout(2000)

                        # Check if we're on a login/auth page and handle accordingly
                        current_url = page.url
                        if "login" in current_url.lower() or "auth" in current_url.lower() or "sign" in current_url.lower():
                            logger.warning(f"Detected authentication page: {current_url}")
                            logger.warning("User may need to re-authenticate manually")
                            # Don't proceed with form restoration if we're on auth page
                            return True

                    except Exception as e:
                        logger.error(f"Failed to navigate to saved URL: {e}")
                        return False

                # Step 4: Extended wait for dynamic content and authentication
                logger.info("Waiting for page to fully settle and authentication to complete...")
                await page.wait_for_timeout(2000)
                
                # Try multiple restoration strategies
                await self._restore_form_fields_comprehensive(page, session, browser_state)
                
                logger.info(f"Session {session_id} resumed successfully")
                session.status = "in_progress"
                session.last_updated = time.time()
                self.save_sessions()
                return True
            else:
                logger.warning(f"No browser state found for session {session_id}")
                return False
                
        except Exception as e:
            logger.error(f"Failed to resume session {session_id}: {e}")
            return False
    
    async def _restore_form_fields_comprehensive(self, page: Page, session: ApplicationSession, browser_state: dict):
        """Comprehensive form field restoration using multiple strategies"""
        try:
            logger.info("Starting comprehensive form field restoration")
            
            # Strategy 1: Try to trigger form auto-fill from browser state
            await self._trigger_browser_autofill(page)
            
            # Strategy 2: Restore from completed fields data
            if session.completed_fields:
                await self._restore_from_completed_fields(page, session)
            
            # Strategy 3: Try to extract and restore from saved page content
            if browser_state.get("page_content"):
                await self._restore_from_page_content(page, browser_state["page_content"])
            
            logger.info("Comprehensive form field restoration completed")
            
        except Exception as e:
            logger.error(f"Error during comprehensive form field restoration: {e}")
    
    async def _trigger_browser_autofill(self, page: Page):
        """Try to trigger browser's built-in autofill"""
        try:
            # Look for common form fields and trigger autofill
            email_fields = await page.locator('input[type="email"], input[name*="email"], input[id*="email"]').all()
            for field in email_fields:
                if await field.is_visible():
                    await field.click()
                    await field.press('Tab')  # This often triggers autofill
                    await page.wait_for_timeout(500)
                    break
                    
            # Trigger autofill on name fields
            name_fields = await page.locator('input[name*="name"], input[id*="name"], input[placeholder*="name"]').all()
            for field in name_fields:
                if await field.is_visible():
                    await field.click()
                    await field.press('Tab')
                    await page.wait_for_timeout(500)
                    break
                    
        except Exception as e:
            logger.debug(f"Browser autofill trigger failed: {e}")
    
    async def _restore_from_completed_fields(self, page: Page, session: ApplicationSession):
        """Restore form fields from session completed_fields data"""
        try:
            logger.info(f"Restoring {len(session.completed_fields)} completed fields")
            
            for field_id, field_data in session.completed_fields.items():
                try:
                    field_value = field_data.get('value')
                    field_type = field_data.get('field_type', 'text')
                    field_label = field_data.get('label', '')
                    
                    if not field_value or field_value in ['unchecked', 'false']:
                        continue
                    
                    # Multiple selector strategies
                    element = None
                    
                    if field_id.startswith('id:'):
                        element_id = field_id[3:]
                        # Try attribute selector for complex IDs (safer)
                        element = page.locator(f'[id="{element_id}"]').first
                    elif field_id.startswith('name:'):
                        name = field_id[5:]
                        element = page.locator(f'[name="{name}"]').first
                    
                    # If element found, restore the value
                    if element:
                        try:
                            if await element.is_visible():
                                if field_type in ['radio', 'checkbox']:
                                    if field_value in ['checked', 'true', 'yes']:
                                        await element.check()
                                        logger.debug(f"✓ Restored {field_type}: {field_label}")
                                elif field_type in ['text_input', 'email_input', 'tel_input', 'text']:
                                    await element.fill(str(field_value))
                                    logger.debug(f"✓ Restored text: {field_label} = {str(field_value)}")
                                elif 'dropdown' in field_type:
                                    try:
                                        await element.select_option(str(field_value))
                                        logger.debug(f"✓ Restored dropdown: {field_label} = {field_value}")
                                    except:
                                        # Alternative dropdown handling
                                        await element.click()
                                        await page.wait_for_timeout(300)
                                        option = page.locator(f'text="{field_value}"').first
                                        if await option.is_visible():
                                            await option.click()
                            else:
                                logger.debug(f"Element not visible: {field_label}")
                        except Exception as elem_error:
                            logger.debug(f"Failed to interact with element {field_label}: {elem_error}")
                        
                except Exception as field_error:
                    logger.warning(f"Failed to restore field {field_id}: {field_error}")
                    
        except Exception as e:
            logger.error(f"Error restoring from completed fields: {e}")
    
    async def _restore_from_page_content(self, page: Page, saved_content: str):
        """Extract and restore form values from saved page content"""
        try:
            # This is a more advanced approach - extract form values from the saved HTML
            # and try to restore them to the current page
            
            import re
            
            # Extract email values from saved content
            email_matches = re.findall(r'value="([^"]*@[^"]*)"', saved_content)
            if email_matches:
                email_value = email_matches[0]
                email_fields = await page.locator('input[type="email"], input[name*="email"]').all()
                for field in email_fields:
                    if await field.is_visible() and not await field.input_value():
                        await field.fill(email_value)
                        logger.debug(f"✓ Restored email from page content: {email_value}")
                        break
            
            # Extract phone numbers
            phone_matches = re.findall(r'value="(\(\d{3}\)\s?\d{3}-\d{4}|\d{10}|\+\d[\d\s\-\(\)]+)"', saved_content)
            if phone_matches:
                phone_value = phone_matches[0]
                phone_fields = await page.locator('input[type="tel"], input[name*="phone"]').all()
                for field in phone_fields:
                    if await field.is_visible() and not await field.input_value():
                        await field.fill(phone_value)
                        logger.debug(f"✓ Restored phone from page content: {phone_value}")
                        break
            
            # Extract name values
            name_matches = re.findall(r'value="([A-Z][a-z]+\s+[A-Z][a-z]+)"', saved_content)
            if name_matches:
                name_value = name_matches[0]
                name_fields = await page.locator('input[name*="name"], input[placeholder*="Name"]').all()
                for field in name_fields:
                    if await field.is_visible() and not await field.input_value():
                        await field.fill(name_value)
                        logger.debug(f"✓ Restored name from page content: {name_value}")
                        break
                        
        except Exception as e:
            logger.debug(f"Page content restoration failed: {e}")
    
    def delete_session(self, session_id: str) -> bool:
        """Delete a session and its associated files"""
        try:
            session = self.get_session(session_id)
            if not session:
                return False
            
            # Delete screenshot
            if session.screenshot_path and os.path.exists(session.screenshot_path):
                os.remove(session.screenshot_path)
            
            # Delete browser state file
            state_file = session.browser_state.get("state_file")
            if state_file and os.path.exists(state_file):
                os.remove(state_file)
            
            # Remove from memory and save
            del self.sessions[session_id]
            self.save_sessions()
            
            logger.info(f"Session {session_id} deleted successfully")
            return True
            
        except Exception as e:
            logger.error(f"Failed to delete session {session_id}: {e}")
            return False
    
    def get_dashboard_data(self) -> Dict[str, Any]:
        """Get dashboard summary data"""
        sessions = self.get_all_sessions()
        
        status_counts = {
            "completed": len([s for s in sessions if s.status == "completed"]),
            "needs_attention": len([s for s in sessions if s.status == "needs_attention"]),
            "in_progress": len([s for s in sessions if s.status == "in_progress"]),
            "frozen": len([s for s in sessions if s.status == "frozen"]),
            "failed": len([s for s in sessions if s.status == "failed"])
        }
        
        return {
            "total_sessions": len(sessions),
            "status_counts": status_counts,
            "sessions": [s.to_dict() for s in sessions]
        }
