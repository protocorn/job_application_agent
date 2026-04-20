from sqlalchemy.orm import Session
from database_config import UserProfile, User, SessionLocal, ActionHistory
from typing import Optional, Dict, Any
import logging
import uuid
from datetime import datetime, timedelta
from profile_strength import score_profile_strength

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

    # ── Pool-merge helpers ────────────────────────────────────────────────────

    @staticmethod
    def _is_empty_value(value: Any) -> bool:
        """Return True if value is considered null/empty (None, '', [], or all-blank)."""
        if value is None:
            return True
        if isinstance(value, str):
            return value.strip() == ''
        if isinstance(value, list):
            if len(value) == 0:
                return True
            return all(
                (isinstance(v, str) and v.strip() == '') or
                (isinstance(v, dict) and all(ProfileService._is_empty_value(vv) for vv in v.values()))
                for v in value
            )
        if isinstance(value, dict):
            return all(ProfileService._is_empty_value(v) for v in value.values())
        return False

    @staticmethod
    def _merge_list_of_dicts(existing: list, new_list: list, key_fields: list) -> list:
        """Merge two lists of dicts.

        Existing items are preserved (pool behaviour).  If an incoming item
        matches an existing one on any of the key_fields (case-insensitive),
        the existing entry is replaced with the fresh resume data.  Incoming
        items that have no match are appended.
        """
        if not new_list:
            return existing or []
        if not existing:
            return new_list

        non_empty_existing = [
            item for item in existing
            if isinstance(item, dict) and any(not ProfileService._is_empty_value(v) for v in item.values())
        ]
        result = list(non_empty_existing)

        for new_item in new_list:
            if not isinstance(new_item, dict) or ProfileService._is_empty_value(new_item):
                continue
            match_idx = None
            for i, ex_item in enumerate(result):
                for key in key_fields:
                    new_val = str(new_item.get(key, '')).strip().lower()
                    ex_val = str(ex_item.get(key, '')).strip().lower()
                    if new_val and ex_val and new_val == ex_val:
                        match_idx = i
                        break
                if match_idx is not None:
                    break
            if match_idx is not None:
                result[match_idx] = new_item
            else:
                result.append(new_item)

        return result if result else new_list

    @staticmethod
    def _merge_skills(existing: dict, new_skills: dict) -> dict:
        """Union each skill sub-list so manually-added skills are never lost."""
        if not new_skills or not isinstance(new_skills, dict):
            return existing or {}
        if not existing or not isinstance(existing, dict):
            return new_skills
        result = dict(existing)
        for key, new_items in new_skills.items():
            if not isinstance(new_items, list) or not any(
                isinstance(v, str) and v.strip() for v in new_items
            ):
                continue
            existing_items = result.get(key, [])
            seen = {v.strip().lower() for v in existing_items if isinstance(v, str) and v.strip()}
            merged = list(existing_items)
            for item in new_items:
                if isinstance(item, str) and item.strip() and item.strip().lower() not in seen:
                    merged.append(item)
                    seen.add(item.strip().lower())
            result[key] = merged
        return result

    # ─────────────────────────────────────────────────────────────────────────

    @staticmethod
    def create_or_update_profile(
        user_id: str,
        profile_data: Dict[str, Any],
        preserve_existing: bool = False,
    ) -> Dict[str, Any]:
        """Create or update user profile.

        Args:
            preserve_existing: When True (used during resume processing) the
                update never overwrites a non-empty DB value with null/empty,
                and merges pool fields (projects / education / work_experience /
                skills) so that manually-added entries are retained.
        """
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

                        if preserve_existing:
                            existing_value = getattr(existing_profile, db_field, None)

                            # Never replace a real value with null/empty
                            if ProfileService._is_empty_value(converted_value):
                                continue

                            # Pool fields: merge instead of replace
                            if db_field == 'projects' and not ProfileService._is_empty_value(existing_value):
                                converted_value = ProfileService._merge_list_of_dicts(
                                    existing_value, converted_value, ['name']
                                )
                            elif db_field == 'education' and not ProfileService._is_empty_value(existing_value):
                                converted_value = ProfileService._merge_list_of_dicts(
                                    existing_value, converted_value, ['institution', 'degree']
                                )
                            elif db_field == 'work_experience' and not ProfileService._is_empty_value(existing_value):
                                converted_value = ProfileService._merge_list_of_dicts(
                                    existing_value, converted_value, ['company', 'title']
                                )
                            elif db_field == 'skills' and not ProfileService._is_empty_value(existing_value):
                                converted_value = ProfileService._merge_skills(existing_value, converted_value)

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
            profile_strength = score_profile_strength(profile_data)

            return {
                'success': True,
                'profile': profile_data,
                'profile_strength': profile_strength,
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
            'race_ethnicity': 'race_ethnicity',
            'race ethnicity': 'race_ethnicity',
            'race/ethnicity': 'race_ethnicity',
            'veteran status': 'veteran_status',
            'visa status': 'visa_status',
            'visa sponsorship': 'visa_sponsorship',
            'preferred location': 'preferred_location',
            'willing to relocate': 'willing_to_relocate',
            'resume_url': 'resume_url',
            'resume_source_type': 'resume_source_type',
            'resume_text': 'resume_text',
            'resume_filename': 'resume_filename',
            'resume_file_base64': 'resume_file_base64',
            'api_primary_mode': 'api_primary_mode',
            'api_secondary_mode': 'api_secondary_mode',
            'custom_gemini_api_key': 'custom_gemini_api_key',
            'latex_main_tex_path': 'latex_main_tex_path',
            'latex_file_manifest': 'latex_file_manifest',
            'gender': 'gender',
            'nationality': 'nationality',
            'preferred language': 'preferred_language',
            'resume_keywords': 'resume_keywords',
        }
        return field_mapping.get(frontend_field, frontend_field)

    @staticmethod
    def _profile_to_dict(profile: UserProfile) -> Dict[str, Any]:
        """Convert database profile to frontend format"""
        return {
            'api_primary_mode': profile.api_primary_mode or None,   # None = not yet configured → triggers setup modal
            'api_secondary_mode': profile.api_secondary_mode or None,
            # custom_gemini_api_key is intentionally NOT included here -
            # the encrypted blob is only returned via the dedicated /api/settings/ai-keys endpoint.
            'resume_url': profile.resume_url or '',
            'resume_source_type': profile.resume_source_type or '',
            'resume_text': profile.resume_text or '',
            'resume_filename': profile.resume_filename or '',
            'resume_file_base64': profile.resume_file_base64 or '',
            'latex_main_tex_path': profile.latex_main_tex_path or '',
            'latex_file_manifest': profile.latex_file_manifest or [],
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
            'projects': profile.projects or [{'name': '', 'dates': '', 'description': '', 'technologies': [''], 'github_url': '', 'live_url': '', 'features': ['']}],
            'skills': profile.skills or {'technical': [''], 'programming_languages': [''], 'frameworks': [''], 'tools': [''], 'soft_skills': [''], 'languages': ['']},
            'summary': profile.summary or '',
            'disabilities': profile.disabilities or [],
            'race_ethnicity': profile.race_ethnicity or '',
            'veteran status': profile.veteran_status or '',
            'visa status': profile.visa_status or '',
            'visa sponsorship': profile.visa_sponsorship or '',
            'preferred location': profile.preferred_location or [''],
            'willing to relocate': profile.willing_to_relocate if profile.willing_to_relocate is not None else '',
            'cover_letter_template': profile.cover_letter_template or '',
            'resume_keywords': profile.resume_keywords or None,
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
                'email': user.email,
                'pending_email': user.pending_email or None
            })
            profile_strength = score_profile_strength(profile_data)

            return {
                'success': True,
                'profile': profile_data,
                'profile_strength': profile_strength,
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
            'api_primary_mode': None,   # NULL until user explicitly configures
            'api_secondary_mode': None,
            'resume_url': '',
            'resume_source_type': '',
            'resume_text': '',
            'resume_filename': '',
            'resume_file_base64': '',
            'latex_main_tex_path': '',
            'latex_file_manifest': [],
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
            'projects': [{'name': '', 'dates': '', 'description': '', 'technologies': [''], 'github_url': '', 'live_url': '', 'features': ['']}],
            'skills': {'technical': [''], 'programming_languages': [''], 'frameworks': [''], 'tools': [''], 'soft_skills': [''], 'languages': ['']},
            'summary': '',
            'disabilities': [],
            'race_ethnicity': '',
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