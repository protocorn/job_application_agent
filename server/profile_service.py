from sqlalchemy.orm import Session
from database_config import UserProfile, User, SessionLocal, ActionHistory
from typing import Optional, Dict, Any
import logging
import uuid
from datetime import datetime, timedelta

class ProfileService:

    @staticmethod
    def _convert_user_id(user_id: str) -> uuid.UUID:
        """Convert user_id string to UUID"""
        if isinstance(user_id, uuid.UUID):
            return user_id
        try:
            return uuid.UUID(user_id)
        except (ValueError, AttributeError, TypeError) as e:
            raise ValueError(f"Invalid user ID format: {user_id}")

    @staticmethod
    def create_or_update_profile(user_id: str, profile_data: Dict[str, Any]) -> Dict[str, Any]:
        """Create or update user profile"""
        db = SessionLocal()
        try:
            # Convert user_id to UUID
            user_uuid = ProfileService._convert_user_id(user_id)

            # Treat all incoming fields as profile-only; do NOT update users table
            profile_data_filtered = { key: value for key, value in profile_data.items() if key not in {'first name', 'last name', 'email'} }

            # Check if profile already exists
            existing_profile = db.query(UserProfile).filter(UserProfile.user_id == user_uuid).first()

            if existing_profile:
                # Update existing profile
                for key, value in profile_data_filtered.items():
                    # Map frontend field names to database field names
                    db_field = ProfileService._map_field_name(key)
                    if hasattr(existing_profile, db_field):
                        # Convert empty strings to None for boolean fields
                        converted_value = ProfileService._convert_field_value(db_field, value)
                        setattr(existing_profile, db_field, converted_value)

                db.commit()
                db.refresh(existing_profile)
                profile = existing_profile
            else:
                # Create new profile
                mapped_data = {}
                for key, value in profile_data_filtered.items():
                    db_field = ProfileService._map_field_name(key)
                    # Only include fields that exist in UserProfile model
                    if hasattr(UserProfile, db_field):
                        # Convert empty strings to None for boolean fields
                        converted_value = ProfileService._convert_field_value(db_field, value)
                        mapped_data[db_field] = converted_value

                profile = UserProfile(
                    user_id=user_uuid,
                    **mapped_data
                )

                db.add(profile)
                db.commit()
                db.refresh(profile)

            return {
                'success': True,
                'message': 'Profile saved successfully',
                'profile_id': profile.id
            }

        except Exception as e:
            db.rollback()
            logging.error(f"Error saving profile: {e}")
            return {
                'success': False,
                'error': 'Failed to save profile'
            }
        finally:
            db.close()

    @staticmethod
    def get_profile(user_id: str) -> Dict[str, Any]:
        """Get user profile by user_id"""
        db = SessionLocal()
        try:
            # Convert user_id to UUID
            user_uuid = ProfileService._convert_user_id(user_id)
            profile = db.query(UserProfile).filter(UserProfile.user_id == user_uuid).first()

            if not profile:
                return {
                    'success': False,
                    'error': 'Profile not found'
                }

            # Convert database profile to frontend format
            profile_data = ProfileService._profile_to_dict(profile)

            return {
                'success': True,
                'profile': profile_data
            }

        except Exception as e:
            logging.error(f"Error getting profile: {e}")
            return {
                'success': False,
                'error': 'Failed to get profile'
            }
        finally:
            db.close()

    @staticmethod
    def _convert_field_value(field_name: str, value: Any) -> Any:
        """Convert field values to proper types for database"""
        # Boolean fields that need conversion
        boolean_fields = {'willing_to_relocate'}

        if field_name in boolean_fields:
            # Convert empty string to None, string 'true'/'false' to boolean
            if value == '' or value is None:
                return None
            if isinstance(value, bool):
                return value
            if isinstance(value, str):
                return value.lower() in ('true', 'yes', '1')

        return value

    @staticmethod
    def _map_field_name(frontend_field: str) -> str:
        """Map frontend field names to database field names"""
        field_mapping = {
            'first name': 'first_name',  # This is in User table, not profile
            'last name': 'last_name',    # This is in User table, not profile
            'email': 'email',            # This is in User table, not profile
            'date of birth': 'date_of_birth',
            'phone': 'phone',
            'address': 'address',
            'city': 'city',
            'state': 'state',
            'zip': 'zip_code',
            'country': 'country',
            'country_code': 'country_code',
            'state_code': 'state_code',
            'linkedin': 'linkedin',
            'github': 'github',
            'other links': 'other_links',
            'education': 'education',
            'work experience': 'work_experience',
            'projects': 'projects',
            'skills': 'skills',
            'summary': 'summary',
            'disabilities': 'disabilities',
            'veteran status': 'veteran_status',
            'visa status': 'visa_status',
            'visa sponsorship': 'visa_sponsorship',
            'preferred location': 'preferred_location',
            'willing to relocate': 'willing_to_relocate',
            'resume_url': 'resume_url',
            'gender': 'gender',
            'nationality': 'nationality',
            'preferred language': 'preferred_language'
        }
        return field_mapping.get(frontend_field, frontend_field)

    @staticmethod
    def _profile_to_dict(profile: UserProfile) -> Dict[str, Any]:
        """Convert database profile to frontend format"""
        return {
            'resume_url': profile.resume_url or '',
            'first name': '',  # Will be populated from User table
            'last name': '',   # Will be populated from User table
            'email': '',       # Will be populated from User table
            'date of birth': profile.date_of_birth or '',
            'gender': profile.gender or '',
            'nationality': profile.nationality or '',
            'preferred language': profile.preferred_language or '',
            'phone': profile.phone or '',
            'address': profile.address or '',
            'city': profile.city or '',
            'state': profile.state or '',
            'zip': profile.zip_code or '',
            'country': profile.country or '',
            'country_code': profile.country_code or '',
            'state_code': profile.state_code or '',
            'linkedin': profile.linkedin or '',
            'github': profile.github or '',
            'other links': profile.other_links or [''],
            'education': profile.education or [{'degree': '', 'institution': '', 'graduation_year': '', 'gpa': '', 'relevant_courses': ['']}],
            'work experience': profile.work_experience or [{'title': '', 'company': '', 'start_date': '', 'end_date': '', 'description': '', 'achievements': ['']}],
            'projects': profile.projects or [{'name': '', 'description': '', 'technologies': [''], 'github_url': '', 'live_url': '', 'features': ['']}],
            'skills': profile.skills or {'technical': [''], 'programming_languages': [''], 'frameworks': [''], 'tools': [''], 'soft_skills': [''], 'languages': ['']},
            'summary': profile.summary or '',
            'disabilities': profile.disabilities or [],
            'veteran status': profile.veteran_status or '',
            'visa status': profile.visa_status or '',
            'visa sponsorship': profile.visa_sponsorship or '',
            'preferred location': profile.preferred_location or [''],
            'willing to relocate': profile.willing_to_relocate if profile.willing_to_relocate is not None else '',
            'cover_letter_template': profile.cover_letter_template or ''
        }

    @staticmethod
    def get_complete_profile(user_id: str) -> Dict[str, Any]:
        """Get complete profile including user and profile data"""
        db = SessionLocal()
        try:
            # Convert user_id to UUID
            user_uuid = ProfileService._convert_user_id(user_id)

            # Get user data
            user = db.query(User).filter(User.id == user_uuid).first()
            if not user:
                return {
                    'success': False,
                    'error': 'User not found'
                }

            # Get profile data
            profile = db.query(UserProfile).filter(UserProfile.user_id == user_uuid).first()

            if profile:
                profile_data = ProfileService._profile_to_dict(profile)
            else:
                # Return default profile structure if no profile exists
                profile_data = ProfileService._get_default_profile_structure()

            # Add user data to profile
            profile_data.update({
                'first name': user.first_name,
                'last name': user.last_name,
                'email': user.email
            })

            return {
                'success': True,
                'profile': profile_data
            }

        except Exception as e:
            logging.error(f"Error getting complete profile: {e}")
            return {
                'success': False,
                'error': 'Failed to get profile'
            }
        finally:
            db.close()

    @staticmethod
    def _get_default_profile_structure() -> Dict[str, Any]:
        """Return default profile structure for new users"""
        return {
            'resume_url': '',
            'first name': '',
            'last name': '',
            'email': '',
            'date of birth': '',
            'gender': '',
            'nationality': '',
            'preferred language': '',
            'phone': '',
            'address': '',
            'city': '',
            'state': '',
            'zip': '',
            'country': '',
            'country_code': '',
            'state_code': '',
            'linkedin': '',
            'github': '',
            'other links': [''],
            'education': [{'degree': '', 'institution': '', 'graduation_year': '', 'gpa': '', 'relevant_courses': ['']}],
            'work experience': [{'title': '', 'company': '', 'start_date': '', 'end_date': '', 'description': '', 'achievements': ['']}],
            'projects': [{'name': '', 'description': '', 'technologies': [''], 'github_url': '', 'live_url': '', 'features': ['']}],
            'skills': {'technical': [''], 'programming_languages': [''], 'frameworks': [''], 'tools': [''], 'soft_skills': [''], 'languages': ['']},
            'summary': '',
            'disabilities': [],
            'veteran status': '',
            'visa status': '',
            'visa sponsorship': '',
            'preferred location': [''],
            'willing to relocate': ''
        }

    # -------- Action History methods --------
    @staticmethod
    def save_action_history(user_id: str, job_id: str, action_log: Dict[str, Any]) -> Dict[str, Any]:
        db = SessionLocal()
        try:
            # Convert user_id to UUID
            user_uuid = ProfileService._convert_user_id(user_id)

            # Clean up expired histories for this user/job
            now = datetime.utcnow()
            db.query(ActionHistory).filter(
                ActionHistory.user_id == user_uuid,
                ActionHistory.job_id == job_id,
                (ActionHistory.expires_at <= now) | (ActionHistory.completed == True)
            ).delete(synchronize_session=False)

            record = ActionHistory(
                user_id=user_uuid,
                job_id=job_id,
                action_log=action_log,
                created_at=now,
                expires_at=now + timedelta(hours=24),
                completed=False
            )
            db.add(record)
            db.commit()
            db.refresh(record)
            return { 'success': True, 'id': record.id }
        except Exception as e:
            db.rollback()
            logging.error(f"Error saving action history: {e}")
            return { 'success': False, 'error': 'Failed to save action history' }
        finally:
            db.close()

    @staticmethod
    def get_action_history(user_id: str, job_id: str) -> Dict[str, Any]:
        db = SessionLocal()
        try:
            # Convert user_id to UUID
            user_uuid = ProfileService._convert_user_id(user_id)

            now = datetime.utcnow()
            # delete expired
            db.query(ActionHistory).filter(ActionHistory.expires_at <= now).delete(synchronize_session=False)
            db.commit()
            rec = db.query(ActionHistory).filter(
                ActionHistory.user_id == user_uuid,
                ActionHistory.job_id == job_id,
                ActionHistory.completed == False,
                ActionHistory.expires_at > now
            ).order_by(ActionHistory.created_at.desc()).first()
            if not rec:
                return { 'success': True, 'action_log': None }
            return { 'success': True, 'action_log': rec.action_log, 'created_at': rec.created_at.isoformat(), 'expires_at': rec.expires_at.isoformat() }
        except Exception as e:
            logging.error(f"Error fetching action history: {e}")
            return { 'success': False, 'error': 'Failed to fetch action history' }
        finally:
            db.close()

    @staticmethod
    def mark_action_history_completed(user_id: str, job_id: str) -> Dict[str, Any]:
        db = SessionLocal()
        try:
            # Convert user_id to UUID
            user_uuid = ProfileService._convert_user_id(user_id)

            rec = db.query(ActionHistory).filter(
                ActionHistory.user_id == user_uuid,
                ActionHistory.job_id == job_id,
                ActionHistory.completed == False
            ).order_by(ActionHistory.created_at.desc()).first()
            if not rec:
                return { 'success': True }
            rec.completed = True
            rec.completed_at = datetime.utcnow()
            db.commit()
            return { 'success': True }
        except Exception as e:
            db.rollback()
            logging.error(f"Error completing action history: {e}")
            return { 'success': False, 'error': 'Failed to complete action history' }
        finally:
            db.close()