"""
Detector for account creation pages (Workday and other ATS systems)
Distinguishes between login pages and account creation pages
"""
import re
from typing import Optional, Dict, Any, List
from playwright.async_api import Page, Locator
from loguru import logger


class AccountCreationDetector:
    """
    Detects account creation pages and extracts requirements
    """
    
    # Keywords that indicate account creation (not login)
    ACCOUNT_CREATION_KEYWORDS = [
        'create account',
        'create your account',
        'register',
        'sign up',
        'new account',
        'candidate home',
        'verify new password',
        'verify password',
        'confirm password',
        'password requirements',
        'set up your account'
    ]
    
    # Keywords that indicate login (should NOT auto-fill)
    LOGIN_KEYWORDS = [
        'sign in',
        'log in',
        'login',
        'enter your password',
        'forgot password',
        'remember me'
    ]
    
    def __init__(self, page: Page):
        self.page = page
    
    async def is_account_creation_page(self) -> bool:
        """
        Determine if the current page is an account creation page
        
        Returns:
            True if this is an account creation page, False otherwise
        """
        try:
            # Get page content
            page_text = await self.page.text_content('body')
            if not page_text:
                return False
            
            page_text_lower = page_text.lower()
            
            # Check for account creation keywords
            creation_matches = sum(
                1 for keyword in self.ACCOUNT_CREATION_KEYWORDS
                if keyword in page_text_lower
            )
            
            # Check for login keywords
            login_matches = sum(
                1 for keyword in self.LOGIN_KEYWORDS
                if keyword in page_text_lower
            )
            
            # More creation keywords than login keywords = account creation
            if creation_matches > login_matches:
                logger.info(f"✅ Detected account creation page (creation: {creation_matches}, login: {login_matches})")
                return True
            
            # Additional check: Look for password confirmation field
            # Account creation pages almost always have "confirm password" or "verify password"
            confirm_password = await self.page.locator(
                'input[type="password"][data-automation-id*="verify"], '
                'input[type="password"][name*="confirm"], '
                'input[type="password"][id*="confirm"], '
                'input[type="password"][placeholder*="confirm"]'
            ).count()
            
            if confirm_password > 0:
                logger.info("✅ Detected account creation page (has password confirmation field)")
                return True
            
            return False
        
        except Exception as e:
            logger.error(f"Error detecting account creation page: {e}")
            return False
    
    async def get_password_requirements(self) -> Dict[str, Any]:
        """
        Extract password requirements from the page
        
        Returns:
            Dict with password requirements (min_length, requires_uppercase, etc.)
        """
        requirements = {
            'min_length': 8,  # Default minimum
            'requires_uppercase': True,
            'requires_lowercase': True,
            'requires_number': True,
            'requires_special': True,
            'requirements_text': ''
        }
        
        try:
            # Look for password requirements section
            requirements_selectors = [
                '[data-automation-id*="passwordRules"]',
                '[class*="password-requirements"]',
                '[class*="passwordRequirements"]',
                'div:has-text("Password Requirements")',
                'div:has-text("password must")'
            ]
            
            for selector in requirements_selectors:
                element = self.page.locator(selector).first
                if await element.count() > 0:
                    text = await element.text_content()
                    if text:
                        requirements['requirements_text'] = text
                        
                        # Parse requirements
                        text_lower = text.lower()
                        
                        # Extract minimum length
                        length_match = re.search(r'(\d+)\s+character', text_lower)
                        if length_match:
                            requirements['min_length'] = int(length_match.group(1))
                        
                        # Check for specific requirements
                        requirements['requires_uppercase'] = 'uppercase' in text_lower
                        requirements['requires_lowercase'] = 'lowercase' in text_lower
                        requirements['requires_number'] = 'numeric' in text_lower or 'number' in text_lower or 'digit' in text_lower
                        requirements['requires_special'] = 'special' in text_lower
                        
                        logger.info(f"📋 Password requirements: {requirements}")
                        break
        
        except Exception as e:
            logger.warning(f"Could not extract password requirements: {e}")
        
        return requirements
    
    async def get_account_creation_fields(self) -> Dict[str, Any]:
        """
        Get all fields related to account creation
        
        Returns:
            Dict with email, password, and confirm_password locators
        """
        fields = {
            'email': None,
            'password': None,
            'confirm_password': None,
            'checkbox': None
        }
        
        try:
            # Find email field
            email_selectors = [
                'input[type="email"]',
                'input[data-automation-id*="email"]',
                'input[name*="email"]',
                'input[id*="email"]'
            ]
            
            for selector in email_selectors:
                element = self.page.locator(selector).first
                if await element.count() > 0 and await element.is_visible():
                    fields['email'] = element
                    break
            
            # Find password field (not confirm)
            password_selectors = [
                'input[type="password"][data-automation-id="password"]',
                'input[type="password"][name="password"]',
                'input[type="password"][id*="password"]:not([id*="confirm"]):not([id*="verify"])'
            ]
            
            for selector in password_selectors:
                element = self.page.locator(selector).first
                if await element.count() > 0 and await element.is_visible():
                    fields['password'] = element
                    break
            
            # If no specific password field, get first password field
            if not fields['password']:
                password_fields = await self.page.locator('input[type="password"]').all()
                if len(password_fields) >= 1:
                    fields['password'] = password_fields[0]
                    if len(password_fields) >= 2:
                        fields['confirm_password'] = password_fields[1]
            
            # Find confirm password field
            if not fields['confirm_password']:
                confirm_selectors = [
                    'input[type="password"][data-automation-id*="verify"]',
                    'input[type="password"][name*="confirm"]',
                    'input[type="password"][id*="confirm"]'
                ]
                
                for selector in confirm_selectors:
                    element = self.page.locator(selector).first
                    if await element.count() > 0 and await element.is_visible():
                        fields['confirm_password'] = element
                        break
            
            # Find agreement checkbox (common on Workday)
            checkbox_selectors = [
                'input[type="checkbox"][data-automation-id*="createAccount"]',
                'input[type="checkbox"][data-automation-id*="agreement"]',
                'input[type="checkbox"]'
            ]
            
            for selector in checkbox_selectors:
                element = self.page.locator(selector).first
                if await element.count() > 0 and await element.is_visible():
                    fields['checkbox'] = element
                    break
            
            logger.info(f"🔍 Found account creation fields: "
                       f"email={fields['email'] is not None}, "
                       f"password={fields['password'] is not None}, "
                       f"confirm={fields['confirm_password'] is not None}, "
                       f"checkbox={fields['checkbox'] is not None}")
        
        except Exception as e:
            logger.error(f"Error getting account creation fields: {e}")
        
        return fields
    
    async def get_company_info_from_page(self) -> Dict[str, str]:
        """
        Extract company name and domain from the current page
        
        Returns:
            Dict with 'company_name' and 'company_domain'
        """
        info = {
            'company_name': '',
            'company_domain': ''
        }
        
        try:
            # Get domain from URL
            url = self.page.url
            
            # Extract domain from URL
            domain_match = re.search(r'https?://(?:www\.)?([^/]+)', url)
            if domain_match:
                full_domain = domain_match.group(1)
                
                # For Workday URLs like "troutmanpepper.wd1.myworkdayjobs.com"
                # Extract the company identifier
                if 'myworkdayjobs.com' in full_domain:
                    company_identifier = full_domain.split('.')[0]
                    info['company_domain'] = f"{company_identifier}.myworkdayjobs.com"
                    info['company_name'] = company_identifier.replace('-', ' ').title()
                else:
                    info['company_domain'] = full_domain
                
                logger.info(f"🏢 Extracted company info: {info}")
            
            # Try to get company name from page title or headings
            if not info['company_name']:
                title = await self.page.title()
                if title:
                    # Extract company name from title (usually before " - " or " | ")
                    title_parts = re.split(r'\s+[-|]\s+', title)
                    if title_parts:
                        info['company_name'] = title_parts[0].strip()
        
        except Exception as e:
            logger.warning(f"Could not extract company info: {e}")
        
        return info


