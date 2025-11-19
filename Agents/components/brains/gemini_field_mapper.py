import json
import re
from typing import Dict, List, Optional, Any, Tuple
from loguru import logger
import google.generativeai as genai
import os

class GeminiFieldMapper:
    """Uses Gemini Flash model to intelligently map form fields to profile schema."""
    
    def __init__(self):
        self.model_name = "gemini-2.0-flash"  # Using the fast, lite model
        self._configure_gemini()
        
        # Our profile schema - what data we have available (matching actual profile structure)
        self.profile_schema = {
            # Personal Information
            'first_name': 'First name',
            'last_name': 'Last name', 
            'email': 'Email address',
            'phone': 'Phone number',
            'address': 'Full address',
            'city': 'City',
            'state': 'State/Province',
            'state_code': 'State code (e.g., MD, CA)',
            'zip_code': 'ZIP/Postal code',
            'country': 'Country',
            'country_code': 'Country phone code (e.g., +1)',
            'nationality': 'Nationality (e.g., Indian, American)',
            'date_of_birth': 'Date of birth (YYYY-MM-DD)',
            'preferred_language': 'Preferred language',
            'linkedin': 'LinkedIn profile URL',
            'github': 'GitHub profile URL',
            'other_links': 'Other personal/portfolio links',
            'resume_path': 'Path to resume file',
            'resume_url': 'Online resume URL',
            
            # Demographic and EEO Information
            'gender': 'Gender identity (Male/Female/Non-binary/Prefer not to say)',
            'race_ethnicity': 'Race/Ethnicity (White/Black/Hispanic/Asian/Native American/Other)',
            'veteran_status': 'Veteran status (Yes/No/Prefer not to say)',
            'disability_status': 'Disability status (Yes/No/Prefer not to say)',
            
            # Work Authorization
            'work_authorization': 'Work authorization status in country',
            'visa_status': 'Current visa/immigration status (F-1, H1B, Green Card, etc.)',
            'require_sponsorship': 'Require visa sponsorship (Yes/No)',
            
            # Skills and Technical Information
            'programming_languages': 'Programming languages (Python, JavaScript, etc.)',
            'frameworks': 'Frameworks and libraries (React, TensorFlow, etc.)',
            'tools': 'Tools and technologies (AWS, Docker, Git, etc.)',
            'technical_skills': 'Technical skills and domains (AI, ML, Data Science, etc.)',
            
            # Additional Information
            'summary': 'Professional summary',
            'cover_letter': 'Cover letter or additional comments',
            'salary_expectation': 'Expected salary range',
            'availability': 'Start date availability',
            'willing_to_relocate': 'Willing to relocate (Yes/No)',
            'preferred_locations': 'Preferred work locations',
            'source': 'How candidate heard about the position',
            'referral_source': 'Referral source information',
            
            # Professional Details
            'years_experience': 'Years of professional experience',
            'current_title': 'Current job title',
            'current_company': 'Current employer',
            
            # Nested arrays
            'work_experience': [
                {
                    'company': 'Company name',
                    'title': 'Job title/position',
                    'start_date': 'Employment start date',
                    'end_date': 'Employment end date', 
                    'current': 'Currently employed (boolean)',
                    'description': 'Job description/responsibilities'
                }
            ],
            'education': [
                {
                    'institution': 'School/University name',
                    'degree': 'Degree type',
                    'field_of_study': 'Major/Field of study',
                    'graduation_date': 'Graduation date',
                    'gpa': 'Grade point average'
                }
            ]
        }
    
    def _configure_gemini(self):
        """Configure Gemini API with the key from token.json."""
        try:
            # Look for token.json in the Agents directory
            token_path = os.path.join(os.path.dirname(__file__), '..', 'token.json')
            if os.path.exists(token_path):
                with open(token_path, 'r') as f:
                    token_data = json.load(f)
                    api_key = token_data.get('gemini_api_key')
                    if api_key:
                        genai.configure(api_key=api_key)
                        logger.info("✅ Gemini API configured successfully")
                        return
            
            # Fallback to environment variable
            api_key = os.getenv('GEMINI_API_KEY')
            if api_key:
                genai.configure(api_key=api_key)
                logger.info("✅ Gemini API configured from environment")
            else:
                logger.warning("⚠️ No Gemini API key found. Field mapping will be disabled.")
                
        except Exception as e:
            logger.error(f"❌ Failed to configure Gemini API: {e}")
    
    async def map_fields_to_profile(self, form_fields: List[Dict[str, Any]], profile: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
        """
        Maps form fields to our profile schema using Gemini with explicit ID-based mapping.
        
        Args:
            form_fields: List of field dictionaries with 'stable_id', 'label', 'options', etc.
            profile: User's profile data for context
            
        Returns:
            Dictionary mapping stable field IDs to profile data and selected values
        """
        try:
            if not form_fields:
                return {}
            
            # Create a comprehensive field catalog with unique IDs
            field_catalog = self._create_field_catalog(form_fields)
            
            # Get AI mapping for all fields at once with explicit ID references
            ai_mapping = await self._get_ai_field_mapping(field_catalog, profile)
            
            # Process AI mapping results
            result = {}
            for field in form_fields:
                field_id = self._get_field_identifier(field)
                if field_id in ai_mapping:
                    result[field_id] = ai_mapping[field_id]
            
            simple_count = sum(1 for v in result.values() if v.get('type') == 'simple')
            dropdown_count = sum(1 for v in result.values() if v.get('type') == 'dropdown')
            manual_count = sum(1 for v in result.values() if v.get('type') == 'manual')
            
            logger.info(f"🧠 Gemini processed {len(result)} fields: {simple_count} simple, {dropdown_count} dropdown, {manual_count} manual")
            return result
            
        except Exception as e:
            logger.error(f"❌ Gemini field mapping failed: {e}")
            return {}

    def _create_field_catalog(self, form_fields: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
        """Create a comprehensive catalog of all form fields with unique identifiers."""
        catalog = {}
        
        for field in form_fields:
            field_id = self._get_field_identifier(field)
            label = field.get('label', '')
            field_category = field.get('field_category', 'text_input')
            options = field.get('options', [])
            
            # For radio buttons and checkboxes, include question context if available
            field_question = field.get('field_question', '')
            
            catalog[field_id] = {
                'label': label,
                'field_category': field_category,
                'options': options,
                'required': field.get('required', False),
                'placeholder': field.get('placeholder', ''),
                'input_type': field.get('input_type', 'text'),
                'is_dropdown': 'dropdown' in field_category,
                'requires_manual_writing': self._requires_manual_writing(label, field_category),
                'field_question': field_question  # Question text for radio/checkbox groups
            }
        
        return catalog

    async def _get_ai_field_mapping(self, field_catalog: Dict[str, Dict[str, Any]], profile: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
        """Get AI mapping for all fields with explicit ID-based responses."""
        try:
            # Create comprehensive prompt with field catalog
            prompt = self._create_comprehensive_mapping_prompt(field_catalog, profile)
            
            model = genai.GenerativeModel(self.model_name)
            response = model.generate_content(prompt)
            
            # Parse the AI response into structured mapping
            return self._parse_comprehensive_mapping_response(response.text, field_catalog, profile)
            
        except Exception as e:
            logger.error(f"❌ Error getting AI field mapping: {e}")
            return {}

    def _create_comprehensive_mapping_prompt(self, field_catalog: Dict[str, Dict[str, Any]], profile: Dict[str, Any]) -> str:
        """Create a comprehensive prompt that includes all field IDs and options."""
        # Create profile context
        profile_context = self._create_profile_context(profile, "comprehensive mapping")

        # Create field catalog text
        catalog_text = []
        for field_id, field_info in field_catalog.items():
            field_text = f"ID: {field_id}\n"
            field_text += f"  Label: {field_info['label']}\n"
            field_text += f"  Type: {field_info['field_category']}\n"
            
            # Include question context for radio/checkbox fields
            if field_info.get('field_question'):
                field_text += f"  Question: {field_info['field_question']}\n"

            if field_info['options']:
                options_text = ", ".join([opt['text'] if isinstance(opt, dict) else str(opt) for opt in field_info['options'][:15]])
                field_text += f"  Options: [{options_text}]\n"

            if field_info['requires_manual_writing']:
                field_text += f"  Requires: Manual AI writing\n"

            catalog_text.append(field_text)

        catalog_str = "\n".join(catalog_text)

        prompt = f"""
You are helping to fill out a job application form. I will provide you with a catalog of all form fields with their unique IDs, and you need to map each field to the appropriate action.

USER PROFILE:
{profile_context}

FORM FIELDS CATALOG:
{catalog_str}

CRITICAL INSTRUCTIONS - CONFIDENCE-BASED FIELD FILLING:
Use the profile data with CONFIDENCE. If the profile contains information, USE IT - don't default to "prefer not to say" or safe options.

1. SIMPLE MAPPING: For factual data fields where profile contains the exact information
   - Examples: first name, last name, email, phone, address, city, state, zip, country
   - Response format: "ID: <field_id> -> SIMPLE: <exact_profile_value>"
   - ONLY use if the exact information exists in the profile

2. DROPDOWN SELECTION: For dropdown/select fields where you can match profile data to options
   - Choose the best option from the available options list based on profile
   - Response format: "ID: <field_id> -> DROPDOWN: <selected_option>"
   - CONFIDENT MAPPING FOR DEMOGRAPHICS (USE PROFILE DATA WHEN AVAILABLE):
     * GENDER: If profile has gender="Male" -> SELECT "Male", "M", "Man" (NEVER "Prefer not to say")
     * RACE/ETHNICITY: Infer from nationality (e.g., Asian countries → "Asian", European → "White", etc.) (NEVER "Prefer not to say" when data exists)
     * HISPANIC: Infer from nationality (e.g., non-Latin American countries → "No", "Non-Hispanic") (confident inference)
     * DISABILITY: If not specified in profile -> ONLY THEN use "No" or "Prefer not to say"
     * VETERAN: If not specified in profile -> ONLY THEN use "No" or "Prefer not to say"
     * VISA/WORK AUTH: Use visa_status (e.g., F-1, H-1B, Green Card, Citizen) + require_sponsorship from profile with confidence
     * COUNTRY/STATE: Use exact matches from profile 
     * EDUCATION: Map to education array data when relevant
     * NOTICE PERIOD: For students -> "Immediately", "Upon graduation", "Flexible"
   - SMART INFERENCE FOR COMMON YES/NO QUESTIONS:
     * "Are you 18+ years old?" / "18 years of age?" -> If has work_experience or Master's degree -> "Yes"
     * "Are you authorized to work in US?" -> If location is US state AND education in US -> "Yes" (visa holders with work authorization)
     * "Do you require visa sponsorship?" -> If visa_status OR location=US but nationality=non-US -> Check require_sponsorship or infer "Yes"
     * "Have you worked at [Company X]?" -> Check work_experience array for company name match -> "Yes" if found, else "No"
     * "Have you applied before?" -> Default "No" (unless profile indicates otherwise)
     * "Have you entered NDA/non-compete?" -> Default "No" (unless profile indicates otherwise)
     * "Do you have security clearance?" -> Default "No" (unless profile indicates otherwise)
     * "Currently working on project with [Company]?" -> Check current work_experience -> Usually "No"
   - **CRITICAL: DATE-AWARE DROPDOWN SELECTION (GRADUATION/ENROLLMENT)**:
     * For questions about "expected graduation date" or "when will you graduate" with dropdown options
     * **COMPARE the graduation date from profile with CURRENT DATE from context**
     * If graduation date is in the FUTURE (e.g., May 2025 when today is October 2024):
       - User is CURRENTLY ENROLLED
       - SELECT the actual graduation date option from dropdown (e.g., "May 2025", "December 2025")
       - **DO NOT SELECT "I am not currently enrolled" or "No"**
     * If graduation date is in the PAST:
       - User has already GRADUATED
       - SELECT "I am not currently enrolled" or "Already graduated"
     * Example: Today is October 2024, graduation is May 2025, options are ["May 2025", "December 2025", "I am not currently enrolled"]
       - **CORRECT**: Select "May 2025" (matches profile graduation date)
       - **WRONG**: Select "I am not currently enrolled" (graduation is still in future!)
     * 

2.1. TERMS & CONDITIONS / AGREEMENT CHECKBOXES: ALWAYS CHECK THESE
   - CRITICAL: Even if the field ID contains "honeypot", "honey-pot", etc., if it's a VISIBLE checkbox with terms/conditions language, it is LEGITIMATE
   - For checkboxes with labels containing: "terms", "conditions", "agreement", "consent", "acknowledge", "privacy policy", "I agree", "I have read"
   - Response format: "ID: <field_id> -> SIMPLE: true" or "ID: <field_id> -> SIMPLE: checked"
   - These are NOT honeypots - they are required legal checkboxes that must be checked
   - Examples: "I agree to the terms and conditions", "I acknowledge the privacy policy", "Accept terms"
   - DO NOT skip these just because they have "honeypot" in the ID - that's a false positive from bad naming by website developers

2.2. RADIO BUTTONS & RADIO GROUPS: RETURN EXACT OPTION TEXT, NOT BOOLEAN
   - CRITICAL: For radio buttons, you MUST return the EXACT text of the option to select from the available options
   - NEVER return "true", "false", "Yes", "No" as generic values - these must match the actual option text shown
   - Example: If the question is "Are you a veteran?" and options are ["Veteran", "Not a Veteran", "Decline to Answer"]
     * CORRECT: "ID: veteran_status -> DROPDOWN: Not a Veteran" (exact option text)
     * WRONG: "ID: veteran_status -> SIMPLE: false" (boolean value - this will fail!)
   - Example: If question is "Gender?" and options are ["Male", "Female", "Non-binary", "Prefer not to say"]
     * CORRECT: "ID: gender -> DROPDOWN: Male" (exact option from list)
     * WRONG: "ID: gender -> SIMPLE: Male" (should be DROPDOWN with exact text from options)
   - The system needs the exact option text to click the right radio button
   - Always use the DROPDOWN type for radio buttons and provide the exact option text to select

2.3. CHECKBOX GROUPS: SINGLE CHECKBOX vs MULTI-SELECT
   - SINGLE CHECKBOX (e.g., "Do you have work authorization?"):
     * If only ONE checkbox in the group, return boolean
     * Format: "ID: work_auth -> SIMPLE: true" or "SIMPLE: false"
   - MULTI-SELECT CHECKBOXES (e.g., "What is your race/ethnicity? Select all that apply"):
     * If MULTIPLE checkboxes in the group, return comma-separated list of options to check
     * Format: "ID: race_group -> MULTISELECT: Asian, White" (select multiple)
     * Format: "ID: sexual_orientation -> MULTISELECT: Prefer not to answer" (select one from many)
   - Use profile data to determine which options to select
   - For diversity questions, use profile's demographic data confidently when available

2.5. MULTISELECT SKILLS: For Workday multiselect fields (skills, technologies, tools)
   - Map to relevant skills from profile skill categories
   - Response format: "ID: <field_id> -> MULTISELECT_SKILLS: <comma_separated_skills>"
   - Extract from: programming_languages, frameworks, tools, technical_skills
   - Examples: "Python,JavaScript,React.js,MongoDB" for technical skills field

2.6. SPECIALIZED FIELD HANDLING: For complex interactive elements
   - Checkboxes, radio buttons, and dropdowns are now handled by specialized AI processors
   - These fields are categorized and processed separately with full context analysis
   - No longer use generic mapping - each field type gets dedicated AI analysis

3. MANUAL WRITING: For essay questions, cover letters, or long descriptions
   - Examples: "Why do you want to work here?", "Tell us about yourself", "Cover letter"
   - Response format: "ID: <field_id> -> MANUAL: <brief_description>"

4. NEEDS_HUMAN_INPUT: For fields where profile data is insufficient or missing
   - Examples: notice period (not in profile), salary expectations (not specified), start dates, etc.
   - Response format: "ID: <field_id> -> NEEDS_HUMAN_INPUT: <reason>"
   - Use this when you don't have the specific information needed

CRITICAL RULES - CONFIDENCE-BASED APPROACH:
- BE CONFIDENT with profile data: If profile contains information, USE IT DECISIVELY
- STOP defaulting to "prefer not to say" when you have data to work with
- Use profile data intelligently for complex fields (gender, race, visa status, etc.)
- For QUESTION-BASED fields (especially radio buttons), analyze the FULL QUESTION CONTEXT:
  * "Have you worked for [Company] before?" → Check work_experience array for company matches
  * "Are you authorized to work in the US?" → Use visa_status and work_authorization
  * "Do you require visa sponsorship?" → Use require_sponsorship from profile
  * "Have you applied to this position before?" → Usually "No" unless specified
  * "Are you willing to relocate?" → Use willing_to_relocate from profile
- For fields WITHOUT profile data:
  * Notice period (not in profile) → NEEDS_HUMAN_INPUT
  * Salary expectations (not specified) → NEEDS_HUMAN_INPUT
  * Specific company questions without profile context → NEEDS_HUMAN_INPUT
- For fields WITH profile data or inferable data - USE WITH CONFIDENCE:
  * Gender: Use profile.gender value → SELECT matching option confidently
  * Race/Ethnicity: Infer from profile.nationality → SELECT appropriate race option confidently
  * Hispanic: Infer from profile.nationality → SELECT "Yes"/"No" confidently
  * Disability/Veteran: If not in profile → "No" (not "prefer not to say")
  * Work Authorization: Use profile.visa_status + require_sponsorship confidently
  * Education/Skills: Use education and skills arrays from profile
  * Company-specific questions: Check work_experience for company matches
- NEVER make up personal details, but DO use confident logical inference from available data
- ONLY use "prefer not to say" when profile explicitly states it or data is truly unavailable
- PAY SPECIAL ATTENTION to radio button labels that contain questions - use the FULL context

RESPONSE FORMAT:
Provide one line per field ID with the mapping decision.

EXAMPLES (generalized patterns, replace with actual profile data):

# Basic profile fields
ID: id:first_name -> SIMPLE: <profile.first_name>
ID: id:email -> SIMPLE: <profile.email>
ID: id:phone -> SIMPLE: <profile.phone>
ID: id:city -> SIMPLE: <profile.city>

# Fields requiring human input
ID: id:notice_period -> NEEDS_HUMAN_INPUT: Notice period not specified in profile
ID: id:salary_expectations -> NEEDS_HUMAN_INPUT: Salary not specified in profile
ID: id:start_date -> NEEDS_HUMAN_INPUT: Start date preference not provided

# Demographic fields (use profile data confidently)
ID: id:gender -> DROPDOWN: <profile.gender> (use exact value from profile)
ID: id:race -> DROPDOWN: <infer from profile.nationality> (e.g., nationality=Indian → Asian)
ID: id:hispanic -> DROPDOWN: <infer from profile.nationality> (e.g., nationality=Indian → No)
ID: id:disability -> DROPDOWN: <profile.disability_status or "Prefer not to say">
ID: id:veteran -> DROPDOWN: <profile.veteran_status or "No" for students/recent grads>

# Work authorization (use profile fields)
ID: id:visa_sponsorship -> DROPDOWN: <profile.require_sponsorship> (Yes/No from profile)
ID: id:work_authorization -> DROPDOWN: <profile.visa_status> (e.g., F-1, H-1B, Green Card, Citizen)
ID: id:authorized_to_work_us -> DROPDOWN: Yes (if profile has US education/location + visa_status)

# Education fields
ID: id:university -> SIMPLE: <profile.education[0].school>
ID: id:degree -> SIMPLE: <profile.education[0].degree>
ID: id:major -> SIMPLE: <profile.education[0].field_of_study>
ID: id:graduation_year -> SIMPLE: <profile.education[0].end_date.year>

# Skills (extract from profile arrays)
ID: id:skills -> MULTISELECT_SKILLS: <comma_separated from profile.skills>
ID: id:technical_skills -> MULTISELECT_SKILLS: <from profile.programming_languages + frameworks>
ID: id:programming_languages -> MULTISELECT_SKILLS: <from profile.programming_languages>
ID: id:frameworks -> MULTISELECT_SKILLS: <from profile.frameworks>

# Company-specific YES/NO questions (check work_experience array)
ID: id:worked_for_company_before -> DROPDOWN: No (checked work_experience, company not found)
ID: id:have_you_worked_for_google -> DROPDOWN: <Yes if "Google" in work_experience, else No>
ID: id:worked_at_acme_corp -> DROPDOWN: <Yes if "Acme Corp" in work_experience, else No>

# Age verification (infer from education/work history)
ID: id:are_you_18_years_old -> DROPDOWN: Yes (has university degree and/or work experience)

# Security/clearance questions (safe defaults for civilians)
ID: id:have_security_clearance -> DROPDOWN: No (not mentioned in profile, safe default)
ID: id:non_disclosure_agreement -> DROPDOWN: No (not mentioned in profile, safe default)
ID: id:government_employee -> DROPDOWN: No (check if work_experience contains government entities)
ID: id:currently_work_with_company -> DROPDOWN: No (unless current role mentions this company)

# Terms and conditions checkboxes (ALWAYS check these, even if ID looks like honeypot)
ID: id:honey-pot-0 -> SIMPLE: true (checkbox for terms/conditions - NOT an actual honeypot, just bad naming)
ID: id:terms_checkbox -> SIMPLE: checked (agree to terms)
ID: id:privacy_agreement -> SIMPLE: true (acknowledge privacy policy)
ID: id:accept_conditions -> SIMPLE: checked (accept terms and conditions)

# Cover letter / Essays
ID: id:cover_letter -> MANUAL: Job application cover letter
ID: id:why_work_here -> MANUAL: Essay question about motivation

YOUR RESPONSE:
"""

        return prompt

    def _parse_comprehensive_mapping_response(self, response_text: str, field_catalog: Dict[str, Dict[str, Any]], profile: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
        """Parse the comprehensive AI mapping response."""
        result = {}
        
        try:
            lines = response_text.strip().split('\n')
            
            for line in lines:
                line = line.strip()
                if not line or not line.startswith('ID:'):
                    continue
                
                try:
                    # Parse: "ID: field_id -> ACTION: value"
                    parts = line.split(' -> ')
                    if len(parts) != 2:
                        continue
                    
                    id_part = parts[0].replace('ID:', '').strip()
                    action_part = parts[1].strip()
                    
                    # Parse action
                    if ':' in action_part:
                        action_type, action_value = action_part.split(':', 1)
                        action_type = action_type.strip()
                        action_value = action_value.strip()
                        
                        if action_type == 'SIMPLE':
                            # Validate that simple fields have reasonable values
                            validated_value = self._validate_simple_field_value(action_value, field_catalog.get(id_part, {}))
                            if validated_value:  # If validation passed
                                result[id_part] = {
                                    'type': 'simple',
                                    'value': validated_value,
                                    'source': 'profile_data'
                                }
                            else:  # If validation failed, treat as needs human input
                                result[id_part] = {
                                    'type': 'needs_human_input',
                                    'reason': f"AI provided invalid value: '{action_value}'",
                                    'label': field_catalog.get(id_part, {}).get('label', ''),
                                    'requires_human_review': True
                                }
                        elif action_type == 'DROPDOWN':
                            result[id_part] = {
                                'type': 'dropdown',
                                'value': action_value,
                                'source': 'ai_selection',
                                'label': field_catalog.get(id_part, {}).get('label', '')
                            }
                        elif action_type == 'MULTISELECT_SKILLS':
                            # Parse comma-separated skills
                            skills = [skill.strip() for skill in action_value.split(',') if skill.strip()]
                            result[id_part] = {
                                'type': 'multiselect_skills',
                                'value': skills,
                                'source': 'profile_skills',
                                'label': field_catalog.get(id_part, {}).get('label', '')
                            }
                        elif action_type == 'MULTISELECT':
                            # Parse comma-separated options for checkbox groups
                            options = [opt.strip() for opt in action_value.split(',') if opt.strip()]
                            result[id_part] = {
                                'type': 'multiselect',
                                'value': options,  # List of options to check
                                'source': 'ai_multiselect',
                                'label': field_catalog.get(id_part, {}).get('label', '')
                            }
                        elif action_type == 'MANUAL':
                            result[id_part] = {
                                'type': 'manual',
                                'description': action_value,
                                'requires_ai_writing': True,
                                'label': field_catalog.get(id_part, {}).get('label', '')
                            }
                        elif action_type == 'NEEDS_HUMAN_INPUT':
                            result[id_part] = {
                                'type': 'needs_human_input',
                                'reason': action_value,
                                'label': field_catalog.get(id_part, {}).get('label', ''),
                                'requires_human_review': True
                            }
                        
                        logger.debug(f"Mapped {id_part} -> {action_type}: {action_value}")
                        
                except Exception as e:
                    logger.debug(f"Error parsing mapping line '{line}': {e}")
                    continue
            
            logger.info(f"Successfully parsed {len(result)} field mappings from AI response")
            return result
            
        except Exception as e:
            logger.error(f"Error parsing comprehensive mapping response: {e}")
            return {}
    
    def _requires_manual_writing(self, label: str, field_category: str) -> bool:
        """Check if a field requires manual AI writing rather than simple data mapping."""
        label_lower = label.lower()
        
        # Fields that definitely require manual writing (essays, cover letters, etc.)
        manual_keywords = [
            'why', 'cover letter', 'motivation', 'interest', 'why do you want',
            'tell us about', 'describe yourself', 'explain', 'additional information',
            'comments', 'essay', 'statement', 'objective', 'summary', 'goals',
            'what interests you', 'personal statement', 'additional comments'
        ]
        
        # Fields that should NEVER be manual (simple data fields)
        simple_field_keywords = [
            'notice period', 'work authorization', 'first name', 'last name', 'email',
            'phone', 'address', 'city', 'state', 'zip', 'country', 'linkedin',
            'github', 'salary', 'availability', 'start date', 'current title',
            'current company', 'years of experience', 'willing to relocate',
            'visa status', 'sponsorship', 'gender', 'race', 'ethnicity', 'veteran',
            'disability', 'how did you hear'
        ]
        
        # If it's a simple field, never treat as manual
        if any(keyword in label_lower for keyword in simple_field_keywords):
            return False
        
        # Only treat as manual if it's clearly an essay/description field
        return (
            field_category in ['textarea'] and
            (any(keyword in label_lower for keyword in manual_keywords) or
             len(label) > 80)  # Increased threshold for essay detection
        )

    def _get_field_identifier(self, field: Dict[str, Any]) -> str:
        """Get a unique identifier for a field."""
        # Use stable_id if available (new system)
        stable_id = field.get('stable_id', '').strip()
        if stable_id:
            return stable_id
        
        # Fallback to old system
        name = field.get('name', '').strip()
        id_attr = field.get('id', '').strip()
        label = field.get('label', '').strip()
        
        if name: return name
        if id_attr: return id_attr
        return f"field_{hash(label)}"

    async def _map_simple_fields(self, fields: List[Dict[str, Any]]) -> Dict[str, str]:
        """Map simple fields to profile schema paths."""
        field_descriptions = []
        for i, field in enumerate(fields):
            field_desc = {
                'index': i,
                'name': field.get('name', ''),
                'id': field.get('id', ''),
                'label': field.get('label', ''),
                'placeholder': field.get('placeholder', ''),
            'type': field.get('input_type', 'text'),
            'category': field.get('field_category', 'text_input')
            }
            field_descriptions.append(field_desc)
        
        # Create the prompt
        prompt = self._create_simple_mapping_prompt(field_descriptions)
        
        # Query Gemini
        model = genai.GenerativeModel(self.model_name)
        response = model.generate_content(prompt)
        
        # Parse the response
        mapping = self._parse_mapping_response(response.text)
        
        return mapping

    async def _handle_dropdown_fields(self, fields: List[Dict[str, Any]], profile: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
        """Handle dropdown fields with intelligent option selection."""
        result = {}
        
        for field in fields:
            field_id = self._get_field_identifier(field)
            options = field.get('options', [])
            label = field.get('label', '')
            
            logger.debug(f"Processing dropdown '{label}' with {len(options)} options")
            
            # Use AI to select the best option (even if options list is empty - AI will handle fallback)
            selected_option = await self._select_best_dropdown_option(label, options, profile)
            
            if selected_option:
                result[field_id] = {
                    'type': 'dropdown',
                    'value': selected_option['text'],
                    'selected_option': selected_option,
                    'all_options': options[:10],  # Limit for logging
                    'label': label
                }
                logger.info(f"✅ Dropdown '{label}' mapped to '{selected_option['text']}'")
            else:
                # Even if no option selected, log it for troubleshooting
                logger.warning(f"⚠️ Could not map dropdown '{label}' with {len(options)} options")
                # Still include in result but mark as unmapped
                result[field_id] = {
                    'type': 'dropdown_unmapped',
                    'label': label,
                    'all_options': options[:10],
                    'reason': 'No suitable option found'
                }
        
        return result

    async def _select_best_dropdown_option(self, field_label: str, options: List[Dict[str, str]], profile: Dict[str, Any]) -> Optional[Dict[str, str]]:
        """Use AI to select the best option from a dropdown based on profile data."""
        try:
            # If no options were extracted, try common fallbacks
            if not options:
                return self._get_fallback_option(field_label, profile)
            
            # Create options text
            options_text = "\n".join([f"- {opt['text']}" for opt in options[:25]])  # Show more options
            
            # Create relevant profile context
            profile_context = self._create_profile_context(profile, field_label)
            
            # Enhanced prompt with better examples and fallback logic
            prompt = f"""
You are helping fill out a job application form. Select the most appropriate option for this field based on the user's profile.

Field Label: "{field_label}"

Available Options:
{options_text}

User Profile Context:
{profile_context}

Selection Guidelines:
1. EXACT MATCHES: Look for exact matches to profile data first
2. LOGICAL DEFAULTS: For common questions, use these defaults:
   - "Have you worked at [company]?" → "No" (unless profile shows that specific company)
   - "Are you authorized to work?" → Look for "Yes" or "Authorized" options
   - "Gender" → Look for user's preference or "Prefer not to say"
   - "Race/Ethnicity" → Look for "Prefer not to say" or user's preference
   - "Veteran Status" → "No" (unless profile indicates otherwise)
   - "Disability Status" → "No" (unless profile indicates otherwise)
   - "How did you hear about us?" → Look for "LinkedIn", "Job Board", or "Website"
   - "Location preferences" → Look for locations matching profile address/city
3. PARTIAL MATCHES: If no exact match, find the closest option
4. SAFE DEFAULTS: When uncertain, choose conservative/privacy-respecting options

IMPORTANT: You must respond with EXACTLY one of the option texts from the list above.
If the list is empty or you cannot make a good choice, respond with "SKIP".

Your response (exact option text only):"""

            model = genai.GenerativeModel(self.model_name)
            response = model.generate_content(prompt)
            
            selected_text = response.text.strip()
            
            # Handle SKIP response
            if selected_text == "SKIP":
                return self._get_fallback_option(field_label, profile)
            
            # Find exact match first
            for option in options:
                if option['text'].strip().lower() == selected_text.lower():
                    logger.info(f"🎯 AI selected '{option['text']}' (exact) for '{field_label}'")
                    return option
            
            # Try partial matches
            for option in options:
                if (selected_text.lower() in option['text'].lower() or 
                    option['text'].lower() in selected_text.lower()):
                    logger.info(f"🎯 AI selected '{option['text']}' (partial match) for '{field_label}'")
                    return option
            
            # If AI response doesn't match any option, try fallback
            logger.warning(f"⚠️ AI response '{selected_text}' didn't match any option for '{field_label}'")
            return self._get_fallback_option(field_label, profile)
            
        except Exception as e:
            logger.error(f"❌ Error selecting dropdown option: {e}")
            return self._get_fallback_option(field_label, profile)

    def _get_fallback_option(self, field_label: str, profile: Dict[str, Any]) -> Optional[Dict[str, str]]:
        """Get a reasonable fallback option when AI selection fails."""
        label_lower = field_label.lower()
        
        # Common fallback patterns
        fallback_rules = [
            # Work authorization
            (["authorized", "work", "country"], ["yes", "authorized"]),
            # Company history
            (["worked", "employee", "contractor"], ["no"]),
            # Demographics - prefer privacy
            (["gender", "pronouns"], ["prefer not to say", "decline", "not specified"]),
            (["race", "ethnicity", "hispanic", "latino"], ["prefer not to say", "decline", "not specified"]),
            (["veteran"], ["no", "not a veteran"]),
            (["disability"], ["no", "not disabled"]),
            # Source/referral
            (["how", "hear", "source"], ["linkedin", "job board", "website", "online"]),
            # Location
            (["hub", "location", "office"], self._get_location_options(profile)),
        ]
        
        # Try to find a fallback based on common patterns
        for keywords, preferred_values in fallback_rules:
            if any(keyword in label_lower for keyword in keywords):
                if callable(preferred_values):
                    return preferred_values()
                # This would need to be matched against actual options
                # For now, just return None to indicate no suitable fallback
                logger.debug(f"Would suggest fallback values {preferred_values} for '{field_label}'")
        
        return None

    def _get_location_options(self, profile: Dict[str, Any]) -> List[str]:
        """Get location-based options from profile."""
        locations = []
        if profile.get('city'):
            locations.append(profile['city'])
        if profile.get('state'):
            locations.append(profile['state'])
        return locations or ["remote", "flexible"]

    def _create_profile_context(self, profile: Dict[str, Any], context_type: str = "general") -> str:
        """Create comprehensive profile context for field mapping."""
        from datetime import datetime
        
        # Extract ALL profile information systematically
        context_parts = []
        
        # CURRENT DATE (for date-aware decisions)
        current_date = datetime.now()
        context_parts.append("=== CURRENT DATE (FOR REFERENCE) ===")
        context_parts.append(f"Today: {current_date.strftime('%B %d, %Y')}")
        context_parts.append(f"Current Year: {current_date.year}")
        context_parts.append(f"Note: Use this to determine if graduation dates are in future (currently enrolled) or past (graduated)")
        
        # Basic Personal Information
        context_parts.append("\n=== PERSONAL INFORMATION ===")
        # Handle both 'first_name' and 'first name' formats
        first_name = profile.get('first_name') or profile.get('first name')
        last_name = profile.get('last_name') or profile.get('last name')
        if first_name: context_parts.append(f"First Name: {first_name}")
        if last_name: context_parts.append(f"Last Name: {last_name}")
        if profile.get('email'): context_parts.append(f"Email: {profile['email']}")
        if profile.get('phone'): context_parts.append(f"Phone: {profile['phone']}")
        if profile.get('address'): context_parts.append(f"Address: {profile['address']}")
        if profile.get('city'): context_parts.append(f"City: {profile['city']}")
        if profile.get('state'): context_parts.append(f"State: {profile['state']}")
        if profile.get('state_code'): context_parts.append(f"State Code: {profile['state_code']}")
        if profile.get('zip_code'): context_parts.append(f"ZIP Code: {profile['zip_code']}")
        if profile.get('country'): context_parts.append(f"Country: {profile['country']}")
        if profile.get('country_code'): context_parts.append(f"Country Code: {profile['country_code']}")
        if profile.get('nationality'): context_parts.append(f"Nationality: {profile['nationality']}")
        if profile.get('date_of_birth'): context_parts.append(f"Date of Birth: {profile['date_of_birth']}")
        if profile.get('preferred_language'): context_parts.append(f"Preferred Language: {profile['preferred_language']}")
        if profile.get('linkedin'): context_parts.append(f"LinkedIn: {profile['linkedin']}")
        if profile.get('github'): context_parts.append(f"GitHub: {profile['github']}")
        if profile.get('other_links'): context_parts.append(f"Other Links: {', '.join(profile['other_links'])}")
        if profile.get('summary'): context_parts.append(f"Summary: {profile['summary']}")
        
        # Work Authorization
        context_parts.append("\n=== WORK AUTHORIZATION ===")
        if profile.get('visa_status'): context_parts.append(f"Visa Status: {profile['visa_status']}")
        if profile.get('require_sponsorship'): context_parts.append(f"Requires Sponsorship: {profile['require_sponsorship']}")
        if profile.get('work_authorization'): context_parts.append(f"Work Authorization: {profile['work_authorization']}")
        
        # Skills and Technical Information
        context_parts.append("\n=== SKILLS AND TECHNICAL ===")
        if profile.get('programming_languages'): context_parts.append(f"Programming Languages: {', '.join(profile['programming_languages'])}")
        if profile.get('frameworks'): context_parts.append(f"Frameworks: {', '.join(profile['frameworks'])}")
        if profile.get('tools'): context_parts.append(f"Tools: {', '.join(profile['tools'])}")
        if profile.get('technical_skills'): context_parts.append(f"Technical Skills: {', '.join(profile['technical_skills'])}")
        
        # Location Preferences
        context_parts.append("\n=== LOCATION PREFERENCES ===")
        if profile.get('preferred_locations'): context_parts.append(f"Preferred Locations: {', '.join(profile['preferred_locations'])}")
        if profile.get('willing_to_relocate'): context_parts.append(f"Willing to Relocate: {profile['willing_to_relocate']}")
        if profile.get('availability'): context_parts.append(f"Availability: {profile['availability']}")
        
        # Demographics - USE REAL DATA when available
        context_parts.append("\n=== DEMOGRAPHICS (Use actual data when available) ===")
        if profile.get('gender'):
            context_parts.append(f"Gender: {profile['gender']} (USE THIS - do not decline)")
        else:
            context_parts.append("Gender: Not specified (decline appropriate)")
        
        if profile.get('race_ethnicity'):
            context_parts.append(f"Race/Ethnicity: {profile['race_ethnicity']} (USE THIS - do not decline)")
        else:
            context_parts.append("Race/Ethnicity: Not specified (decline appropriate)")
        
        if profile.get('veteran_status'):
            context_parts.append(f"Veteran Status: {profile['veteran_status']} (USE THIS - do not decline)")
        else:
            context_parts.append("Veteran Status: Not specified (assume 'No' if not veteran)")
        
        if profile.get('disability_status'):
            context_parts.append(f"Disability Status: {profile['disability_status']} (USE THIS - do not decline)")
        else:
            context_parts.append("Disability Status: Not specified (assume 'No' if no disability)")
        
        # Work Authorization
        context_parts.append("\n=== WORK AUTHORIZATION ===")
        if profile.get('work_authorization'):
            context_parts.append(f"Work Authorization: {profile['work_authorization']}")
        elif profile.get('visa sponsorship'):
            if profile['visa sponsorship'].lower() == 'required':
                context_parts.append("Work Authorization: Requires visa sponsorship - choose 'No' for US work authorization")
            else:
                context_parts.append("Work Authorization: Has work authorization - choose 'Yes'")
        
        else:
            context_parts.append("Work Authorization: Not specified in profile - use NEEDS_HUMAN_INPUT if asked")
        
        # CRITICAL: What to do when information is missing
        context_parts.append("\n=== HANDLING MISSING INFORMATION ===")
        context_parts.append("If profile doesn't contain specific information → use NEEDS_HUMAN_INPUT")
        context_parts.append("Do NOT assume or guess values not explicitly in the profile")
        context_parts.append("Only use profile data that is clearly present and relevant")
        
        # Education Details
        context_parts.append("\n=== EDUCATION ===")
        if profile.get('education') and len(profile['education']) > 0:
            for i, edu in enumerate(profile['education']):
                context_parts.append(f"Education {i+1}:")
                if edu.get('degree'): context_parts.append(f"  Degree: {edu['degree']}")
                if edu.get('field'): context_parts.append(f"  Field: {edu['field']}")
                if edu.get('institution'): context_parts.append(f"  Institution: {edu['institution']}")
                if edu.get('start_date'): context_parts.append(f"  Start Date: {edu['start_date']}")
                if edu.get('end_date'): context_parts.append(f"  End Date (Graduation): {edu['end_date']}")
                if edu.get('graduation_date'): context_parts.append(f"  Graduation: {edu['graduation_date']}")
                if edu.get('gpa'): context_parts.append(f"  GPA: {edu['gpa']}")
        
        # Work Experience
        context_parts.append("\n=== WORK EXPERIENCE ===")
        if profile.get('work_experience') and len(profile['work_experience']) > 0:
            for i, work in enumerate(profile['work_experience']):
                context_parts.append(f"Experience {i+1}:")
                if work.get('title'): context_parts.append(f"  Title: {work['title']}")
                if work.get('company'): context_parts.append(f"  Company: {work['company']}")
                if work.get('description'): context_parts.append(f"  Description: {work['description'][:100]}...")
                if work.get('start_date'): context_parts.append(f"  Start: {work['start_date']}")
                if work.get('end_date'): context_parts.append(f"  End: {work['end_date']}")
        
        # Skills and Interests
        if profile.get('skills'):
            context_parts.append(f"\n=== SKILLS ===")
            context_parts.append(f"Skills: {', '.join(profile['skills'])}")
        
        return "\n".join(context_parts)

    def _create_simple_mapping_prompt(self, field_descriptions: List[Dict[str, Any]]) -> str:
        """Create a prompt for Gemini to map simple fields to our profile schema."""
        
        schema_text = json.dumps(self.profile_schema, indent=2)
        fields_text = json.dumps(field_descriptions, indent=2)
        
        prompt = f"""
You are an expert form field mapper. I need you to map form fields to a specific profile schema.

PROFILE SCHEMA (what data I have available):
{schema_text}

FORM FIELDS TO MAP:
{fields_text}

TASK:
For each form field, determine which profile schema field it should map to. Return a JSON object where:
- Key: field identifier (use field.name if available, otherwise field.id, otherwise "field_{{index}}")
- Value: profile schema path (e.g., "first_name", "work_experience.0.company")

MAPPING RULES:
1. Map similar concepts (e.g., "current employer" → "work_experience.0.company")
2. Handle variations (e.g., "full name" could map to first_name if no separate last_name field)
3. Use index 0 for work_experience and education arrays (most recent/current)
4. Be AGGRESSIVE - map fields even if the match is not perfect, as long as it makes logical sense
5. For questions about internship type, graduation date, location preferences, etc., infer from available data
6. Consider field types (email fields → email, file fields → resume_path)
7. Use FLAT paths for direct fields (no nesting): "first_name", "email", "phone", "linkedin", etc.
8. For dropdown questions, map to the most relevant profile data even if not exact match

SPECIAL HANDLING FOR COMPLEX FIELDS:
9. GENDER: If profile has gender="Male", map to dropdown options like "Male", "M", "Man", etc. Use exact profile value.
10. RACE/ETHNICITY: Use nationality to infer race questions, "Non-Hispanic" for Hispanic questions.
11. DISABILITY: Look for "Yes"/"No" options first. If profile has no disability info, default to "Prefer not to say".
12. VETERAN STATUS: Similar to disability - look for "Yes"/"No", default to "Prefer not to say" if not specified.
13. VISA/WORK AUTH: Use visa_status and require_sponsorship to intelligently answer complex work authorization questions.
14. COUNTRY/STATE: Use country, state, state_code, or/and city for location dropdowns.
15. EDUCATION/WORK: Check if field relates to education/work experience and map to appropriate array elements.
16. SKILLS: Map programming languages, frameworks, tools to relevant skill fields when asked about technical expertise.
17. NOTICE PERIOD: For current students, typically "Immediately" or "Upon graduation", else "Flexible".

EXAMPLES:
- "first_name" or "fname" or "name" → "first_name"
- "email" → "email"
- "phone" → "phone"
- "linkedin" → "linkedin"
- "github" → "github"
- "current_company" or "employer" → "work_experience.0.company"
- "job_title" or "position" → "work_experience.0.title"
- "university" or "school" → "education.0.institution"
- "resume" or "cv" or "attach" (file input) → "resume_path"
- "gender" or "sex" → "gender"
- "race" or "ethnicity" → "race_ethnicity"
- "veteran" → "veteran_status"
- "disability" → "disability_status"
- "work authorization" → "work_authorization"
- "visa" → "visa_status"
- "sponsorship" → "require_sponsorship"
- "cover letter" or "additional information" → "cover_letter"
- "salary" → "salary_expectation"
- "start date" or "availability" → "availability"
- "relocate" → "willing_to_relocate"
- "graduation date" or "expected graduation" → "education.0.graduation_date"
- "internship type" or "area of interest" → "education.0.field_of_study"
- "work location" or "preferred location" → "preferred_locations.0"
- "how did you hear about us" or "referral source" → "source" or "referral_source"
- "why join" or "motivation" → "cover_letter" -> summary in profile schema
- "additional information" or "other information" → "cover_letter" -> summary in profile schema
- "worked for company before" → "work_experience.0.company" (check if company matches)
- "gender" or "sex" → "gender" (use exact value from profile, e.g., "Male", "Female", "Non-binary")
- "race" or "ethnicity" → "race_ethnicity" (infer from nationality in profile)
- "hispanic or latino" → "race_ethnicity" (infer from nationality in profile)
- "disability" or "disabled" → "disability_status" (default "No" if not specified)
- "veteran" → "veteran_status" (default "No" if not specified)
- "work authorization" or "authorized to work" → "work_authorization" + "visa_status"
- "visa sponsorship" or "sponsor" → "require_sponsorship" (from profile)
- "country" or "citizenship" → "country" (from profile location/citizenship)
- "state" or "province" → "state" (from profile) or "state_code" (abbreviation)
- "university" or "school" → "education.0.institution" (from profile education array)
- "degree" or "major" → "education.0.degree" (from profile education array)
- "graduation" → "education.0.graduation_year" (from profile education array)
- "programming languages" → "programming_languages" (from profile skills array)
- "frameworks" → "frameworks" (from profile skills/frameworks array)
- "notice period" → "availability" (e.g., "Immediately" for students, from profile if specified)

Return ONLY valid JSON, no other text:
"""
        return prompt
    
    def _parse_mapping_response(self, response_text: str) -> Dict[str, str]:
        """Parse Gemini's response to extract field mappings."""
        try:
            # Extract JSON from response (handle potential markdown formatting)
            json_match = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', response_text, re.DOTALL)
            if json_match:
                json_text = json_match.group(1)
            else:
                # Assume the entire response is JSON
                json_text = response_text.strip()
            
            # Parse JSON
            mapping = json.loads(json_text)
            
            # Validate mapping values against our schema
            validated_mapping = {}
            for field_id, schema_path in mapping.items():
                if self._validate_schema_path(schema_path):
                    validated_mapping[field_id] = schema_path
                else:
                    logger.warning(f"Invalid schema path '{schema_path}' for field '{field_id}'")
            
            return validated_mapping
            
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse Gemini response as JSON: {e}")
            logger.debug(f"Response text: {response_text}")
            return {}
        except Exception as e:
            logger.error(f"Error parsing mapping response: {e}")
            return {}
    
    def _validate_schema_path(self, schema_path: str) -> bool:
        """Validate that a schema path exists in our profile schema."""
        try:
            parts = schema_path.split('.')
            current = self.profile_schema
            
            for part in parts:
                if part.isdigit():
                    # Array index - skip validation since arrays can be extended
                    continue
                elif isinstance(current, dict) and part in current:
                    current = current[part]
                elif isinstance(current, list) and len(current) > 0:
                    current = current[0]  # Use first item as template
                    if isinstance(current, dict) and part in current:
                        current = current[part]
                    else:
                        return False
                else:
                    return False
            
            return True
            
        except Exception:
            return False
    
    def get_profile_value(self, profile: Dict[str, Any], schema_path: str) -> Optional[str]:
        """Extract a value from the profile using a schema path."""
        try:
            parts = schema_path.split('.')
            current = profile
            logger.debug(f"🔍 Extracting '{schema_path}' from profile...")
            
            for i, part in enumerate(parts):
                logger.debug(f"  Step {i+1}: Looking for '{part}' in {type(current).__name__}")
                
                if part.isdigit():
                    idx = int(part)
                    if isinstance(current, list) and len(current) > idx:
                        current = current[idx]
                        logger.debug(f"    ✅ Found array element [{idx}]: {type(current).__name__}")
                    else:
                        logger.debug(f"    ❌ Array index [{idx}] not found or invalid")
                        return None
                elif isinstance(current, dict) and part in current:
                    current = current[part]
                    logger.debug(f"    ✅ Found key '{part}': {type(current).__name__} = '{str(current)[:50]}...'")
                else:
                    if isinstance(current, dict):
                        logger.debug(f"    ❌ Key '{part}' not found. Available keys: {list(current.keys())}")
                    else:
                        logger.debug(f"    ❌ Cannot access '{part}' on {type(current).__name__}")
                    return None
            
            result = str(current) if current is not None else None
            logger.debug(f"🎯 Final value for '{schema_path}': '{result}'")
            return result
            
        except Exception as e:
            logger.debug(f"❌ Error extracting '{schema_path}': {e}")
            return None
    
    def get_smart_profile_value(self, profile: Dict[str, Any], schema_path: str, field_label: str = "") -> Optional[str]:
        """Extract value with smart inference for complex fields."""
        try:
            # First try normal extraction
            result = self.get_profile_value(profile, schema_path)
            if result:
                return result
            
            # Smart inference for common patterns
            if schema_path == "education.0.graduation_date":
                education = profile.get('education', [])
                if education and len(education) > 0:
                    grad_date = education[0].get('graduation_date', '')
                    if grad_date:
                        return str(grad_date)
            
            elif schema_path == "preferred_locations.0":
                locations = profile.get('preferred_locations', [])
                if locations and len(locations) > 0:
                    return str(locations[0])
            
            elif schema_path == "source":
                return profile.get('source', '')
            
            elif schema_path == "referral_source":
                return profile.get('referral_source', '')
            
            elif schema_path == "work_experience.0.company":
                work_exp = profile.get('work_experience', [])
                if work_exp and len(work_exp) > 0:
                    user_company = work_exp[0].get('company', '').lower()
                    
                    # Extract company name from field label (e.g., "Have you ever worked for Figma before?")
                    if field_label:
                        # Look for company name in the question
                        import re
                        # Try to find company name patterns like "worked for X before" or "worked at X"
                        company_match = re.search(r'worked for (\w+) before|worked at (\w+)', field_label.lower())
                        if company_match:
                            asked_company = (company_match.group(1) or company_match.group(2)).lower()
                            # Check if user has worked for this specific company
                            if asked_company in user_company or user_company in asked_company:
                                return 'Yes'
                            else:
                                return 'No'
                    
                    # Default to No if we can't determine the company
                    return 'No'
            
            return None
            
        except Exception as e:
            logger.debug(f"❌ Error in smart extraction for '{schema_path}': {e}")
            return None

    def _validate_simple_field_value(self, value: str, field_info: Dict[str, Any]) -> str:
        """Validate simple field values and return empty string if problematic."""
        if not value or not value.strip():
            return ""
        
        value = value.strip()
        label = field_info.get('label', '').lower()
        
        # Check for common problematic patterns
        problematic_patterns = [
            'as a', 'i am', 'during my time', 'my experience', 'i have',
            'i worked', 'my role', 'in my position', 'my background'
        ]
        
        # If it looks like a narrative response, reject it
        if any(pattern in value.lower() for pattern in problematic_patterns):
            logger.warning(f"🚨 Detected narrative response for simple field '{label}': {value[:50]}...")
            return ""  # Let it be handled as needs_human_input
        
        # If it's too long for a simple field, reject it
        if len(value) > 50:
            logger.warning(f"🚨 Response too long for simple field '{label}': {len(value)} chars")
            return ""  # Let it be handled as needs_human_input
        
        # Specific field validations
        if 'work authorization' in label or 'authorized to work' in label:
            # Ensure work authorization fields don't contain location names
            location_words = ['maryland', 'california', 'texas', 'new york', 'florida']
            if any(loc in value.lower() for loc in location_words):
                logger.warning(f"🚨 Location name in work authorization field: {value}")
                return ""  # Let it be handled as needs_human_input
        
        return value

    async def select_best_dropdown_option_from_list(
        self, 
        target_value: str, 
        available_options: List[Dict[str, str]], 
        profile: Optional[Dict[str, Any]] = None
    ) -> Optional[Dict[str, Any]]:
        """Use AI to intelligently select the best option from a list of available dropdown options."""
        try:
            if not target_value:
                return None
            
            if not available_options:
                logger.warning("No dropdown options provided to AI for selection")
                return None
            
            # Create profile context for AI
            profile_context = ""
            if profile:
                profile_context = f"""
USER PROFILE CONTEXT:
- Name: {profile.get('first_name', '')} {profile.get('last_name', '')}
- Email: {profile.get('email', '')}
- Phone: {profile.get('phone', '')}
- Country: {profile.get('country', '')}
- State: {profile.get('state', '')}
- City: {profile.get('city', '')}
- Work Authorization: {profile.get('work_authorization', '')}
- Visa Status: {profile.get('visa_status', '')}
- Gender: {profile.get('gender', '')}
- Veteran Status: {profile.get('veteran_status', '')}
- Disability Status: {profile.get('disability_status', '')}
- Race/Ethnicity: {profile.get('race_ethnicity', '')}
"""
            
            # Format the available options
            options_text = "\n".join([f"  - {opt['text']}" for opt in available_options])
            
            prompt = f"""
You are helping fill out a job application form. You need to select the BEST matching option from the dropdown for the value: "{target_value}"

{profile_context}

AVAILABLE DROPDOWN OPTIONS:
{options_text}

SELECTION RULES:
1. EXACT MATCH: If there's an exact or very close text match to "{target_value}", select that
2. SEMANTIC MATCH: If the meaning matches even if wording differs, select that option
3. PROFILE-BASED: Use the user's profile information to make intelligent selections:
   - For location (country/state/city): Use profile's location data
   - For work authorization: Use profile's work_authorization or visa_status
   - For demographic questions: Use profile data or "Prefer not to say" if available
   - For experience level: Infer from work history
4. SAFE DEFAULTS: When uncertain, choose privacy-respecting options like "Prefer not to say"

IMPORTANT: You MUST select EXACTLY ONE option text from the list above. Do not create new text.

Please return a JSON response with:
{{
    "best_option_text": "exact text of the selected option from the list above",
    "reason": "brief explanation for this selection",
    "confidence": 0.9
}}

If no reasonable match exists, return:
{{
    "best_option_text": null,
    "reason": "no suitable match found",
    "confidence": 0.0
}}

Your response (JSON only):
"""
            
            import google.generativeai as genai
            model = genai.GenerativeModel(self.model_name)
            response = model.generate_content(prompt)
            
            # Parse the response
            result = self._parse_dropdown_response(response.text)
            
            # Validate that the selected option actually exists in the available options
            if result and result.get('best_option_text'):
                selected_text = result['best_option_text']
                # Check if the selected option exists (case-insensitive)
                option_exists = any(
                    opt['text'].strip().lower() == selected_text.strip().lower() 
                    for opt in available_options
                )
                if not option_exists:
                    logger.warning(f"AI selected '{selected_text}' but it doesn't exist in options, trying partial match")
                    # Try partial match
                    for opt in available_options:
                        if selected_text.lower() in opt['text'].lower() or opt['text'].lower() in selected_text.lower():
                            logger.info(f"Found partial match: '{opt['text']}' for AI selection '{selected_text}'")
                            result['best_option_text'] = opt['text']
                            option_exists = True
                            break
                    
                if not option_exists:
                    logger.error(f"AI selected invalid option '{selected_text}', not in available options")
                    return None
            
            return result
            
        except Exception as e:
            logger.error(f"❌ AI dropdown option selection failed: {e}")
            return None

    async def generate_text_field_response(
        self, 
        field_label: str, 
        field_type: str,
        profile: Dict[str, Any],
        job_context: Optional[Dict[str, Any]] = None,
        max_length: int = 500
    ) -> Optional[str]:
        """
        Generate AI-written response for essay questions, motivation fields, etc.
        
        Args:
            field_label: The field's label/question
            field_type: Type of field (textarea, text_input, etc.)
            profile: User's profile data
            job_context: Optional job description and company info
            max_length: Maximum length for the response
            
        Returns:
            Generated text response or None if generation fails
        """
        try:
            import google.generativeai as genai
            
            # Create comprehensive profile context
            profile_context = self._create_profile_context(profile, "text_generation")
            
            # Create job context if available
            job_context_text = ""
            if job_context:
                job_context_text = "\n=== JOB CONTEXT ===\n"
                if job_context.get('job_title'):
                    job_context_text += f"Position: {job_context['job_title']}\n"
                if job_context.get('company'):
                    job_context_text += f"Company: {job_context['company']}\n"
                if job_context.get('job_description'):
                    # Truncate long job descriptions
                    desc = job_context['job_description']
                    if len(desc) > 2000:
                        desc = desc[:2000] + "..."
                    job_context_text += f"Job Description:\n{desc}\n"
            
            # Determine response length guidelines
            length_guide = "2-3 sentences" if max_length < 200 else "a short paragraph (3-5 sentences)"
            if max_length > 500:
                length_guide = "1-2 paragraphs (5-8 sentences)"
            
            prompt = f"""
You are helping a job applicant fill out an application form. Write a professional, compelling response to this question:

QUESTION: "{field_label}"

{profile_context}

{job_context_text}

INSTRUCTIONS:
1. Write a response that is {length_guide} long
2. Use information from the profile to make it specific and authentic
3. If job context is provided, tailor the response to the company and position
4. Be professional, enthusiastic, and concise
5. Avoid generic phrases - use specific examples from the profile
6. Maximum length: {max_length} characters
7. DO NOT use placeholder text or [brackets] - write actual content
8. DO NOT start with "I am writing to..." or "Dear hiring manager" - just answer the question directly

RESPONSE GUIDELINES FOR COMMON QUESTIONS:
- "Why do you want to work here/join us?" → Connect your skills/experience to the company's mission, show enthusiasm
- "What interests you about this role?" → Highlight relevant skills and career goals that align with the position
- "Tell us about yourself" → Brief professional summary highlighting relevant experience and skills
- "Additional information/comments" → Mention unique qualifications, projects, or achievements not covered elsewhere
- "How did you hear about us?" → Be honest (LinkedIn, job board, referral, etc.)

Your response (text only, no JSON, no formatting):"""
            
            model = genai.GenerativeModel(self.model_name)
            response = model.generate_content(prompt)
            
            generated_text = response.text.strip()
            
            # Clean up the response
            # Remove any JSON formatting if present
            if generated_text.startswith('{') or generated_text.startswith('['):
                logger.warning("AI returned JSON format, extracting text...")
                import json
                try:
                    data = json.loads(generated_text)
                    generated_text = data.get('response', '') or data.get('text', '') or str(data)
                except:
                    pass
            
            # Truncate if too long
            if len(generated_text) > max_length:
                # Try to truncate at a sentence boundary
                sentences = generated_text[:max_length].split('. ')
                if len(sentences) > 1:
                    generated_text = '. '.join(sentences[:-1]) + '.'
                else:
                    generated_text = generated_text[:max_length-3] + '...'
            
            logger.info(f"✍️ Generated {len(generated_text)} char response for '{field_label}'")
            return generated_text
            
        except Exception as e:
            logger.error(f"❌ Text generation failed for '{field_label}': {e}")
            return None

    async def analyze_dropdown_options(self, dropdown_html: str, target_value: str, profile: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
        """Use AI to analyze dropdown HTML and suggest the best option for a target value using profile context."""
        try:
            if not dropdown_html or not target_value:
                return None
            
            # Create profile context for AI
            profile_context = ""
            if profile:
                profile_context = f"""
            USER PROFILE CONTEXT:
            - Name: {profile.get('first_name', '')} {profile.get('last_name', '')}
            - Email: {profile.get('email', '')}
            - Phone: {profile.get('phone', '')}
            - Country: {profile.get('country', '')}
            - State: {profile.get('state', '')}
            - City: {profile.get('city', '')}
            - Work Authorization: {profile.get('work_authorization', '')}
            - Gender: {profile.get('gender', '')}
            - Veteran Status: {profile.get('veteran_status', '')}
            - Disability Status: {profile.get('disability_status', '')}
            - Education: {profile.get('education', [])}
            - Work Experience: {profile.get('work_experience', [])}
            """
            
            prompt = f"""
            Analyze this dropdown HTML and help select the best option for the value '{target_value}'.
            
            {profile_context}
            
            HTML Context:
            {dropdown_html[:2000]}
            
            Please return a JSON response with:
            {{
                "best_option_text": "the text of the best matching option",
                "reason": "why this option was selected",
                "confidence": 0.0-1.0,
                "selector": "CSS selector to target this option"
            }}
            
            Look for options that match '{target_value}' by:
            1. Exact text match
            2. Partial text match  
            3. Year/date matching (if target_value is a year)
            4. Semantic similarity
            5. Profile-based inference (use the user's profile to make intelligent choices)
            
            IMPORTANT: Use the user's profile information to make smart selections:
            - For country/state/city dropdowns, match the user's location
            - For graduation dates, use the user's education information
            - For work authorization, use the user's authorization status
            - For demographic questions, use the user's provided information
            
            If no good match is found, return null.
            """
            
            model = genai.GenerativeModel(self.model_name)
            response = model.generate_content(prompt)
            
            # Parse the response
            result = self._parse_dropdown_response(response.text)
            return result
            
        except Exception as e:
            logger.error(f"❌ AI dropdown analysis failed: {e}")
            return None

    def _parse_dropdown_response(self, response_text: str) -> Optional[Dict[str, Any]]:
        """Parse AI response for dropdown analysis."""
        try:
            # Extract JSON from response
            json_match = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', response_text, re.DOTALL)
            if json_match:
                json_text = json_match.group(1)
            else:
                # Try to find JSON object in the response
                json_match = re.search(r'\{.*\}', response_text, re.DOTALL)
                if json_match:
                    json_text = json_match.group()
                else:
                    json_text = response_text.strip()
            
            result = json.loads(json_text)
            
            # Validate the result
            if not isinstance(result, dict):
                return None
            
            # Check if AI found a match
            if result.get('best_option_text') and result.get('confidence', 0) > 0.3:
                return result
            
            return None
            
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse dropdown AI response as JSON: {e}")
            return None
        except Exception as e:
            logger.error(f"Error parsing dropdown response: {e}")
            return None