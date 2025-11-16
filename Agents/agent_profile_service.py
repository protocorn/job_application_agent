import sys
import os

# Add parent directory to path to access database_config
sys.path.append(os.path.join(os.path.dirname(__file__), '..'))

from database_config import SessionLocal, User, UserProfile
from typing import Optional, Dict, Any, Union
import logging
from uuid import UUID

class AgentProfileService:
    """Service for agents to access user profile data from PostgreSQL"""

    @staticmethod
    def get_profile_by_user_id(user_id: Union[str, UUID]) -> Optional[Dict[str, Any]]:
        """
        Get complete profile data for a specific user

        Args:
            user_id: User UUID (can be string or UUID object)
        """
        db = SessionLocal()
        try:
            # Convert string to UUID if needed
            if isinstance(user_id, str):
                user_id = UUID(user_id)

            # Get user data
            user = db.query(User).filter(User.id == user_id).first()
            if not user:
                logging.error(f"User with ID {user_id} not found")
                return None

            # Get profile data
            profile = db.query(UserProfile).filter(UserProfile.user_id == user_id).first()

            # Build complete profile data in the format agents expect
            profile_data = {
                "resume_url": profile.resume_url if profile and profile.resume_url else "",
                "first name": user.first_name,
                "last name": user.last_name,
                "email": user.email,
                "date of birth": profile.date_of_birth if profile and profile.date_of_birth else "",
                "gender": profile.gender if profile and profile.gender else "",
                "nationality": profile.nationality if profile and profile.nationality else "",
                "preferred language": profile.preferred_language if profile and profile.preferred_language else "",
                "phone": profile.phone if profile and profile.phone else "",
                "address": profile.address if profile and profile.address else "",
                "city": profile.city if profile and profile.city else "",
                "state": profile.state if profile and profile.state else "",
                "zip": profile.zip_code if profile and profile.zip_code else "",
                "country": profile.country if profile and profile.country else "",
                "country_code": profile.country_code if profile and profile.country_code else "",
                "state_code": profile.state_code if profile and profile.state_code else "",
                "linkedin": profile.linkedin if profile and profile.linkedin else "",
                "github": profile.github if profile and profile.github else "",
                "other links": profile.other_links if profile and profile.other_links else [""],
                "education": profile.education if profile and profile.education else [
                    {
                        "degree": "",
                        "institution": "",
                        "graduation_year": "",
                        "gpa": "",
                        "relevant_courses": [""]
                    }
                ],
                "work experience": profile.work_experience if profile and profile.work_experience else [
                    {
                        "title": "",
                        "company": "",
                        "start_date": "",
                        "end_date": "",
                        "description": "",
                        "achievements": [""]
                    }
                ],
                "projects": profile.projects if profile and profile.projects else [
                    {
                        "name": "",
                        "description": "",
                        "technologies": [""],
                        "github_url": "",
                        "live_url": "",
                        "features": [""]
                    }
                ],
                "skills": profile.skills if profile and profile.skills else {
                    "technical": [""],
                    "programming_languages": [""],
                    "frameworks": [""],
                    "tools": [""],
                    "soft_skills": [""],
                    "languages": [""]
                },
                "summary": profile.summary if profile and profile.summary else "",
                "disabilities": profile.disabilities if profile and profile.disabilities else [],
                "veteran status": profile.veteran_status if profile and profile.veteran_status else "",
                "visa status": profile.visa_status if profile and profile.visa_status else "",
                "visa sponsorship": profile.visa_sponsorship if profile and profile.visa_sponsorship else "",
                "preferred location": profile.preferred_location if profile and profile.preferred_location else [""],
                "willing to relocate": profile.willing_to_relocate if profile and profile.willing_to_relocate is not None else ""
            }

            return profile_data

        except Exception as e:
            logging.error(f"Error getting profile for user {user_id}: {e}")
            return None
        finally:
            db.close()

    @staticmethod
    def get_profile_by_email(email: str) -> Optional[Dict[str, Any]]:
        """Get complete profile data for a user by email"""
        db = SessionLocal()
        try:
            user = db.query(User).filter(User.email == email).first()
            if not user:
                logging.error(f"User with email {email} not found")
                return None

            return AgentProfileService.get_profile_by_user_id(user.id)

        except Exception as e:
            logging.error(f"Error getting profile for email {email}: {e}")
            return None
        finally:
            db.close()

    @staticmethod
    def get_latest_user_profile() -> Optional[Dict[str, Any]]:
        """Get the most recently created user's profile (for backward compatibility)"""
        db = SessionLocal()
        try:
            # Get the most recently created user
            latest_user = db.query(User).order_by(User.created_at.desc()).first()
            if not latest_user:
                logging.error("No users found in database")
                return None

            return AgentProfileService.get_profile_by_user_id(latest_user.id)

        except Exception as e:
            logging.error(f"Error getting latest user profile: {e}")
            return None
        finally:
            db.close()

    @staticmethod
    def validate_profile_completeness(profile_data: Dict[str, Any]) -> Dict[str, Any]:
        """Validate if profile has sufficient data for job applications"""
        required_fields = {
            "first name": profile_data.get("first name", ""),
            "last name": profile_data.get("last name", ""),
            "email": profile_data.get("email", ""),
            "phone": profile_data.get("phone", ""),
            "resume_url": profile_data.get("resume_url", "")
        }

        missing_fields = [field for field, value in required_fields.items() if not value]

        return {
            "is_complete": len(missing_fields) == 0,
            "missing_fields": missing_fields,
            "completion_percentage": ((len(required_fields) - len(missing_fields)) / len(required_fields)) * 100
        }