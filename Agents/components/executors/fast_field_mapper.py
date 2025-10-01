"""
Fast Field Mapper - Direct profile-to-field mapping without AI
Speeds up form filling by avoiding AI calls for common, predictable fields
"""
import re
from typing import Dict, Any, Optional, List, Tuple
from loguru import logger

class FastFieldMapper:
    """Maps profile data directly to form fields using keyword matching and pattern recognition."""

    def __init__(self):
        # Fast lookup tables for instant matching
        self.direct_mappings = {
            # Personal Information
            'first_name': ['first name', 'fname', 'given name', 'forename'],
            'last_name': ['last name', 'lname', 'surname', 'family name', 'lastname'],
            'email': ['email', 'e-mail', 'email address', 'mail'],
            'phone': ['phone', 'phone number', 'telephone', 'mobile', 'cell'],
            'address': ['address', 'street address', 'address line 1', 'home address'],
            'city': ['city', 'town', 'locality'],
            'state': ['state', 'province', 'region'],
            'zip': ['zip', 'zip code', 'postal code', 'zipcode'],
            'country': ['country', 'country of residence'],

            # Identity & Demographics
            'gender': ['gender'],  # Removed 'sex' to avoid matching sexual orientation
            'nationality': ['nationality', 'country of citizenship', 'citizenship'],
            'date_of_birth': ['date of birth', 'dob', 'birth date', 'birthday'],

            # Professional Links
            'linkedin': ['linkedin', 'linkedin profile', 'linkedin url'],
            'github': ['github', 'github profile', 'github url', 'git hub'],

            # Work Authorization & Visa
            'visa_status': ['visa status', 'current visa', 'immigration status'],
            'visa_sponsorship': ['visa sponsorship', 'sponsorship', 'sponsorship required'],

            # Other
            'preferred_language': ['preferred language', 'language'],
        }

        # Common Yes/No questions that can be answered from profile context
        self.yes_no_patterns = {
            'visa_sponsorship': {
                'patterns': [
                    r'visa.*sponsorship.*required',
                    r'require.*visa.*sponsorship',
                    r'need.*sponsorship',
                    r'sponsorship.*required',
                    r'h1b.*sponsorship'
                ],
                'profile_check': lambda p: p.get('visa sponsorship', '').lower() == 'required',
                'profile_answer': 'Yes',
                'default_answer': 'Yes'  # Based on profile: "Required"
            },
            'work_authorization': {
                'patterns': [
                    r'authorized.*work.*us',
                    r'work.*authorization.*us',
                    r'legal.*work.*us',
                    r'eligible.*work.*us',
                    r'legally.*authorized.*work'
                ],
                'profile_check': lambda p: p.get('visa status', '') in ['F-1', 'H-1B', 'Green Card', 'Citizen'],
                'profile_answer': 'Yes',
                'default_answer': 'Yes'  # Student/work visa holders are authorized
            },
            'disability': {
                'patterns': [
                    r'disability.*accommodation',
                    r'reasonable.*accommodation',
                    r'ada.*accommodation',
                    r'require.*accommodation'
                ],
                'default_answer': 'No'  # Common default
            },
            'veteran_status': {
                'patterns': [
                    r'veteran.*status',
                    r'military.*service',
                    r'protected.*veteran'
                ],
                'default_answer': 'No'  # Common default
            },
            'onsite_availability': {
                'patterns': [
                    r'available.*work.*onsite',
                    r'onsite.*office',
                    r'work.*office.*location',
                    r'relocate.*office',
                    r'commute.*office'
                ],
                'default_answer': 'Yes'  # Default to being flexible
            },
            'background_check': {
                'patterns': [
                    r'background.*check',
                    r'consent.*background',
                    r'criminal.*background'
                ],
                'default_answer': 'Yes'  # Standard consent
            },
            'drug_test': {
                'patterns': [
                    r'drug.*test',
                    r'substance.*test'
                ],
                'default_answer': 'Yes'  # Standard consent
            },
            'equal_opportunity': {
                'patterns': [
                    r'equal.*opportunity',
                    r'eeo.*questionnaire',
                    r'voluntary.*self.*identification'
                ],
                'default_answer': 'Prefer not to answer'  # Common choice
            }
        }

        # Geographic mappings for faster location handling
        self.location_mappings = {
            'United States': ['US', 'USA', 'United States', 'America', 'U.S.', 'U.S.A.'],
            'Canada': ['CA', 'CAN', 'Canada'],
            'United Kingdom': ['UK', 'GB', 'United Kingdom', 'Britain', 'England'],
        }

        # Education level mappings
        self.education_mappings = {
            'Bachelor': ['Bachelor', 'BS', 'BA', 'BE', 'BTech', 'B.Tech', 'B.E.', 'B.S.', 'B.A.'],
            'Master': ['Master', 'MS', 'MA', 'ME', 'MTech', 'M.Tech', 'M.E.', 'M.S.', 'M.A.', 'Masters'],
            'PhD': ['PhD', 'Ph.D.', 'Doctorate', 'Doctoral'],
            'High School': ['High School', 'Secondary', 'Diploma']
        }

        # Gender mappings
        self.gender_mappings = {
            'Male': ['Male', 'M', 'Man'],
            'Female': ['Female', 'F', 'Woman'],
            'Non-binary': ['Non-binary', 'Non binary', 'Other'],
            'Prefer not to answer': ['Prefer not to answer', 'Decline to answer', 'Not specified']
        }

        # Visa status mappings
        self.visa_mappings = {
            'F-1': ['F-1', 'F1', 'Student Visa', 'F-1 Student'],
            'H-1B': ['H-1B', 'H1B', 'Work Visa'],
            'Green Card': ['Green Card', 'Permanent Resident', 'LPR'],
            'US Citizen': ['US Citizen', 'Citizen', 'American Citizen'],
            'Other': ['Other', 'Different Visa']
        }

        # Race/Ethnicity options (common EEO questions)
        self.ethnicity_mappings = {
            'Asian': ['Asian', 'Asian American', 'Asian or Pacific Islander'],
            'White': ['White', 'Caucasian', 'European American'],
            'Hispanic': ['Hispanic', 'Latino', 'Hispanic or Latino'],
            'Black': ['Black', 'African American', 'Black or African American'],
            'Native American': ['Native American', 'American Indian', 'Alaska Native'],
            'Two or more races': ['Two or more races', 'Mixed', 'Multiracial'],
            'Prefer not to answer': ['Prefer not to answer', 'Decline to answer']
        }

    def can_fast_map(self, field_label: str, field_type: str) -> bool:
        """Check if this field can be mapped quickly without AI."""
        label_lower = field_label.lower().strip()

        # Direct profile mappings
        for key, variants in self.direct_mappings.items():
            if any(variant in label_lower for variant in variants):
                return True

        # Yes/No questions
        for category, config in self.yes_no_patterns.items():
            for pattern in config['patterns']:
                if re.search(pattern, label_lower, re.IGNORECASE):
                    return True

        # Additional fields we can handle regardless of input type
        additional_mappable_fields = [
            # Geographic
            'country', 'location', 'nationality', 'state', 'province',
            # Demographics
            'gender', 'sex', 'race', 'ethnicity', 'ethnic',
            # Education
            'education', 'degree', 'level', 'graduation',
            # Work/Visa related
            'visa', 'sponsorship', 'authorization', 'work status',
            # Other common fields
            'language', 'veteran', 'disability', 'accommodation'
        ]

        # Check if this field matches any additional mappable patterns
        if any(term in label_lower for term in additional_mappable_fields):
            return True

        return False

    def fast_map_field(self, field_label: str, field_type: str, profile: Dict[str, Any]) -> Optional[str]:
        """Directly map a field value from profile data without AI."""
        label_lower = field_label.lower().strip()

        # Direct profile mappings - only if we have EXACT matches
        for key, variants in self.direct_mappings.items():
            if any(variant in label_lower for variant in variants):
                value = self._get_profile_value(key, profile)
                if value:
                    logger.info(f"⚡ Fast mapped '{field_label}' → '{value}'")
                    return value
                else:
                    # Don't have this data in profile - skip instead of guessing
                    logger.info(f"⚡ Skipping '{field_label}' - no {key} data in profile")
                    return None

        # Yes/No questions
        for category, config in self.yes_no_patterns.items():
            for pattern in config['patterns']:
                if re.search(pattern, label_lower, re.IGNORECASE):
                    answer = self._get_yes_no_answer(category, profile, config['default_answer'])
                    logger.info(f"⚡ Fast Y/N '{field_label}' → '{answer}' ({category})")
                    return answer

        # Location/Country fields
        if any(term in label_lower for term in ['country', 'nationality']):
            country = profile.get('country', '')
            if country:
                logger.info(f"⚡ Fast country '{field_label}' → '{country}'")
                return country

        # Gender fields - only match explicit gender questions
        if 'gender' in label_lower and 'sexual' not in label_lower:
            gender = profile.get('gender', '')
            if gender:
                logger.info(f"⚡ Fast gender '{field_label}' → '{gender}'")
                return gender
            else:
                logger.info(f"⚡ Skipping gender field '{field_label}' - no gender in profile")
                return None

        # Race/Ethnicity fields - only if we have explicit ethnicity data in profile
        if any(term in label_lower for term in ['race', 'ethnicity', 'ethnic', 'origin']):
            ethnicity = profile.get('ethnicity', '') or profile.get('race', '')
            if ethnicity:
                logger.info(f"⚡ Fast ethnicity '{field_label}' → '{ethnicity}'")
                return ethnicity
            else:
                logger.info(f"⚡ Skipping ethnicity field '{field_label}' - no ethnicity data in profile")
                return None

        # Visa status fields (can be text or dropdown)
        if any(term in label_lower for term in ['visa status', 'immigration status', 'current visa']):
            visa_status = profile.get('visa status', '')
            if visa_status:
                logger.info(f"⚡ Fast visa status '{field_label}' → '{visa_status}'")
                return visa_status

        # Language fields
        if 'language' in label_lower and 'prefer' in label_lower:
            language = profile.get('preferred language', '')
            if language:
                logger.info(f"⚡ Fast language '{field_label}' → '{language}'")
                return language

        # Education level fields (can be text or dropdown)
        if any(term in label_lower for term in ['education', 'degree', 'level']):
            education = profile.get('education', [])
            if education:
                # Get the highest degree
                degrees = [edu.get('degree', '') for edu in education]
                if degrees:
                    highest_degree = degrees[0]  # Assuming first is highest
                    # Extract level (Bachelor, Master, etc.)
                    for level in ['Master', 'Bachelor', 'PhD', 'Doctorate']:
                        if level.lower() in highest_degree.lower():
                            logger.info(f"⚡ Fast education '{field_label}' → '{level}'")
                            return level
            # If no education data or couldn't match, skip
            logger.info(f"⚡ Skipping education field '{field_label}' - no matching education data in profile")
            return None

        return None

    def get_dropdown_candidates(self, field_label: str, profile: Dict[str, Any]) -> List[str]:
        """Get likely dropdown values to look for, avoiding expensive option extraction."""
        label_lower = field_label.lower().strip()
        candidates = []

        # Country/Location candidates
        if any(term in label_lower for term in ['country', 'nationality']):
            country = profile.get('country', '')
            if country in self.location_mappings:
                candidates.extend(self.location_mappings[country])
            candidates.append(country)

        # Gender candidates - only for explicit gender questions
        if 'gender' in label_lower and 'sexual' not in label_lower:
            gender = profile.get('gender', '')
            if gender in self.gender_mappings:
                candidates.extend(self.gender_mappings[gender])
            candidates.append(gender)

        # Visa status candidates
        if any(term in label_lower for term in ['visa', 'immigration', 'status']):
            visa_status = profile.get('visa status', '')
            if visa_status in self.visa_mappings:
                candidates.extend(self.visa_mappings[visa_status])
            candidates.append(visa_status)

        # Education level candidates
        if any(term in label_lower for term in ['education', 'degree', 'level']):
            education = profile.get('education', [])
            if education:
                for edu in education:
                    degree = edu.get('degree', '')
                    for level, variants in self.education_mappings.items():
                        if any(variant.lower() in degree.lower() for variant in variants):
                            candidates.extend(variants)
                            break

        # Race/Ethnicity candidates (EEO questions)
        if any(term in label_lower for term in ['race', 'ethnicity', 'ethnic', 'origin']):
            # Based on profile nationality - if Indian, likely Asian
            nationality = profile.get('nationality', '').lower()
            if 'indian' in nationality or 'asia' in nationality:
                candidates.extend(self.ethnicity_mappings['Asian'])
            # Always add prefer not to answer option
            candidates.extend(self.ethnicity_mappings['Prefer not to answer'])

        # Work authorization candidates
        if re.search(r'work.*authorization|visa.*sponsor', label_lower, re.IGNORECASE):
            candidates.extend(['Yes', 'No', 'Authorized', 'Not Authorized', 'US Citizen', 'Green Card'])

        # Sponsorship candidates
        if 'sponsorship' in label_lower:
            sponsorship = profile.get('visa sponsorship', '')
            if 'required' in sponsorship.lower():
                candidates.extend(['Yes', 'Required', 'Need Sponsorship'])
            else:
                candidates.extend(['No', 'Not Required'])

        return list(set(candidates))  # Remove duplicates

    def _get_profile_value(self, key: str, profile: Dict[str, Any]) -> Optional[str]:
        """Get value from profile with key normalization."""
        # Direct key lookup
        if key in profile:
            return str(profile[key])

        # Try variations
        key_variations = {
            'first_name': ['first name', 'firstname', 'fname'],
            'last_name': ['last name', 'lastname', 'lname'],
            'linkedin': ['linkedin', 'linkedin_url'],
            'github': ['github', 'github_url'],
            'visa_status': ['visa status', 'visa_status'],
            'visa_sponsorship': ['visa sponsorship', 'visa_sponsorship'],
            'date_of_birth': ['date of birth', 'dob', 'birth_date'],
            'preferred_language': ['preferred language', 'preferred_language'],
            'state': ['state', 'state_code'],
            'zip': ['zip', 'zipcode', 'postal_code']
        }

        if key in key_variations:
            for variation in key_variations[key]:
                if variation in profile:
                    return str(profile[variation])

        return None

    def _get_yes_no_answer(self, category: str, profile: Dict[str, Any], default: str) -> str:
        """Get Yes/No answer based on profile context."""
        config = self.yes_no_patterns.get(category, {})

        # Check if we have a profile-based check
        if 'profile_check' in config:
            try:
                if config['profile_check'](profile):
                    return config.get('profile_answer', default)
            except Exception:
                pass  # Fall back to default if profile check fails

        # Use the configured default
        return config.get('default_answer', default)

    def batch_map_fields(self, fields: List[Dict], profile: Dict[str, Any]) -> Tuple[List[Dict], List[Dict]]:
        """Batch process fields - return (fast_mapped, needs_ai)."""
        fast_mapped = []
        needs_ai = []

        for field in fields:
            label = field.get('label', '')
            field_type = field.get('field_category', '')

            if self.can_fast_map(label, field_type):
                value = self.fast_map_field(label, field_type, profile)
                if value:
                    field['fast_mapped_value'] = value
                    fast_mapped.append(field)
                else:
                    needs_ai.append(field)
            else:
                needs_ai.append(field)

        logger.info(f"⚡ Fast mapper: {len(fast_mapped)} mapped instantly, {len(needs_ai)} need AI")
        return fast_mapped, needs_ai