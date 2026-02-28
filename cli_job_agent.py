#!/usr/bin/env python3
"""
Terminal-Based Job Application Agent
A CLI interface for the Job Application Agent with full functionality
"""

import os
import sys
import asyncio
import json
import getpass
from typing import Optional, Dict, Any, List
from datetime import datetime, timedelta
import bcrypt
from uuid import UUID
import time
from collections import deque
import signal
import re

# Setup path - add both root and Agents directory
ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
AGENTS_DIR = os.path.join(ROOT_DIR, 'Agents')
sys.path.insert(0, ROOT_DIR)
sys.path.insert(0, AGENTS_DIR)

from database_config import SessionLocal, User, UserProfile, JobApplication
from Agents.agent_profile_service import AgentProfileService
from playwright.async_api import async_playwright
from logging_config import setup_file_logging
import logging

# Initialize logging first (with INFO level for CLI to reduce noise)
setup_file_logging(log_level=logging.INFO, console_logging=False)
logger = logging.getLogger(__name__)

# Import resume tailoring functions
try:
    from Agents.resume_tailoring_agent import tailor_resume_and_return_url, get_google_services
    from Agents.latex_tailoring_agent import tailor_latex_resume_from_base64, compile_latex_zip_to_pdf
    RESUME_TAILORING_AVAILABLE = True
    print("[OK] Resume tailoring module loaded")
except Exception as e:
    print(f"[WARN] Resume tailoring not available: {e}")
    logger.warning(f"Resume tailoring not available: {e}")
    RESUME_TAILORING_AVAILABLE = False

# Import job discovery agent
try:
    from Agents.multi_source_job_discovery_agent import MultiSourceJobDiscoveryAgent
    JOB_DISCOVERY_AVAILABLE = True
    print("[OK] Job discovery module loaded")
except Exception as e:
    print(f"[WARN] Job discovery not available: {e}")
    logger.warning(f"Job discovery not available: {e}")
    JOB_DISCOVERY_AVAILABLE = False

# Import Gemini query optimizer
try:
    from Agents.gemini_query_optimizer import GeminiQueryOptimizer
    QUERY_OPTIMIZER_AVAILABLE = True
    print("[OK] Gemini query optimizer loaded")
except Exception as e:
    print(f"[WARN] Query optimizer not available: {e}")
    logger.warning(f"Query optimizer not available: {e}")
    QUERY_OPTIMIZER_AVAILABLE = False

# Import job application agent
try:
    from Agents.job_application_agent import RefactoredJobAgent
    JOB_APPLICATION_AVAILABLE = True
    print("[OK] Job application module loaded")
except Exception as e:
    print(f"[ERROR] Job application agent not available: {e}")
    logger.error(f"Job application agent not available: {e}", exc_info=True)
    JOB_APPLICATION_AVAILABLE = False

# Import Mimikree credential service (used to enforce tailoring prerequisite)
try:
    from server.mimikree_service import mimikree_service
    MIMIKREE_SERVICE_AVAILABLE = True
except Exception as e:
    print(f"[WARN] Mimikree service not available: {e}")
    logger.warning(f"Mimikree service not available: {e}")
    MIMIKREE_SERVICE_AVAILABLE = False

# Direct Mimikree auth fallback for CLI-only environments
try:
    from Agents.mimikree_integration import MimikreeClient
    MIMIKREE_CLIENT_AVAILABLE = True
except Exception as e:
    print(f"[WARN] Mimikree client not available: {e}")
    logger.warning(f"Mimikree client not available: {e}")
    MIMIKREE_CLIENT_AVAILABLE = False


class Colors:
    """ANSI color codes for terminal output"""
    HEADER = '\033[95m'
    OKBLUE = '\033[94m'
    OKCYAN = '\033[96m'
    OKGREEN = '\033[92m'
    WARNING = '\033[93m'
    FAIL = '\033[91m'
    ENDC = '\033[0m'
    BOLD = '\033[1m'
    UNDERLINE = '\033[4m'


class CLIJobAgent:
    """Terminal-based Job Application Agent"""
    
    def __init__(self):
        self.db = SessionLocal()
        self.current_user: Optional[User] = None
        self.current_profile: Optional[Dict[str, Any]] = None
        self.running = True
        # Session-scoped Mimikree credentials fallback (used if service layer is unavailable)
        self._session_mimikree_email: Optional[str] = None
        self._session_mimikree_password: Optional[str] = None
        # Flags for incomplete application handling
        self._should_open_incomplete = False
        self._incomplete_report_file = None
        
    def __del__(self):
        """Cleanup database connection"""
        if hasattr(self, 'db'):
            self.db.close()
    
    def clear_screen(self):
        """Clear terminal screen"""
        os.system('cls' if os.name == 'nt' else 'clear')
    
    def print_header(self, text: str):
        """Print formatted header"""
        print(f"\n{Colors.HEADER}{Colors.BOLD}{'=' * 60}{Colors.ENDC}")
        print(f"{Colors.HEADER}{Colors.BOLD}{text.center(60)}{Colors.ENDC}")
        print(f"{Colors.HEADER}{Colors.BOLD}{'=' * 60}{Colors.ENDC}\n")
    
    def print_success(self, text: str):
        """Print success message"""
        print(f"{Colors.OKGREEN}[OK] {text}{Colors.ENDC}")
    
    def print_error(self, text: str):
        """Print error message"""
        print(f"{Colors.FAIL}[ERROR] {text}{Colors.ENDC}")
    
    def print_info(self, text: str):
        """Print info message"""
        print(f"{Colors.OKCYAN}[INFO] {text}{Colors.ENDC}")
    
    def print_warning(self, text: str):
        """Print warning message"""
        print(f"{Colors.WARNING}[WARN] {text}{Colors.ENDC}")
    
    def get_input(self, prompt: str, password: bool = False) -> str:
        """Get user input"""
        if password:
            return getpass.getpass(f"{Colors.OKBLUE}{prompt}{Colors.ENDC}")
        return input(f"{Colors.OKBLUE}{prompt}{Colors.ENDC}")
    
    def pause(self):
        """Pause and wait for user input"""
        input(f"\n{Colors.OKCYAN}Press Enter to continue...{Colors.ENDC}")

    def _is_latex_resume_mode(self) -> bool:
        source_type = (self.current_profile or {}).get('resume_source_type', 'google_doc')
        return source_type == 'latex_zip'

    def _ensure_resume_ready_for_auto_apply(self) -> bool:
        """
        Ensure auto-apply has a usable resume path.
        For LaTeX mode, compile stored ZIP to a local PDF and persist resume_url.
        """
        if not self.current_profile:
            self.print_error("Profile not loaded. Please log in again.")
            return False

        # LaTeX resume mode is not yet available in production — require a Google Docs URL.
        if self._is_latex_resume_mode():
            self.print_error("LaTeX resume auto-apply is not yet available in this version.")
            self.print_info("Please set your resume source to Google Docs in your profile.")
            return False

        resume_url = self.current_profile.get('resume_url')
        if not resume_url:
            self.print_error("Please complete your profile and add a resume URL (Google Docs link) first.")
            return False
        return True

    def _get_connected_mimikree_credentials(self) -> tuple[Optional[str], Optional[str]]:
        """
        Return decrypted Mimikree credentials for the current user if connected.
        """
        if not MIMIKREE_SERVICE_AVAILABLE or not self.current_user:
            return None, None

        try:
            # Refresh local user state before checking connection flags
            db_user = self.db.query(User).filter(User.id == self.current_user.id).first()
            if db_user:
                self.current_user = db_user

            if not self.current_user or not getattr(self.current_user, 'mimikree_is_connected', False):
                return None, None

            creds = mimikree_service.get_user_mimikree_credentials(self.current_user.id)
            if not creds:
                return None, None

            return creds
        except Exception as e:
            logger.error(f"Failed to read Mimikree credentials: {e}", exc_info=True)
            return None, None

    def ensure_mimikree_connected_for_tailoring(self) -> tuple[Optional[str], Optional[str]]:
        """
        Ensure Mimikree is connected before any resume tailoring starts.
        Prompts for credentials and connects if needed.
        """
        if not self.current_user:
            self.print_error("You must be logged in before using resume tailoring.")
            return None, None

        # Reuse session credentials first if present (CLI fallback mode)
        if self._session_mimikree_email and self._session_mimikree_password:
            return self._session_mimikree_email, self._session_mimikree_password

        if MIMIKREE_SERVICE_AVAILABLE:
            email, password = self._get_connected_mimikree_credentials()
            if email and password:
                self._session_mimikree_email = email
                self._session_mimikree_password = password
                self.print_success(f"Mimikree is connected ({email})")
                return email, password

        self.print_warning("Mimikree is not connected.")
        self.print_info("You need to log in to Mimikree before resume tailoring can start.")
        connect_now = self.get_input("Connect Mimikree now? (y/n): ").strip().lower()
        if connect_now != 'y':
            self.print_warning("Tailoring cancelled because Mimikree is not connected.")
            return None, None

        while True:
            email = self.get_input("Mimikree Email: ").strip()
            password = self.get_input("Mimikree Password: ", password=True)

            if not email or not password:
                self.print_error("Both email and password are required.")
                retry = self.get_input("Try again? (y/n): ").strip().lower()
                if retry != 'y':
                    return None, None
                continue

            self.print_info("Validating Mimikree credentials...")
            if MIMIKREE_SERVICE_AVAILABLE:
                result = mimikree_service.connect_user_mimikree(self.current_user.id, email, password)
                if result.get('success'):
                    self.print_success("Mimikree connected successfully.")
                    # Refresh user object to keep local state in sync with DB
                    refreshed_user = self.db.query(User).filter(User.id == self.current_user.id).first()
                    if refreshed_user:
                        self.current_user = refreshed_user

                    connected_email, connected_password = self._get_connected_mimikree_credentials()
                    if connected_email and connected_password:
                        self._session_mimikree_email = connected_email
                        self._session_mimikree_password = connected_password
                        return connected_email, connected_password

                    # Fallback to just-entered credentials if decryption fetch is delayed
                    self._session_mimikree_email = email
                    self._session_mimikree_password = password
                    return email, password

                self.print_error(result.get('error', 'Failed to connect to Mimikree'))
            elif MIMIKREE_CLIENT_AVAILABLE:
                try:
                    client = MimikreeClient()
                    if client.authenticate(email, password):
                        self._session_mimikree_email = email
                        self._session_mimikree_password = password
                        self.print_success("Mimikree login successful (session mode).")
                        self.print_warning("Credentials are kept for this session only.")
                        return email, password

                    self.print_error("Invalid Mimikree email or password.")
                except Exception as e:
                    self.print_error(f"Mimikree login failed: {e}")
            else:
                self.print_error("Mimikree integration is unavailable. Cannot validate credentials.")
                return None, None

            retry = self.get_input("Try again? (y/n): ").strip().lower()
            if retry != 'y':
                return None, None
    
    # ============================================================================
    # Authentication Methods
    # ============================================================================
    
    def hash_password(self, password: str) -> str:
        """Hash password using bcrypt"""
        return bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')
    
    def verify_password(self, password: str, hashed: str) -> bool:
        """Verify password against hash"""
        return bcrypt.checkpw(password.encode('utf-8'), hashed.encode('utf-8'))
    
    def register_user(self):
        """Register new user"""
        self.clear_screen()
        self.print_header("USER REGISTRATION")
        
        try:
            email = self.get_input("Email: ").strip()
            if not email or '@' not in email:
                self.print_error("Invalid email address")
                self.pause()
                return
            
            # Check if user exists
            existing = self.db.query(User).filter(User.email == email).first()
            if existing:
                self.print_error("Email already registered")
                self.pause()
                return
            
            first_name = self.get_input("First Name: ").strip()
            last_name = self.get_input("Last Name: ").strip()
            password = self.get_input("Password: ", password=True)
            password_confirm = self.get_input("Confirm Password: ", password=True)
            
            if password != password_confirm:
                self.print_error("Passwords do not match")
                self.pause()
                return
            
            if len(password) < 8:
                self.print_error("Password must be at least 8 characters")
                self.pause()
                return
            
            # Create user
            user = User(
                email=email,
                first_name=first_name,
                last_name=last_name,
                password_hash=self.hash_password(password),
                is_active=True,
                email_verified=True  # Auto-verify for CLI
            )
            
            self.db.add(user)
            self.db.commit()
            self.db.refresh(user)
            
            # Create empty profile
            profile = UserProfile(user_id=user.id)
            self.db.add(profile)
            self.db.commit()
            
            self.print_success(f"Registration successful! Welcome, {first_name}!")
            self.pause()
            
        except Exception as e:
            self.db.rollback()
            self.print_error(f"Registration failed: {str(e)}")
            logger.error(f"Registration error: {e}", exc_info=True)
            self.pause()
    
    def login_user(self) -> bool:
        """Login user"""
        self.clear_screen()
        self.print_header("USER LOGIN")
        
        try:
            email = self.get_input("Email: ").strip()
            password = self.get_input("Password: ", password=True)
            
            user = self.db.query(User).filter(User.email == email).first()
            
            if not user or not self.verify_password(password, user.password_hash):
                self.print_error("Invalid email or password")
                self.pause()
                return False
            
            if not user.is_active:
                self.print_error("Account is disabled")
                self.pause()
                return False
            
            self.current_user = user
            
            # Load profile
            self.current_profile = AgentProfileService.get_profile_by_user_id(user.id)
            
            self.print_success(f"Welcome back, {user.first_name}!")
            self.pause()
            return True
            
        except Exception as e:
            self.print_error(f"Login failed: {str(e)}")
            logger.error(f"Login error: {e}", exc_info=True)
            self.pause()
            return False
    
    # ============================================================================
    # Main Menu
    # ============================================================================
    
    def show_main_menu(self):
        """Display main menu"""
        while self.running and self.current_user:
            self.clear_screen()
            self.print_header("JOB APPLICATION AGENT - MAIN MENU")
            
            print(f"  Logged in as: {Colors.OKGREEN}{self.current_user.first_name} {self.current_user.last_name}{Colors.ENDC}")
            print(f"  Email: {Colors.OKCYAN}{self.current_user.email}{Colors.ENDC}\n")
            
            print(f"{Colors.BOLD}1.{Colors.ENDC} Profile Management")
            print(f"{Colors.BOLD}2.{Colors.ENDC} Resume Tailoring")
            print(f"{Colors.BOLD}3.{Colors.ENDC} Search Jobs")
            print(f"{Colors.BOLD}4.{Colors.ENDC} Auto Apply to Job(s) - Batch Mode (up to 10)")
            print(f"{Colors.BOLD}5.{Colors.ENDC} View Application History")
            print(f"{Colors.BOLD}6.{Colors.ENDC} 🚀 100% Auto Job Apply - Continuous Mode")
            print(f"{Colors.BOLD}7.{Colors.ENDC} 🌐 Browser Profile Setup (One-Time Setup)")
            print(f"{Colors.BOLD}8.{Colors.ENDC} Settings")
            print(f"{Colors.BOLD}9.{Colors.ENDC} Logout")
            print(f"{Colors.BOLD}10.{Colors.ENDC} Exit\n")
            
            choice = self.get_input("Select option (1-10): ").strip()
            
            if choice == '1':
                self.profile_menu()
            elif choice == '2':
                self.resume_tailoring_menu()
            elif choice == '3':
                self.job_search_menu()
            elif choice == '4':
                asyncio.run(self.auto_apply_menu())
            elif choice == '5':
                self.view_application_history()
            elif choice == '6':
                asyncio.run(self.continuous_auto_apply_menu())
            elif choice == '7':
                asyncio.run(self.browser_profile_setup_menu())
            elif choice == '8':
                self.settings_menu()
            elif choice == '9':
                self.logout()
                break
            elif choice == '10':
                self.running = False
                break
            else:
                self.print_error("Invalid option")
                self.pause()
    
    # ============================================================================
    # Profile Management
    # ============================================================================
    
    def profile_menu(self):
        """Profile management menu"""
        while True:
            self.clear_screen()
            self.print_header("PROFILE MANAGEMENT")
            
            print(f"{Colors.BOLD}1.{Colors.ENDC} View Profile")
            print(f"{Colors.BOLD}2.{Colors.ENDC} Update Basic Info")
            print(f"{Colors.BOLD}3.{Colors.ENDC} Update Contact Info")
            print(f"{Colors.BOLD}4.{Colors.ENDC} Update Education")
            print(f"{Colors.BOLD}5.{Colors.ENDC} Update Work Experience")
            print(f"{Colors.BOLD}6.{Colors.ENDC} Update Skills")
            print(f"{Colors.BOLD}7.{Colors.ENDC} Update Resume URL")
            print(f"{Colors.BOLD}8.{Colors.ENDC} Back to Main Menu\n")
            
            choice = self.get_input("Select option (1-8): ").strip()
            
            if choice == '1':
                self.view_profile()
            elif choice == '2':
                self.update_basic_info()
            elif choice == '3':
                self.update_contact_info()
            elif choice == '4':
                self.update_education()
            elif choice == '5':
                self.update_work_experience()
            elif choice == '6':
                self.update_skills()
            elif choice == '7':
                self.update_resume_url()
            elif choice == '8':
                break
            else:
                self.print_error("Invalid option")
                self.pause()
    
    def view_profile(self):
        """View current profile"""
        self.clear_screen()
        self.print_header("YOUR PROFILE")
        
        if not self.current_profile:
            self.print_warning("No profile data available")
            self.pause()
            return
        
        print(f"\n{Colors.BOLD}Basic Information:{Colors.ENDC}")
        print(f"  Name: {self.current_profile.get('first name', '')} {self.current_profile.get('last name', '')}")
        print(f"  Email: {self.current_profile.get('email', '')}")
        print(f"  Phone: {self.current_profile.get('phone', 'Not set')}")
        print(f"  Date of Birth: {self.current_profile.get('date of birth', 'Not set')}")
        
        print(f"\n{Colors.BOLD}Address:{Colors.ENDC}")
        print(f"  {self.current_profile.get('address', 'Not set')}")
        print(f"  {self.current_profile.get('city', '')}, {self.current_profile.get('state', '')} {self.current_profile.get('zip_code', '')}")
        print(f"  {self.current_profile.get('country', '')}")
        
        print(f"\n{Colors.BOLD}Social Links:{Colors.ENDC}")
        print(f"  LinkedIn: {self.current_profile.get('linkedin', 'Not set')}")
        print(f"  GitHub: {self.current_profile.get('github', 'Not set')}")
        
        print(f"\n{Colors.BOLD}Resume:{Colors.ENDC}")
        print(f"  URL: {self.current_profile.get('resume_url', 'Not set')}")
        
        # Education
        education = self.current_profile.get('education', [])
        if education:
            print(f"\n{Colors.BOLD}Education:{Colors.ENDC}")
            for i, edu in enumerate(education, 1):
                print(f"  {i}. {edu.get('degree', '')} in {edu.get('field', '')} - {edu.get('school', '')}")
        
        # Work Experience
        work_exp = self.current_profile.get('work_experience', [])
        if work_exp:
            print(f"\n{Colors.BOLD}Work Experience:{Colors.ENDC}")
            for i, exp in enumerate(work_exp, 1):
                print(f"  {i}. {exp.get('title', '')} at {exp.get('company', '')}")
        
        # Skills
        skills = self.current_profile.get('skills', {})
        if skills:
            print(f"\n{Colors.BOLD}Skills:{Colors.ENDC}")
            for category, skill_list in skills.items():
                if skill_list:
                    print(f"  {category}: {', '.join(skill_list)}")
        
        self.pause()
    
    def update_basic_info(self):
        """Update basic information"""
        self.clear_screen()
        self.print_header("UPDATE BASIC INFO")
        
        try:
            profile = self.db.query(UserProfile).filter(
                UserProfile.user_id == self.current_user.id
            ).first()
            
            if not profile:
                profile = UserProfile(user_id=self.current_user.id)
                self.db.add(profile)
            
            print("Leave blank to keep current value\n")
            
            dob = self.get_input(f"Date of Birth (current: {profile.date_of_birth or 'Not set'}): ").strip()
            if dob:
                profile.date_of_birth = dob
            
            gender = self.get_input(f"Gender (current: {profile.gender or 'Not set'}): ").strip()
            if gender:
                profile.gender = gender
            
            nationality = self.get_input(f"Nationality (current: {profile.nationality or 'Not set'}): ").strip()
            if nationality:
                profile.nationality = nationality
            
            self.db.commit()
            
            # Reload profile
            self.current_profile = AgentProfileService.get_profile_by_user_id(self.current_user.id)
            
            self.print_success("Basic info updated successfully!")
            self.pause()
            
        except Exception as e:
            self.db.rollback()
            self.print_error(f"Update failed: {str(e)}")
            logger.error(f"Profile update error: {e}", exc_info=True)
            self.pause()
    
    def update_contact_info(self):
        """Update contact information"""
        self.clear_screen()
        self.print_header("UPDATE CONTACT INFO")
        
        try:
            profile = self.db.query(UserProfile).filter(
                UserProfile.user_id == self.current_user.id
            ).first()
            
            if not profile:
                profile = UserProfile(user_id=self.current_user.id)
                self.db.add(profile)
            
            print("Leave blank to keep current value\n")
            
            phone = self.get_input(f"Phone (current: {profile.phone or 'Not set'}): ").strip()
            if phone:
                profile.phone = phone
            
            address = self.get_input(f"Address (current: {profile.address or 'Not set'}): ").strip()
            if address:
                profile.address = address
            
            city = self.get_input(f"City (current: {profile.city or 'Not set'}): ").strip()
            if city:
                profile.city = city
            
            state = self.get_input(f"State (current: {profile.state or 'Not set'}): ").strip()
            if state:
                profile.state = state
            
            zip_code = self.get_input(f"ZIP Code (current: {profile.zip_code or 'Not set'}): ").strip()
            if zip_code:
                profile.zip_code = zip_code
            
            country = self.get_input(f"Country (current: {profile.country or 'Not set'}): ").strip()
            if country:
                profile.country = country
            
            linkedin = self.get_input(f"LinkedIn (current: {profile.linkedin or 'Not set'}): ").strip()
            if linkedin:
                profile.linkedin = linkedin
            
            github = self.get_input(f"GitHub (current: {profile.github or 'Not set'}): ").strip()
            if github:
                profile.github = github
            
            self.db.commit()
            
            # Reload profile
            self.current_profile = AgentProfileService.get_profile_by_user_id(self.current_user.id)
            
            self.print_success("Contact info updated successfully!")
            self.pause()
            
        except Exception as e:
            self.db.rollback()
            self.print_error(f"Update failed: {str(e)}")
            logger.error(f"Profile update error: {e}", exc_info=True)
            self.pause()
    
    def update_education(self):
        """Update education"""
        self.clear_screen()
        self.print_header("UPDATE EDUCATION")
        
        try:
            profile = self.db.query(UserProfile).filter(
                UserProfile.user_id == self.current_user.id
            ).first()
            
            if not profile:
                profile = UserProfile(user_id=self.current_user.id)
                self.db.add(profile)
            
            education = profile.education or []
            
            print(f"Current education entries: {len(education)}\n")
            print(f"{Colors.BOLD}1.{Colors.ENDC} Add new education")
            print(f"{Colors.BOLD}2.{Colors.ENDC} View/Edit existing")
            print(f"{Colors.BOLD}3.{Colors.ENDC} Cancel\n")
            
            choice = self.get_input("Select option: ").strip()
            
            if choice == '1':
                print("\n" + "=" * 60)
                school = self.get_input("School/University: ").strip()
                degree = self.get_input("Degree (e.g., Bachelor's, Master's): ").strip()
                field = self.get_input("Field of Study: ").strip()
                start_date = self.get_input("Start Date (MM/YYYY): ").strip()
                end_date = self.get_input("End Date (MM/YYYY or 'Present'): ").strip()
                gpa = self.get_input("GPA (optional): ").strip()
                
                edu_entry = {
                    "school": school,
                    "degree": degree,
                    "field": field,
                    "start_date": start_date,
                    "end_date": end_date,
                    "gpa": gpa if gpa else None
                }
                
                education.append(edu_entry)
                profile.education = education
                self.db.commit()
                
                # Reload profile
                self.current_profile = AgentProfileService.get_profile_by_user_id(self.current_user.id)
                
                self.print_success("Education added successfully!")
            elif choice == '2':
                if not education:
                    self.print_warning("No education entries to edit")
                else:
                    for i, edu in enumerate(education, 1):
                        print(f"\n{i}. {edu.get('degree', '')} in {edu.get('field', '')} - {edu.get('school', '')}")
                    
                    idx = self.get_input("\nSelect entry to edit (0 to cancel): ").strip()
                    if idx.isdigit() and 1 <= int(idx) <= len(education):
                        # Simple implementation: just show current values
                        self.print_info("Education editing not fully implemented yet")
            
            self.pause()
            
        except Exception as e:
            self.db.rollback()
            self.print_error(f"Update failed: {str(e)}")
            logger.error(f"Education update error: {e}", exc_info=True)
            self.pause()
    
    def update_work_experience(self):
        """Update work experience"""
        self.clear_screen()
        self.print_header("UPDATE WORK EXPERIENCE")
        
        try:
            profile = self.db.query(UserProfile).filter(
                UserProfile.user_id == self.current_user.id
            ).first()
            
            if not profile:
                profile = UserProfile(user_id=self.current_user.id)
                self.db.add(profile)
            
            work_exp = profile.work_experience or []
            
            print(f"Current work experience entries: {len(work_exp)}\n")
            print(f"{Colors.BOLD}1.{Colors.ENDC} Add new experience")
            print(f"{Colors.BOLD}2.{Colors.ENDC} View existing")
            print(f"{Colors.BOLD}3.{Colors.ENDC} Cancel\n")
            
            choice = self.get_input("Select option: ").strip()
            
            if choice == '1':
                print("\n" + "=" * 60)
                company = self.get_input("Company: ").strip()
                title = self.get_input("Job Title: ").strip()
                start_date = self.get_input("Start Date (MM/YYYY): ").strip()
                end_date = self.get_input("End Date (MM/YYYY or 'Present'): ").strip()
                location = self.get_input("Location: ").strip()
                description = self.get_input("Description: ").strip()
                
                exp_entry = {
                    "company": company,
                    "title": title,
                    "start_date": start_date,
                    "end_date": end_date,
                    "location": location,
                    "description": description
                }
                
                work_exp.append(exp_entry)
                profile.work_experience = work_exp
                self.db.commit()
                
                # Reload profile
                self.current_profile = AgentProfileService.get_profile_by_user_id(self.current_user.id)
                
                self.print_success("Work experience added successfully!")
            elif choice == '2':
                if not work_exp:
                    self.print_warning("No work experience entries")
                else:
                    for i, exp in enumerate(work_exp, 1):
                        print(f"\n{i}. {exp.get('title', '')} at {exp.get('company', '')}")
                        print(f"   {exp.get('start_date', '')} - {exp.get('end_date', '')}")
            
            self.pause()
            
        except Exception as e:
            self.db.rollback()
            self.print_error(f"Update failed: {str(e)}")
            logger.error(f"Work experience update error: {e}", exc_info=True)
            self.pause()
    
    def update_skills(self):
        """Update skills"""
        self.clear_screen()
        self.print_header("UPDATE SKILLS")
        
        try:
            profile = self.db.query(UserProfile).filter(
                UserProfile.user_id == self.current_user.id
            ).first()
            
            if not profile:
                profile = UserProfile(user_id=self.current_user.id)
                self.db.add(profile)
            
            skills = profile.skills or {}
            
            print("Enter skills separated by commas\n")
            
            technical = self.get_input("Technical Skills: ").strip()
            if technical:
                skills['technical'] = [s.strip() for s in technical.split(',')]
            
            programming = self.get_input("Programming Languages: ").strip()
            if programming:
                skills['programming_languages'] = [s.strip() for s in programming.split(',')]
            
            tools = self.get_input("Tools & Technologies: ").strip()
            if tools:
                skills['tools'] = [s.strip() for s in tools.split(',')]
            
            soft = self.get_input("Soft Skills: ").strip()
            if soft:
                skills['soft_skills'] = [s.strip() for s in soft.split(',')]
            
            profile.skills = skills
            self.db.commit()
            
            # Reload profile
            self.current_profile = AgentProfileService.get_profile_by_user_id(self.current_user.id)
            
            self.print_success("Skills updated successfully!")
            self.pause()
            
        except Exception as e:
            self.db.rollback()
            self.print_error(f"Update failed: {str(e)}")
            logger.error(f"Skills update error: {e}", exc_info=True)
            self.pause()
    
    def update_resume_url(self):
        """Update resume URL"""
        self.clear_screen()
        self.print_header("UPDATE RESUME URL")
        
        try:
            profile = self.db.query(UserProfile).filter(
                UserProfile.user_id == self.current_user.id
            ).first()
            
            if not profile:
                profile = UserProfile(user_id=self.current_user.id)
                self.db.add(profile)
            
            print(f"Current resume URL: {profile.resume_url or 'Not set'}\n")
            
            url = self.get_input("New Resume URL (Google Doc link): ").strip()
            
            if url:
                profile.resume_url = url
                self.db.commit()
                
                # Reload profile
                self.current_profile = AgentProfileService.get_profile_by_user_id(self.current_user.id)
                
                self.print_success("Resume URL updated successfully!")
            else:
                self.print_warning("No changes made")
            
            self.pause()
            
        except Exception as e:
            self.db.rollback()
            self.print_error(f"Update failed: {str(e)}")
            logger.error(f"Resume URL update error: {e}", exc_info=True)
            self.pause()
    
    # ============================================================================
    # Resume Tailoring
    # ============================================================================
    
    def resume_tailoring_menu(self):
        """Resume tailoring menu"""
        self.clear_screen()
        self.print_header("RESUME TAILORING")
        
        if not RESUME_TAILORING_AVAILABLE:
            self.print_error("Resume tailoring feature is not available")
            self.print_info("Missing dependencies or configuration")
            self.pause()
            return

        # LaTeX resume tailoring is not yet available in production
        if self._is_latex_resume_mode():
            self.print_warning("LaTeX resume tailoring is not yet available in this version.")
            self.print_info("Please set your resume source to Google Docs in your profile and use a Google Docs resume URL.")
            self.pause()
            return

        # Required gate: Mimikree must be connected before tailoring can begin
        mimikree_email, mimikree_password = self.ensure_mimikree_connected_for_tailoring()
        if not mimikree_email or not mimikree_password:
            self.pause()
            return
        
        # Check if user has resume URL
        resume_url = self.current_profile.get('resume_url') if self.current_profile else None
        if not resume_url:
            self.print_error("No resume URL found in your profile")
            self.print_info("Please add your Google Docs resume URL in Profile Management")
            self.pause()
            return
        
        self.print_info("Resume tailoring will create a customized version of your resume")
        self.print_info("tailored to a specific job description")
        
        print(f"\nCurrent resume: {resume_url[:60]}...")
        print("\nThis feature requires:")
        print("  1. Your resume URL in profile ✓")
        print("  2. A job description")
        print("  3. Google OAuth connection (token.json)")
        
        proceed = self.get_input("\nProceed? (y/n): ").strip().lower()
        
        if proceed == 'y':
            job_description = self.get_input("\nEnter job description (or paste text): ").strip()
            
            if not job_description:
                self.print_error("Job description is required")
                self.pause()
                return
            
            job_title = self.get_input("Job Title: ").strip() or "Position"
            company = self.get_input("Company Name: ").strip() or "Company"
            
            try:
                self.print_info("\nStarting resume tailoring... This may take 1-2 minutes")
                
                # Get user's full name for document naming
                user_full_name = f"{self.current_user.first_name}_{self.current_user.last_name}"
                
                # Call the tailoring function
                tailored_url = tailor_resume_and_return_url(
                    original_resume_url=resume_url,
                    job_description=job_description,
                    job_title=job_title,
                    company=company,
                    credentials=None,  # Will use token.json
                    mimikree_email=mimikree_email,
                    mimikree_password=mimikree_password,
                    user_full_name=user_full_name
                )
                
                if tailored_url:
                    self.print_success("\nResume tailored successfully!")
                    self._display_tailored_resume_download(tailored_url, company)
                else:
                    self.print_error("Resume tailoring failed - no URL returned")
                
                self.pause()
                
            except Exception as e:
                self.print_error(f"Resume tailoring failed: {str(e)}")
                logger.error(f"Resume tailoring error: {e}", exc_info=True)
                self.pause()
        else:
            return
    
    def _handle_latex_resume_tailoring(self):
        """Handle LaTeX resume tailoring workflow"""
        try:
            # Get LaTeX ZIP from database
            db = SessionLocal()
            try:
                profile = db.query(UserProfile).filter(UserProfile.user_id == self.current_user.id).first()
                if not profile or not profile.latex_zip_base64:
                    self.print_error("No LaTeX resume ZIP found in your profile")
                    self.print_info("Please upload your LaTeX ZIP file in the web profile page first")
                    self.pause()
                    return
                
                latex_zip_base64 = profile.latex_zip_base64
                main_tex_path = profile.latex_main_tex_path
                if not main_tex_path:
                    self.print_error("Main TeX file path not set")
                    self.pause()
                    return
            finally:
                db.close()
            
            self.print_info("LaTeX resume tailoring will customize your LaTeX resume")
            self.print_info("for a specific job and generate a tailored ZIP file")
            
            proceed = self.get_input("\nProceed? (y/n): ").strip().lower()
            if proceed != 'y':
                return
            
            job_description = self.get_input("\nEnter job description (or paste text): ").strip()
            if not job_description:
                self.print_error("Job description is required")
                self.pause()
                return
            
            job_title = self.get_input("Job Title: ").strip() or "Position"
            company = self.get_input("Company Name: ").strip() or "Company"
            
            self.print_info("\nStarting resume tailoring... This may take 1-2 minutes")
            print("[INIT] Systematic tailoring modules loaded successfully")
            
            # Call LaTeX tailoring function
            result = tailor_latex_resume_from_base64(
                latex_zip_base64=latex_zip_base64,
                main_tex_file=main_tex_path,
                job_description=job_description,
                job_title=job_title,
                company=company
            )
            
            print(f"{Colors.OKGREEN}[OK]{Colors.ENDC}")
            print("Resume tailored successfully!")
            self._display_latex_tailored_resume(result, company)
            
        except Exception as e:
            self.print_error(f"LaTeX resume tailoring failed: {str(e)}")
            logger.error(f"LaTeX tailoring error: {e}", exc_info=True)
        finally:
            self.pause()
    
    def _display_latex_tailored_resume(self, tailoring_result: dict, company: str):
        """Display LaTeX tailored resume information and generate PDF if possible"""
        import base64
        import tempfile
        import os
        import subprocess
        import zipfile
        
        print(f"\n{Colors.OKGREEN}{'='*60}{Colors.ENDC}")
        print(f"{Colors.BOLD}{Colors.OKGREEN}✨ Resume Tailored Successfully!{Colors.ENDC}")
        print(f"{Colors.OKGREEN}{'='*60}{Colors.ENDC}")
        
        # Save ZIP file
        tailored_zip_base64 = tailoring_result.get('tailored_zip_base64')
        if tailored_zip_base64:
            zip_filename = tailoring_result.get('tailored_zip_filename', f'tailored_{company}.zip')
            zip_path = os.path.join(tempfile.gettempdir(), zip_filename)
            
            with open(zip_path, 'wb') as f:
                f.write(base64.b64decode(tailored_zip_base64))
            
            # Try to compile PDF
            pdf_path = None
            try:
                # Extract ZIP to temp directory
                extract_dir = os.path.join(tempfile.gettempdir(), f'latex_compile_{company}')
                os.makedirs(extract_dir, exist_ok=True)
                
                with zipfile.ZipFile(zip_path, 'r') as zip_ref:
                    zip_ref.extractall(extract_dir)
                
                # Find main tex file
                main_tex = tailoring_result.get('main_tex_file', 'resume.tex')
                main_tex_path = os.path.join(extract_dir, main_tex)
                
                if os.path.exists(main_tex_path):
                    # Compile PDF using MiKTeX pdflatex (Windows)
                    pdflatex_path = r"C:\Users\proto\AppData\Local\Programs\MiKTeX\miktex\bin\x64\pdflatex.EXE"
                    compile_result = subprocess.run(
                        [pdflatex_path, "-interaction=nonstopmode", "-halt-on-error", main_tex],
                        cwd=extract_dir,
                        capture_output=True,
                        text=True,
                        timeout=120,
                    )

                    # If LaTeX failed, surface stdout+stderr (LaTeX often writes errors to stdout)
                    if compile_result.returncode != 0:
                        out = (compile_result.stdout or "") + "\n" + (compile_result.stderr or "")
                        tail = out.strip()[-1500:] if out.strip() else "(no output)"
                        raise Exception(
                            f"pdflatex failed (exit {compile_result.returncode}). "
                            f"Output tail:\n{tail}"
                        )

                    # Check if PDF was generated
                    pdf_name = main_tex.replace('.tex', '.pdf')
                    pdf_temp_path = os.path.join(extract_dir, pdf_name)

                    if os.path.exists(pdf_temp_path):
                        # Move PDF to temp directory with better name
                        pdf_path = os.path.join(tempfile.gettempdir(), f"tailored_{company}.pdf")
                        import shutil
                        shutil.copy2(pdf_temp_path, pdf_path)
                    else:
                        raise Exception(f"PDF file not generated (expected at: {pdf_temp_path})")
                        
            except Exception as e:
                self.print_warning("\nPDF could not be generated from tailored LaTeX.")
                self.print_info(f"Reason: {str(e)}")
                logger.warning(f"PDF generation failed: {e}")
            
            # Display results
            if pdf_path and os.path.exists(pdf_path):
                print(f"\n{Colors.BOLD}📄 Tailored Resume PDF:{Colors.ENDC}")
                print(f"   Path: {Colors.OKCYAN}{pdf_path}{Colors.ENDC}")
                
                # Open PDF
                download = self.get_input("\n   Open PDF now? (y/n, default: y): ").strip().lower()
                if download != 'n':
                    try:
                        subprocess.run(["start", pdf_path], shell=True)
                        self.print_success("   ✓ PDF opened in your default viewer!")
                    except Exception as e:
                        logger.error(f"Failed to open PDF: {e}")
                        self.print_warning(f"   Could not open automatically. Please open manually.")
            
            print(f"\n{Colors.BOLD}🧾 Tailored LaTeX ZIP:{Colors.ENDC}")
            print(f"   Path: {Colors.OKCYAN}{zip_path}{Colors.ENDC}")
            
            # Display stats
            keywords = tailoring_result.get('keywords', {})
            job_required = keywords.get('job_required', [])
            already_present = keywords.get('already_present', [])
            newly_added = keywords.get('newly_added', [])
            could_not_add = keywords.get('could_not_add', [])
            
            print(f"\n{Colors.BOLD}📊 Tailoring Stats:{Colors.ENDC}")
            
            if job_required:
                total_keywords = len(job_required)
                present_count = len(already_present) + len(newly_added)
                match_rate = (present_count / total_keywords * 100) if total_keywords > 0 else 0
                
                print(f"   Match Rate: {match_rate:.1f}%")
                print(f"   Keywords Present: {present_count}/{total_keywords}")
                print(f"   Keywords Added: {len(newly_added)}")
                print(f"   Keywords Missing: {len(could_not_add)}")
            
            print(f"{Colors.OKGREEN}{'='*60}{Colors.ENDC}")
    
    # ============================================================================
    # Job Search
    # ============================================================================
    
    def job_search_menu(self):
        """Job search menu"""
        self.clear_screen()
        self.print_header("JOB SEARCH")
        
        if not JOB_DISCOVERY_AVAILABLE:
            self.print_error("Job search feature is not available")
            self.print_info("Missing dependencies or configuration")
            self.pause()
            return
        
        self.print_info("Search for jobs across multiple sources (Indeed, LinkedIn, etc.)")
        
        print(f"\n{Colors.BOLD}Search Parameters:{Colors.ENDC}")
        
        keywords = self.get_input("Job Keywords (e.g., 'Software Engineer'): ").strip()
        if not keywords:
            self.print_error("Keywords are required")
            self.pause()
            return
        
        location = self.get_input("Location (optional, e.g., 'New York, NY'): ").strip()
        remote = self.get_input("Remote only? (y/n): ").strip().lower() == 'y'
        easy_apply = self.get_input("Easy Apply only? (y/n, default: n): ").strip().lower() == 'y'
        hours_old_str = self.get_input("Only jobs posted in last N hours? (optional): ").strip()
        hours_old = None
        if hours_old_str:
            try:
                hours_old = max(1, int(hours_old_str))
            except ValueError:
                self.print_warning("Invalid hours value. Using no recency filter.")
                hours_old = None
        max_results = self.get_input("Max results (default 20): ").strip()
        
        try:
            max_results = int(max_results) if max_results else 20
        except ValueError:
            max_results = 20
        
        try:
            self.print_info("\nSearching for jobs... This may take a moment")
            
            # Initialize job discovery agent
            agent = MultiSourceJobDiscoveryAgent(user_id=str(self.current_user.id))
            
            # Use search_all_sources with manual parameters
            # This searches all configured job sources and ranks by resume relevance
            result = agent.search_all_sources(
                min_relevance_score=30,
                manual_keywords=keywords if keywords else None,
                manual_location=location if location else None,
                manual_remote=remote,
                manual_search_overrides={
                    "easy_apply": easy_apply,
                    "hours_old": hours_old
                }
            )
            
            results = result.get('data', [])
            
            if not results:
                self.print_warning("No jobs found matching your criteria")
                self.print_info(f"Sources searched: {result.get('sources', {})}")
                self.pause()
                return
            
            # Display results
            self.clear_screen()
            self.print_header(f"SEARCH RESULTS ({len(results)} jobs found)")
            self.print_info(f"Average relevance score: {result.get('average_score', 0):.1f}%")
            self.print_info(f"Sources: {result.get('sources', {})}\n")
            
            for i, job in enumerate(results[:max_results], 1):
                print(f"\n{Colors.BOLD}{i}. {job.get('title', 'Unknown Title')}{Colors.ENDC}")
                print(f"   Company: {job.get('company', 'Unknown')}")
                print(f"   Location: {job.get('location', 'Not specified')}")
                if job.get('salary') and job.get('salary') != 'null':
                    print(f"   Salary: {job.get('salary')}")
                
                # Display multiple application links
                apply_links = job.get('apply_links', {})
                if apply_links:
                    primary_url = apply_links.get('primary', '')
                    if primary_url:
                        print(f"   {Colors.OKGREEN}Apply → {primary_url}{Colors.ENDC}")
                    
                    # Show alternative links (Indeed, LinkedIn)
                    indeed_url = apply_links.get('indeed', '')
                    linkedin_url = apply_links.get('linkedin', '')
                    if indeed_url or linkedin_url:
                        print(f"   {Colors.OKCYAN}Also on:{Colors.ENDC}", end='')
                        if indeed_url:
                            print(f" Indeed | ", end='')
                        if linkedin_url:
                            print(f"LinkedIn", end='')
                        print()  # New line
                else:
                    # Fallback to single URL
                    job_url = job.get('job_url') or job.get('url')
                    if job_url:
                        print(f"   {Colors.OKCYAN}Apply: {job_url}{Colors.ENDC}")
                
                print(f"   Source: {job.get('source', 'Unknown')}")
                
                if job.get('relevance_score'):
                    score = job.get('relevance_score', 0)
                    print(f"   Relevance: {score:.1f}%")
            
            print("\n" + "=" * 60)
            
            # Option to save or apply
            action = self.get_input("\nOptions: [A]pply to job, [S]ave results, [Q]uit: ").strip().lower()
            
            if action == 'a':
                job_num = self.get_input("Enter job number to apply: ").strip()
                if job_num.isdigit() and 1 <= int(job_num) <= len(results):
                    selected_job = results[int(job_num) - 1]
                    
                    # Automatically use the first/primary link
                    job_url = None
                    apply_links = selected_job.get('apply_links', {})
                    if apply_links:
                        # Use primary link first
                        job_url = apply_links.get('primary') or apply_links.get('indeed') or apply_links.get('linkedin')
                    if not job_url:
                        # Fallback to direct url fields
                        job_url = selected_job.get('job_url') or selected_job.get('url')
                    
                    if job_url:
                        self.print_info(f"Opening auto-apply for: {selected_job.get('title')}")
                        self.print_info(f"Using URL: {job_url}")
                        self.pause()
                        # Redirect to auto-apply
                        asyncio.run(self.auto_apply_single(job_url))
                    else:
                        self.print_error("No URL available for this job")
            elif action == 's':
                # Save results to file
                filename = f"job_search_results_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
                with open(filename, 'w') as f:
                    json.dump(results, f, indent=2)
                self.print_success(f"Results saved to {filename}")
            
            self.pause()
            
        except Exception as e:
            self.print_error(f"Job search failed: {str(e)}")
            logger.error(f"Job search error: {e}", exc_info=True)
            self.pause()
    
    # ============================================================================
    # Auto Apply
    # ============================================================================
    
    async def auto_apply_menu(self):
        """Auto apply to job(s)"""
        self.clear_screen()
        self.print_header("AUTO APPLY TO JOB(S)")
        
        if not JOB_APPLICATION_AVAILABLE:
            self.print_error("Auto-apply feature is not available")
            self.print_info("Missing dependencies or configuration")
            self.pause()
            return

        if not self._ensure_resume_ready_for_auto_apply():
            self.pause()
            return
        
        self.print_info("Automatically fill and submit job applications")
        self.print_info("You can apply to multiple jobs (up to 10) in one batch\n")
        
        # Ask user how many jobs they want to apply to
        num_jobs_str = self.get_input("How many jobs do you want to apply to? (1-10, default: 1): ").strip()
        
        if not num_jobs_str:
            num_jobs = 1
        else:
            try:
                num_jobs = int(num_jobs_str)
                if num_jobs < 1:
                    self.print_error("Number of jobs must be at least 1")
                    self.pause()
                    return
                if num_jobs > 10:
                    self.print_warning("Maximum 10 jobs allowed. Setting to 10.")
                    num_jobs = 10
            except ValueError:
                self.print_error("Invalid number. Please enter a number between 1 and 10.")
                self.pause()
                return
        
        # Collect job URLs
        job_urls = []
        self.print_info(f"\nPlease enter {num_jobs} job application URL(s):")
        
        for i in range(num_jobs):
            job_url = self.get_input(f"  Job #{i+1} URL: ").strip()
            
            if not job_url:
                self.print_warning(f"Skipping empty URL for job #{i+1}")
                continue
            
            # Basic URL validation
            if not job_url.startswith(('http://', 'https://')):
                self.print_warning(f"Invalid URL for job #{i+1} (must start with http:// or https://)")
                continue
            
            job_urls.append(job_url)
        
        if not job_urls:
            self.print_error("No valid job URLs provided")
            self.pause()
            return
        
        self.print_success(f"\n✓ Collected {len(job_urls)} valid job URL(s)")
        
        # Get batch preferences
        print()
        headless = self.get_input("Run in headless mode? (y/n, default: n): ").strip().lower() == 'y'
        
        # Ask for tailor preference
        tailor_option = self.get_input("Tailor resume: (a)ll jobs, (n)one, or (i)ndividual choice? (a/n/i, default: n): ").strip().lower()
        
        tailor_settings = []
        if tailor_option == 'a':
            # Tailor all jobs
            tailor_settings = [True] * len(job_urls)
            self.print_info("✓ Will tailor resume for all jobs")
        elif tailor_option == 'i':
            # Ask for each job
            self.print_info("\nTailor resume for each job:")
            for i, url in enumerate(job_urls):
                tailor = self.get_input(f"  Tailor for job #{i+1}? (y/n, default: n): ").strip().lower() == 'y'
                tailor_settings.append(tailor)
        else:
            # No tailoring
            tailor_settings = [False] * len(job_urls)
            self.print_info("✓ Will not tailor resume for any jobs")

        if any(tailor_settings):
            mimikree_email, mimikree_password = self.ensure_mimikree_connected_for_tailoring()
            if not mimikree_email or not mimikree_password:
                self.pause()
                return
        
        # Process batch application
        await self.auto_apply_batch(job_urls, tailor_settings, headless)
    
    async def auto_apply_batch(self, job_urls: list, tailor_settings: list, headless: bool = False):
        """Apply to multiple jobs sequentially in batch mode"""
        total_jobs = len(job_urls)
        successful_applications = []
        failed_applications = []
        detailed_results = []  # Track detailed info for each application
        
        self.print_info(f"\n{'='*60}")
        self.print_info(f"BATCH APPLICATION MODE")
        self.print_info(f"{'='*60}")
        self.print_info(f"Total jobs to apply: {total_jobs}")
        self.print_info(f"Headless mode: {'Yes' if headless else 'No (browsers will be visible)'}")
        self.print_info(f"{'='*60}\n")
        
        self.print_warning("⚠️  All browser windows will remain open during the process")
        self.print_warning("⚠️  Do not close the browser windows manually")
        self.print_info("\nStarting batch application process...\n")
        
        try:
            # Use a single playwright instance for all jobs
            async with async_playwright() as playwright:
                
                for idx, job_url in enumerate(job_urls, start=1):
                    tailor = tailor_settings[idx - 1]
                    
                    self.print_header(f"JOB {idx}/{total_jobs}")
                    self.print_info(f"URL: {job_url}")
                    self.print_info(f"Tailor Resume: {'Yes' if tailor else 'No'}")
                    self.print_info("-" * 60)
                    
                    # Initialize result tracking for this job
                    job_result = {
                        'number': idx,
                        'job_url': job_url,
                        'job_title': f'Job #{idx}',
                        'company': 'Unknown',
                        'timestamp': datetime.now().isoformat(),
                        'success': False,
                        'submitted': False,
                        'fields_filled': 0,
                        'field_details': [],
                        'error': None,
                        'tailored': tailor
                    }
                    
                    try:
                        self.print_info("Opening browser and starting application...")
                        self.print_info(f"User ID: {self.current_user.id}")
                        self.print_info(f"Persistent profile: {'Yes' if True else 'No'}")
                        
                        # Pre-fetch job description before opening the application form.
                        # Application form pages (Lever/Greenhouse/Workday) typically have no
                        # description text — the listing page does.  We fetch it here so the
                        # tailoring agent always has quality context.
                        pre_fetched_desc = None
                        if tailor:
                            self.print_info("   📄 Pre-fetching job description for tailoring...")
                            try:
                                pre_fetched_desc = await asyncio.to_thread(
                                    self._fetch_job_description_from_url, job_url
                                )
                                if pre_fetched_desc:
                                    self.print_success(f"   ✓ Description fetched ({len(pre_fetched_desc)} chars)")
                                else:
                                    self.print_info("   ↳ Will extract description from page during navigation")
                            except Exception as _pf_err:
                                logger.debug(f"Pre-fetch description error: {_pf_err}")

                        # Create agent for this job
                        agent = RefactoredJobAgent(
                            playwright=playwright,
                            headless=headless,
                            keep_open=True,
                            debug=True,
                            user_id=str(self.current_user.id),
                            tailor_resume=tailor,
                            mimikree_email=self._session_mimikree_email if tailor else None,
                            mimikree_password=self._session_mimikree_password if tailor else None,
                            job_url=job_url,
                            use_persistent_profile=True,
                            pre_fetched_description=pre_fetched_desc,
                        )

                        # Process the job application
                        await agent.process_link(job_url)
                        
                        # Display tailored resume download if tailoring was enabled
                        if tailor:
                            # Get profile from state machine context
                            profile = None
                            if hasattr(agent, 'state_machine') and agent.state_machine:
                                if hasattr(agent.state_machine, 'app_state'):
                                    profile = agent.state_machine.app_state.context.get('profile', {})
                            
                            if profile and profile.get('tailoring_metrics'):
                                self._display_tailored_resume_download(
                                    profile['tailoring_metrics'],
                                    job_result.get('company', 'Unknown')
                                )
                        
                        # Check if human intervention was needed
                        human_intervention_needed = getattr(agent, 'keep_browser_open_for_human', False)
                        
                        # Get action recorder data if available
                        if hasattr(agent, 'action_recorder') and agent.action_recorder:
                            actions = agent.action_recorder.actions
                            
                            # Extract filled fields
                            for action in actions:
                                if action.type in ['fill_field', 'enhanced_field_fill', 'select_option']:
                                    if action.success:
                                        job_result['fields_filled'] += 1
                                        job_result['field_details'].append({
                                            'label': action.field_label or 'Unknown',
                                            'value': action.value or '',
                                            'type': action.field_type or 'unknown'
                                        })
                            
                            # Check if submitted
                            submit_actions = [a for a in actions if 'submit' in a.type.lower() or 
                                            (a.type == 'click' and 'submit' in (a.element_text or '').lower())]
                            if submit_actions and len(submit_actions) > 0 and not human_intervention_needed:
                                job_result['submitted'] = True
                        
                        # If human intervention was needed, mark as not submitted
                        if human_intervention_needed:
                            job_result['submitted'] = False
                            job_result['error'] = 'Human intervention required - application not submitted'
                        
                        # Determine if truly successful
                        if job_result['fields_filled'] > 0 and job_result['submitted']:
                            job_result['success'] = True
                            self.record_application(job_url)
                            successful_applications.append({
                                'number': idx,
                                'url': job_url,
                                'tailored': tailor
                            })
                            self.print_success(f"✓ Job #{idx} submitted! ({job_result['fields_filled']} fields filled)")
                        else:
                            job_result['success'] = False
                            job_result['submitted'] = False
                            if not job_result.get('error'):
                                job_result['error'] = f"Application incomplete (only {job_result['fields_filled']} fields filled, not submitted)"
                            failed_applications.append({
                                'number': idx,
                                'url': job_url,
                                'error': job_result['error']
                            })
                            self.print_warning(f"⚠ Job #{idx} incomplete ({job_result['fields_filled']} fields filled)")
                        
                    except Exception as e:
                        error_str = str(e)
                        job_result['error'] = error_str
                        self.print_error(f"✗ Job #{idx} application failed: {error_str[:100]}")
                        logger.error(f"Job #{idx} auto apply error: {e}", exc_info=True)
                        failed_applications.append({
                            'number': idx,
                            'url': job_url,
                            'error': error_str
                        })
                    
                    # Add to detailed results
                    detailed_results.append(job_result)
                    
                    # Add separator between jobs
                    if idx < total_jobs:
                        self.print_info("\n" + "="*60 + "\n")
        
        except Exception as e:
            self.print_error(f"Batch process error: {str(e)}")
            logger.error(f"Batch application error: {e}", exc_info=True)
        
        # Save detailed progress report
        report_filename = f"batch_progress_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        try:
            report = {
                'report_type': 'batch_report',
                'generated_at': datetime.now().isoformat(),
                'total_jobs': total_jobs,
                'successful': len(successful_applications),
                'failed': len(failed_applications),
                'incomplete': len([j for j in detailed_results if not j['submitted']]),
                'applications': detailed_results
            }
            with open(report_filename, 'w') as f:
                json.dump(report, f, indent=2)
            self.print_info(f"✓ Progress report saved: {report_filename}")
        except Exception as e:
            logger.error(f"Failed to save batch report: {e}")
        
        # Display summary
        self.print_header("BATCH APPLICATION SUMMARY")
        self.print_info(f"Total jobs processed: {total_jobs}")
        self.print_success(f"Successful applications: {len(successful_applications)}")
        
        if successful_applications:
            self.print_info("\n✓ Successful Applications:")
            for app in successful_applications:
                self.print_info(f"  • Job #{app['number']}: {app['url'][:60]}...")
        
        if failed_applications:
            self.print_error(f"\nFailed applications: {len(failed_applications)}")
            for app in failed_applications:
                self.print_error(f"  • Job #{app['number']}: {app['url'][:60]}...")
                self.print_error(f"    Error: {app['error'][:100]}")
        
        self.print_info("\n" + "="*60)
        self.print_success(f"\n📄 Detailed Progress Report: {report_filename}")
        
        # Check for incomplete applications
        incomplete_count = len([j for j in detailed_results if not j['submitted']])
        if incomplete_count > 0:
            print("\n" + "=" * 60)
            self.print_warning(f"⚠️  {incomplete_count} application(s) were not fully submitted")
            self.print_info("\nWould you like to continue these applications manually?")
            self.print_info("This will open all incomplete applications in your")
            self.print_info("persistent browser profile (with logins preserved).")
            self.print_info("")
            self.print_warning("📝 NOTE: Fields will start blank (AI-filled data not preserved)")
            self.print_info("   TIP: If you want pre-filled fields, complete them DURING")
            self.print_info("        the first run when 'HUMAN INTERVENTION REQUIRED' appears")
            self.print_info("        (browser stays open with filled fields).")
            print("=" * 60)
            
            choice = self.get_input("\nOpen incomplete applications? (y/n): ").strip().lower()
            if choice == 'y':
                # Set flags for opening incomplete applications
                self._should_open_incomplete = True
                self._incomplete_report_file = report_filename
                await self._open_incomplete_applications(report_filename)
        else:
            self.print_info("\n" + "="*60)
            self.print_warning("⚠️  All browser windows are still open for your review")
            self.print_info("You can now manually review each application and close the browsers")
        
        self.pause()
    
    async def auto_apply_single(self, job_url: str):
        """Apply to a single job (helper method)"""
        if not JOB_APPLICATION_AVAILABLE:
            self.print_error("Auto-apply feature is not available")
            self.pause()
            return

        if not self._ensure_resume_ready_for_auto_apply():
            self.pause()
            return
        
        # Options
        headless = self.get_input("Run in headless mode? (y/n, default: n): ").strip().lower() == 'y'
        tailor = self.get_input("Tailor resume for this job? (y/n, default: n): ").strip().lower() == 'y'

        if tailor:
            mimikree_email, mimikree_password = self.ensure_mimikree_connected_for_tailoring()
            if not mimikree_email or not mimikree_password:
                self.pause()
                return
        
        try:
            self.print_info("\nStarting automated job application...")
            self.print_info("This will open a browser and fill the application")
            self.print_warning("You may need to complete CAPTCHA or final submission manually")

            # Pre-fetch job description before opening the application form.
            pre_fetched_desc = None
            if tailor:
                self.print_info("📄 Pre-fetching job description for tailoring...")
                try:
                    pre_fetched_desc = await asyncio.to_thread(
                        self._fetch_job_description_from_url, job_url
                    )
                    if pre_fetched_desc:
                        self.print_success(f"✓ Description fetched ({len(pre_fetched_desc)} chars)")
                    else:
                        self.print_info("↳ Will extract description from page during navigation")
                except Exception as _pf_err:
                    logger.debug(f"Pre-fetch description error: {_pf_err}")

            # Start the job application agent
            async with async_playwright() as playwright:
                agent = RefactoredJobAgent(
                    playwright=playwright,
                    headless=headless,
                    keep_open=True,
                    debug=True,
                    user_id=str(self.current_user.id),
                    tailor_resume=tailor,
                    mimikree_email=self._session_mimikree_email if tailor else None,
                    mimikree_password=self._session_mimikree_password if tailor else None,
                    job_url=job_url,
                    use_persistent_profile=True,
                    pre_fetched_description=pre_fetched_desc,
                )

                await agent.process_link(job_url)
            
            # Display tailored resume download if tailoring was enabled
            if tailor:
                # Get profile from state machine context
                profile = None
                if hasattr(agent, 'state_machine') and agent.state_machine:
                    if hasattr(agent.state_machine, 'app_state'):
                        profile = agent.state_machine.app_state.context.get('profile', {})
                
                if profile and profile.get('tailoring_metrics'):
                    self._display_tailored_resume_download(
                        profile['tailoring_metrics'],
                        'Company'  # Extract from job if available
                    )
            
            # Record application
            self.record_application(job_url)
            
            self.print_success("Application process completed!")
            self.print_info("Check the browser for final status")
            self.pause()
            
        except Exception as e:
            self.print_error(f"Auto apply failed: {str(e)}")
            logger.error(f"Auto apply error: {e}", exc_info=True)
            self.pause()
    
    def record_application(self, job_url: str):
        """Record job application in database"""
        try:
            # Extract company and title from URL (basic implementation)
            company = "Unknown Company"
            title = "Unknown Position"
            
            application = JobApplication(
                user_id=self.current_user.id,
                job_id=f"cli_{datetime.now().timestamp()}",
                company_name=company,
                job_title=title,
                job_url=job_url,
                status="completed",
                applied_at=datetime.now()
            )
            
            self.db.add(application)
            self.db.commit()
            
        except Exception as e:
            logger.error(f"Failed to record application: {e}", exc_info=True)
    
    def _display_tailored_resume_download(self, tailoring_metrics: dict, company: str):
        """
        Display tailored resume download information with interactive options
        
        Args:
            tailoring_metrics: Dict containing 'url', 'pdf_path', and other metrics
            company: Company name
        """
        try:
            pdf_path = tailoring_metrics.get('pdf_path')
            google_doc_url = tailoring_metrics.get('url')
            
            print(f"\n{Colors.OKGREEN}{'='*60}{Colors.ENDC}")
            print(f"{Colors.BOLD}{Colors.OKGREEN}✨ Resume Tailored Successfully!{Colors.ENDC}")
            print(f"{Colors.OKGREEN}{'='*60}{Colors.ENDC}")
            
            # Show download options
            if pdf_path and os.path.exists(pdf_path):
                print(f"\n{Colors.BOLD}📄 Download Tailored Resume:{Colors.ENDC}")
                print(f"   Path: {Colors.OKCYAN}{pdf_path}{Colors.ENDC}")
                
                # Interactive download option
                download = self.get_input("\n   Download now? (y/n, default: y): ").strip().lower()
                if download != 'n':
                    try:
                        # Windows PowerShell command to open file
                        import subprocess
                        subprocess.run(["start", pdf_path], shell=True)
                        self.print_success("   ✓ Resume opened in your default PDF viewer!")
                    except Exception as e:
                        logger.error(f"Failed to open PDF: {e}")
                        self.print_warning(f"   Could not open automatically. Please open manually:")
                        self.print_info(f"   {pdf_path}")
            
            # Show Google Doc URL
            if google_doc_url:
                print(f"\n{Colors.BOLD}🔗 Google Doc URL:{Colors.ENDC}")
                print(f"   {Colors.OKCYAN}{google_doc_url}{Colors.ENDC}")
            
            # Show tailoring stats if available
            match_stats = tailoring_metrics.get('match_stats', {})
            if match_stats:
                print(f"\n{Colors.BOLD}📊 Tailoring Stats:{Colors.ENDC}")
                match_pct = match_stats.get('match_percentage', 0)
                added = match_stats.get('added', 0)
                missing = match_stats.get('missing', 0)
                
                print(f"   Match Rate: {Colors.OKGREEN}{match_pct:.1f}%{Colors.ENDC}")
                if added > 0:
                    print(f"   Keywords Added: {Colors.OKGREEN}{added}{Colors.ENDC}")
                if missing > 0:
                    print(f"   Keywords Missing: {Colors.WARNING}{missing}{Colors.ENDC}")
            
            print(f"{Colors.OKGREEN}{'='*60}{Colors.ENDC}\n")
            
        except Exception as e:
            logger.error(f"Failed to display tailored resume info: {e}")
            self.print_warning("Resume tailored but display error occurred")
    
    def get_applied_job_urls(self) -> set:
        """
        Efficiently fetch all job URLs the user has previously applied to.
        Returns a set for O(1) lookup performance.
        
        Returns:
            set: Set of job URLs (strings) that have been applied to
        """
        try:
            # Query only the job_url column for this user (optimized query)
            # Filter out NULL urls and only get completed/in_progress applications
            applied_urls = self.db.query(JobApplication.job_url).filter(
                JobApplication.user_id == self.current_user.id,
                JobApplication.job_url.isnot(None),
                JobApplication.status.in_(['completed', 'in_progress', 'queued'])
            ).all()
            
            # Convert to set for O(1) lookups (flatten tuples to strings)
            url_set = {url[0] for url in applied_urls if url[0]}
            
            logger.info(f"Loaded {len(url_set)} previously applied job URLs for deduplication")
            return url_set
            
        except Exception as e:
            logger.error(f"Failed to fetch applied job URLs: {e}", exc_info=True)
            # Return empty set on error (fail gracefully)
            return set()
    
    # ============================================================================
    # Browser Profile Setup - One-Time Configuration
    # ============================================================================
    
    async def browser_profile_setup_menu(self):
        """Browser profile setup - one-time manual login to job boards"""
        from Agents.persistent_browser_manager import PersistentBrowserManager
        
        self.clear_screen()
        self.print_header("🌐 BROWSER PROFILE SETUP")
        
        print("\n" + "=" * 60)
        print("  WHY SETUP A PERSISTENT BROWSER PROFILE?")
        print("=" * 60)
        print("\n✓ Stay logged into job boards (LinkedIn, Indeed, etc.)")
        print("✓ Prevent bot detection and verification codes")
        print("✓ Build trust with job sites over time")
        print("✓ Use same profile for manual AND automated applications")
        print("✓ Resume applications exactly where you left off")
        print("\n" + "=" * 60)
        print("  ONE-TIME SETUP STEPS:")
        print("=" * 60)
        print("\n1. A browser window will open")
        print("2. Login to LinkedIn, Indeed, Glassdoor, etc.")
        print("3. Accept cookie consents")
        print("4. Complete any security verifications")
        print("5. Close browser when done")
        print("\nThis creates a persistent profile that the agent will use.")
        print("=" * 60 + "\n")
        
        proceed = self.get_input("Setup persistent profile now? (y/n): ").strip().lower()
        
        if proceed != 'y':
            print("\nSetup cancelled.")
            self.pause()
            return
        
        try:
            print(f"\n{Colors.OKBLUE}Initializing browser profile...{Colors.ENDC}")
            
            manager = PersistentBrowserManager()
            
            # Check if profile already exists
            profile_info = manager.get_profile_info(str(self.current_user.id))
            
            if profile_info['exists']:
                print(f"\n{Colors.WARNING}⚠️  Profile already exists:{Colors.ENDC}")
                print(f"  Size: {profile_info['size_mb']} MB")
                print(f"  Files: {profile_info['files_count']}")
                print(f"  Path: {profile_info['profile_path']}")
                
                choice = self.get_input("\n1. Continue with existing profile\n2. Reset and setup new\n3. Cancel\n\nChoice (1-3): ").strip()
                
                if choice == '2':
                    confirm = self.get_input("Delete existing profile? (y/n): ").strip().lower()
                    if confirm == 'y':
                        manager.delete_profile(str(self.current_user.id))
                        self.print_success("Existing profile deleted")
                    else:
                        print("\nSetup cancelled.")
                        self.pause()
                        return
                elif choice == '3':
                    print("\nSetup cancelled.")
                    self.pause()
                    return
            
            # Initialize profile for user
            print(f"\n{Colors.OKBLUE}🚀 Launching browser for setup...{Colors.ENDC}")
            print(f"User ID: {self.current_user.id}")
            print(f"Profile path: {manager.get_profile_path(str(self.current_user.id))}\n")
            
            context = await manager.initialize_profile_for_user(
                user_id=str(self.current_user.id),
                manual_setup=True
            )
            
            # Close browser
            try:
                await context.close()
                if hasattr(context, '_playwright'):
                    await context._playwright.stop()
                
                # IMPORTANT: Remove context from registry after closing
                # This prevents auto-apply from trying to reuse a closed context
                from Agents.persistent_browser_manager import PersistentBrowserManager
                PersistentBrowserManager.close_browser_for_user(str(self.current_user.id))
            except Exception as e:
                logger.warning(f"Error closing browser: {e}")
            
            # Show final stats
            profile_info = manager.get_profile_info(str(self.current_user.id))
            
            print(f"\n{Colors.OKGREEN}✓ Browser profile setup complete!{Colors.ENDC}\n")
            print(f"Profile details:")
            print(f"  Size: {profile_info['size_mb']} MB")
            print(f"  Files: {profile_info['files_count']}")
            print(f"  Location: {profile_info['profile_path']}")
            print(f"\nYou're now ready to use automated job applications!")
            print(f"The agent will use this profile and stay logged in.")
            
        except Exception as e:
            self.print_error(f"Profile setup failed: {str(e)}")
            logger.error(f"Browser profile setup error: {e}", exc_info=True)
        
        self.pause()
    
    # ============================================================================
    # Continuous Auto Apply - 100% Automation
    # ============================================================================

    def _sanitize_search_query(self, query: str, fallback_keywords: str = "") -> str:
        """
        Keep AI-generated queries broad and JobSpy-friendly.
        Removes low-signal phrases and trims over-specific wording.
        """
        if not query:
            return fallback_keywords or ""

        cleaned = str(query).strip()
        cleaned = re.sub(r"\s+", " ", cleaned)

        # Remove phrases that hurt recall in job board searches.
        removal_patterns = [
            r"\bin any location\b",
            r"\bin remote locations?\b",
            r"\bin all locations?\b",
            r"\bjobs for\b",
        ]
        for pattern in removal_patterns:
            cleaned = re.sub(pattern, " ", cleaned, flags=re.IGNORECASE)

        # Remove obvious stopwords used by LLM-generated verbose phrases.
        cleaned = re.sub(r"\b(entry level|mid level|senior)\b", " ", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\s+", " ", cleaned).strip(" ,.-")

        # Avoid over-constraining queries; keep first 8 tokens max.
        tokens = cleaned.split()
        if len(tokens) > 8:
            cleaned = " ".join(tokens[:8])

        return cleaned or (fallback_keywords or "")
    
    async def continuous_auto_apply_menu(self):
        """Continuous auto-apply menu - 100% automation"""
        self.clear_screen()
        self.print_header("🚀 100% AUTO JOB APPLY - CONTINUOUS MODE")
        
        if not JOB_APPLICATION_AVAILABLE or not JOB_DISCOVERY_AVAILABLE:
            self.print_error("Required features are not available")
            self.print_info("Missing job application or job discovery modules")
            self.pause()
            return
        
        if not self._ensure_resume_ready_for_auto_apply():
            self.pause()
            return
        
        self.print_warning("⚠️  WARNING: This mode will run continuously and AUTOMATICALLY SUBMIT applications!")
        self.print_info("\nThis mode will:")
        self.print_info("  • Continuously search for relevant jobs")
        self.print_info("  • Automatically tailor your resume for each job")
        self.print_info("  • Fill and SUBMIT applications automatically")
        self.print_info("  • Handle rate limits gracefully (pause & retry)")
        self.print_info("  • Rotate proxies to avoid IP bans")
        self.print_info("  • Generate detailed progress reports")
        self.print_info("\nYou can stop anytime by pressing Ctrl+C\n")
        
        # Get search parameters
        print(f"\n{Colors.BOLD}Configuration:{Colors.ENDC}")
        keywords = self.get_input("Job Keywords (e.g., 'Software Engineer'): ").strip()
        if not keywords:
            self.print_error("Keywords are required")
            self.pause()
            return
        
        location = self.get_input("Location (optional, leave blank for any): ").strip()
        remote = self.get_input("Remote only? (y/n, default: n): ").strip().lower() == 'y'
        easy_apply = self.get_input("Easy Apply only? (y/n, default: n): ").strip().lower() == 'y'
        hours_old_str = self.get_input("Only jobs posted in last N hours? (optional): ").strip()
        hours_old = None
        if hours_old_str:
            try:
                hours_old = max(1, int(hours_old_str))
            except ValueError:
                self.print_warning("Invalid hours value. Using no recency filter.")
                hours_old = None
        
        # Ask for tailor resume preference
        tailor_all = self.get_input("Tailor resume for each job? (y/n, default: y): ").strip().lower()
        tailor_all = tailor_all != 'n'  # Default to yes

        if tailor_all:
            mimikree_email, mimikree_password = self.ensure_mimikree_connected_for_tailoring()
            if not mimikree_email or not mimikree_password:
                self.pause()
                return
        
        # Headless mode - default to visible so users can see and intervene
        headless = self.get_input("Run in headless mode? (y/n, default: n): ").strip().lower()
        headless = headless == 'y'  # Default to NO (visible mode)
        
        # Proxy configuration
        use_proxies = self.get_input("Use proxies to avoid IP bans? (y/n, default: n): ").strip().lower() == 'y'
        proxy_manager = None
        
        if use_proxies:
            self.print_info("\nProxy Configuration:")
            self.print_info("  You can provide proxies via:")
            self.print_info("  1. Environment variable: PROXY_LIST='proxy1:8080,proxy2:8080'")
            self.print_info("  2. File: proxies.txt (one per line)")
            self.print_info("  3. Manual entry now")
            
            proxy_choice = self.get_input("\nHow to configure? (env/file/manual/skip): ").strip().lower()
            
            if proxy_choice == 'manual':
                self.print_info("Enter proxies (format: host:port or user:pass@host:port)")
                self.print_info("Enter empty line when done:")
                proxies = []
                while True:
                    proxy = self.get_input(f"  Proxy #{len(proxies)+1}: ").strip()
                    if not proxy:
                        break
                    proxies.append(proxy)
                
                if proxies:
                    try:
                        from Agents.proxy_manager import ProxyManager
                        proxy_manager = ProxyManager(proxies, rotation_strategy="round_robin")
                        self.print_success(f"✓ {len(proxies)} proxies configured")
                    except Exception as e:
                        self.print_error(f"Failed to setup proxies: {e}")
            
            elif proxy_choice == 'file':
                proxy_file = self.get_input("Proxy file path (default: proxies.txt): ").strip() or "proxies.txt"
                if os.path.exists(proxy_file):
                    try:
                        from Agents.proxy_manager import ProxyManager
                        proxy_manager = ProxyManager.from_file(proxy_file)
                        self.print_success(f"✓ Proxies loaded from {proxy_file}")
                    except Exception as e:
                        self.print_error(f"Failed to load proxies: {e}")
                else:
                    self.print_error(f"File not found: {proxy_file}")
            
            elif proxy_choice == 'env':
                try:
                    from Agents.proxy_manager import ProxyManager
                    proxy_manager = ProxyManager.from_env()
                    if proxy_manager.proxies:
                        self.print_success(f"✓ {len(proxy_manager.proxies)} proxies loaded from environment")
                    else:
                        self.print_warning("No proxies found in environment")
                except Exception as e:
                    self.print_error(f"Failed to setup proxies: {e}")
        
        # Max applications per session
        max_apps_str = self.get_input("Max applications per session (default: 50): ").strip()
        try:
            max_apps = int(max_apps_str) if max_apps_str else 50
        except ValueError:
            max_apps = 50
        
        # Confirm before starting
        print(f"\n{Colors.BOLD}Summary:{Colors.ENDC}")
        print(f"  Keywords: {keywords}")
        print(f"  Location: {location or 'Any'}")
        print(f"  Remote: {'Yes' if remote else 'No'}")
        print(f"  Easy Apply: {'Yes' if easy_apply else 'No'}")
        print(f"  Hours Old Filter: {hours_old if hours_old is not None else 'Any'}")
        print(f"  Tailor Resume: {'Yes' if tailor_all else 'No'}")
        print(f"  Headless: {'Yes' if headless else 'No'}")
        print(f"  Proxies: {proxy_manager.get_stats()['active_proxies'] if proxy_manager else 'None (direct connection)'}")
        print(f"  Max Applications: {max_apps}")
        print(f"  Job Discovery: Every 1 hour")
        
        confirm = self.get_input(f"\n{Colors.WARNING}Start continuous automation? (type 'START' to confirm): {Colors.ENDC}").strip()
        
        if confirm.upper() != 'START':
            self.print_info("Cancelled")
            self.pause()
            return
        
        # Start the automation engine
        await self.run_continuous_automation(
            keywords=keywords,
            location=location,
            remote=remote,
            easy_apply=easy_apply,
            hours_old=hours_old,
            tailor_resume=tailor_all,
            headless=headless,
            max_applications=max_apps,
            proxy_manager=proxy_manager
        )
    
    async def run_continuous_automation(
        self,
        keywords: str,
        location: str,
        remote: bool,
        easy_apply: bool,
        hours_old: Optional[int],
        tailor_resume: bool,
        headless: bool,
        max_applications: int,
        proxy_manager=None
    ):
        """Run the continuous automation engine"""
        self.print_header("🚀 STARTING AUTOMATION ENGINE")
        
        # Display proxy info if available
        if proxy_manager:
            stats = proxy_manager.get_stats()
            self.print_success(f"✓ Proxy rotation enabled: {stats['active_proxies']} proxies ready")
        
        # Optimize search query using Gemini AI
        optimized_keywords = keywords
        query_variations = [keywords]
        profile_dict = None
        enriched_search_params = {
            "keywords": keywords,
            "location": location,
            "remote": remote,
            "easy_apply": easy_apply,
            "hours_old": hours_old
        }
        
        if QUERY_OPTIMIZER_AVAILABLE:
            try:
                from Agents.gemini_query_optimizer import GeminiQueryOptimizer
                optimizer = GeminiQueryOptimizer()
                
                # Get optimized queries (pass profile dict, not full object)
                if hasattr(self, 'current_profile') and self.current_profile:
                    # Convert SQLAlchemy model to dict if needed
                    if hasattr(self.current_profile, '__dict__'):
                        profile_dict = {k: v for k, v in self.current_profile.__dict__.items() if not k.startswith('_')}
                    else:
                        profile_dict = self.current_profile
                
                optimization_result = optimizer.optimize_search_query(
                    keywords, 
                    location, 
                    profile_dict
                )
                
                if optimization_result and optimization_result.get('success'):
                    raw_primary = optimization_result['primary_query']
                    raw_variations = [raw_primary] + optimization_result.get('variations', [])

                    normalized_variations = []
                    seen_queries = set()
                    for q in raw_variations:
                        normalized = self._sanitize_search_query(q, keywords)
                        if normalized and normalized.lower() not in seen_queries:
                            normalized_variations.append(normalized)
                            seen_queries.add(normalized.lower())

                    query_variations = normalized_variations or [keywords]
                    optimized_keywords = query_variations[0]
                    
                    method = optimization_result.get('method', 'unknown')
                    if method == 'gemini_ai':
                        self.print_success(f"🤖 AI-optimized query: '{keywords}' → '{optimized_keywords}'")
                    elif method == 'rule_based':
                        self.print_success(f"✓ Rule-based optimized query: '{keywords}' → '{optimized_keywords}'")
                    else:
                        self.print_success(f"✓ Optimized query: '{keywords}' → '{optimized_keywords}'")
                    
                    if len(query_variations) > 1:
                        self.print_info(f"✓ Generated {len(query_variations)-1} alternative queries")
                        # Show first alternative as preview
                        if len(query_variations) > 1:
                            self.print_info(f"  Alt 1: '{query_variations[1]}'")
                else:
                    error_msg = optimization_result.get('error', 'Unknown error') if optimization_result else 'No result returned'
                    self.print_warning(f"⚠️  Query optimization failed: {error_msg}")
                    self.print_info(f"   Using original query: '{keywords}'")
                    
            except Exception as e:
                self.print_warning(f"⚠️  Query optimization error: {str(e)}")
                logger.error(f"Query optimization error: {e}", exc_info=True)
        else:
            self.print_info("ℹ️  Using original keywords (optimizer not available)")

        if QUERY_OPTIMIZER_AVAILABLE:
            try:
                optimizer = GeminiQueryOptimizer()
                param_result = optimizer.enrich_jobspy_parameters(
                    user_keywords=optimized_keywords,
                    location=location,
                    profile_data=profile_dict,
                    remote=remote,
                    user_easy_apply=easy_apply,
                    user_hours_old=hours_old
                )

                ai_params = (param_result or {}).get("params", {})
                if ai_params:
                    enriched_search_params.update(ai_params)
                    # User values should always win for explicit toggles
                    enriched_search_params["easy_apply"] = easy_apply
                    if hours_old is not None:
                        enriched_search_params["hours_old"] = hours_old

                    self.print_success("🤖 AI-enriched search parameters enabled")
                    self.print_info(
                        f"  Location: {enriched_search_params.get('location') or 'Any'} | "
                        f"Job Type: {enriched_search_params.get('job_type') or 'Any'} | "
                        f"Hours Old: {enriched_search_params.get('hours_old') or 'Any'} | "
                        f"Easy Apply: {'Yes' if enriched_search_params.get('easy_apply') else 'No'}"
                    )
            except Exception as e:
                self.print_warning(f"⚠️  Parameter enrichment error: {str(e)}")
                logger.error(f"Parameter enrichment error: {e}", exc_info=True)
        
        # Initialize tracking
        automation_state = {
            'start_time': datetime.now(),
            'applications_submitted': 0,
            'applications_failed': 0,
            'jobs_discovered': 0,
            'jobs_processed': 0,
            'rate_limit_hits': 0,
            'running': True,
            'progress_log': [],
            'original_keywords': keywords,
            'optimized_keywords': optimized_keywords,
            'query_variations': query_variations,
            'enriched_search_params': enriched_search_params,
            'current_query_index': 0,  # Track which variation we're using
            'last_job_discovery': None,  # Track when we last searched for jobs
            'job_discovery_interval_seconds': 3600  # Search for jobs every 1 hour (3600 seconds)
        }
        
        # Create progress report file
        report_filename = f"automation_progress_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        
        # Job queue (deque for efficient popleft)
        job_queue = deque()
        processed_urls = set()
        
        # Load previously applied job URLs for deduplication (O(1) lookups with set)
        self.print_info("📋 Loading application history for deduplication...")
        previously_applied_urls = self.get_applied_job_urls()
        if previously_applied_urls:
            self.print_success(f"✓ Loaded {len(previously_applied_urls)} previously applied jobs")
            self.print_info("  → These jobs will be automatically skipped")
        else:
            self.print_info("  → No previous applications found (fresh start)")
        
        # Handle Ctrl+C gracefully
        def signal_handler(sig, frame):
            self.print_warning("\n\n⚠️  Stopping automation... (finishing current job)")
            automation_state['running'] = False
        
        # Register signal handler (Windows-compatible)
        try:
            signal.signal(signal.SIGINT, signal_handler)
        except Exception:
            pass  # Skip if signal handling fails
        
        self.print_success("✓ Automation engine initialized")
        self.print_info(f"✓ Progress report: {report_filename}")
        self.print_info("✓ Press Ctrl+C to stop gracefully\n")
        
        try:
            while automation_state['running'] and automation_state['applications_submitted'] < max_applications:
                
                # Step 1: Job Discovery (every 1 hour OR first time)
                current_time = datetime.now()
                time_since_last_discovery = None
                should_discover_jobs = False
                
                if automation_state['last_job_discovery'] is None:
                    # First time - always discover jobs
                    should_discover_jobs = True
                else:
                    # Check if 1 hour has passed since last discovery
                    time_since_last_discovery = (current_time - automation_state['last_job_discovery']).total_seconds()
                    if time_since_last_discovery >= automation_state['job_discovery_interval_seconds']:
                        should_discover_jobs = True
                
                if should_discover_jobs:
                    self.print_info(f"\n{'='*60}")
                    if automation_state['last_job_discovery'] is None:
                        self.print_info("🔍 DISCOVERING NEW JOBS... (Initial Search)")
                    else:
                        hours_since = time_since_last_discovery / 3600
                        self.print_info(f"🔍 DISCOVERING NEW JOBS... ({hours_since:.1f} hours since last search)")
                    self.print_info(f"{'='*60}")
                    
                    # Try all remaining query variations in this discovery window.
                    current_idx = automation_state.get('current_query_index', 0)
                    all_queries = automation_state.get('query_variations', [automation_state['optimized_keywords']])
                    if current_idx >= len(all_queries):
                        current_idx = 0
                    queries_to_try = all_queries[current_idx:]
                    
                    try:
                        # Initialize job discovery agent with proxy manager
                        agent = MultiSourceJobDiscoveryAgent(
                            user_id=str(self.current_user.id),
                            proxy_manager=proxy_manager
                        )
                        new_count = 0
                        skipped_already_applied = 0
                        skipped_duplicate = 0
                        found_with_query_index = None

                        for idx, query_to_use in enumerate(queries_to_try, start=current_idx):
                            if idx > current_idx:
                                self.print_info(f"Trying alternative query #{idx}: '{query_to_use}'")
                            elif idx > 0:
                                self.print_info(f"Using alternative query #{idx}: '{query_to_use}'")
                            else:
                                self.print_info(f"Using optimized query: '{query_to_use}'")

                            search_overrides = dict(automation_state.get('enriched_search_params', {}))
                            search_overrides["keywords"] = query_to_use
                            if not search_overrides.get("location") and location:
                                search_overrides["location"] = location

                            result = agent.search_all_sources(
                                min_relevance_score=30,
                                manual_keywords=query_to_use,
                                manual_location=search_overrides.get("location") if search_overrides.get("location") else None,
                                manual_remote=remote,
                                manual_search_overrides=search_overrides
                            )

                            new_jobs = result.get('data', [])
                            if len(new_jobs) == 0:
                                if idx < len(all_queries) - 1:
                                    self.print_warning("No jobs with current query, trying next alternative now...")
                                    continue
                                self.print_warning("No jobs found with any query variation in this cycle")
                                break

                            found_with_query_index = idx
                            for job in new_jobs:
                                job_url = self._extract_job_url(job)
                                if not job_url:
                                    continue

                                # Check if already applied in past (O(1) lookup in set)
                                if job_url in previously_applied_urls:
                                    skipped_already_applied += 1
                                    continue

                                # Check if already in current session queue
                                if job_url in processed_urls:
                                    skipped_duplicate += 1
                                    continue

                                # New job - add to queue!
                                job_queue.append({
                                    'url': job_url,
                                    'title': job.get('title', 'Unknown'),
                                    'company': job.get('company', 'Unknown'),
                                    'description': job.get('description', ''),
                                    'relevance_score': job.get('relevance_score', 0),
                                    'search_query': query_to_use
                                })
                                processed_urls.add(job_url)
                                new_count += 1

                            # Stop once we find jobs for a variation in this cycle.
                            if new_count > 0:
                                break

                        if found_with_query_index is not None:
                            automation_state['current_query_index'] = found_with_query_index
                        else:
                            # Reset to primary query for the next discovery cycle.
                            automation_state['current_query_index'] = 0

                        automation_state['jobs_discovered'] += new_count
                        self.print_success(f"✓ Found {new_count} new jobs (Queue: {len(job_queue)})")
                        
                        # Show deduplication stats
                        if skipped_already_applied > 0:
                            self.print_info(f"  ↳ Skipped {skipped_already_applied} jobs (already applied)")
                        if skipped_duplicate > 0:
                            self.print_info(f"  ↳ Skipped {skipped_duplicate} jobs (duplicates in current session)")
                        
                        # Mark discovery timestamp
                        automation_state['last_job_discovery'] = datetime.now()
                        
                        # Show next discovery time
                        next_discovery = automation_state['last_job_discovery'] + timedelta(seconds=automation_state['job_discovery_interval_seconds'])
                        self.print_info(f"📅 Next job search at: {next_discovery.strftime('%I:%M %p')}")
                        
                    except Exception as e:
                        self.print_error(f"Job discovery error: {str(e)}")
                        logger.error(f"Job discovery error: {e}", exc_info=True)
                        
                        # Check for rate limit
                        if self._is_rate_limit_error(e):
                            automation_state['rate_limit_hits'] += 1
                            await self._handle_rate_limit(automation_state)
                            continue
                    
                    # Small delay after discovery
                    await asyncio.sleep(2)
                
                # Step 2: Process jobs from queue
                if not job_queue:
                    if automation_state['jobs_processed'] == 0:
                        self.print_warning("No jobs found matching your criteria in this cycle")

                    # Calculate time until next job discovery
                    if automation_state['last_job_discovery']:
                        next_discovery = automation_state['last_job_discovery'] + timedelta(seconds=automation_state['job_discovery_interval_seconds'])
                        time_until_next = (next_discovery - datetime.now()).total_seconds()
                        
                        if time_until_next > 0:
                            minutes_until = int(time_until_next / 60)
                            self.print_info(f"📭 Queue empty. Next job search in {minutes_until} minutes at {next_discovery.strftime('%I:%M %p')}")
                            self.print_info("💤 Waiting for next scheduled discovery...")
                            
                            # Wait in small intervals so we can check for Ctrl+C
                            wait_interval = min(60, time_until_next)  # Wait max 60 seconds at a time
                            await asyncio.sleep(wait_interval)
                        else:
                            # Time has passed, will discover on next loop iteration
                            await asyncio.sleep(2)
                    else:
                        await asyncio.sleep(5)
                    continue
                
                # Get next job from queue
                job = job_queue.popleft()
                automation_state['jobs_processed'] += 1
                
                self.print_header(f"JOB {automation_state['jobs_processed']} - {job['company']}")
                self.print_info(f"Title: {job['title']}")
                self.print_info(f"URL: {job['url'][:60]}...")
                self.print_info(f"Relevance: {job['relevance_score']:.1f}%")
                self.print_info("-" * 60)
                
                # Step 3: Apply to job
                job_result = await self._apply_to_single_job_automated(
                    job_url=job['url'],
                    job_title=job['title'],
                    company=job['company'],
                    description=job.get('description', ''),
                    tailor_resume=tailor_resume,
                    headless=headless,
                    automation_state=automation_state
                )
                
                # Add to progress log
                automation_state['progress_log'].append(job_result)
                
                # Update previously applied URLs set (for future deduplication in this session)
                if job_result.get('success') or job_result.get('submitted'):
                    previously_applied_urls.add(job['url'])
                    logger.info(f"Added {job['url']} to applied URLs cache")
                
                # Save progress report
                self._save_progress_report(report_filename, automation_state, job_queue)
                
                # Check for rate limit
                if job_result.get('rate_limit_error'):
                    automation_state['rate_limit_hits'] += 1
                    await self._handle_rate_limit(automation_state)
                
                # Small delay between applications
                await asyncio.sleep(3)
            
            # Automation completed
            self.print_header("🎉 AUTOMATION COMPLETED")
            
        except KeyboardInterrupt:
            self.print_warning("\n\n⚠️  Automation stopped by user")
        except Exception as e:
            self.print_error(f"Automation engine error: {str(e)}")
            logger.error(f"Automation error: {e}", exc_info=True)
        finally:
            # Save final progress report
            self._save_progress_report(report_filename, automation_state, job_queue, final=True)
            
            # Display summary
            self._should_open_incomplete = False
            self._incomplete_report_file = None
            self._display_automation_summary(automation_state, report_filename)
            
            # Check if user wants to open incomplete applications
            if self._should_open_incomplete and self._incomplete_report_file:
                await self._open_incomplete_applications(self._incomplete_report_file)
            
            self.pause()
    
    def _extract_job_url(self, job: Dict[str, Any]) -> Optional[str]:
        """Extract job URL from job data"""
        apply_links = job.get('apply_links', {})
        if apply_links:
            return apply_links.get('primary') or apply_links.get('indeed') or apply_links.get('linkedin')
        return job.get('job_url') or job.get('url')

    def _fetch_job_description_from_url(self, url: str) -> Optional[str]:
        """Lightweight HTTP fetch of a job listing URL to extract its description text.

        Used as a pre-flight step before the browser opens the application form,
        because application pages (Lever, Greenhouse, Workday, etc.) often contain
        no job description — only form fields.

        Returns extracted text (≥ 200 chars) or None on failure.
        """
        try:
            import requests
            from html.parser import HTMLParser

            headers = {
                'User-Agent': (
                    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                    'AppleWebKit/537.36 (KHTML, like Gecko) '
                    'Chrome/121.0.0.0 Safari/537.36'
                ),
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
                'Accept-Language': 'en-US,en;q=0.5',
            }

            response = requests.get(url, headers=headers, timeout=10, allow_redirects=True)
            if response.status_code != 200:
                return None

            class _TextExtractor(HTMLParser):
                def __init__(self):
                    super().__init__()
                    self._chunks: list = []
                    self._skip = False

                def handle_starttag(self, tag, attrs):
                    if tag in ('script', 'style', 'nav', 'header', 'footer', 'noscript'):
                        self._skip = True

                def handle_endtag(self, tag):
                    if tag in ('script', 'style', 'nav', 'header', 'footer', 'noscript'):
                        self._skip = False

                def handle_data(self, data):
                    if not self._skip:
                        stripped = data.strip()
                        if stripped:
                            self._chunks.append(stripped)

            extractor = _TextExtractor()
            extractor.feed(response.text)
            text = ' '.join(extractor._chunks)
            text = ' '.join(text.split())  # collapse whitespace

            return text[:12000] if len(text) >= 200 else None

        except Exception as e:
            logger.debug(f"Pre-fetch description failed for {url}: {e}")
            return None
    
    async def _apply_to_single_job_automated(self, job_url: str, job_title: str, company: str,
                                            tailor_resume: bool, headless: bool,
                                            automation_state: Dict[str, Any],
                                            description: str = '') -> Dict[str, Any]:
        """Apply to a single job in automated mode"""
        start_time = time.time()
        
        job_result = {
            'job_url': job_url,
            'job_title': job_title,
            'company': company,
            'timestamp': datetime.now().isoformat(),
            'success': False,
            'submitted': False,
            'fields_filled': 0,
            'field_details': [],
            'error': None,
            'rate_limit_error': False,
            'duration_seconds': 0
        }
        
        try:
            self.print_info("🤖 Starting automated application...")
            self.print_info(f"   Tailor Resume: {'✓ Enabled' if tailor_resume else '✗ Disabled'}")
            
            # Import the global playwright getter
            from Agents.job_application_agent import _get_or_create_playwright
            
            # Get or reuse the global Playwright instance (don't use 'async with' - it auto-closes!)
            playwright = await _get_or_create_playwright()
            
            agent = RefactoredJobAgent(
                playwright=playwright,
                headless=headless,
                keep_open=False,
                debug=False,
                user_id=str(self.current_user.id),
                tailor_resume=tailor_resume,
                mimikree_email=self._session_mimikree_email if tailor_resume else None,
                mimikree_password=self._session_mimikree_password if tailor_resume else None,
                job_url=job_url,
                use_persistent_profile=True,
                pre_fetched_description=description or None,
            )
            
            # Process the application
            await agent.process_link(job_url)
            
            # Display tailored resume download if tailoring was enabled (non-interactive for automation)
            if tailor_resume:
                self.print_info("🔍 Checking for tailored resume...")
                # Get profile from state machine context
                profile = None
                if hasattr(agent, 'state_machine') and agent.state_machine:
                    if hasattr(agent.state_machine, 'app_state'):
                        profile = agent.state_machine.app_state.context.get('profile', {})
                        logger.info(f"Profile found in state: {bool(profile)}")
                        if profile:
                            logger.info(f"Profile has tailoring_metrics: {bool(profile.get('tailoring_metrics'))}")
                
                if profile and profile.get('tailoring_metrics'):
                    tailoring_metrics = profile['tailoring_metrics']
                    pdf_path = tailoring_metrics.get('pdf_path')
                    google_doc_url = tailoring_metrics.get('url')
                    
                    self.print_success(f"✨ Resume tailored for {company}")
                    
                    if pdf_path and os.path.exists(pdf_path):
                        self.print_info(f"   📄 PDF: {pdf_path}")
                        # Store in job result for reporting
                        job_result['tailored_resume_path'] = pdf_path
                    else:
                        self.print_warning(f"   ⚠️  PDF not found at: {pdf_path}")
                    
                    if google_doc_url:
                        self.print_info(f"   🔗 Google Doc: {google_doc_url}")
                        job_result['tailored_resume_url'] = google_doc_url
                    
                    # Show stats if available
                    match_stats = tailoring_metrics.get('match_stats', {})
                    if match_stats:
                        match_pct = match_stats.get('match_percentage', 0)
                        added = match_stats.get('added', 0)
                        self.print_info(f"   📊 Match Rate: {match_pct:.1f}% | Keywords Added: {added}")
                else:
                    failure_reason = None
                    if hasattr(agent, 'state_machine') and agent.state_machine and hasattr(agent.state_machine, 'app_state'):
                        failure_reason = agent.state_machine.app_state.context.get('tailoring_failure_reason')
                    if failure_reason:
                        self.print_error(f"   ❌ Resume tailoring failed: {failure_reason}")
                    else:
                        self.print_warning("   ⚠️  Resume tailoring did not return downloadable output for this job")
                    logger.warning(
                        "Tailor resume enabled but no metrics. "
                        f"profile={bool(profile)}, "
                        f"failure_reason={failure_reason or 'none'}"
                    )
            
            # Check if human intervention was required
            human_intervention_needed = getattr(agent, 'keep_browser_open_for_human', False)
            
            # Get action recorder data if available
            if hasattr(agent, 'action_recorder') and agent.action_recorder:
                actions = agent.action_recorder.actions
                
                # Extract filled fields
                for action in actions:
                    if action.type in ['fill_field', 'enhanced_field_fill', 'select_option']:
                        if action.success:
                            job_result['fields_filled'] += 1
                            job_result['field_details'].append({
                                'label': action.field_label or 'Unknown',
                                'value': action.value or '',
                                'type': action.field_type or 'unknown'
                            })
                
                # Check if submitted (look for submit clicks)
                submit_actions = [a for a in actions if 'submit' in a.type.lower() or 
                                (a.type == 'click' and 'submit' in (a.element_text or '').lower())]
                if submit_actions and len(submit_actions) > 0 and not human_intervention_needed:
                    job_result['submitted'] = True
            
            # If human intervention was needed, mark as not submitted
            if human_intervention_needed:
                job_result['submitted'] = False
                job_result['error'] = 'Human intervention required - application not submitted'
            
            # Only mark as truly submitted if:
            # 1. Fields were filled (at least 1)
            # 2. Submit action occurred
            # 3. No human intervention required
            if job_result['fields_filled'] > 0 and job_result['submitted']:
                job_result['success'] = True
                automation_state['applications_submitted'] += 1
                self.record_application(job_url)
                self.print_success(f"✓ Application submitted! ({job_result['fields_filled']} fields filled)")
            else:
                # Application incomplete - didn't submit
                job_result['success'] = False
                job_result['submitted'] = False
                automation_state['applications_failed'] += 1
                if not job_result.get('error'):
                    job_result['error'] = f"Application incomplete (only {job_result['fields_filled']} fields filled, not submitted)"
                self.print_warning(f"⚠ Application incomplete ({job_result['fields_filled']} fields filled)")
                
        except Exception as e:
            error_str = str(e)
            job_result['error'] = error_str
            automation_state['applications_failed'] += 1
            
            # Check for rate limit
            if self._is_rate_limit_error(e):
                job_result['rate_limit_error'] = True
                self.print_error(f"✗ Rate limit hit: {error_str[:100]}")
            else:
                self.print_error(f"✗ Application failed: {error_str[:100]}")
            
            logger.error(f"Auto apply error: {e}", exc_info=True)
        
        job_result['duration_seconds'] = round(time.time() - start_time, 2)
        return job_result
    
    def _is_rate_limit_error(self, error: Exception) -> bool:
        """Check if error is a rate limit error"""
        error_str = str(error).lower()
        return any(keyword in error_str for keyword in [
            '429', 'rate limit', 'resource_exhausted', 'quota', 'too many requests'
        ])
    
    async def _handle_rate_limit(self, automation_state: Dict[str, Any]):
        """Handle rate limit by waiting"""
        self.print_warning("\n⚠️  RATE LIMIT DETECTED")
        self.print_info("Pausing for 60 seconds before retrying...")
        
        # Check if this is a daily limit
        if automation_state['rate_limit_hits'] > 5:
            self.print_error("\n❌ Multiple rate limit hits detected")
            self.print_error("This might be a daily quota limit")
            
            stop = self.get_input("\nStop automation? (y/n, default: n): ").strip().lower()
            if stop == 'y':
                automation_state['running'] = False
                return
        
        # Wait 60 seconds
        for i in range(60, 0, -5):
            self.print_info(f"  Waiting... {i} seconds remaining", end='\r')
            await asyncio.sleep(5)
        
        self.print_success("\n✓ Resuming automation")
    
    def _save_progress_report(self, filename: str, automation_state: Dict[str, Any], 
                             job_queue: deque, final: bool = False):
        """Save progress report to JSON file"""
        try:
            report = {
                'report_type': 'final_report' if final else 'progress_checkpoint',
                'generated_at': datetime.now().isoformat(),
                'session_info': {
                    'start_time': automation_state['start_time'].isoformat(),
                    'duration_minutes': round((datetime.now() - automation_state['start_time']).total_seconds() / 60, 2),
                    'status': 'completed' if final else 'in_progress',
                    'original_keywords': automation_state.get('original_keywords', ''),
                    'optimized_keywords': automation_state.get('optimized_keywords', ''),
                    'query_variations_used': automation_state.get('query_variations', [])
                },
                'statistics': {
                    'applications_submitted': automation_state['applications_submitted'],
                    'applications_failed': automation_state['applications_failed'],
                    'jobs_discovered': automation_state['jobs_discovered'],
                    'jobs_processed': automation_state['jobs_processed'],
                    'rate_limit_hits': automation_state['rate_limit_hits'],
                    'success_rate': round(
                        (automation_state['applications_submitted'] / max(automation_state['jobs_processed'], 1)) * 100, 2
                    )
                },
                'applications': automation_state['progress_log'],
                'queue_remaining': len(job_queue)
            }
            
            with open(filename, 'w') as f:
                json.dump(report, f, indent=2)
            
            if final:
                logger.info(f"Final progress report saved: {filename}")
            
        except Exception as e:
            logger.error(f"Failed to save progress report: {e}", exc_info=True)
    
    def _display_automation_summary(self, automation_state: Dict[str, Any], report_filename: str):
        """Display automation summary"""
        duration = (datetime.now() - automation_state['start_time']).total_seconds() / 60
        
        self.print_info(f"\nSession Duration: {duration:.1f} minutes")
        self.print_info(f"Jobs Discovered: {automation_state['jobs_discovered']}")
        self.print_info(f"Jobs Processed: {automation_state['jobs_processed']}")
        self.print_success(f"Applications Submitted: {automation_state['applications_submitted']}")
        
        if automation_state['applications_failed'] > 0:
            self.print_error(f"Applications Failed: {automation_state['applications_failed']}")
        
        if automation_state['rate_limit_hits'] > 0:
            self.print_warning(f"Rate Limit Hits: {automation_state['rate_limit_hits']}")
        
        # Calculate success rate
        if automation_state['jobs_processed'] > 0:
            success_rate = (automation_state['applications_submitted'] / automation_state['jobs_processed']) * 100
            self.print_info(f"Success Rate: {success_rate:.1f}%")
        
        self.print_info(f"\n📄 Detailed Progress Report: {report_filename}")
        self.print_info("   This report contains:")
        self.print_info("   • Job URLs and details")
        self.print_info("   • Submission status (yes/no)")
        self.print_info("   • Fields filled count")
        self.print_info("   • What was filled in each field")
        self.print_info("   • Errors encountered")
        
        self.print_success("\n✓ All data has been saved")
        
        # Offer to open incomplete applications for manual completion
        incomplete_count = automation_state['jobs_processed'] - automation_state['applications_submitted']
        if incomplete_count > 0:
            print("\n" + "=" * 60)
            self.print_warning(f"⚠️  {incomplete_count} application(s) were not fully submitted")
            self.print_info("\nWould you like to continue these applications manually?")
            self.print_info("This will open all incomplete applications in your")
            self.print_info("persistent browser profile, so you can pick up where")
            self.print_info("the AI left off and complete/submit them yourself.")
            print("=" * 60)
            
            choice = self.get_input("\nOpen incomplete applications? (y/n): ").strip().lower()
            self._should_open_incomplete = (choice == 'y')
            self._incomplete_report_file = report_filename if choice == 'y' else None
    
    async def _open_incomplete_applications(self, report_filename: str):
        """Open incomplete applications in persistent browser for manual completion"""
        from Agents.persistent_browser_manager import PersistentBrowserManager
        
        try:
            # Read the progress report
            self.print_info(f"\n📖 Reading progress report: {report_filename}")
            
            if not os.path.exists(report_filename):
                self.print_error(f"Progress report not found: {report_filename}")
                return
            
            with open(report_filename, 'r') as f:
                report_data = json.load(f)
            
            # Find incomplete applications (not submitted)
            incomplete_apps = [
                job for job in report_data.get('applications', [])  # Fixed: was 'progress_log', but JSON has 'applications'
                if not job.get('submitted', False)
            ]
            
            if not incomplete_apps:
                self.print_success("All applications were submitted successfully!")
                return
            
            self.print_info(f"\n✓ Found {len(incomplete_apps)} incomplete application(s)")
            
            # Display incomplete applications
            print("\n" + "=" * 60)
            print("Incomplete Applications:")
            print("=" * 60)
            for i, app in enumerate(incomplete_apps, 1):
                print(f"\n{i}. {app.get('job_title', 'Unknown')} at {app.get('company', 'Unknown')}")
                print(f"   URL: {app.get('job_url', 'N/A')[:70]}...")
                print(f"   Fields Filled: {app.get('fields_filled', 0)}")
                if app.get('error'):
                    print(f"   Error: {app.get('error')}")
            print("=" * 60)
            
            confirm = self.get_input(f"\nOpen all {len(incomplete_apps)} application(s) in browser? (y/n): ").strip().lower()
            if confirm != 'y':
                self.print_info("Cancelled.")
                return
            
            # Launch persistent browser
            self.print_info("\n🚀 Opening persistent browser...")
            self.print_info(f"   User ID: {self.current_user.id}")
            
            manager = PersistentBrowserManager()
            profile_path = manager.get_profile_path(str(self.current_user.id))
            
            self.print_info(f"   Profile path: {profile_path}")
            self.print_info(f"   Profile exists: {profile_path.exists()}")
            
            if profile_path.exists():
                size_mb = sum(f.stat().st_size for f in profile_path.rglob('*') if f.is_file()) / (1024 * 1024)
                self.print_info(f"   Profile size: {size_mb:.2f} MB")
            else:
                self.print_error(f"   ⚠️ WARNING: Profile directory does not exist!")
                self.print_warning(f"   You may need to run 'Option 7: Browser Profile Setup' first")
            
            context = await manager.launch_persistent_browser(
                user_id=str(self.current_user.id),
                headless=False  # Must be visible for manual work
            )
            
            self.print_success("✓ Browser opened with persistent profile")
            self.print_info("\n💡 To verify logins are preserved:")
            self.print_info("   1. Check if you're logged into LinkedIn in this browser")
            self.print_info("   2. Check if you're logged into Gmail in this browser")
            self.print_info("   3. If NOT logged in, the profile may not be set up correctly")
            self.print_warning("\n⚠️  NOTE: Fields will NOT be pre-filled (technical limitation)")
            self.print_info("    The AI-filled fields were in the previous browser session.")
            
            # Open each incomplete application in a new tab
            self.print_info(f"\n📂 Opening {len(incomplete_apps)} tab(s)...")
            
            pages = []
            for i, app in enumerate(incomplete_apps, 1):
                job_url = app.get('job_url')
                if not job_url:
                    continue
                
                try:
                    page = await context.new_page()
                    await page.goto(job_url, timeout=30000, wait_until='domcontentloaded')
                    pages.append(page)
                    self.print_success(f"  ✓ Tab {i}: {app.get('job_title', 'Unknown')[:50]}")
                    
                    # Small delay to avoid overwhelming the browser
                    await asyncio.sleep(1)
                    
                except Exception as e:
                    self.print_warning(f"  ⚠ Tab {i} failed: {str(e)}")
                    logger.warning(f"Failed to open tab for {job_url}: {e}")
            
            print("\n" + "=" * 60)
            self.print_success(f"✓ Opened {len(pages)} application(s) in browser tabs")
            print("=" * 60)
            
            self.print_info("\n📝 Instructions:")
            self.print_info("  1. Each tab contains an incomplete application")
            self.print_info("  2. Review and complete any missing fields")
            self.print_info("  3. Submit the applications manually")
            self.print_info("  4. Close the browser when done")
            self.print_info("\n💡 Tip: Your login sessions are preserved in this browser!")
            
            print("\n" + "=" * 60)
            print("Browser is now open. Press Enter here when you're done...")
            print("=" * 60)
            
            # Wait for user to finish
            input()
            
            # Close browser
            self.print_info("\n🔒 Closing browser...")
            try:
                await context.close()
                if hasattr(context, '_playwright'):
                    await context._playwright.stop()
                # Ensure closed context is removed from reuse registry
                PersistentBrowserManager.close_browser_for_user(str(self.current_user.id))
                self.print_success("✓ Browser closed successfully")
            except Exception as e:
                logger.warning(f"Error closing browser: {e}")
            
        except Exception as e:
            self.print_error(f"Failed to open incomplete applications: {str(e)}")
            logger.error(f"Open incomplete applications error: {e}", exc_info=True)
    
    # ============================================================================
    # Application History
    # ============================================================================
    
    def view_application_history(self):
        """View application history"""
        self.clear_screen()
        self.print_header("APPLICATION HISTORY")
        
        try:
            applications = self.db.query(JobApplication).filter(
                JobApplication.user_id == self.current_user.id
            ).order_by(JobApplication.created_at.desc()).limit(50).all()
            
            if not applications:
                self.print_warning("No applications found")
                self.pause()
                return
            
            print(f"Total applications: {len(applications)}\n")
            
            for i, app in enumerate(applications, 1):
                status_color = Colors.OKGREEN if app.status == 'completed' else Colors.WARNING
                print(f"{i}. {Colors.BOLD}{app.job_title}{Colors.ENDC} at {app.company_name}")
                print(f"   Status: {status_color}{app.status}{Colors.ENDC}")
                print(f"   Applied: {app.applied_at or app.created_at}")
                if app.job_url:
                    print(f"   URL: {app.job_url}")
                print()
            
            self.pause()
            
        except Exception as e:
            self.print_error(f"Failed to load history: {str(e)}")
            logger.error(f"Application history error: {e}", exc_info=True)
            self.pause()
    
    # ============================================================================
    # Settings
    # ============================================================================
    
    def settings_menu(self):
        """Settings menu"""
        while True:
            self.clear_screen()
            self.print_header("SETTINGS")
            
            print(f"{Colors.BOLD}1.{Colors.ENDC} Change Password")
            print(f"{Colors.BOLD}2.{Colors.ENDC} Update Email")
            print(f"{Colors.BOLD}3.{Colors.ENDC} View Account Info")
            print(f"{Colors.BOLD}4.{Colors.ENDC} Back to Main Menu\n")
            
            choice = self.get_input("Select option (1-4): ").strip()
            
            if choice == '1':
                self.change_password()
            elif choice == '2':
                self.update_email()
            elif choice == '3':
                self.view_account_info()
            elif choice == '4':
                break
            else:
                self.print_error("Invalid option")
                self.pause()
    
    def change_password(self):
        """Change password"""
        self.clear_screen()
        self.print_header("CHANGE PASSWORD")
        
        try:
            current = self.get_input("Current Password: ", password=True)
            
            if not self.verify_password(current, self.current_user.password_hash):
                self.print_error("Current password is incorrect")
                self.pause()
                return
            
            new = self.get_input("New Password: ", password=True)
            confirm = self.get_input("Confirm New Password: ", password=True)
            
            if new != confirm:
                self.print_error("Passwords do not match")
                self.pause()
                return
            
            if len(new) < 8:
                self.print_error("Password must be at least 8 characters")
                self.pause()
                return
            
            self.current_user.password_hash = self.hash_password(new)
            self.db.commit()
            
            self.print_success("Password changed successfully!")
            self.pause()
            
        except Exception as e:
            self.db.rollback()
            self.print_error(f"Password change failed: {str(e)}")
            logger.error(f"Password change error: {e}", exc_info=True)
            self.pause()
    
    def update_email(self):
        """Update email"""
        self.clear_screen()
        self.print_header("UPDATE EMAIL")
        
        try:
            new_email = self.get_input("New Email: ").strip()
            
            if not new_email or '@' not in new_email:
                self.print_error("Invalid email address")
                self.pause()
                return
            
            # Check if email exists
            existing = self.db.query(User).filter(User.email == new_email).first()
            if existing:
                self.print_error("Email already in use")
                self.pause()
                return
            
            self.current_user.email = new_email
            self.db.commit()
            
            self.print_success("Email updated successfully!")
            self.pause()
            
        except Exception as e:
            self.db.rollback()
            self.print_error(f"Email update failed: {str(e)}")
            logger.error(f"Email update error: {e}", exc_info=True)
            self.pause()
    
    def view_account_info(self):
        """View account information"""
        self.clear_screen()
        self.print_header("ACCOUNT INFORMATION")
        
        print(f"\n{Colors.BOLD}User ID:{Colors.ENDC} {self.current_user.id}")
        print(f"{Colors.BOLD}Email:{Colors.ENDC} {self.current_user.email}")
        print(f"{Colors.BOLD}Name:{Colors.ENDC} {self.current_user.first_name} {self.current_user.last_name}")
        print(f"{Colors.BOLD}Account Created:{Colors.ENDC} {self.current_user.created_at}")
        print(f"{Colors.BOLD}Email Verified:{Colors.ENDC} {'Yes' if self.current_user.email_verified else 'No'}")
        print(f"{Colors.BOLD}Account Status:{Colors.ENDC} {'Active' if self.current_user.is_active else 'Inactive'}")
        
        # Application stats
        app_count = self.db.query(JobApplication).filter(
            JobApplication.user_id == self.current_user.id
        ).count()
        print(f"{Colors.BOLD}Total Applications:{Colors.ENDC} {app_count}")
        
        self.pause()
    
    # ============================================================================
    # Auth Flow
    # ============================================================================
    
    def logout(self):
        """Logout current user"""
        self.current_user = None
        self.current_profile = None
        self.print_success("Logged out successfully!")
        self.pause()
    
    def show_auth_menu(self):
        """Show authentication menu"""
        while self.running:
            self.clear_screen()
            self.print_header("JOB APPLICATION AGENT")
            
            print(f"{Colors.BOLD}Welcome to the Terminal-Based Job Application Agent{Colors.ENDC}\n")
            
            print(f"{Colors.BOLD}1.{Colors.ENDC} Login")
            print(f"{Colors.BOLD}2.{Colors.ENDC} Register")
            print(f"{Colors.BOLD}3.{Colors.ENDC} Exit\n")
            
            choice = self.get_input("Select option (1-3): ").strip()
            
            if choice == '1':
                if self.login_user():
                    self.show_main_menu()
            elif choice == '2':
                self.register_user()
            elif choice == '3':
                self.running = False
                break
            else:
                self.print_error("Invalid option")
                self.pause()
    
    # ============================================================================
    # Main Entry Point
    # ============================================================================
    
    def run(self):
        """Main application loop"""
        try:
            self.show_auth_menu()
            
            if not self.running:
                self.clear_screen()
                self.print_success("Thank you for using Job Application Agent!")
                print(f"{Colors.OKCYAN}Goodbye!{Colors.ENDC}\n")
                
        except KeyboardInterrupt:
            self.clear_screen()
            self.print_warning("\nApplication interrupted by user")
            print(f"{Colors.OKCYAN}Goodbye!{Colors.ENDC}\n")
        except Exception as e:
            self.print_error(f"An error occurred: {str(e)}")
            logger.error(f"Application error: {e}", exc_info=True)
        finally:
            if hasattr(self, 'db'):
                self.db.close()


def main():
    """Entry point"""
    cli = CLIJobAgent()
    cli.run()


if __name__ == "__main__":
    main()

