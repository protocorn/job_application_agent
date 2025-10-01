import asyncio
import re
import json
from typing import Any, Dict, List, Optional
from playwright.async_api import Page, Frame, Locator
from loguru import logger

from .field_interactor import FieldInteractor
from .fast_field_mapper import FastFieldMapper
from components.brains.gemini_form_brain import GeminiFormBrain
from components.brains.gemini_field_mapper import GeminiFieldMapper
from components.detectors.sensitive_field_detector import SensitiveFieldDetector
from components.custom_exceptions import HumanInterventionRequired
from components.state.field_completion_tracker import FieldCompletionTracker

class GenericFormFiller:
    """A smart form filler that orchestrates the application process."""

    def __init__(self, page: Page | Frame, action_recorder=None):
        self.page = page
        self.ai_brain = GeminiFormBrain()
        self.field_mapper = GeminiFieldMapper()
        self.sensitive_field_detector = SensitiveFieldDetector(page)
        # NEW: Fast field mapper for instant profile matching
        self.fast_mapper = FastFieldMapper()
        # NEW: Action recording integration
        self.action_recorder = action_recorder

        # The new FieldInteractor handles all low-level element interactions
        self.interactor = FieldInteractor(page, action_recorder)
        self.attempted_fields = set()  # Track fields that have been attempted (success or failure)
        # NEW: Field completion tracker to avoid redundant work
        self.completion_tracker = FieldCompletionTracker() 
        self.action_patterns = {
            # Removed autofill patterns - we avoid autofill for accuracy and cost efficiency
            'upload': [r'upload resume', r'attach resume'],
            'manual': [r'apply manually', r'manual application', r'start application'],
        }
        # Only handle truly basic/standard fields with pattern matching
        # Everything else should be handled by Gemini for intelligent mapping
        self.field_patterns = {
            # Basic personal information - very straightforward fields
            'first_name': [r'^first name\*?$', r'^given name\*?$', r'^fname\*?$'],
            'last_name': [r'^last name\*?$', r'^family name\*?$', r'^surname\*?$', r'^lname\*?$'],
            'email': [r'^email\*?$', r'^e-mail\*?$', r'^email address\*?$'],
            'phone': [r'^phone\*?$', r'^phone number\*?$', r'^mobile\*?$', r'^telephone\*?$'],
            
            # Basic address fields
            'address': [r'^address\*?$', r'^street address\*?$', r'^address line 1\*?$'],
            'city': [r'^city\*?$', r'^town\*?$'],
            'zip_code': [r'^zip\*?$', r'^zip code\*?$', r'^postal code\*?$', r'^zipcode\*?$'],
            
            # Basic links - very standard
            'linkedin': [r'^linkedin\*?$', r'^linkedin profile\*?$', r'^linkedin url\*?$'],
            'github': [r'^github\*?$', r'^github profile\*?$', r'^github url\*?$'],
            
            # Date of birth - standard format
            'date_of_birth': [r'^date of birth\*?$', r'^birth date\*?$', r'^birthday\*?$', r'^dob\*?$'],
        }

    async def fill_form(self, profile: Dict[str, Any]) -> bool:
        """Orchestrates the form filling process using a strategic approach."""
        logger.info("🚀 Starting generic form filling process...")
        
        # Step 0: Set current page for completion tracking
        current_url = self.page.url
        self.completion_tracker.set_current_page(current_url)
        self.completion_tracker.log_progress()
        
        # Step 1: Expand form sections if needed (education, work, projects)
        await self._expand_form_sections_if_needed(profile)
        
        # Step 1: Detect sensitive fields but don't stop immediately - we'll handle them later
        sensitive_fields = await self.sensitive_field_detector.detect()
        if sensitive_fields:
            logger.warning(f"🛡️ Found {len(sensitive_fields)} sensitive field(s). Will fill non-sensitive fields first.")

        # Step 2: Decide the best overall action (Autofill > Upload > Fill Fields).
        action = await self._decide_next_action(profile)

        # Step 3: Execute the chosen action.
        success = False
        if action == 'autofill' or action == 'upload':
            # These actions often pre-fill many fields, so we follow up by filling the rest.
            success = await self._fill_remaining_fields(profile, sensitive_fields)
        elif action == 'fill_fields':
            success = await self._fill_all_fields_sequentially(profile, sensitive_fields)
        else:
            logger.warning("Could not determine a clear action. Defaulting to sequential fill.")
            success = await self._fill_all_fields_sequentially(profile, sensitive_fields)

        # Step 4: After filling non-sensitive fields, check if intervention is needed for sensitive fields
        if sensitive_fields:
            remaining_sensitive = []
            for field in sensitive_fields:
                try:
                    # Check if the sensitive field is still empty
                    if await field['element'].input_value() == "":
                        remaining_sensitive.append(field)
                except Exception:
                    # If we can't check, assume it still needs filling
                    remaining_sensitive.append(field)
            
            if remaining_sensitive:
                raise HumanInterventionRequired(
                    f"Non-sensitive fields filled successfully. Please manually fill the remaining sensitive field(s): {', '.join(f['type'] for f in remaining_sensitive)}."
                )

        logger.info(f"🏁 Form filling process completed. Success: {success}")
        return success

    async def _decide_next_action(self, profile: Dict[str, Any]) -> str:
        """Determines the best action - AVOIDING AUTOFILL for accuracy and cost efficiency."""
        
        # Strategy 1: PRIORITIZE Manual application buttons (most accurate)
        manual_selectors = [
            'button[data-automation-id*="manual"]',
            'text=Apply Manually',
            'text=Manual Application', 
            'text=Start Application',
        ]
        
        for selector in manual_selectors:
            try:
                element = await self.page.wait_for_selector(selector, timeout=2000)
                if element and await element.is_visible():
                    logger.info(f"✅ Found manual application button (avoiding autofill): {selector}")
                    await element.click()
                    await self.page.wait_for_load_state('networkidle', timeout=15000)
                    return 'navigate'
            except:
                continue
        
        # Strategy 2: Direct resume upload if file inputs are present (clean approach)
        if await self.interactor.upload_resume_if_present(profile.get('resume_path')):
             logger.info("✅ Resume uploaded directly (avoiding autofill)")
             return 'upload'
        
        # Strategy 3: AVOID AUTOFILL - Log warning if detected but don't use
        autofill_detected = False
        autofill_selectors = [
            'button[data-automation-id*="autofill"]',
            'button[aria-label*="autofill"]',
            'text=Autofill with Resume',
            'text=Auto-fill with Resume',
        ]
        
        for selector in autofill_selectors:
            try:
                element = await self.page.wait_for_selector(selector, timeout=1000)
                if element and await element.is_visible():
                    autofill_detected = True
                    logger.warning(f"⚠️ Autofill option detected but IGNORED for accuracy: {selector}")
                    break
            except:
                continue
        
        if autofill_detected:
            logger.info("🎯 Skipping autofill - proceeding with manual field filling for better accuracy")
        
        # Default action: proceed with manual field filling (most reliable)
        return 'fill_fields'

    async def _fill_remaining_fields(self, profile: Dict[str, Any], sensitive_fields: List[Dict[str, Any]] = None) -> bool:
        """After an autofill or upload, fill any fields that were missed."""
        logger.info("Scanning for remaining fields to fill...")
        return await self._fill_all_fields_sequentially(profile, sensitive_fields)

    async def _fill_all_fields_sequentially(self, profile: Dict[str, Any], sensitive_fields: List[Dict[str, Any]] = None) -> bool:
        """Fills the form by first using patterns, fast mapping, then AI for the rest."""
        logger.info("Executing sequential field filling strategy (Patterns -> Fast Mapping -> AI).")

        # Step 1: Fast and reliable pattern-based filling
        filled_by_pattern, remaining_profile = await self._fill_with_patterns(profile, sensitive_fields)
        logger.info(f"📊 Pattern matching filled {len(filled_by_pattern)} fields.")

        # Step 2: Fast profile mapping (NEW - avoids AI for simple fields)
        filled_by_fast_mapping = await self._fill_with_fast_mapping(profile, sensitive_fields)
        logger.info(f"⚡ Fast mapping filled {filled_by_fast_mapping} fields instantly.")

        # Step 3: Intelligent AI-based filling for complex fields only
        filled_by_ai = await self._fill_with_ai(profile, sensitive_fields)
        logger.info(f"🧠 AI mapping filled {filled_by_ai} additional fields.")

        # Now expand sections after filling existing fields (smart approach)
        try:
            await self._expand_sections_after_filling()
        except Exception as e:
            logger.warning(f"Error during section expansion: {e}")
            # Continue without failing the entire form filling

        return (len(filled_by_pattern) + filled_by_fast_mapping + filled_by_ai) > 0

    async def _fill_with_patterns(self, profile: Dict[str, Any], sensitive_fields: List[Dict[str, Any]] = None) -> (Dict[str, Any], Dict[str, Any]):
        """Identifies and fills form fields based on text patterns."""
        filled_fields = {}
        remaining_profile = profile.copy()
        
        # Create a set of sensitive field elements for quick lookup
        sensitive_elements = set()
        if sensitive_fields:
            for field in sensitive_fields:
                sensitive_elements.add(field['element'])
        
        visible_fields = await self.interactor.get_all_form_fields(extract_options=False)

        for field_key, patterns in self.field_patterns.items():
            if field_key not in remaining_profile or not remaining_profile.get(field_key):
                continue
            
            # Use a flag to break out of the outer loop once a match is found and filled
            field_filled = False
            for field in visible_fields:
                if field.get('is_filled'): continue # Skip already filled fields
                
                # Skip sensitive fields - they'll be handled later by human intervention
                if field.get('element') in sensitive_elements:
                    logger.debug(f"Skipping sensitive field: {field.get('label', 'unnamed')}")
                    continue

                label_text = field.get('label', '')
                field_category = field.get('field_category', '')
                
                for pattern in patterns:
                    if re.search(pattern, label_text, re.IGNORECASE):
                        # Skip inappropriate pattern matches
                        # Don't fill phone numbers into radio buttons/checkboxes
                        if (field_key == 'phone' and field_category in ['radio', 'checkbox']) or \
                           (field_key == 'email' and field_category in ['radio', 'checkbox']):
                            logger.debug(f"Skipping pattern match: '{field_key}' not suitable for {field_category} field")
                            continue
                            
                        logger.info(f"Found match for '{field_key}' (Label: '{label_text}')")
                        value_to_fill = remaining_profile[field_key]
                        
                        try:
                            await self.interactor.fill_field(field, value_to_fill, profile)
                            filled_fields[field_key] = value_to_fill
                            del remaining_profile[field_key]
                            field['is_filled'] = True # Mark as filled
                            field_filled = True
                            break # Move to the next field_key
                        except Exception as e:
                            logger.warning(f"Could not fill '{field_key}'. Error: {e}")
                if field_filled:
                    break
        return filled_fields, remaining_profile

    async def _fill_with_fast_mapping(self, profile: Dict[str, Any], sensitive_fields: List[Dict[str, Any]] = None) -> int:
        """Fast profile-to-field mapping without AI for common fields."""
        logger.info("⚡ Starting fast profile mapping...")

        # Create a set of sensitive field elements for quick lookup
        sensitive_elements = set()
        if sensitive_fields:
            for field in sensitive_fields:
                sensitive_elements.add(field['element'])

        # Get all unfilled fields (without expensive dropdown extraction)
        all_fields = await self.interactor.get_all_form_fields(extract_options=False)
        unfilled_fields = [field for field in all_fields
                          if not field.get('is_filled') and field.get('element') not in sensitive_elements]

        # Filter out completed fields
        unfilled_fields = self._filter_unattempted_fields(unfilled_fields)

        if not unfilled_fields:
            logger.info("No unfilled fields found for fast mapping.")
            return 0

        # Batch process fields with fast mapper
        fast_mapped_fields, ai_needed_fields = self.fast_mapper.batch_map_fields(unfilled_fields, profile)

        filled_count = 0
        # Fill the fast-mapped fields
        for field in fast_mapped_fields:
            try:
                element = field.get('element')
                value = field.get('fast_mapped_value')
                field_id = self._get_field_identifier(field)

                # Skip if already completed
                if self.completion_tracker.is_field_completed(field_id):
                    continue

                # Fill the field using the proper method
                await self.interactor.fill_field(field, value)

                # Mark as completed
                self.completion_tracker.mark_field_completed(field_id, field.get('label', ''), value)
                filled_count += 1

                logger.info(f"⚡ Fast filled: {field.get('label')} = {value}")

            except Exception as e:
                logger.warning(f"Fast mapping failed for {field.get('label')}: {e}")

        logger.info(f"⚡ Fast mapping completed: {filled_count} fields filled instantly")
        return filled_count

    async def _fill_with_ai(self, profile: Dict[str, Any], sensitive_fields: List[Dict[str, Any]] = None) -> int:
        """Uses enhanced AI to map and fill the remaining unfilled fields with option-aware selection."""
        
        # Create a set of sensitive field elements for quick lookup
        sensitive_elements = set()
        if sensitive_fields:
            for field in sensitive_fields:
                sensitive_elements.add(field['element'])
        
        # Phase 1: Get all fields and extract dropdown options
        logger.info("Phase 1: Extracting form fields (without expensive dropdown extraction)...")
        # STEP 1: Get basic field metadata WITHOUT extracting dropdown options
        all_fields = await self.interactor.get_all_form_fields(extract_options=False)
        
        # STEP 2: Filter out completed fields BEFORE expensive operations
        unfilled_fields = [field for field in all_fields 
                          if not field.get('is_filled') and field.get('element') not in sensitive_elements]
        
        # STEP 3: Apply completion tracker filtering (the key optimization!)
        unfilled_fields = self._filter_unattempted_fields(unfilled_fields)
        logger.info(f"🎯 After filtering completed fields: {len(unfilled_fields)} fields remaining (was {len(all_fields)})")
        
        # EARLY EXIT: If no fields need processing, we're done!
        if not unfilled_fields:
            logger.info("✅ All fields are already completed! No work needed.")
            return 0
        
        # STEP 4: NOW extract dropdown options only for remaining fields
        if unfilled_fields:
            logger.info("Phase 1b: Extracting dropdown options for remaining fields only...")
            for field in unfilled_fields:
                if field.get('field_category') in ['dropdown', 'greenhouse_dropdown']:
                    # Extract options only for fields we haven't completed
                    field_id = self._get_field_identifier(field)
                    if not self.completion_tracker.is_field_completed(field_id):
                        element = field.get('element')
                        stable_id = field.get('stable_id', field_id)
                        if element:
                            await self.interactor._extract_dropdown_options_safe(element, stable_id)
                    else:
                        logger.debug(f"⏭️ Skipping dropdown extraction for completed field: {field.get('label')}")

        if not unfilled_fields:
            logger.info("No unfilled fields remain for AI mapping.")
            return 0

        logger.info(f"Found {len(unfilled_fields)} unfilled fields for enhanced AI mapping.")
        
        # Phase 2: Categorize fields by type for intelligent processing
        logger.info("Phase 2: Categorizing fields by interaction type...")
        categorized_fields = await self._categorize_fields_by_type(unfilled_fields)
        
        # Phase 3: Process each category with specialized AI logic
        logger.info("Phase 3: Processing each field category with AI...")
        filled_count = 0
        
        # Process standard fields first (text, email, etc.)
        filled_count += await self._process_standard_fields(categorized_fields.get('standard', []), profile)
        
        # Process dropdowns with AI context analysis
        filled_count += await self._process_dropdown_fields(categorized_fields.get('dropdowns', []), profile)
        
        # Process checkboxes/radio buttons with AI decision making
        filled_count += await self._process_checkbox_fields(categorized_fields.get('checkboxes', []), profile)
        
        # Process any remaining complex fields
        filled_count += await self._process_complex_fields(categorized_fields.get('complex', []), profile)
        
        # Log summary
        logger.info(f"✅ Generic AI processing filled {filled_count} fields successfully")
        
        return filled_count
    
    async def _attempt_form_submission(self) -> bool:
        """
        Attempts to submit the form to check if optional fields are actually required.
        
        Returns:
            True if form submits successfully, False if submission fails.
        """
        try:
            from components.detectors.submit_detector import SubmitDetector
            from components.detectors.next_button_detector import NextButtonDetector
            
            # First try to find a submit/next button
            submit_detector = SubmitDetector(self.page)
            next_detector = NextButtonDetector(self.page)
            
            # Try submit button first
            submit_button = await submit_detector.detect()
            if submit_button:
                logger.info("🔘 Found submit button, attempting click...")
                initial_url = self.page.url
                await submit_button.click()
                await self.page.wait_for_timeout(3000)  # Wait for submission processing
                
                # Check if URL changed or we got success indicators
                if self.page.url != initial_url:
                    logger.info("✅ URL changed after submit - likely successful")
                    return True
                
                # Check for error messages
                error_indicators = await self.page.locator("text=/error|invalid|required|please/i").count()
                if error_indicators == 0:
                    logger.info("✅ No error messages found - likely successful")
                    return True
                else:
                    logger.info(f"❌ Found {error_indicators} error indicators")
                    return False
            
            # Try next button as fallback
            next_button = await next_detector.detect()
            if next_button:
                logger.info("🔘 Found next button, attempting click...")
                initial_url = self.page.url
                await next_button.click()
                await self.page.wait_for_timeout(3000)
                
                if self.page.url != initial_url:
                    logger.info("✅ URL changed after next - likely successful")
                    return True
                
                error_indicators = await self.page.locator("text=/error|invalid|required|please/i").count()
                return error_indicators == 0
                
            logger.warning("❌ No submit or next button found")
            return False
            
        except Exception as e:
            logger.error(f"❌ Error during form submission attempt: {e}")
            return False

    async def _get_fresh_element_reference(self, field: Dict[str, Any]) -> Optional[Any]:
        """Get a fresh element reference using stable ID parsing."""
        try:
            stable_id = field.get('stable_id', '')
            
            if not stable_id:
                logger.debug("No stable_id provided for field")
                return None
            
            # Parse stable_id to determine lookup strategy
            if stable_id.startswith('id:'):
                element_id = stable_id[3:]  # Remove 'id:' prefix
                try:
                    # Check if ID contains characters that are invalid in CSS selectors
                    # Invalid characters include: brackets, spaces, underscores in long UUIDs, etc.
                    has_invalid_css_chars = (
                        '[' in element_id or ']' in element_id or 
                        ' ' in element_id or
                        len(element_id) > 50 or  # Very long IDs are often problematic
                        element_id.count('-') > 5  # Multiple hyphens suggest complex generated IDs
                    )
                    
                    if has_invalid_css_chars:
                        # Use attribute selector for complex IDs
                        element = self.interactor.page.locator(f'[id="{element_id}"]').first
                    else:
                        # Use CSS ID selector for simple IDs
                        element = self.interactor.page.locator(f'#{element_id}').first
                    
                    if await element.is_visible():
                        logger.debug(f"Found element by ID: {element_id}")
                        return element
                except Exception as e:
                    logger.debug(f"Failed to find element by ID {element_id}: {e}")
                    pass
            
            elif stable_id.startswith('name:'):
                name = stable_id[5:]  # Remove 'name:' prefix
                try:
                    element = self.interactor.page.locator(f'[name="{name}"]').first
                    if await element.is_visible():
                        logger.debug(f"Found element by name: {name}")
                        return element
                except Exception:
                    pass
            
            elif stable_id.startswith('aria_label:'):
                aria_label = stable_id[11:]  # Remove 'aria_label:' prefix
                try:
                    element = self.interactor.page.locator(f'[aria-label="{aria_label}"]').first
                    if await element.is_visible():
                        logger.debug(f"Found element by aria-label: {aria_label}")
                        return element
                except Exception:
                    pass
            
            elif stable_id.startswith('label:'):
                # Parse: "label:First Name*:input:text"
                parts = stable_id.split(':')
                if len(parts) >= 4:
                    label_text = parts[1]
                    tag_name = parts[2]
                    input_type = parts[3]
                    
                    try:
                        # Try by associated label
                        label_elements = await self.interactor.page.locator(f'label:has-text("{label_text}")').all()
                        for label_elem in label_elements:
                            for_attr = await label_elem.get_attribute('for')
                            if for_attr:
                                target = self.interactor.page.locator(f'#{for_attr}')
                                if await target.is_visible():
                                    logger.debug(f"Found element by label association: {label_text}")
                                    return target
                        
                        # Fallback: find by tag and type near label
                        if tag_name == 'input':
                            selector = f'input[type="{input_type}"]'
                        else:
                            selector = tag_name
                            
                        elements = await self.interactor.page.locator(selector).all()
                        for element in elements:
                            # Check if this element has a label with the expected text
                            element_id = await element.get_attribute('id')
                            if element_id:
                                label_for_element = self.interactor.page.locator(f'label[for="{element_id}"]:has-text("{label_text}")')
                                if await label_for_element.count() > 0 and await element.is_visible():
                                    logger.debug(f"Found element by tag/type near label: {label_text}")
                                    return element
                    except Exception:
                        pass
            
            logger.warning(f"Could not find fresh element reference for stable_id: {stable_id}")
            return None
            
        except Exception as e:
            logger.debug(f"Error getting fresh element reference: {e}")
            return None

    async def _handle_manual_fields(self, manual_fields: List[tuple], profile: Dict[str, Any]) -> int:
        """Handle fields that require AI-generated content like cover letters, essays, etc."""
        if not manual_fields:
            return 0
        
        logger.info(f"🖋️ Processing {len(manual_fields)} fields requiring AI writing...")
        
        # Group fields by type/context for batch processing
        essay_fields = []
        
        for field, mapping_data in manual_fields:
            label = mapping_data.get('label', '').lower()
            
            # For now, treat all manual fields as essay-type questions
            essay_fields.append((field, mapping_data))
        
        filled_count = 0
        
        # Generate content for essay fields
        if essay_fields:
            filled_count += await self._generate_essay_content(essay_fields, profile)
        
        return filled_count

    async def _generate_essay_content(self, essay_fields: List[tuple], profile: Dict[str, Any]) -> int:
        """Generate AI-written content for essay-type fields."""
        filled_count = 0
        
        for field, mapping_data in essay_fields:
            try:
                label = mapping_data.get('label', '')
                field_category = mapping_data.get('field_category', 'textarea')
                
                # Generate content based on the field label/question
                logger.debug(f"🖋️ Generating content for manual field: '{label}'")
                
                if not label:
                    logger.warning(f"⚠️ No label provided for manual field, using description: {mapping_data.get('description', 'Unknown field')}")
                    label = mapping_data.get('description', 'Unknown field')
                
                # Get job context if available from state or profile
                job_context = profile.get('job_context') or getattr(self, 'current_job_context', None)
                content = await self._generate_field_content(label, profile, job_context)
                
                if content:
                    # Get fresh element reference for manual field
                    fresh_element = await self._get_fresh_element_reference(field)
                    if fresh_element:
                        fresh_field_data = {
                            'element': fresh_element,
                            'label': field.get('label', ''),
                            'field_category': field.get('field_category', 'textarea'),
                            'stable_id': field.get('stable_id', '')
                        }
                        await self.interactor.fill_field(fresh_field_data, content, profile)
                        filled_count += 1
                        field['is_filled'] = True
                        logger.info(f"✅ AI wrote content for '{label}' ({len(content)} chars)")
                    else:
                        logger.warning(f"❌ Could not find element for manual field '{label}'")
                else:
                    logger.warning(f"⚠️ Could not generate content for '{label}'")
                    
            except Exception as e:
                logger.warning(f"Failed to fill manual field '{mapping_data.get('label')}': {e}")
        
        return filled_count

    async def _generate_field_content(self, field_label: str, profile: Dict[str, Any], job_context: Dict[str, Any] = None) -> str:
        """Generate appropriate content for a specific field using AI with job context."""
        try:
            # Create context from profile
            profile_context = self._create_profile_summary(profile)

            # Extract candidate name from profile
            candidate_name = f"{profile.get('first name', profile.get('first_name', ''))} {profile.get('last name', profile.get('last_name', ''))}".strip()
            if not candidate_name:
                candidate_name = "the candidate"

            # Add job context if available
            job_context_text = ""
            if job_context:
                job_context_text = f"""
JOB CONTEXT:
Company: {job_context.get('company', 'Unknown Company')}
Position: {job_context.get('title', 'Unknown Position')}
Job Description: {job_context.get('description', 'No description provided')[:500]}...
Requirements: {job_context.get('requirements', 'No specific requirements listed')[:300]}...
"""

            prompt = f"""
You are filling out a job application form. Write a professional, personalized response that can be submitted directly without any edits.

FIELD/QUESTION: "{field_label}"

CANDIDATE PROFILE:
{profile_context}

{job_context_text}

REQUIREMENTS:
Your response must be:
1. Written in first person from {candidate_name}'s perspective
2. SUBMISSION-READY - no editing required, no placeholders, no assumptions
3. Professional and polished, ready to be pasted directly into the application
4. Based ONLY on the actual information provided above
5. 50-200 words, concise but compelling
6. Free of any brackets, assumptions, or speculative language

CONTENT RULES:
- Focus on your actual experience, education, and skills from the profile
- If company/role details are limited, emphasize your relevant qualifications
- Write confidently about what you know, ignore what you don't know
- No phrases like "I assume", "I believe", "presumably", "likely"
- No bracketed text like [Company does X] or [Assume: Y]
- No questions or requests for more information

Generate a clean, professional response that can be submitted immediately:
"""

            from components.brains.gemini_field_mapper import GeminiFieldMapper
            field_mapper = GeminiFieldMapper()
            
            import google.generativeai as genai
            model = genai.GenerativeModel("gemini-2.0-flash")
            response = model.generate_content(prompt)
            
            content = response.text.strip()

            # Basic validation - content should be submission-ready
            if len(content) < 10:
                logger.warning(f"Generated content too short for '{field_label}': {content}")
                return ""

            # Log a warning if assumption patterns are detected (shouldn't happen with improved prompt)
            if '[' in content and ']' in content:
                logger.warning(f"Generated content may contain placeholders for '{field_label}': {content[:100]}...")

            return content
            
        except Exception as e:
            logger.error(f"Error generating content for '{field_label}': {e}")
            return ""

    def _create_profile_summary(self, profile: Dict[str, Any]) -> str:
        """Create a summary of the profile for AI content generation."""
        summary_parts = []
        
        # Basic info - handle both formats
        first_name = profile.get('first_name') or profile.get('first name', '')
        last_name = profile.get('last_name') or profile.get('last name', '')
        name = f"{first_name} {last_name}".strip()
        if name:
            summary_parts.append(f"Name: {name}")
        
        # Current work
        if profile.get('current_title') and profile.get('current_company'):
            summary_parts.append(f"Current Role: {profile['current_title']} at {profile['current_company']}")
        
        # Education (all entries)
        if profile.get('education') and len(profile['education']) > 0:
            summary_parts.append("\n=== EDUCATION ===")
            for i, edu in enumerate(profile['education']):
                summary_parts.append(f"Education {i+1}:")
                if edu.get('degree'): summary_parts.append(f"  Degree: {edu['degree']}")
                if edu.get('field') or edu.get('field_of_study'): 
                    field = edu.get('field') or edu.get('field_of_study')
                    summary_parts.append(f"  Field: {field}")
                if edu.get('institution'): summary_parts.append(f"  Institution: {edu['institution']}")
                if edu.get('graduation_date'): summary_parts.append(f"  Graduation: {edu['graduation_date']}")
        
        # Work experience (all entries) - handle both formats
        work_exp = profile.get('work_experience') or profile.get('work experience', [])
        if work_exp and len(work_exp) > 0:
            summary_parts.append("\n=== WORK EXPERIENCE ===")
            for i, work in enumerate(work_exp):
                summary_parts.append(f"Experience {i+1}:")
                if work.get('title'): summary_parts.append(f"  Title: {work['title']}")
                if work.get('company'): summary_parts.append(f"  Company: {work['company']}")
                if work.get('description'): summary_parts.append(f"  Description: {work['description']}")
                if work.get('achievements'): 
                    summary_parts.append(f"  Achievements: {', '.join(work['achievements'][:3])}")
        
        # Skills - handle nested structure
        skills_data = profile.get('skills', {})
        if skills_data:
            all_skills = []
            if isinstance(skills_data, dict):
                # Extract skills from nested structure
                for category, skills_list in skills_data.items():
                    if isinstance(skills_list, list):
                        all_skills.extend(skills_list)
            elif isinstance(skills_data, list):
                all_skills = skills_data
            
            if all_skills:
                summary_parts.append(f"\n=== SKILLS ===\n{', '.join(all_skills)}")
        
        # Context
        summary_parts.append(f"\n=== APPLICATION CONTEXT ===")
        summary_parts.append("This is for a job application - use relevant background and experience")
        
        return "\n".join(summary_parts)

    def _is_nonsensical_value(self, value: str, label: str) -> bool:
        """Check if a value is nonsensical for a given field label."""
        if not value or not value.strip():
            return False
        
        value_lower = value.lower()
        label_lower = label.lower()
        
        # Detect narrative responses in simple fields
        narrative_patterns = [
            'as a', 'i am', 'during my time', 'my experience', 'i have',
            'i worked', 'my role', 'in my position', 'my background'
        ]
        
        if any(pattern in value_lower for pattern in narrative_patterns):
            return True
        
        # Check for location names in work authorization fields
        if 'work authorization' in label_lower or 'authorized to work' in label_lower:
            location_words = ['maryland', 'california', 'texas', 'new york', 'florida']
            if any(loc in value_lower for loc in location_words):
                return True
        
        # Check if value is too long for simple fields
        if len(value) > 50 and any(keyword in label_lower for keyword in ['notice', 'authorization', 'sponsorship', 'salary', 'start date']):
            return True
        
        return False

    async def _expand_form_sections_if_needed(self, profile: Dict[str, Any]) -> None:
        """Prepare section expansion data - actual expansion happens after filling existing fields."""
        logger.info("🔍 Preparing section expansion data...")
        
        # Store section data for later processing
        self._pending_sections = {
            'education': profile.get('education', []),
            'work_experience': profile.get('work experience', []) or profile.get('work_experience', []),
            'projects': profile.get('projects', [])
        }
        
        # Log what we found
        for section_type, entries in self._pending_sections.items():
            if entries:
                logger.info(f"📋 Found {len(entries)} {section_type} entries to potentially add")
            else:
                logger.debug(f"No {section_type} entries in profile")

    def _get_field_identifier(self, field: Dict[str, Any]) -> str:
        """Get a unique identifier for a field to track attempts."""
        # Use multiple attributes to create a unique ID
        identifier_parts = []
        
        if field.get('id'):
            identifier_parts.append(f"id:{field['id']}")
        if field.get('name'):
            identifier_parts.append(f"name:{field['name']}")
        if field.get('label'):
            identifier_parts.append(f"label:{field['label']}")
        if field.get('type'):
            identifier_parts.append(f"type:{field['type']}")
            
        return "|".join(identifier_parts) if identifier_parts else str(hash(str(field)))

    def _has_field_been_attempted(self, field: Dict[str, Any]) -> bool:
        """Check if this field has already been attempted."""
        field_id = self._get_field_identifier(field)
        return field_id in self.attempted_fields

    def _mark_field_attempted(self, field: Dict[str, Any], success: bool = False, value: str = None) -> None:
        """Mark a field as attempted (regardless of success/failure)."""
        field_id = self._get_field_identifier(field)
        field_label = field.get('label', 'Unknown')
        
        # Legacy tracking (keep for compatibility)
        self.attempted_fields.add(field_id)
        
        # NEW: Enhanced completion tracking
        self.completion_tracker.mark_field_attempted(field_id, field_label, success)
        
        # If successful, mark as completed (with or without value)
        if success:
            field_type = field.get('field_category', field.get('type', 'unknown'))
            display_value = value if value else "filled"
            self.completion_tracker.mark_field_completed(field_id, field_label, display_value, field_type)
        
        status = "✅ SUCCESS" if success else "❌ ATTEMPTED"
        logger.debug(f"🔖 Marked field as attempted: {field_label} - {status}")

    def _filter_unattempted_fields(self, fields: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Filter out fields that have already been attempted or completed."""
        unattempted = []
        for field in fields:
            field_id = self._get_field_identifier(field)
            field_label = field.get('label', 'Unknown')
            
            # Check completion tracker first (more reliable)
            if self.completion_tracker.should_skip_field(field_id, field_label):
                continue
                
            # Fallback to legacy check
            if not self._has_field_been_attempted(field):
                unattempted.append(field)
            else:
                logger.debug(f"⏭️ Skipping already attempted field: {field_label}")
        return unattempted

    async def _expand_sections_after_filling(self) -> None:
        """Expand sections after filling existing fields - one at a time, fill, then check if more needed."""
        if not hasattr(self, '_pending_sections'):
            return
            
        logger.info("🔄 Expanding sections after filling existing fields...")
        
        for section_type, entries in self._pending_sections.items():
            if not entries:
                continue
                
            logger.info(f"📝 Processing {section_type} sections...")
            
            # Count existing sections on the form  
            keywords_map = {
                'education': ['education', 'school', 'university', 'degree'],
                'work_experience': ['work', 'experience', 'employment', 'job', 'position'], 
                'projects': ['project', 'portfolio', 'achievement']
            }
            section_keywords = keywords_map.get(section_type, [section_type])
            existing_count = await self._count_existing_sections(section_keywords)
            needed_count = len(entries)
            
            logger.info(f"📊 Found {existing_count} existing {section_type} sections, need {needed_count}")
            
            # Add sections one by one
            for i in range(existing_count, needed_count):
                logger.info(f"➕ Adding {section_type} section {i + 1}/{needed_count}")
                
                # Click Add button once
                success = await self._click_single_add_button(section_type)
                if not success:
                    logger.warning(f"❌ Failed to add {section_type} section {i + 1}")
                    break
                    
                # Wait a moment for the new section to appear
                await asyncio.sleep(1)
                
                # Fill the newly added section
                await self._fill_newly_added_section(section_type, entries[i])
                
                logger.info(f"✅ Added and filled {section_type} section {i + 1}")

    async def _click_single_add_button(self, section_type: str) -> bool:
        """Click a single Add button for the specified section type."""
        try:
            # Look for section-specific Add buttons first
            section_patterns = [
                f"*:has-text('{section_type}') >> .. >> button:has-text('Add')",
                f"button:has-text('Add {section_type.title()}')",
                f"button:has-text('Add {section_type}')",
                f"*:has-text('{section_type}') >> button:has-text('Add')"
            ]
            
            for pattern in section_patterns:
                try:
                    button = self.page.locator(pattern).first
                    if await button.is_visible(timeout=2000):
                        await button.click()
                        logger.info(f"✅ Clicked {section_type} Add button using pattern: {pattern}")
                        return True
                except:
                    continue
            
            # Fallback to generic Add button
            try:
                button = self.page.locator("button:has-text('Add')").first
                if await button.is_visible(timeout=2000):
                    await button.click()
                    logger.info(f"✅ Clicked generic Add button for {section_type}")
                    return True
            except:
                pass
                
            logger.warning(f"❌ Could not find Add button for {section_type}")
            return False
            
        except Exception as e:
            logger.error(f"Error clicking Add button for {section_type}: {e}")
            return False

    async def _fill_newly_added_section(self, section_type: str, entry_data: Dict[str, Any]) -> None:
        """Fill the newly added section with the provided entry data."""
        try:
            # This would need to be implemented based on the specific form structure
            # For now, just log that we would fill it
            logger.info(f"📝 Would fill {section_type} section with: {entry_data.get('title', 'Unknown')}")
        except Exception as e:
            logger.error(f"Error filling {section_type} section: {e}")

    async def _expand_section_type(self, profile: Dict[str, Any], profile_key: str, section_keywords: List[str]) -> None:
        """Expand a specific section type (education, work, projects) if needed."""
        try:
            # Get profile entries for this section
            profile_entries = profile.get(profile_key, [])
            if not profile_entries or not isinstance(profile_entries, list):
                logger.debug(f"No {profile_key} entries in profile, skipping expansion")
                return
            
            entries_needed = len(profile_entries)
            if entries_needed <= 1:
                logger.debug(f"Only {entries_needed} {profile_key} entry needed, no expansion required")
                return
            
            logger.info(f"📋 Profile has {entries_needed} {profile_key} entries, checking form sections...")
            
            # Count existing sections on the form
            existing_sections = await self._count_existing_sections(section_keywords)
            logger.info(f"📊 Found {existing_sections} existing {profile_key} sections on form")
            
            # Calculate how many Add button clicks we need
            clicks_needed = max(0, entries_needed - existing_sections)
            
            if clicks_needed == 0:
                logger.info(f"✅ Form already has enough {profile_key} sections ({existing_sections})")
                return
            
            logger.info(f"🎯 Need to click Add button {clicks_needed} times for {profile_key}")
            
            # Find and click Add buttons
            await self._click_add_buttons(section_keywords, clicks_needed)
            
        except Exception as e:
            logger.warning(f"Error expanding {profile_key} sections: {e}")

    async def _count_existing_sections(self, section_keywords: List[str]) -> int:
        """Count how many sections of a given type already exist on the form."""
        try:
            max_count = 0
            
            # Method 1: Count form fieldsets/sections with relevant keywords
            for keyword in section_keywords:
                patterns = [
                    f'fieldset:has-text("{keyword}")',
                    f'div[class*="{keyword}"]',
                    f'div[id*="{keyword}"]',
                    f'section:has-text("{keyword}")',
                    f'*[class*="{keyword}-section"]',
                    f'*[class*="{keyword}s-section"]',  # plural
                    f'div[class*="{keyword}-item"]',
                    f'div[class*="{keyword}-entry"]'
                ]
                
                for pattern in patterns:
                    try:
                        # First do a quick count to avoid processing too many elements
                        element_count = await self.page.locator(pattern).count()
                        if element_count > 50:  # Skip patterns that match too many elements
                            logger.debug(f"Skipping pattern '{pattern}' - too many matches ({element_count})")
                            continue
                            
                        elements = await self.page.locator(pattern).all()
                        visible_count = 0
                        for element in elements:
                            if await element.is_visible():
                                visible_count += 1
                        
                        if visible_count > max_count:
                            max_count = visible_count
                            logger.debug(f"Found {visible_count} {keyword} sections using pattern: {pattern}")
                    except Exception:
                        continue
            
            # Method 2: Count input groups that might represent sections
            # Look for patterns like repeated input groups
            for keyword in section_keywords:
                try:
                    # Look for input fields with names containing the keyword and numbers
                    input_patterns = [
                        f'input[name*="{keyword}"][name*="0"]',  # Look for indexed inputs
                        f'input[name*="{keyword}"][name*="1"]',
                        f'input[name*="{keyword}"][name*="2"]',
                        f'input[id*="{keyword}"][id*="0"]',
                        f'input[id*="{keyword}"][id*="1"]',
                        f'input[id*="{keyword}"][id*="2"]'
                    ]
                    
                    indexed_inputs = 0
                    for pattern in input_patterns:
                        elements = await self.page.locator(pattern).all()
                        if elements:
                            indexed_inputs = max(indexed_inputs, len(elements))
                    
                    if indexed_inputs > max_count:
                        max_count = indexed_inputs
                        logger.debug(f"Found {indexed_inputs} {keyword} sections based on indexed inputs")
                        
                except Exception:
                    continue
            
            # Method 3: Look for section headers or labels that might indicate sections
            for keyword in section_keywords:
                try:
                    header_patterns = [
                        f'h1:has-text("{keyword}")',
                        f'h2:has-text("{keyword}")',
                        f'h3:has-text("{keyword}")',
                        f'h4:has-text("{keyword}")',
                        f'label:has-text("{keyword}")',
                        f'legend:has-text("{keyword}")',
                        f'*[class*="title"]:has-text("{keyword}")',
                        f'*[class*="header"]:has-text("{keyword}")'
                    ]
                    
                    for pattern in header_patterns:
                        elements = await self.page.locator(pattern).all()
                        visible_count = sum(1 for el in elements if await el.is_visible())
                        
                        if visible_count > max_count:
                            max_count = visible_count
                            logger.debug(f"Found {visible_count} {keyword} sections based on headers")
                            
                except Exception:
                    continue
            
            # Return the highest count found, with a minimum of 1 and maximum of 10 (sanity check)
            result = max(1, min(max_count, 10)) if max_count > 0 else 1
            logger.debug(f"Final section count: {result} (capped from {max_count})")
            return result
            
        except Exception as e:
            logger.debug(f"Error counting existing sections: {e}")
            return 1  # Conservative assumption

    async def _click_add_buttons(self, section_keywords: List[str], clicks_needed: int) -> None:
        """Find and click Add buttons for a specific section type the specified number of times."""
        try:
            if clicks_needed <= 0:
                return
                
            # Get the primary section keyword for more focused searching
            primary_keyword = section_keywords[0] if section_keywords else "section"
            logger.info(f"🎯 Looking for '{primary_keyword}' specific Add buttons")
            
            # Section-specific Add button patterns (most specific first)
            specific_patterns = []
            for keyword in section_keywords:
                specific_patterns.extend([
                    # Exact text matches (highest priority)
                    f'button:has-text("Add {keyword.title()}")',
                    f'button:has-text("Add {keyword}")',
                    f'button:has-text("+ {keyword.title()}")',
                    f'button:has-text("+ {keyword}")',
                    f'a:has-text("Add {keyword.title()}")',
                    f'a:has-text("Add {keyword}")',
                    
                    # Near section content
                    f'button:near(text=/{keyword}/i):has-text("Add")',
                    f'*:has-text("{keyword}") >> .. >> button:has-text("Add")',
                    
                    # Class/ID based patterns
                    f'button[class*="add-{keyword}"]',
                    f'button[id*="add-{keyword}"]',
                    f'*[data-testid*="add-{keyword}"]',
                ])
            
            # ATS-specific patterns for sections
            ats_patterns = [
                # Workday specific
                f'button[data-automation-id*="add"]:near(text=/{primary_keyword}/i)',
                f'*[data-automation-id*="addButton"]:near(text=/{primary_keyword}/i)',
                
                # Greenhouse specific  
                f'button[class*="add-section"]:near(text=/{primary_keyword}/i)',
                f'button[class*="add-item"]:near(text=/{primary_keyword}/i)',
            ]
            
            # Only use generic patterns as absolute last resort
            fallback_patterns = [
                'button:has-text("Add")',
                'button:has-text("+")',
                '[role="button"]:has-text("Add")',
            ]
            
            successful_clicks = 0
            
            for click_count in range(clicks_needed):
                logger.info(f"🔘 Attempting to click {primary_keyword} Add button (click {click_count + 1}/{clicks_needed})")
                
                button_found = False
                
                # Try section-specific patterns first (highest priority)
                for pattern in specific_patterns:
                    try:
                        buttons = await self.page.locator(pattern).all()
                        for button in buttons:
                            if await button.is_visible():
                                await button.click()
                                logger.info(f"✅ Clicked {primary_keyword} Add button using specific pattern: {pattern}")
                                button_found = True
                                successful_clicks += 1
                                break
                        if button_found:
                            break
                    except Exception:
                        continue
                
                # Try ATS-specific patterns
                if not button_found:
                    for pattern in ats_patterns:
                        try:
                            button = self.page.locator(pattern).first
                            if await button.is_visible(timeout=1000):
                                await button.click()
                                logger.info(f"✅ Clicked {primary_keyword} Add button using ATS pattern: {pattern}")
                                button_found = True
                                successful_clicks += 1
                                break
                        except Exception:
                            continue
                
                # Only use fallback if we're desperate AND this is the first click
                if not button_found and click_count == 0:
                    logger.warning(f"⚠️ No specific {primary_keyword} Add button found, trying fallback patterns")
                    for pattern in fallback_patterns:
                        try:
                            button = self.page.locator(pattern).first
                            if await button.is_visible(timeout=1000):
                                await button.click()
                                logger.info(f"⚠️ Clicked fallback Add button for {primary_keyword}: {pattern}")
                                button_found = True
                                successful_clicks += 1
                                break
                        except Exception:
                            continue
                
                if not button_found:
                    logger.warning(f"❌ Could not find {primary_keyword} Add button for click {click_count + 1}")
                    break
                
                # Wait for the new section to load
                await self.page.wait_for_timeout(1500)
            
            if successful_clicks > 0:
                logger.info(f"✅ Successfully clicked {primary_keyword} Add button {successful_clicks} times")
            else:
                logger.warning(f"❌ Failed to click any {primary_keyword} Add buttons")
            
        except Exception as e:
            logger.warning(f"Error clicking {primary_keyword} Add buttons: {e}")


    def _get_field_identifier(self, field: Dict[str, Any]) -> str:
        """Get a unique identifier for a field."""
        stable_id = field.get('stable_id', '').strip()
        if stable_id:
            return stable_id
        
        name = field.get('name', '').strip()
        id_attr = field.get('id', '').strip()
        label = field.get('label', '').strip()
        
        if name: return name
        if id_attr: return id_attr
        return f"field_{hash(label)}"

    async def _get_all_form_fields(self) -> List[Dict[str, Any]]:
        """Compatibility proxy used by callers still referencing the old API."""
        return await self.interactor.get_all_form_fields(extract_options=False)

    async def _categorize_fields_by_type(self, fields: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
        """Categorize fields by interaction type for specialized processing."""
        categories = {
            'standard': [],      # text, email, phone, etc.
            'dropdowns': [],     # all dropdown types
            'checkboxes': [],    # checkboxes and radio buttons
            'complex': []        # multiselect, file upload, etc.
        }
        
        for field in fields:
            field_category = field.get('field_category', 'text_input')
            
            if field_category in ['text_input', 'email_input', 'tel_input', 'url_input', 'number_input']:
                categories['standard'].append(field)
            elif 'dropdown' in field_category or field_category in ['custom_dropdown', 'workday_dropdown', 'greenhouse_dropdown', 'ashby_button_group']:
                categories['dropdowns'].append(field)
            elif field_category in ['checkbox', 'radio']:
                categories['checkboxes'].append(field)
            elif field_category in ['workday_multiselect', 'file_upload', 'textarea']:
                categories['complex'].append(field)
            else:
                # Default to standard for unknown types
                categories['standard'].append(field)
        
        logger.info(f"📊 Field categories: {len(categories['standard'])} standard, {len(categories['dropdowns'])} dropdowns, {len(categories['checkboxes'])} checkboxes, {len(categories['complex'])} complex")
        return categories

    async def _process_standard_fields(self, fields: List[Dict[str, Any]], profile: Dict[str, Any]) -> int:
        """Process standard text fields using existing pattern matching and AI."""
        if not fields:
            return 0
        
        logger.info(f"🔤 Processing {len(fields)} standard text fields...")
        filled_count = 0
        
        # Use existing field mapping for standard fields
        field_mapping = await self.field_mapper.map_fields_to_profile(fields, profile)
        
        for field in fields:
            field_id = self._get_field_identifier(field)
            if field_id in field_mapping:
                mapping_data = field_mapping[field_id]
                mapping_type = mapping_data.get('type', 'simple')
                
                if mapping_type == 'simple':
                    value = mapping_data.get('value')
                    if value:
                        try:
                            fresh_element = await self._get_fresh_element_reference(field)
                            if fresh_element:
                                # Route through FieldInteractor to ensure action recording
                                fresh_field_data = {
                                    'element': fresh_element,
                                    'label': field.get('label', ''),
                                    'field_category': field.get('field_category', 'text_input'),
                                    'stable_id': field.get('stable_id', ''),
                                    'input_type': field.get('input_type', 'text'),
                                }
                                await self.interactor.fill_field(fresh_field_data, str(value), profile)
                                filled_count += 1
                                self._mark_field_attempted(field, success=True)
                                logger.info(f"✅ Filled standard field '{field.get('label')}': {value}")
                        except Exception as e:
                            self._mark_field_attempted(field, success=False)
                            logger.warning(f"❌ Failed to fill standard field: {e}")
        
        return filled_count

    async def _process_dropdown_fields(self, fields: List[Dict[str, Any]], profile: Dict[str, Any]) -> int:
        """Process dropdown fields using AI context analysis."""
        if not fields:
            return 0
        
        logger.info(f"📋 Processing {len(fields)} dropdown fields with AI...")
        
        # Prepare dropdown context for AI
        dropdown_contexts = []
        for field in fields:
            context = {
                'id': self._get_field_identifier(field),
                'label': field.get('label', ''),
                'question': field.get('label', ''),
                'options': [opt.get('text', '') for opt in field.get('options', [])],
                'field_type': 'dropdown'
            }
            dropdown_contexts.append(context)
        
        # Ask AI to make dropdown selections
        # Debug: Log dropdown contexts being sent to AI
        logger.debug(f"📋 Sending {len(dropdown_contexts)} dropdown contexts to AI")
        for i, context in enumerate(dropdown_contexts[:3]):  # Log first 3 for debugging
            logger.debug(f"   Context {i+1}: {context.get('label', 'Unknown')} with {len(context.get('options', []))} options")
        
        ai_selections = await self._get_ai_dropdown_selections(dropdown_contexts, profile)
        
        filled_count = 0
        for field in fields:
            field_id = self._get_field_identifier(field)
            if field_id in ai_selections:
                selection = ai_selections[field_id]
                try:
                    fresh_element = await self._get_fresh_element_reference(field)
                    if fresh_element:
                        fresh_field_data = {
                            'element': fresh_element,
                            'label': field.get('label', ''),
                            'field_category': field.get('field_category', 'dropdown'),
                            'stable_id': field.get('stable_id', ''),
                        }
                        await self.interactor.fill_field(fresh_field_data, selection, profile)
                        filled_count += 1
                        self._mark_field_attempted(field, success=True, value=selection)
                        logger.info(f"✅ AI selected dropdown '{field.get('label')}': {selection}")
                except Exception as e:
                    self._mark_field_attempted(field, success=False)
                    logger.warning(f"❌ Failed to fill dropdown field: {e}")
        
        return filled_count

    async def _process_checkbox_fields(self, fields: List[Dict[str, Any]], profile: Dict[str, Any]) -> int:
        """Process checkbox/radio fields using AI decision making."""
        if not fields:
            return 0
        
        logger.info(f"☑️ Processing {len(fields)} checkbox/radio fields with AI...")
        
        # Prepare checkbox context for AI
        checkbox_contexts = []
        for field in fields:
            context = {
                'id': self._get_field_identifier(field),
                'label': field.get('label', ''),
                'question': field.get('label', ''),
                'field_type': field.get('field_category', 'checkbox'),
                'current_value': 'unchecked'  # Default state
            }
            checkbox_contexts.append(context)
        
        # Ask AI to make checkbox decisions
        ai_decisions = await self._get_ai_checkbox_decisions(checkbox_contexts, profile)
        
        filled_count = 0
        for field in fields:
            field_id = self._get_field_identifier(field)
            if field_id in ai_decisions:
                should_check = ai_decisions[field_id].get('check', False)
                reason = ai_decisions[field_id].get('reason', '')
                
                try:
                    fresh_element = await self._get_fresh_element_reference(field)
                    if fresh_element:
                        checkbox_value = "checked" if should_check else "unchecked"
                        if should_check:
                            try:
                                # Try standard check method first
                                await fresh_element.check()
                                logger.info(f"✅ AI checked '{field.get('label')}': {reason}")
                            except Exception as check_error:
                                logger.debug(f"Standard .check() failed for '{field.get('label')}', trying click: {check_error}")
                                try:
                                    # Fallback to click for custom elements (like Ashby buttons)
                                    await fresh_element.click()
                                    logger.info(f"✅ AI clicked '{field.get('label')}' (fallback): {reason}")
                                except Exception as click_error:
                                    logger.warning(f"❌ Both .check() and .click() failed for '{field.get('label')}': {click_error}")
                                    raise click_error
                        else:
                            if await fresh_element.is_checked():
                                try:
                                    await fresh_element.uncheck()
                                except Exception:
                                    # Try clicking to uncheck for custom elements
                                    await fresh_element.click()
                            logger.info(f"⬜ AI left unchecked '{field.get('label')}': {reason}")
                        filled_count += 1
                        self._mark_field_attempted(field, success=True, value=checkbox_value)
                    else:
                        logger.warning(f"❌ Could not find fresh element for checkbox '{field.get('label')}' with stable_id: {field.get('stable_id')}")
                        self._mark_field_attempted(field, success=False)
                except Exception as e:
                    self._mark_field_attempted(field, success=False)
                    logger.warning(f"❌ Failed to handle checkbox field '{field.get('label')}': {e}")
        
        return filled_count

    async def _process_complex_fields(self, fields: List[Dict[str, Any]], profile: Dict[str, Any]) -> int:
        """Process complex fields like multiselect, file upload, etc."""
        if not fields:
            return 0
        
        logger.info(f"🔧 Processing {len(fields)} complex fields...")
        filled_count = 0
        
        for field in fields:
            field_category = field.get('field_category', '')
            
            try:
                fresh_element = await self._get_fresh_element_reference(field)
                if fresh_element:
                    fresh_field_data = {
                        'element': fresh_element,
                        'label': field.get('label', ''),
                        'field_category': field_category,
                        'stable_id': field.get('stable_id', ''),
                    }
                    
                    if field_category == 'workday_multiselect':
                        # Extract skills from profile
                        skills = self._extract_all_skills_from_profile(profile)
                        await self.interactor.fill_field(fresh_field_data, skills, profile)
                        filled_count += 1
                        self._mark_field_attempted(field, success=True)
                        logger.info(f"✅ Filled multiselect field '{field.get('label')}'")
                    elif field_category == 'file_upload':
                        resume_path = profile.get('resume_path', 'Resumes/Sahil-Chordia-Resume.pdf')
                        await self.interactor.fill_field(fresh_field_data, resume_path, profile)
                        filled_count += 1
                        self._mark_field_attempted(field, success=True)
                        logger.info(f"✅ Uploaded file to '{field.get('label')}'")
                    elif field_category == 'textarea':
                        # Use AI to generate content with job context
                        job_context = profile.get('job_context') or getattr(self, 'current_job_context', None)
                        content = await self._generate_field_content(field.get('label', ''), profile, job_context)
                        if content:
                            try:
                                await fresh_element.fill(content, timeout=10000)  # 10 second timeout
                                filled_count += 1
                                self._mark_field_attempted(field, success=True)
                                logger.info(f"✅ AI wrote content for '{field.get('label')}'")
                            except Exception as fill_error:
                                self._mark_field_attempted(field, success=False)
                                logger.warning(f"⚠️ Failed to fill complex field '{field.get('label')}': {fill_error}")
                                # Continue with other fields instead of crashing
                        else:
                            self._mark_field_attempted(field, success=False)
                            logger.warning(f"⚠️ Could not generate content for '{field.get('label')}'")
            except Exception as e:
                logger.warning(f"❌ Failed to process complex field: {e}")
        
        return filled_count

    def _extract_all_skills_from_profile(self, profile: Dict[str, Any]) -> List[str]:
        """Extract all skills from profile for multiselect fields."""
        all_skills = []
        skills_data = profile.get('skills', {})
        
        if isinstance(skills_data, dict):
            for category, skill_list in skills_data.items():
                if isinstance(skill_list, list):
                    all_skills.extend(skill_list)
        
        # Also get from other skill-related fields
        for key in ['programming_languages', 'frameworks', 'tools', 'technical_skills']:
            if key in profile and isinstance(profile[key], list):
                all_skills.extend(profile[key])
        
        return list(set([skill.strip() for skill in all_skills if skill and skill.strip()]))

    def _create_profile_summary_for_checkboxes(self, profile: Dict[str, Any]) -> str:
        """Create a comprehensive summary of the candidate's profile for AI checkbox decisions."""
        summary_parts = []

        # Basic Personal Information
        # Handle both 'first_name'/'first name' formats
        first_name = profile.get('first_name') or profile.get('first name')
        last_name = profile.get('last_name') or profile.get('last name')
        if first_name and last_name:
            summary_parts.append(f"Name: {first_name} {last_name}")
        elif profile.get('name'):
            summary_parts.append(f"Name: {profile['name']}")

        if profile.get('email'):
            summary_parts.append(f"Email: {profile['email']}")
        if profile.get('phone'):
            summary_parts.append(f"Phone: {profile['phone']}")

        # CRITICAL: Add demographic information for checkbox decisions
        summary_parts.append("\n=== DEMOGRAPHICS (IMPORTANT FOR CHECKBOX DECISIONS) ===")
        if profile.get('gender'):
            summary_parts.append(f"Gender: {profile['gender']} (USE THIS - do not decline)")
        if profile.get('nationality'):
            summary_parts.append(f"Nationality: {profile['nationality']} (infer race/ethnicity from this)")
        if profile.get('date_of_birth'):
            summary_parts.append(f"Date of Birth: {profile['date_of_birth']}")
        if profile.get('race_ethnicity'):
            summary_parts.append(f"Race/Ethnicity: {profile['race_ethnicity']}")
        if profile.get('veteran_status'):
            summary_parts.append(f"Veteran Status: {profile['veteran_status']}")
        if profile.get('disability_status'):
            summary_parts.append(f"Disability Status: {profile['disability_status']}")

        # Location
        if profile.get('current_location'):
            summary_parts.append(f"\nCurrent Location: {profile['current_location']}")
        if profile.get('city'):
            summary_parts.append(f"City: {profile['city']}")
        if profile.get('state'):
            summary_parts.append(f"State: {profile['state']}")
        if profile.get('country'):
            summary_parts.append(f"Country: {profile['country']}")
        if profile.get('preferred_locations'):
            summary_parts.append(f"Preferred Locations: {', '.join(profile['preferred_locations'])}")

        # Work authorization
        summary_parts.append("\n=== WORK AUTHORIZATION ===")
        if profile.get('visa_status'):
            summary_parts.append(f"Visa Status: {profile['visa_status']}")
        if profile.get('work_authorization'):
            summary_parts.append(f"Work Authorization: {profile['work_authorization']}")
        if profile.get('require_sponsorship'):
            summary_parts.append(f"Requires Sponsorship: {profile['require_sponsorship']}")

        # INFERENCE RULES for AI
        summary_parts.append("\n=== AI INFERENCE RULES ===")
        summary_parts.append("SAFE TO INFER:")
        summary_parts.append("- If nationality='Indian' → race/ethnicity='Asian' (confident inference)")
        summary_parts.append("- If nationality='Indian' → hispanic='No' (confident inference)")
        summary_parts.append("- If gender='Male' → select 'Male', 'Man', 'M' options (confident)")
        summary_parts.append("")
        summary_parts.append("NEVER INFER - SKIP IF NOT IN PROFILE:")
        summary_parts.append("- Transgender status: SKIP unless explicitly stated")
        summary_parts.append("- Sexual orientation: SKIP unless explicitly stated")
        summary_parts.append("- Disability status: SKIP unless explicitly stated")
        summary_parts.append("- Veteran status: SKIP unless explicitly stated")
        summary_parts.append("- Religion/beliefs: SKIP unless explicitly stated")
        summary_parts.append("- Mental health: ALWAYS SKIP")
        summary_parts.append("- Medical conditions: ALWAYS SKIP")
        
        # Education
        education = profile.get('education', [])
        if education:
            summary_parts.append(f"Education: {len(education)} entries")
            for edu in education[:2]:  # Show first 2
                if isinstance(edu, dict):
                    degree = edu.get('degree', '')
                    school = edu.get('institution', '')
                    if degree and school:
                        summary_parts.append(f"  - {degree} from {school}")
        
        # Work experience
        work_exp = profile.get('work_experience', [])
        if work_exp:
            summary_parts.append(f"Work Experience: {len(work_exp)} entries")
            for work in work_exp[:2]:  # Show first 2
                if isinstance(work, dict):
                    title = work.get('job_title', '')
                    company = work.get('company', '')
                    if title and company:
                        summary_parts.append(f"  - {title} at {company}")
        
        # Skills
        skills = profile.get('skills', {})
        if skills:
            all_skills = []
            for category, skill_list in skills.items():
                if isinstance(skill_list, list):
                    all_skills.extend(skill_list[:3])  # Limit skills per category
            if all_skills:
                summary_parts.append(f"Key Skills: {', '.join(all_skills[:10])}")  # Show top 10
        
        return '\n'.join(summary_parts)


    async def _get_ai_dropdown_selections(self, dropdown_contexts: List[Dict[str, Any]], profile: Dict[str, Any]) -> Dict[str, str]:
        """Use AI to make intelligent dropdown selections based on context and profile."""
        if not dropdown_contexts:
            return {}
        
        try:
            # Create context for AI
            profile_summary = self._create_profile_summary(profile)
            
            prompt = f"""
You are helping fill out a job application form. I will provide you with dropdown fields and their options, and you need to select the most appropriate option for each based on the candidate's profile.

CANDIDATE PROFILE:
{profile_summary}

DROPDOWN FIELDS:
{json.dumps(dropdown_contexts, indent=2)}

INSTRUCTIONS:
For each dropdown field, analyze the question/label and available options, then select the most appropriate option based on the candidate's profile data.

DECISION RULES:
- Work authorization: Use visa_status and work_authorization from profile
- Location preferences: Use preferred_locations, current location (state/country)  
- Education: Use education array data
- Experience level: Infer from work experience and education
- Company-specific questions: Check work_experience for company matches
- Demographics: Use available profile data or select reasonable defaults
- Technical questions: Use skills and experience data

RESPONSE FORMAT:
Return a JSON object where each key is the field ID and the value is the selected option text.

Example:
{{
  "field_1": "Yes",
  "field_2": "Bachelor's Degree",
  "field_3": "2-5 years"
}}

Your response (JSON only):
"""

            import google.generativeai as genai
            model = genai.GenerativeModel("gemini-2.0-flash")
            response = model.generate_content(prompt)
            
            # Check if response is empty
            if not response.text or not response.text.strip():
                logger.warning("AI returned empty response for dropdown selections")
                return {}
            
            response_text = response.text.strip()
            logger.debug(f"AI dropdown response: {response_text[:200]}...")  # Log first 200 chars
            
            # Handle markdown code blocks if present
            if response_text.startswith('```json'):
                response_text = response_text.split('```json')[1].split('```')[0].strip()
            elif response_text.startswith('```'):
                response_text = response_text.split('```')[1].split('```')[0].strip()
            
            # Parse AI response
            ai_selections = json.loads(response_text)
            
            logger.info(f"🧠 AI made {len(ai_selections)} dropdown selections")
            return ai_selections
            
        except json.JSONDecodeError as e:
            logger.error(f"AI dropdown response was not valid JSON: {e}")
            if 'response_text' in locals():
                logger.error(f"Raw response: {response_text}")
            return {}
        except Exception as e:
            logger.error(f"Error getting AI dropdown selections: {e}")
            return {}

    async def _get_ai_checkbox_decisions(self, checkbox_contexts: List[Dict[str, Any]], profile: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
        """Use AI to make intelligent checkbox/radio decisions based on context and profile."""
        if not checkbox_contexts:
            return {}
        
        try:
            # Create context for AI
            profile_summary = self._create_profile_summary_for_checkboxes(profile)

            prompt = f"""
You are helping fill out a job application form. I will provide you with checkbox/radio button fields, and you need to decide which ones to check based on the candidate's profile.

CRITICAL: USE THE PROFILE DATA CONFIDENTLY. DO NOT default to "prefer not to say" when you have information.

CANDIDATE PROFILE:
{profile_summary}

CHECKBOX/RADIO FIELDS:
{json.dumps(checkbox_contexts, indent=2)}

INSTRUCTIONS:
For each checkbox/radio field, analyze the question/label and decide whether to check it based on the candidate's profile data.

CRITICAL DECISION RULES:
- If profile has gender="Male" → CHECK "Male", "Man", "M" options (DO NOT decline)
- If profile has nationality="Indian" → CHECK "Asian" options (confident inference)
- If profile has nationality="Indian" → CHECK "No" for Hispanic questions (confident inference)
- If profile has specific data → USE IT, don't choose "prefer not to say"
- Only decline when profile explicitly says to or data is truly missing

SENSITIVE DEMOGRAPHICS - NEVER INFER OR ASSUME:
- Transgender status: ONLY if profile explicitly states it, otherwise SKIP/leave unchecked
- Sexual orientation: ONLY if explicitly in profile, otherwise SKIP/leave unchecked
- Disability status: ONLY if explicitly in profile, otherwise SKIP/leave unchecked
- Religion/beliefs: NEVER infer, SKIP unless explicitly stated in profile
- Veteran status: ONLY if explicitly in profile, otherwise SKIP/leave unchecked
- LGBTQ+ status: NEVER infer from gender, SKIP unless explicitly stated
- Mental health: NEVER infer, always SKIP
- Medical conditions: NEVER infer, always SKIP

CRITICAL: For sensitive fields, if not explicitly in profile → LEAVE UNCHECKED (do not check any option)

DECISION RULES:
- Location checkboxes: Check if the location is in preferred_locations or matches current location
- Work authorization: Use visa_status and work_authorization data
- Terms/conditions: Generally check "Yes" for standard terms and conditions
- Demographics: Use available profile data
- Experience/skills: Use work experience and skills data
- Company-specific: Check work_experience for relevant matches
- Default behavior: When unsure, lean towards reasonable defaults (e.g., accept standard terms)

RESPONSE FORMAT:
Return a JSON object where each key is the field ID and the value is an object with:
- "check": true/false (whether to check the box)  
- "reason": brief explanation for the decision

Example:
{{
  "field_1": {{"check": true, "reason": "Texas is in preferred locations"}},
  "field_2": {{"check": false, "reason": "Colorado not in preferred locations"}},
  "field_3": {{"check": true, "reason": "Standard terms acceptance"}}
}}

Your response (JSON only):
"""

            import google.generativeai as genai
            model = genai.GenerativeModel("gemini-2.0-flash")
            response = model.generate_content(prompt)
            
            # Parse AI response
            response_text = response.text.strip()
            if not response_text:
                logger.warning("🧠 AI returned empty response for checkbox decisions")
                return {}
            
            # Remove markdown code block formatting if present
            if response_text.startswith('```json') and response_text.endswith('```'):
                response_text = response_text[7:-3].strip()
            elif response_text.startswith('```') and response_text.endswith('```'):
                response_text = response_text[3:-3].strip()
                
            ai_decisions = json.loads(response_text)
            
            logger.info(f"🧠 AI made {len(ai_decisions)} checkbox decisions")
            return ai_decisions
            
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse AI checkbox response as JSON: {e}")
            logger.debug(f"Raw AI response: {response.text}")
            return {}
        except Exception as e:
            logger.error(f"Error getting AI checkbox decisions: {e}")
            return {}