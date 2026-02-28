"""
Handler for automatically filling account creation forms
Generates and saves credentials for reuse
"""
import asyncio
from typing import Optional, Dict, Any
from playwright.async_api import Page
from loguru import logger

from components.detectors.account_creation_detector import AccountCreationDetector
from components.services.company_credentials_service import CompanyCredentialsService, PasswordGenerator


class AccountCreationHandler:
    """
    Handles account creation on ATS platforms (especially Workday)
    """
    
    def __init__(self, page: Page, user_id: str):
        """
        Initialize the handler
        
        Args:
            page: Playwright page object
            user_id: UUID of the current user
        """
        self.page = page
        self.detector = AccountCreationDetector(page)
        self.credentials_service = CompanyCredentialsService()  # Creates its own DB session
        self.user_id = user_id
    
    def __del__(self):
        """Clean up database session"""
        if hasattr(self, 'credentials_service'):
            self.credentials_service.close()
    
    async def handle_account_creation(self, user_email: str) -> Dict[str, Any]:
        """
        Detect and handle account creation if on an account creation page
        
        Args:
            user_email: The user's email address to use for the account
        
        Returns:
            Dict with 'handled' (bool), 'success' (bool), and 'message' (str)
        """
        result = {
            'handled': False,
            'success': False,
            'message': '',
            'credentials': None
        }
        
        try:
            # Check if this is an account creation page
            is_creation_page = await self.detector.is_account_creation_page()
            
            if not is_creation_page:
                logger.debug("Not an account creation page, skipping auto-fill")
                return result
            
            result['handled'] = True
            logger.info("🔐 Detected account creation page - initiating auto-fill")
            
            # Get company info
            company_info = await self.detector.get_company_info_from_page()
            company_name = company_info.get('company_name', 'Unknown Company')
            company_domain = company_info.get('company_domain', '')
            
            if not company_domain:
                result['message'] = "Could not determine company domain"
                logger.error(result['message'])
                return result
            
            # Check if we already have credentials for this company
            existing_credentials = self.credentials_service.get_credentials(
                user_id=self.user_id,
                company_domain=company_domain
            )
            
            if existing_credentials:
                logger.info(f"✅ Found existing credentials for {company_name}")
                password = existing_credentials['password']
                email = existing_credentials['email']
                result['message'] = f"Using existing credentials for {company_name}"
            else:
                # Generate new password
                logger.info(f"🔐 Generating new credentials for {company_name}")
                password = self.credentials_service.generate_and_save_credentials(
                    user_id=self.user_id,
                    company_name=company_name,
                    company_domain=company_domain,
                    email=user_email,
                    ats_type='workday'
                )
                
                if not password:
                    result['message'] = "Failed to generate password"
                    logger.error(result['message'])
                    return result
                
                email = user_email
                result['message'] = f"Generated new credentials for {company_name}"
            
            # Get account creation fields
            fields = await self.detector.get_account_creation_fields()
            
            # Fill the form
            fill_success = await self._fill_account_creation_form(
                fields=fields,
                email=email,
                password=password
            )
            
            if fill_success:
                # Click the "Create Account" button
                button_clicked = await self._click_create_account_button()
                
                if button_clicked:
                    result['success'] = True
                    result['credentials'] = {
                        'email': email,
                        'password': password,
                        'company_name': company_name,
                        'company_domain': company_domain
                    }
                    logger.info(f"✅ Successfully created account for {company_name}")
                else:
                    result['success'] = False
                    result['message'] = "Filled form but failed to click Create Account button"
                    logger.warning(result['message'])
            else:
                result['message'] = "Failed to fill account creation form"
                logger.error(result['message'])
        
        except Exception as e:
            result['message'] = f"Error handling account creation: {str(e)}"
            logger.error(result['message'])
            import traceback
            logger.debug(traceback.format_exc())
        
        return result
    
    async def _fill_account_creation_form(
        self,
        fields: Dict[str, Any],
        email: str,
        password: str
    ) -> bool:
        """
        Fill the account creation form with generated credentials
        
        Args:
            fields: Dict of field locators from detector
            email: Email to fill
            password: Password to fill
        
        Returns:
            True if successful, False otherwise
        """
        try:
            # Fill email field
            if fields.get('email'):
                logger.info("📧 Filling email field")
                await fields['email'].fill(email)
                await asyncio.sleep(0.3)
            else:
                # Guardrail: if we cannot find email, this is likely a sign-in form, not account creation.
                logger.warning("⚠️ Email field not found - refusing to submit as account creation")
                return False
            
            # Fill password field
            if fields.get('password'):
                logger.info("🔒 Filling password field")
                await fields['password'].fill(password)
                await asyncio.sleep(0.3)
            else:
                logger.error("❌ Password field not found")
                return False
            
            # Fill confirm password field
            if fields.get('confirm_password'):
                logger.info("🔒 Filling confirm password field")
                await fields['confirm_password'].fill(password)
                await asyncio.sleep(0.3)
            else:
                logger.warning("⚠️ Confirm password field not found")
            
            # Check agreement checkbox if present
            if fields.get('checkbox'):
                try:
                    is_checked = await fields['checkbox'].is_checked()
                    if not is_checked:
                        logger.info("☑️ Checking agreement checkbox")
                        await fields['checkbox'].check()
                        await asyncio.sleep(0.2)
                except Exception as e:
                    logger.warning(f"Could not check checkbox: {e}")
            
            logger.info("✅ Account creation form filled successfully")
            return True
        
        except Exception as e:
            logger.error(f"Error filling account creation form: {e}")
            import traceback
            logger.debug(traceback.format_exc())
            return False
    
    async def _click_create_account_button(self) -> bool:
        """
        Click the "Create Account" button after filling the form
        
        Returns:
            True if button was clicked successfully, False otherwise
        """
        try:
            logger.info("🔘 Looking for Create Account button...")
            
            # Try multiple selectors for "Create Account" button
            # Workday uses <div role="button"> instead of <button>
            selectors = [
                # Workday-specific (div with role="button")
                '[data-automation-id="click_filter"][aria-label*="Create Account"]',
                'div[role="button"][aria-label*="Create Account"]',
                '[role="button"][aria-label*="Create Account"]',
                
                # Traditional button elements
                'button[data-automation-id="createAccountSubmitButton"]',
                'button:has-text("Create Account")',
                'button:has-text("Sign Up")',
                'button[type="submit"]:has-text("Create")',
                
                # Fallback to any clickable element with explicit create-account text
                '[role="button"]:has-text("Create Account")'
            ]
            
            for selector in selectors:
                try:
                    button = self.page.locator(selector).first
                    
                    # Quick check if button exists and is visible (no long timeout)
                    if await button.count() > 0:
                        # Try to check visibility with short timeout
                        try:
                            is_visible = await button.is_visible()
                            if not is_visible:
                                logger.debug(f"Button found with '{selector}' but not visible, trying next...")
                                continue
                        except Exception:
                            logger.debug(f"Visibility check failed for '{selector}', trying next...")
                            continue
                        
                        # Button found and visible - click it immediately!
                        logger.info(f"✅ Found Create Account button: {selector}")
                        logger.info("🖱️ Clicking Create Account button NOW...")
                        
                        await button.click(timeout=5000)  # 5 second timeout for click
                        logger.info("✅ Create Account button clicked successfully!")
                        
                        await asyncio.sleep(2)  # Wait for page to process
                        return True
                        
                except Exception as e:
                    logger.debug(f"Selector '{selector}' failed: {str(e)[:100]}")
                    continue
            
            logger.warning("⚠️ Could not find or click Create Account button with any selector")
            return False
        
        except Exception as e:
            logger.error(f"Error in Create Account button handler: {e}")
            import traceback
            logger.debug(traceback.format_exc())
            return False
    
    async def handle_login_with_saved_credentials(self) -> Dict[str, Any]:
        """
        Detect and handle login page using saved credentials
        
        Returns:
            Dict with 'handled' (bool), 'success' (bool), and 'message' (str)
        """
        result = {
            'handled': False,
            'success': False,
            'message': '',
            'credentials': None
        }
        
        try:
            # Check if this is a login page
            is_login_page = await self._is_login_page()
            
            if not is_login_page:
                logger.debug("Not a login page, skipping auto-login")
                return result
            
            result['handled'] = True
            logger.info("🔑 Detected login page - checking for saved credentials")
            
            # Get company info from URL
            company_info = await self.detector.get_company_info_from_page()
            company_domain = company_info.get('company_domain', '')
            company_name = company_info.get('company_name', 'Unknown Company')
            
            if not company_domain:
                result['message'] = "Could not determine company domain"
                logger.warning(result['message'])
                return result
            
            # Get saved credentials
            credentials = self.credentials_service.get_credentials(
                user_id=self.user_id,
                company_domain=company_domain
            )
            
            if not credentials:
                result['message'] = f"No saved credentials found for {company_name}"
                logger.info(result['message'])
                return result
            
            logger.info(f"✅ Found saved credentials for {company_name}")
            
            # Fill login form
            login_success = await self._fill_login_form(
                email=credentials['email'],
                password=credentials['password']
            )
            
            if login_success:
                result['success'] = True
                result['credentials'] = credentials
                result['message'] = f"Logged in with saved credentials for {company_name}"
                logger.info(f"✅ {result['message']}")
            else:
                result['message'] = f"Failed to fill login form for {company_name}"
                logger.error(result['message'])
        
        except Exception as e:
            result['message'] = f"Error handling login: {str(e)}"
            logger.error(result['message'])
            import traceback
            logger.debug(traceback.format_exc())
        
        return result
    
    async def _is_login_page(self) -> bool:
        """
        Check if current page is a login page (not account creation)
        
        Returns:
            True if this is a login page, False otherwise
        """
        try:
            page_text = await self.page.text_content('body')
            if not page_text:
                return False
            
            page_text_lower = page_text.lower()
            url = self.page.url.lower()
            
            # Check URL for login indicators
            if '/login' in url or 'signin' in url:
                logger.debug("URL contains login/signin")
                
                # Make sure it's not account creation
                creation_keywords = ['create account', 'sign up', 'register', 'new account']
                if not any(keyword in page_text_lower for keyword in creation_keywords):
                    logger.debug("No account creation keywords found - confirmed login page")
                    return True
            
            # Check for login-specific keywords without account creation
            login_keywords = ['sign in', 'log in', 'email address', 'password', 'forgot password']
            creation_keywords = ['create account', 'sign up', 'verify password', 'password requirements']
            
            login_count = sum(1 for kw in login_keywords if kw in page_text_lower)
            creation_count = sum(1 for kw in creation_keywords if kw in page_text_lower)
            
            # More login keywords than creation = login page
            if login_count >= 3 and creation_count == 0:
                logger.debug(f"Login page detected (login:{login_count}, creation:{creation_count})")
                return True
            
            return False
        
        except Exception as e:
            logger.debug(f"Error detecting login page: {e}")
            return False
    
    async def _fill_login_form(self, email: str, password: str) -> bool:
        """
        Fill login form with saved credentials
        
        Args:
            email: Email to fill
            password: Password to fill
        
        Returns:
            True if successful, False otherwise
        """
        try:
            logger.info("📧 Filling login email field...")
            
            # Find email field
            email_selectors = [
                'input[type="email"]',
                'input[data-automation-id="email"]',
                'input[name*="email"]',
                'input[id*="email"]',
                'input[type="text"]'  # Sometimes email is just text input
            ]
            
            email_filled = False
            for selector in email_selectors:
                try:
                    field = self.page.locator(selector).first
                    if await field.count() > 0 and await field.is_visible():
                        await field.fill(email)
                        email_filled = True
                        logger.info(f"✅ Email filled: {email}")
                        break
                except Exception:
                    continue
            
            if not email_filled:
                logger.error("❌ Could not find email field")
                return False
            
            await asyncio.sleep(0.3)
            
            # Find password field
            logger.info("🔒 Filling login password field...")
            password_field = self.page.locator('input[type="password"]').first
            
            if await password_field.count() > 0 and await password_field.is_visible():
                await password_field.fill(password)
                logger.info("✅ Password filled")
            else:
                logger.error("❌ Could not find password field")
                return False
            
            await asyncio.sleep(0.5)
            
            # Click sign in button
            logger.info("🔘 Looking for Sign In button...")
            sign_in_selectors = [
                '[data-automation-id*="signIn"]',
                'button:has-text("Sign In")',
                'button:has-text("Log In")',
                'button[type="submit"]:has-text("Sign")',
                '[role="button"]:has-text("Sign In")',
                'button[type="submit"]'
            ]
            
            for selector in sign_in_selectors:
                try:
                    button = self.page.locator(selector).first
                    if await button.count() > 0:
                        is_visible = await button.is_visible()
                        if is_visible:
                            logger.info(f"✅ Found Sign In button: {selector}")
                            logger.info("🖱️ Clicking Sign In button...")
                            await button.click(timeout=5000)
                            logger.info("✅ Sign In button clicked successfully!")
                            await asyncio.sleep(2)
                            return True
                except Exception as e:
                    logger.debug(f"Selector '{selector}' failed: {str(e)[:100]}")
                    continue
            
            logger.warning("⚠️ Could not find Sign In button")
            return False
        
        except Exception as e:
            logger.error(f"Error filling login form: {e}")
            import traceback
            logger.debug(traceback.format_exc())
            return False
    
    async def try_login_with_saved_credentials(self, company_domain: str) -> bool:
        """
        Try to log in using saved credentials if on a login page
        
        Args:
            company_domain: Domain of the company to look up credentials
        
        Returns:
            True if login was attempted, False otherwise
        """
        try:
            # Get saved credentials
            credentials = self.credentials_service.get_credentials(
                user_id=self.user_id,
                company_domain=company_domain
            )
            
            if not credentials:
                logger.debug(f"No saved credentials found for {company_domain}")
                return False
            
            logger.info(f"🔑 Found saved credentials for {company_domain}, attempting login")
            
            # Find login fields
            email_field = await self.page.locator(
                'input[type="email"], input[name*="email"], input[id*="email"]'
            ).first.element_handle()
            
            password_field = await self.page.locator(
                'input[type="password"]'
            ).first.element_handle()
            
            if not email_field or not password_field:
                logger.warning("Could not find login fields")
                return False
            
            # Fill login form
            await self.page.fill('input[type="email"], input[name*="email"]', credentials['email'])
            await asyncio.sleep(0.3)
            await self.page.fill('input[type="password"]', credentials['password'])
            await asyncio.sleep(0.3)
            
            # Try to find and click login button
            login_button = await self.page.locator(
                'button:has-text("Sign In"), '
                'button:has-text("Log In"), '
                'button[type="submit"]'
            ).first
            
            if await login_button.count() > 0:
                await login_button.click()
                await asyncio.sleep(2)
                logger.info("✅ Login form submitted")
                return True
            
            return False
        
        except Exception as e:
            logger.error(f"Error logging in with saved credentials: {e}")
            return False

