import os
import re
from typing import Any, Dict, List, Optional
from playwright.async_api import Page, Frame, Locator
from loguru import logger

class FieldInteractor:
    """A specialist in finding, identifying, and interacting with complex form fields."""

    def __init__(self, page: Page | Frame, action_recorder=None):
        self.page = page
        self.action_recorder = action_recorder
        self._cached_fields: Optional[List[Dict[str, Any]]] = None

    async def fill_field(self, field_data: Dict[str, Any], value: Any, profile: Optional[Dict[str, Any]] = None, max_retries: int = 2) -> None:
        """Fills a single field with verification and retry logic."""
        element = field_data['element']
        category = field_data.get('field_category', 'text_input')
        field_label = field_data.get('label', '')
        stable_id = field_data.get('stable_id', '')
        input_type = field_data.get('input_type', 'text')

        logger.debug(f"Interactor filling '{field_label}' (Category: {category})")

        # Check if field is already filled - skip if it is
        try:
            is_already_filled = await self._check_if_filled(element, input_type, category)
            if is_already_filled:
                logger.info(f"⏭️ Field '{field_label}' is already filled, skipping")
                # Return success without action
                if self.action_recorder:
                    self.action_recorder.record_enhanced_field_interaction(field_data, value, {
                        "success": True,
                        "method": "skipped_already_filled",
                        "final_value": "already_filled",
                        "error": None,
                        "verification": {},
                        "retry_count": 0
                    })
                return
        except Exception as e:
            logger.debug(f"Error checking if field '{field_label}' is filled: {e}, continuing with fill attempt")

        # Enhanced action recording with full context
        interaction_result = {
            "success": False,
            "method": "unknown",
            "final_value": value,
            "error": None,
            "verification": {},
            "retry_count": 0
        }

        last_error = None

        # Retry loop
        for attempt in range(max_retries):
            try:
                interaction_result["retry_count"] = attempt

                if category == 'file_upload':
                    await self.upload_resume(str(value), trigger_element=element)
                    # Verify file upload
                    if await self._verify_file_upload(element):
                        interaction_result.update({
                            "success": True,
                            "method": "file_upload",
                            "final_value": str(value)
                        })
                        break
                    else:
                        raise Exception("File upload verification failed")

                elif category == 'workday_multiselect':
                    await self._handle_workday_multiselect(element, value, profile)
                    # Verification for multiselect is complex, assume success if no exception
                    interaction_result.update({
                        "success": True,
                        "method": "workday_multiselect",
                        "final_value": str(value)
                    })
                    break

                elif 'dropdown' in category:
                    await self._handle_dropdown_selection(element, str(value), profile)

                    # Verify the selection with retry-aware logic
                    verification_passed, selected_value = await self._verify_dropdown_selection(element, str(value))

                    if verification_passed:
                        interaction_result.update({
                            "success": True,
                            "method": "dropdown_selection",
                            "final_value": selected_value,
                            "verification": {
                                "expected": str(value),
                                "actual": selected_value,
                                "passed": True
                            }
                        })
                        break
                    else:
                        raise Exception(f"Dropdown verification failed: expected '{value}', got '{selected_value}'")

                elif category == 'ashby_button_group':
                    await self._handle_ashby_button_group(element, value, profile)
                    # Verification for button groups
                    if await self._verify_button_group_selection(element, value):
                        interaction_result.update({
                            "success": True,
                            "method": "ashby_button_group",
                            "final_value": str(value)
                        })
                        break
                    else:
                        raise Exception("Button group selection verification failed")

                elif category in ['selection', 'radio', 'checkbox']:
                    if str(value).lower() in ['true', 'yes', '1', 'on', 'checked']:
                        await element.check()
                        # Verify checkbox/radio state
                        if await element.is_checked():
                            interaction_result.update({
                                "success": True,
                                "method": "check",
                                "final_value": "checked"
                            })
                            break
                        else:
                            raise Exception("Checkbox verification failed - not checked")
                    else:
                        try:
                            await element.click()
                            method = "click"
                        except Exception:
                            await element.check()
                            method = "check_fallback"

                        # Verify the action
                        if await self._verify_checkbox_state(element, value):
                            interaction_result.update({
                                "success": True,
                                "method": method,
                                "final_value": str(value)
                            })
                            break
                        else:
                            raise Exception("Radio/checkbox verification failed")

                else:
                    # Text input fields
                    await element.fill(str(value))

                    # Wait briefly for value to settle
                    await self.page.wait_for_timeout(100)

                    # Verify the value was actually filled
                    filled_value = await element.input_value()

                    if filled_value == str(value):
                        interaction_result.update({
                            "success": True,
                            "method": "text_fill",
                            "final_value": filled_value,
                            "verification": {"expected": str(value), "actual": filled_value, "passed": True}
                        })
                        break
                    else:
                        raise Exception(f"Text verification failed: expected '{value}', got '{filled_value}'")

            except Exception as e:
                last_error = e
                logger.warning(f"Attempt {attempt + 1}/{max_retries} failed for '{field_label}': {e}")

                if attempt < max_retries - 1:
                    # Wait before retry with exponential backoff
                    wait_time = 300 * (attempt + 1)
                    await self.page.wait_for_timeout(wait_time)
                    logger.debug(f"Retrying field '{field_label}' after {wait_time}ms...")
                else:
                    # Final attempt failed
                    interaction_result.update({
                        "success": False,
                        "error": str(last_error)
                    })
                    raise

        # Record the interaction
        if self.action_recorder:
            self.action_recorder.record_enhanced_field_interaction(field_data, value, interaction_result)
            if interaction_result["success"]:
                logger.debug(f"✅ Recorded successful field interaction: {field_label} = {value} (attempts: {interaction_result['retry_count'] + 1})")
            else:
                logger.warning(f"❌ Recorded failed field interaction: {field_label}")
        else:
            logger.warning(f"⚠️ No action recorder available for field: {field_label}")

    async def upload_resume(self, resume_path: str, trigger_element: Optional[Locator] = None) -> bool:
        """Robustly finds and uploads a resume using multiple strategies."""
        # Convert relative path to absolute path relative to project root
        if not os.path.isabs(resume_path):
            # Get the project root (two levels up from this file)
            current_dir = os.path.dirname(os.path.abspath(__file__))
            project_root = os.path.dirname(os.path.dirname(current_dir))  # Go up from components/executors to project root
            resume_path = os.path.join(project_root, resume_path)
        
        if not os.path.exists(resume_path):
            logger.error(f"Resume file not found at path: {resume_path}")
            return False

        # Handle iframe context by getting the parent page
        page_context = self._get_page_context()
        
        try:
            async with page_context.expect_file_chooser() as fc_info:
                if trigger_element:
                    await trigger_element.click()
                else: # Fallback if no specific button was provided
                    await self.page.locator('input[type="file"]').first.click()
            
            file_chooser = await fc_info.value
            await file_chooser.set_files(resume_path)
            logger.info(f"✅ Resume uploaded successfully using file chooser: {resume_path}")
            # Record action
            if self.action_recorder:
                self.action_recorder.record_file_upload('input[type="file"]', resume_path, success=True)
            return True
        except Exception as e:
            logger.error(f"❌ File upload failed: {e}")
            # Try direct file input approach as fallback
            return await self._try_direct_file_input(resume_path, trigger_element)

    def _get_page_context(self):
        """Get the appropriate page context for file operations."""
        # If self.page is a Frame, get its parent page
        if hasattr(self.page, 'page') and self.page.page:
            return self.page.page
        # Otherwise, assume it's already a Page
        return self.page

    async def _try_direct_file_input(self, resume_path: str, trigger_element: Optional[Locator] = None) -> bool:
        """Fallback method to try direct file input setting."""
        try:
            # Find file input element
            if trigger_element:
                file_input = trigger_element
            else:
                file_input = self.page.locator('input[type="file"]').first
            
            # Try to set files directly
            await file_input.set_input_files(resume_path)
            logger.info(f"✅ Resume uploaded using direct file input: {resume_path}")
            # Record action
            if self.action_recorder:
                self.action_recorder.record_file_upload('input[type="file"]', resume_path, success=True)
            return True
        except Exception as e:
            logger.error(f"❌ Direct file input also failed: {e}")
            return False

    async def _is_resume_already_uploaded(self) -> bool:
        """Check if a resume file is already uploaded by looking for file names or upload confirmations."""
        try:
            # Strategy 1: Look for uploaded file names (common patterns)
            uploaded_file_indicators = [
                # Generic patterns for uploaded files
            r"text=/.*\.pdf/i",
            r"text=/.*\.doc/i",
            r"text=/.*\.docx/i",
            r"text=/.*resume.*\.pdf/i",
            r"text=/.*cv.*\.pdf/i",
                # Specific file confirmation patterns
                "[data-automation-id*='file-name']",
                "[class*='uploaded-file']",
                "[class*='file-name']",
                "[class*='attachment']",
                # Success indicators
                "text=/uploaded successfully/i",
                "text=/file attached/i",
                "text=/resume uploaded/i",
                # Visual indicators (icons, check marks)
                "[aria-label*='uploaded']",
                "[title*='uploaded']",
                # Workday specific
                "[data-automation-id*='file-upload-file-name']",
                "[data-automation-id*='uploaded-file']"
            ]
            
            for pattern in uploaded_file_indicators:
                try:
                    elements = await self.page.locator(pattern).all()
                    for element in elements:
                        if await element.is_visible():
                            text_content = await element.text_content()
                            if text_content and text_content.strip():
                                # Check if it looks like a file name
                                if any(ext in text_content.lower() for ext in ['.pdf', '.doc', '.docx']):
                                    logger.info(f"🔍 Found uploaded file: {text_content.strip()}")
                                    return True
                except Exception:
                    continue
            
            # Strategy 2: Look for hidden file inputs that have files
            try:
                file_inputs = await self.page.locator('input[type="file"]').all()
                for file_input in file_inputs:
                    # Check if the input has files
                    files = await file_input.evaluate("el => el.files && el.files.length > 0")
                    if files:
                        logger.info("🔍 Found file input with uploaded files")
                        return True
            except Exception:
                pass
                
            return False
            
        except Exception as e:
            logger.debug(f"Error checking for uploaded resume: {e}")
            return False

    async def upload_resume_if_present(self, resume_path: str) -> bool:
        """If a resume upload control is present, upload the resume and return True; else False."""
        try:
            # First check if a resume is already uploaded
            if await self._is_resume_already_uploaded():
                logger.info("✅ Resume already uploaded, skipping re-upload")
                return True
            # Strategy 1: Workday-specific file upload handling
            workday_selectors = [
                'button[data-automation-id="select-files"]',
                '[data-automation-id="file-upload-drop-zone"]',
                'input[data-automation-id="file-upload-input-ref"]'
            ]
            
            for selector in workday_selectors:
                try:
                    element = await self.page.wait_for_selector(selector, timeout=3000)
                    if element and await element.is_visible():
                        logger.info(f"🎯 Found Workday upload element: {selector}")
                        
                        # For Workday, use the file input directly if available
                        if 'input' in selector:
                            # Convert relative path to absolute path
                            if not os.path.isabs(resume_path):
                                current_dir = os.path.dirname(os.path.abspath(__file__))
                                project_root = os.path.dirname(os.path.dirname(current_dir))
                                resume_path = os.path.join(project_root, resume_path)
                            
                            await element.set_input_files(resume_path)
                            logger.info("✅ Resume uploaded via Workday file input")
                            return True
                        else:
                            # For buttons/drop zones, use file chooser
                            page_context = self._get_page_context()
                            async with page_context.expect_file_chooser() as fc_info:
                                await element.click()
                            file_chooser = await fc_info.value
                            
                            # Convert relative path to absolute path
                            if not os.path.isabs(resume_path):
                                current_dir = os.path.dirname(os.path.abspath(__file__))
                                project_root = os.path.dirname(os.path.dirname(current_dir))
                                resume_path = os.path.join(project_root, resume_path)
                            
                            await file_chooser.set_files(resume_path)
                            logger.info(f"✅ Resume uploaded via Workday {selector}")
                            return True
                except Exception as e:
                    logger.debug(f"Workday upload strategy failed for {selector}: {e}")
                    continue

            # Strategy 2: Direct file input visible (generic)
            file_inputs = await self.page.locator('input[type="file"]').all()
            for fi in file_inputs:
                try:
                    if await fi.is_visible():
                        # Convert relative path to absolute path relative to project root
                        if not os.path.isabs(resume_path):
                            current_dir = os.path.dirname(os.path.abspath(__file__))
                            project_root = os.path.dirname(os.path.dirname(current_dir))
                            resume_path = os.path.join(project_root, resume_path)
                        await fi.set_input_files(resume_path)
                        logger.info("✅ Resume uploaded via visible file input")
                        return True
                except Exception:
                    continue

            # Strategy 3: Buttons/links that likely trigger file chooser
            trigger_text_patterns = [
                'select file', 'upload', 'attach', 'resume', 'cv', 'choose file', 'browse'
            ]
            trigger_locator = self.page.get_by_role("button").filter(has_text=re.compile('|'.join(trigger_text_patterns), re.IGNORECASE)).first
            try:
                if await trigger_locator.is_visible():
                    return await self.upload_resume(resume_path, trigger_element=trigger_locator)
            except Exception:
                pass

            # Strategy 4: Generic clickable elements that mention resume
            generic_trigger = self.page.locator("text=/upload|attach|resume|cv|choose file|browse|select file/i").first
            try:
                if await generic_trigger.is_visible():
                    return await self.upload_resume(resume_path, trigger_element=generic_trigger)
            except Exception:
                pass

            return False
        except Exception as e:
            logger.debug(f"upload_resume_if_present encountered an issue: {e}")
            return False

    async def get_all_form_fields(self, extract_options: bool = True) -> List[Dict[str, Any]]:
        """Gets metadata for all visible and interactive form fields, with stable element identification."""
        # Don't use caching for now to avoid stale references
        form_fields = []

        # Expanded selector to include custom form components used by Greenhouse, Workday, etc.
        selector = '''
            input:not([type="hidden"]):not([type="submit"]):not([type="button"]),
            select,
            textarea,
            [contenteditable="true"],
            [role="combobox"],
            [role="textbox"],
            [role="listbox"],
            [data-provides="typeahead"]
        '''
        elements = await self.page.locator(selector).all()
        logger.info(f"🔍 Found {len(elements)} potential form fields using standard selectors")

        visible_count = 0
        skipped_count = 0
        error_count = 0

        for i, element in enumerate(elements):
            try:
                # Relaxed visibility check: Check if element has non-zero dimensions
                # This catches Greenhouse/Workday fields that use CSS tricks
                try:
                    box = await element.bounding_box()
                    if box is None:
                        # Element not in viewport or detached - try is_visible as fallback
                        try:
                            if not await element.is_visible(timeout=100):
                                skipped_count += 1
                                continue
                        except:
                            # If both fail, skip
                            skipped_count += 1
                            continue
                    elif box['width'] == 0 or box['height'] == 0:
                        # Element has zero size - likely hidden
                        skipped_count += 1
                        continue
                except Exception as e:
                    logger.debug(f"Visibility check failed for element {i}: {e}")
                    skipped_count += 1
                    continue

                visible_count += 1
                
                label = await self._get_field_label(element)
                tag_name = await element.evaluate('el => el.tagName.toLowerCase()')
                input_type = await element.get_attribute('type') or 'text'
                
                # Enhanced field category detection
                field_category = await self._detect_field_type(element, tag_name, input_type, label)
                
                # Create a stable identifier for this field
                stable_id = await self._create_stable_field_id(element, i, label)
                
                # Extract options if it's a dropdown (but only when explicitly requested)
                options = []
                if extract_options and 'dropdown' in field_category:
                    options = await self._extract_dropdown_options_safe(element, stable_id)

                # Basic check if field is filled
                is_filled = await self._check_if_filled(element, input_type, field_category)

                form_fields.append({
                    'element': element,
                    'label': label,
                    'id': await element.get_attribute('id') or '',
                    'name': await element.get_attribute('name') or '',
                    'field_category': field_category,
                    'input_type': input_type,
                    'tag_name': tag_name,
                    'is_dropdown': 'dropdown' in field_category,
                    'is_filled': is_filled,
                    'options': options,  # Available options for dropdowns
                    'required': await element.get_attribute('required') is not None,
                    'placeholder': await element.get_attribute('placeholder') or '',
                    'stable_id': stable_id,  # Stable identifier
                    'element_index': i,  # Original index for reference
                })
            except Exception as e:
                error_count += 1
                logger.warning(f"⚠️ Error processing element {i}: {e}")
                import traceback
                logger.debug(f"Traceback: {traceback.format_exc()}")

        logger.info(f"📊 Field detection results: {len(form_fields)} fields returned, {visible_count} visible, {skipped_count} not visible, {error_count} errors")

        if len(form_fields) == 0 and visible_count == 0 and error_count > 0:
            logger.error(f"❌ All {error_count} fields failed to process! Check errors above.")

        return form_fields

    async def _create_stable_field_id(self, element: Locator, index: int, label: str) -> str:
        """Create a stable identifier for a form field that survives DOM changes."""
        try:
            # Try multiple stable identifiers in order of preference
            element_id = await element.get_attribute('id')
            if element_id:
                return f"id:{element_id}"
            
            name = await element.get_attribute('name')
            if name:
                return f"name:{name}"
            
            # Use aria-label or other stable attributes
            aria_label = await element.get_attribute('aria-label')
            if aria_label:
                return f"aria_label:{aria_label}"
            
            # Use a combination of attributes for uniqueness
            tag_name = await element.evaluate('el => el.tagName.toLowerCase()')
            input_type = await element.get_attribute('type') or 'text'
            placeholder = await element.get_attribute('placeholder') or ''
            
            # Create a composite identifier with index for uniqueness
            if label:
                return f"label:{label}:{tag_name}:{input_type}:{index}"
            elif placeholder:
                return f"placeholder:{placeholder}:{tag_name}:{input_type}:{index}"
            else:
                return f"index:{index}:{tag_name}:{input_type}"
                
        except Exception:
            return f"fallback:{index}:{label}"

    async def _extract_dropdown_options_safe(self, element: Locator, stable_id: str) -> List[Dict[str, str]]:
        """Safely extract dropdown options without breaking field state."""
        try:
            # Store the original state first
            original_value = ""
            try:
                original_value = await element.input_value()
            except Exception:
                pass
            
            logger.debug(f"Extracting options for field '{stable_id}'")
            options = await self._extract_dropdown_options(element)
            
            # Try to restore original state if it was changed
            if original_value:
                try:
                    await element.fill(original_value)
                except Exception:
                    pass
            
            return options
        except Exception as e:
            logger.debug(f"Safe option extraction failed for '{stable_id}': {e}")
            return []

    def get_field_identifier(self, field: Dict[str, Any]) -> str:
        """Gets a unique and stable identifier for a form field."""
        # Use stable_id if available (new system)
        stable_id = field.get('stable_id', '').strip()
        if stable_id:
            return stable_id
        
        # Fallback to old system
        name = field.get('name', '').strip()
        id = field.get('id', '').strip()
        label = field.get('label', '').strip()
        if name: return name
        if id: return id
        return f"field_{hash(label)}"

    async def _get_field_label(self, element: Locator) -> str:
        """Gets the most likely human-readable label for a form element with enhanced context extraction."""
        try:
            # Method 1: <label for="...id">
            element_id = await element.get_attribute('id')
            if element_id:
                label = self.page.locator(f'label[for="{element_id}"]').first
                if await label.count() > 0:
                    label_text = await label.inner_text()
                    if label_text.strip():
                        return label_text.strip()
            
            # Method 2: aria-label
            aria_label = await element.get_attribute('aria-label')
            if aria_label and aria_label.strip(): 
                return aria_label.strip()

            # Method 3: aria-labelledby
            aria_labelledby = await element.get_attribute('aria-labelledby')
            if aria_labelledby:
                label = self.page.locator(f'#{aria_labelledby}').first
                if await label.count() > 0:
                    label_text = await label.inner_text()
                    if label_text.strip():
                        return label_text.strip()

            # Method 4: For radio buttons and checkboxes, look for fieldset legend or group context
            input_type = await element.get_attribute('type')
            if input_type in ['radio', 'checkbox']:
                context = await self._get_radio_checkbox_context(element)
                if context:
                    return context

            # Method 5: Look for nearby text that might be the question/label
            nearby_context = await self._get_nearby_label_context(element)
            if nearby_context:
                return nearby_context

            # Method 6: Placeholder attribute
            placeholder = await element.get_attribute('placeholder')
            if placeholder and placeholder.strip(): 
                return placeholder.strip()
            
            return ""
            
        except Exception as e:
            logger.debug(f"Error getting field label: {e}")
            return ""

    async def _detect_field_type(self, element: Locator, tag_name: str, input_type: str, label: str) -> str:
        """Enhanced field type detection with comprehensive categorization."""
        try:
            # File upload
            if input_type == 'file':
                return 'file_upload'
            
            # Checkbox and radio buttons
            if input_type == 'checkbox':
                return 'checkbox'
            if input_type == 'radio':
                return 'radio'
            
            # Standard select dropdown
            if tag_name == 'select':
                return 'dropdown'
            
            # Textarea for long text
            if tag_name == 'textarea':
                return 'textarea'
            
            # Workday multiselect detection (for skills and similar fields)
            if await self._is_workday_multiselect(element):
                return 'workday_multiselect'
            
            # Greenhouse-style dropdown (complex custom dropdowns)
            if await self._is_greenhouse_style_dropdown(element):
                return 'greenhouse_dropdown'
            
            # Custom dropdown detection (role="combobox" or aria-haspopup)
            role = await element.get_attribute('role')
            aria_haspopup = await element.get_attribute('aria-haspopup')
            if role == 'combobox' or aria_haspopup in ['true', 'listbox', 'menu']:
                return 'custom_dropdown'
            
            # Workday-specific dropdown detection
            if await self._is_workday_dropdown(element):
                return 'workday_dropdown'

            # Ashby-style button group detection (multiple choice buttons)
            if await self._is_ashby_button_group(element):
                return 'ashby_button_group'

            # Check if label suggests it's a dropdown
            dropdown_keywords = ['select', 'choose', 'dropdown', 'option']
            if any(keyword in label.lower() for keyword in dropdown_keywords):
                return 'potential_dropdown'
            
            # Date/time inputs
            if input_type in ['date', 'datetime-local', 'time', 'month', 'week']:
                return 'date_input'
            
            # Number inputs
            if input_type == 'number':
                return 'number_input'
            
            # Email/URL/Tel inputs
            if input_type in ['email', 'url', 'tel']:
                return f'{input_type}_input'
            
            # Password inputs
            if input_type == 'password':
                return 'password_input'
            
            # Default text input
            return 'text_input'
            
        except Exception as e:
            logger.debug(f"Error detecting field type: {e}")
            return 'text_input'

    async def _extract_dropdown_options(self, element: Locator) -> List[Dict[str, str]]:
        """Extract available options from dropdown elements."""
        options = []
        try:
            tag_name = await element.evaluate('el => el.tagName.toLowerCase()')
            
            if tag_name == 'select':
                # Standard select element
                option_elements = await element.locator('option').all()
                for option in option_elements:
                    text = await option.text_content()
                    value = await option.get_attribute('value')
                    if text and text.strip():
                        options.append({
                            'text': text.strip(),
                            'value': value or text.strip()
                        })
            
            elif await self._is_greenhouse_style_dropdown(element):
                # For Greenhouse dropdowns, we need to open them first to see options
                try:
                    # Scroll element into view first
                    await element.scroll_into_view_if_needed()
                    await self.page.wait_for_timeout(200)

                    # Click to open dropdown with more lenient conditions
                    await element.click(timeout=5000, force=True)
                    await self.page.wait_for_timeout(200)  # Wait for dropdown to open
                    
                    # Look for options with various selectors
                    option_selectors = [
                        '[role="option"]',
                        '.select__option',
                        '.dropdown-option',
                        '[class*="option"]',
                        'div[role="option"]',
                        'li[role="option"]',
                        '[class*="item"]'
                    ]
                    
                    options_found = False
                    for selector in option_selectors:
                        try:
                            # Wait for options to appear with this selector
                            await self.page.wait_for_selector(selector, timeout=3000)
                            option_elements = await self.page.locator(selector).all()
                            
                            if option_elements and len(option_elements) > 1:  # More than just placeholder
                                for option in option_elements[:25]:  # Reasonable limit
                                    try:
                                        text = await option.text_content(timeout=1000)
                                        if text and text.strip() and len(text.strip()) > 0:
                                            clean_text = text.strip()
                                            # Avoid duplicates and empty options
                                            if clean_text not in [opt['text'] for opt in options]:
                                                options.append({
                                                    'text': clean_text,
                                                    'value': clean_text
                                                })
                                    except Exception:
                                        continue
                                
                                if options:
                                    options_found = True
                                    logger.debug(f"🔍 Extracted {len(options)} options for dropdown")
                                    break  # Stop after finding options with one selector
                        except Exception:
                            continue
                    
                    # Close dropdown by clicking elsewhere or pressing escape
                    try:
                        await element.press("Escape", timeout=1000)
                    except Exception:
                        try:
                            await self.page.click('body', timeout=1000)
                        except Exception:
                            pass  # Ignore if can't close
                    
                    await self.page.wait_for_timeout(200)
                    
                    if not options_found:
                        logger.debug("Could not extract options from Greenhouse dropdown - may be empty or still loading")
                    
                except Exception as e:
                    logger.debug(f"Could not extract Greenhouse dropdown options: {e}")
            
        except Exception as e:
            logger.debug(f"Error extracting dropdown options: {e}")
        
        return options

    async def _check_if_filled(self, element: Locator, input_type: str, field_category: str) -> bool:
        """Check if a field is already filled."""
        try:
            if input_type == 'file':
                # For file inputs, check if files are selected
                files = await element.evaluate('el => el.files.length')
                return files > 0
            elif field_category in ['checkbox', 'radio']:
                return await element.is_checked()
            elif 'dropdown' in field_category:
                # For dropdowns, check both input value and displayed value
                # Check standard input value first
                try:
                    value = await element.input_value()
                    if value and value.strip():
                        return True
                except Exception:
                    pass

                # For Greenhouse/custom dropdowns, check the displayed value
                if field_category in ['greenhouse_dropdown', 'custom_dropdown']:
                    try:
                        # Check if there's a visible selected value displayed
                        parent = element.locator('..')
                        display_selectors = [
                            '[class*="singleValue"]',
                            '[class*="value"]',
                            '.select__single-value'
                        ]

                        for selector in display_selectors:
                            try:
                                display_element = parent.locator(selector).first
                                if await display_element.count() > 0:
                                    selected_text = await display_element.text_content()
                                    if selected_text and selected_text.strip():
                                        # Check if it's not a placeholder
                                        placeholder_keywords = ['select', 'choose', 'pick']
                                        if not any(keyword in selected_text.lower() for keyword in placeholder_keywords):
                                            logger.debug(f"Found filled dropdown with value: '{selected_text.strip()}'")
                                            return True
                            except Exception:
                                continue
                    except Exception:
                        pass

                return False
            else:
                value = await element.input_value()
                return value is not None and value.strip() != ""
        except Exception:
            return False

    async def _handle_dropdown_selection(self, element: Locator, value: str, profile: Optional[Dict[str, Any]] = None) -> None:
        """Handles both standard <select> and complex custom dropdowns including Greenhouse-style."""
        try:
            # First, try the standard method for <select> elements
            await element.select_option(label=value, timeout=2000)
            logger.info(f"✅ Selected '{value}' using standard select_option.")
            return
        except Exception:
            logger.debug(f"Standard select_option failed for '{value}'. Trying custom dropdown logic.")

        # Custom Dropdown Logic (for divs, inputs acting as dropdowns, etc.)
        try:
            # Check if this is a Greenhouse-style dropdown
            if await self._is_greenhouse_style_dropdown(element):
                logger.info(f"🏢 Detected Greenhouse-style dropdown, using specialized logic for '{value}'")
                await self._handle_greenhouse_dropdown(element, value)
                return
            
            # Check if this is a Workday dropdown
            if await self._is_workday_dropdown(element):
                logger.info(f"🏢 Detected Workday dropdown, using specialized logic for '{value}'")
                await self._handle_workday_dropdown(element, value)
                return
            
            # Generic custom dropdown handling
            await element.click(timeout=5000) # Open the dropdown
            await self.page.wait_for_timeout(300) # Wait for options to appear
            
            # Look for an option that exactly matches the text.
            # This is a common pattern in React/Vue dropdown libraries.
            option_locator = self.page.get_by_role("option", name=value).first
            
            if await option_locator.is_visible(timeout=2000):
                await option_locator.click(timeout=3000)
                logger.info(f"✅ Clicked custom dropdown option '{value}'.")
            else: # Fallback if direct match isn't found
                 await element.fill(value)
                 await element.press("Enter")
                 logger.info(f"Typed '{value}' into dropdown and pressed Enter.")

        except Exception as e:
            logger.error(f"❌ All standard dropdown interaction methods failed for value '{value}': {e}")
            # Extract dropdown options and use AI to select the best match
            dropdown_options = await self._extract_visible_dropdown_options()
            if dropdown_options:
                logger.info(f"🤖 Found {len(dropdown_options)} dropdown options, using AI to select best match")
                await self._ai_assisted_dropdown_selection(element, value, dropdown_options, profile)
            else:
                logger.warning(f"⚠️ No dropdown options found, trying AI with HTML context")
                await self._ai_assisted_dropdown_selection(element, value, [], profile)

    async def _is_workday_dropdown(self, element: Locator) -> bool:
        """Check if element is a Workday dropdown field."""
        try:
            # Check for Workday dropdown patterns
            aria_haspopup = await element.get_attribute('aria-haspopup')
            tag_name = await element.evaluate('el => el.tagName.toLowerCase()')
            
            # Primary Workday dropdown indicators
            if tag_name == 'button' and aria_haspopup == 'listbox':
                logger.debug(f"🏢 Found Workday dropdown: button with aria-haspopup='listbox'")
                return True
            
            # Additional Workday dropdown patterns
            class_name = await element.get_attribute('class') or ''
            if 'css-' in class_name and aria_haspopup in ['listbox', 'menu']:
                logger.debug(f"🏢 Found Workday dropdown: CSS class with aria-haspopup='{aria_haspopup}'")
                return True
            
            return False
            
        except Exception as e:
            logger.debug(f"Error checking Workday dropdown: {e}")
            return False

    async def _is_workday_multiselect(self, element: Locator) -> bool:
        """Check if element is a Workday multiselect field specifically for skills/technologies."""
        try:
            # Check for Workday multiselect attributes
            data_uxi_widget_type = await element.get_attribute('data-uxi-widget-type')
            data_automation_id = await element.get_attribute('data-automation-id')
            
            # First check if it's a multiselect at all
            is_multiselect = False
            
            if data_uxi_widget_type == 'multiselect':
                is_multiselect = True
            elif data_automation_id and 'multiSelectContainer' in data_automation_id:
                is_multiselect = True
            elif await element.locator('xpath=ancestor::*[@data-uxi-widget-type="multiselect"]').count() > 0:
                is_multiselect = True
            elif (data_uxi_widget_type == 'selectinput' and 
                  await element.get_attribute('data-uxi-multiselect-id')):
                is_multiselect = True
            
            # If it's not a multiselect at all, return False
            if not is_multiselect:
                return False
            
            # Now check if it's specifically for skills/technologies
            label = await self._get_field_label(element)
            label_lower = label.lower()
            
            # Keywords that indicate skills/technology fields
            skills_keywords = [
                'skill', 'technology', 'technologies', 'programming', 'tools', 'software',
                'expertise', 'competenc', 'proficien', 'language', 'framework', 'technical',
                'certification', 'platform', 'database', 'library', 'api'
            ]
            
            # Keywords that indicate NON-skills fields (but only if NOT combined with skills keywords)
            non_skills_keywords = [
                'phone', 'country', 'code', 'region', 'location', 'address', 'contact',
                'preference', 'option', 'select', 'choose', 'device', 'state',
                'city', 'postal', 'zip', 'extension', 'area', 'timezone', 'currency'
            ]
            
            # Special case: if "type" appears with "skill", it's still a skills field
            if 'type' in label_lower and 'skill' in label_lower:
                is_non_skills = False  # Override the type check for skills fields
            else:
                is_non_skills = any(keyword in label_lower for keyword in non_skills_keywords)
            
            # Check if label contains skills keywords
            is_skills_field = any(keyword in label_lower for keyword in skills_keywords)
            
            if is_skills_field and not is_non_skills:
                logger.debug(f"🎯 Found Workday SKILLS multiselect: '{label}'")
                return True
            else:
                logger.debug(f"🚫 Found Workday multiselect but NOT for skills: '{label}' (treating as regular dropdown)")
                return False
            
        except Exception as e:
            logger.debug(f"Error checking Workday multiselect: {e}")
            return False

    async def _is_greenhouse_style_dropdown(self, element: Locator) -> bool:
        """Check if element is a Greenhouse-style dropdown."""
        try:
            # Check if element is inside a div with class="select"
            parent = element.locator('..')
            select_wrapper = parent.locator('div.select')
            
            if await select_wrapper.count() > 0:
                wrapper_class = await select_wrapper.get_attribute('class')
                logger.debug(f"🏢 Found div.select wrapper: {wrapper_class}")
                return True
            
            # Check for Greenhouse-specific attributes
            role = await element.get_attribute('role')
            aria_expanded = await element.get_attribute('aria-expanded')
            aria_haspopup = await element.get_attribute('aria-haspopup')
            
            if role == 'combobox' and aria_haspopup == 'true':
                logger.debug(f"🏢 Found Greenhouse combobox with role='{role}', aria-haspopup='{aria_haspopup}'")
                return True
            
            return False
            
        except Exception as e:
            logger.debug(f"Error checking Greenhouse dropdown: {e}")
            return False

    async def _is_ashby_button_group(self, element: Locator) -> bool:
        """Check if element is an Ashby-style button group (multiple choice buttons)."""
        try:
            # Pattern: Container with multiple buttons + hidden input/checkbox
            # Look for parent container that has multiple buttons
            parent = element.locator('xpath=..')

            # Check if parent has multiple button elements
            buttons = await parent.locator('button').count()
            if buttons >= 2:  # At least 2 buttons for multiple choice
                # Check if there's a hidden input/checkbox (Ashby pattern)
                hidden_inputs = await parent.locator('input[type="checkbox"], input[type="radio"], input[type="hidden"]').count()
                if hidden_inputs > 0:
                    # Check for Ashby-specific class patterns (optional)
                    parent_class = await parent.get_attribute('class') or ''
                    button_classes = []
                    button_elements = await parent.locator('button').all()
                    for btn in button_elements[:3]:  # Check first 3 buttons
                        btn_class = await btn.get_attribute('class') or ''
                        button_classes.append(btn_class)

                    # Common patterns: _option_, _button_, _choice_
                    is_ashby_pattern = (
                        '_option' in parent_class or
                        any('_option' in btn_class for btn_class in button_classes) or
                        '_choice' in parent_class or
                        '_container' in parent_class
                    )

                    logger.debug(f"🎯 Ashby button group detected: {buttons} buttons, parent_class='{parent_class}'")
                    return True

            return False

        except Exception as e:
            logger.debug(f"Error checking Ashby button group: {e}")
            return False

    async def _handle_ashby_button_group(self, element: Locator, value: Any, profile: Optional[Dict[str, Any]] = None) -> None:
        """Handle Ashby-style button group selection (multiple choice buttons)."""
        try:
            logger.info(f"🎯 Handling Ashby button group with value: {value}")

            # Get parent container that has the buttons
            parent = element.locator('xpath=..')
            buttons = await parent.locator('button').all()

            target_value = str(value).strip().lower()
            selected_button = None

            # Find the button that matches the target value
            for button in buttons:
                button_text = await button.inner_text()
                if button_text.strip().lower() == target_value:
                    selected_button = button
                    break

            # Fallback: partial matching
            if not selected_button:
                for button in buttons:
                    button_text = await button.inner_text()
                    if target_value in button_text.strip().lower() or button_text.strip().lower() in target_value:
                        selected_button = button
                        break

            if selected_button:
                await selected_button.click()
                button_text = await selected_button.inner_text()
                logger.info(f"✅ Clicked Ashby button: '{button_text}'")
            else:
                button_texts = []
                for button in buttons:
                    text = await button.inner_text()
                    button_texts.append(text.strip())
                logger.warning(f"⚠️ Could not find matching button for '{value}'. Available options: {button_texts}")

        except Exception as e:
            logger.error(f"❌ Error handling Ashby button group: {e}")

    async def _get_radio_checkbox_context(self, element: Locator) -> str:
        """Get context/question for radio buttons and checkboxes by looking at fieldset, legend, or group labels."""
        try:
            # Method 1: Look for fieldset legend
            fieldset = element.locator('xpath=ancestor::fieldset').first
            if await fieldset.count() > 0:
                legend = fieldset.locator('legend').first
                if await legend.count() > 0:
                    legend_text = await legend.inner_text()
                    if legend_text.strip():
                        # Also get the option value to create full context
                        option_value = await self._get_radio_option_value(element)
                        if option_value:
                            return f"{legend_text.strip()} [{option_value}]"
                        return legend_text.strip()

            # Method 2: Look for parent div/container with role="group" or similar
            group_container = element.locator('xpath=ancestor::*[@role="group" or @role="radiogroup" or contains(@class, "radio-group") or contains(@class, "checkbox-group")]').first
            if await group_container.count() > 0:
                # Look for a label or heading within this container
                group_label = group_container.locator('label, h1, h2, h3, h4, h5, h6, .question, .label').first
                if await group_label.count() > 0:
                    group_text = await group_label.inner_text()
                    if group_text.strip():
                        option_value = await self._get_radio_option_value(element)
                        if option_value:
                            return f"{group_text.strip()} [{option_value}]"
                        return group_text.strip()

            # Method 3: Look for preceding text that might be a question
            preceding_text = await self._find_preceding_question_text(element)
            if preceding_text:
                option_value = await self._get_radio_option_value(element)
                if option_value:
                    return f"{preceding_text} [{option_value}]"
                return preceding_text

            return ""
            
        except Exception as e:
            logger.debug(f"Error getting radio/checkbox context: {e}")
            return ""

    async def _get_radio_option_value(self, element: Locator) -> str:
        """Get the value/text of a radio button or checkbox option."""
        try:
            # Try to get the associated label text
            element_id = await element.get_attribute('id')
            if element_id:
                option_label = self.page.locator(f'label[for="{element_id}"]').first
                if await option_label.count() > 0:
                    return await option_label.inner_text()
            
            # Try to get value attribute
            value = await element.get_attribute('value')
            if value:
                return value
                
            # Look for sibling text
            parent = element.locator('..')
            parent_text = await parent.inner_text()
            if parent_text:
                # Clean up the text to just get the option part
                return parent_text.strip()
            
            return ""
            
        except Exception:
            return ""

    async def _find_preceding_question_text(self, element: Locator) -> str:
        """Find question text that precedes a form element."""
        try:
            # Look for previous siblings that might contain question text
            parent = element.locator('..')
            
            # Try to find text in preceding elements
            preceding_selectors = [
                'xpath=preceding-sibling::*[contains(@class, "question")]',
                'xpath=preceding-sibling::label',
                'xpath=preceding-sibling::div[contains(text(), "?")]',
                'xpath=preceding-sibling::p[contains(text(), "?")]',
                'xpath=preceding-sibling::span[contains(text(), "?")]'
            ]
            
            for selector in preceding_selectors:
                preceding_element = parent.locator(selector).last  # Get the closest one
                if await preceding_element.count() > 0:
                    text = await preceding_element.inner_text()
                    if text and '?' in text:
                        return text.strip()
            
            # Look in parent container for question-like text
            container = element.locator('xpath=ancestor::div[contains(@class, "form-group") or contains(@class, "field") or contains(@class, "question")]').first
            if await container.count() > 0:
                container_text = await container.inner_text()
                # Extract question part (text before options)
                if '?' in container_text:
                    question_part = container_text.split('?')[0] + '?'
                    return question_part.strip()
            
            return ""
            
        except Exception:
            return ""

    async def _get_nearby_label_context(self, element: Locator) -> str:
        """Get context from nearby labels or text for any form element, including descriptions."""
        try:
            # Method 1: Look for associated label with full context
            element_id = await element.get_attribute('id')
            if element_id:
                # Find the label associated with this element
                label = self.page.locator(f'label[for="{element_id}"]').first
                if await label.count() > 0:
                    label_text = await label.inner_text()
                    if label_text and label_text.strip():
                        context = label_text.strip()

                        # Look for description text in the same parent container
                        parent = label.locator('xpath=..')
                        description_selectors = [
                            '*[contains(@class, "description")]',
                            '*[contains(@class, "help")]',
                            '*[contains(@class, "instruction")]',
                            'div p',  # Common pattern for descriptions
                            'div ul',  # Lists of instructions
                        ]

                        for desc_selector in description_selectors:
                            desc_element = parent.locator(desc_selector).first
                            if await desc_element.count() > 0:
                                desc_text = await desc_element.inner_text()
                                if desc_text and desc_text.strip():
                                    # Combine label and description
                                    context += "\n\n" + desc_text.strip()
                                    break

                        return context

            # Method 2: Look for nearby elements that might contain the question
            nearby_selectors = [
                'xpath=preceding-sibling::*[1][self::label or self::div or self::span or self::p]',
                'xpath=ancestor::*[1]/*[1][self::label or contains(@class, "label")]',
                'xpath=ancestor::div[contains(@class, "form-group")]//label',
                'xpath=ancestor::div[contains(@class, "field")]//label',
                'xpath=ancestor::*[contains(@class, "field")]',  # Get entire field container
            ]

            for selector in nearby_selectors:
                nearby_element = element.locator(selector).first
                if await nearby_element.count() > 0:
                    text = await nearby_element.inner_text()
                    if text and text.strip() and len(text.strip()) > 3:  # Avoid empty/minimal text
                        return text.strip()

            return ""

        except Exception:
            return ""

    async def _handle_workday_multiselect(self, element: Locator, value: Any, profile: Optional[Dict[str, Any]] = None) -> None:
        """Handle Workday multiselect fields (like skills)."""
        try:
            logger.info(f"🏢 Handling Workday multiselect field with value: {value}")
            
            # Extract skills from the value - could be a list or comma-separated string
            skills_to_add = []
            if isinstance(value, list):
                skills_to_add = value
            elif isinstance(value, str):
                # Handle comma-separated skills or single skill
                skills_to_add = [skill.strip() for skill in value.split(',') if skill.strip()]
            else:
                skills_to_add = [str(value)]
            
            # If we have profile data, extract relevant skills for this field
            if profile and not skills_to_add:
                skills_to_add = self._extract_relevant_skills_from_profile(profile)
            
            if not skills_to_add:
                logger.warning("No skills to add to Workday multiselect field")
                return
            
            logger.info(f"🎯 Adding {len(skills_to_add)} skills: {skills_to_add}")
            
            # Find the search input field within the multiselect container
            search_input = await self._find_workday_multiselect_input(element)
            if not search_input:
                logger.error("Could not find Workday multiselect search input")
                return
            
            # Add each skill
            for skill in skills_to_add:
                try:
                    await self._add_skill_to_workday_multiselect(search_input, skill)
                    await self.page.wait_for_timeout(200)  # Small delay between additions
                except Exception as e:
                    logger.warning(f"Failed to add skill '{skill}': {e}")
                    continue
            
            logger.info(f"✅ Successfully processed Workday multiselect field")
            
        except Exception as e:
            logger.error(f"❌ Failed to handle Workday multiselect: {e}")

    async def _find_workday_multiselect_input(self, element: Locator) -> Optional[Locator]:
        """Find the search input field within a Workday multiselect container."""
        try:
            # Strategy 1: If element is the input itself
            data_uxi_widget_type = await element.get_attribute('data-uxi-widget-type')
            if data_uxi_widget_type == 'selectinput':
                return element
            
            # Strategy 2: Find input within the multiselect container
            container = element
            if data_uxi_widget_type != 'multiselect':
                # Find the multiselect container
                container = element.locator('xpath=ancestor-or-self::*[@data-uxi-widget-type="multiselect"]').first
            
            # Look for the search input within the container
            search_input = container.locator('input[data-uxi-widget-type="selectinput"]').first
            if await search_input.count() > 0:
                return search_input
            
            # Fallback: look for input with placeholder="Search"
            search_input = container.locator('input[placeholder*="Search" i]').first
            if await search_input.count() > 0:
                return search_input
            
            return None
            
        except Exception as e:
            logger.debug(f"Error finding Workday multiselect input: {e}")
            return None

    async def _robust_click(self, target: Locator) -> bool:
        """Attempt multiple safe strategies to click a target avoiding overlay interceptions."""
        try:
            # Ensure in viewport and stable
            try:
                await target.scroll_into_view_if_needed()
            except Exception:
                pass
            
            # Try simple click
            try:
                await target.click()
                return True
            except Exception:
                pass
            
            # Focus via JS and click with force
            try:
                handle = await target.element_handle()
                if handle:
                    await self.page.evaluate('(el) => { el.scrollIntoView({block: "center", inline: "center"}); el.focus(); }', handle)
                await target.click(force=True)
                return True
            except Exception:
                pass
            
            # Mouse click by bounding box center
            try:
                box = await target.bounding_box()
                if box:
                    await self.page.mouse.move(box['x'] + box['width'] / 2, box['y'] + box['height'] / 2)
                    await self.page.mouse.down()
                    await self.page.mouse.up()
                    return True
            except Exception:
                pass
            
            # Dispatch click event
            try:
                await target.dispatch_event('click')
                return True
            except Exception:
                pass
            
            # As last resort, try dismissing footers/overlays and retry
            await self._dismiss_potential_overlays()
            try:
                await target.click(force=True)
                return True
            except Exception:
                return False
        except Exception:
            return False

    async def _dismiss_potential_overlays(self) -> None:
        """Close or scroll away common Workday overlays/footers that may intercept pointer events."""
        try:
            # Common page footer container
            footer = self.page.locator('[data-automation-id="pageFooter"], .css-6zr5c').first
            if await footer.count() > 0:
                # Scroll the footer out of the way by moving target near top
                await self.page.evaluate('() => window.scrollBy(0, -150)')
                await self.page.wait_for_timeout(100)
            
            # Dismiss any open toast/dialog overlays that might block
            close_buttons = self.page.locator('[data-automation-id="closeButton"], [aria-label="Close"], button:has-text("Close")')
            count = await close_buttons.count()
            for i in range(min(count, 3)):
                try:
                    await close_buttons.nth(i).click()
                except Exception:
                    continue
            
            # Click on a safe blank area to clear hover overlays
            try:
                await self.page.mouse.move(10, 10)
                await self.page.mouse.down()
                await self.page.mouse.up()
            except Exception:
                pass
        except Exception:
            pass

    async def _add_skill_to_workday_multiselect(self, search_input: Locator, skill: str) -> None:
        """Add a single skill to a Workday multiselect field with intelligent Enter handling."""
        try:
            # Focus robustly
            clicked = await self._robust_click(search_input)
            if not clicked:
                # Try focusing via keyboard as fallback
                try:
                    await search_input.focus()
                except Exception:
                    pass
            await self.page.wait_for_timeout(300)
            
            # Clear any existing text and type the skill
            try:
                await search_input.fill('')
            except Exception:
                # Fallback: select all and delete
                await search_input.press('Control+A')
                await search_input.press('Backspace')
            await search_input.type(skill, delay=50)
            await self.page.wait_for_timeout(300)  # Wait for search results
            
            # First, try to click on an exact or close match option
            skill_selected = await self._try_click_skill_option(skill)
            
            if skill_selected:
                logger.info(f"✅ Added skill by clicking option: {skill}")
                try:
                    await search_input.fill('')
                except Exception:
                    pass
                return
            
            # If no clickable option found, use the intelligent Enter approach
            logger.info(f"🔄 No clickable option found for '{skill}', using intelligent Enter approach")
            await self._intelligent_enter_skill_addition(search_input, skill)
            
            # Clear the search input for the next skill
            try:
                await search_input.fill('')
            except Exception:
                pass
            
        except Exception as e:
            logger.warning(f"Failed to add skill '{skill}': {e}")
            raise

    async def _try_click_skill_option(self, skill: str) -> bool:
        """Try to click on a dropdown option that matches the skill."""
        try:
            # Look for dropdown options that match the skill
            option_selectors = [
                f'[role="option"]:has-text("{skill}")',
                f'[data-automation-id="promptOption"]:has-text("{skill}")',
                f'.css-xy5u20:has-text("{skill}")',
                f'*[class*="option"]:has-text("{skill}")'
            ]
            
            for selector in option_selectors:
                try:
                    await self.page.wait_for_selector(selector, timeout=2000)
                    option = self.page.locator(selector).first
                    if await option.count() == 0:
                        continue
                    
                    # Ensure visible and not covered
                    try:
                        await option.scroll_into_view_if_needed()
                    except Exception:
                        pass
                    
                    if await option.is_visible():
                        option_text = await option.inner_text()
                        if self._is_skill_match(skill, option_text):
                            # Robust click
                            if await self._robust_click(option):
                                return True
                except Exception:
                    continue
            
            return False
            
        except Exception:
            return False

    async def _intelligent_enter_skill_addition(self, search_input: Locator, skill: str) -> None:
        """Use intelligent Enter approach for Workday skill addition."""
        try:
            # Press Enter first time to trigger suggestions
            await search_input.press('Enter')
            await self.page.wait_for_timeout(300)  # Wait longer for suggestions to appear
            
            # Try multiple times to get suggestions (they might take time to load)
            max_attempts = 3
            top_suggestion = ""
            
            for attempt in range(max_attempts):
                top_suggestion = await self._get_top_skill_suggestion()
                if top_suggestion:
                    break
                if attempt < max_attempts - 1:
                    logger.debug(f"No suggestions found on attempt {attempt + 1} for '{skill}', waiting...")
                    await self.page.wait_for_timeout(200)
            
            if top_suggestion:
                logger.info(f"🔍 Top suggestion for '{skill}': '{top_suggestion}'")
                
                # Check if top suggestion is a good match (80% similarity or contains the skill word)
                if self._is_good_skill_match(skill, top_suggestion):
                    logger.info(f"✅ Top suggestion '{top_suggestion}' is a good match for '{skill}', pressing Enter again")
                    await search_input.press('Enter')  # Second Enter to confirm
                    await self.page.wait_for_timeout(200)
                    return
                else:
                    logger.warning(f"❌ Top suggestion '{top_suggestion}' is not a good match for '{skill}' (< 80% match)")
                    # Press Escape to cancel and try alternative approach
                    await search_input.press('Escape')
                    await self.page.wait_for_timeout(300)
                    
                    # Try typing the skill more precisely or skip it
                    logger.info(f"⚠️ Skipping skill '{skill}' due to poor suggestion match")
                    return
            else:
                logger.warning(f"❌ No top suggestion found for '{skill}', pressing Enter anyway")
                await search_input.press('Enter')  # Second Enter to add whatever is there
                await self.page.wait_for_timeout(200)
                
        except Exception as e:
            logger.warning(f"Error in intelligent Enter approach for '{skill}': {e}")
            # Fallback: just press Enter again
            try:
                await search_input.press('Enter')
                await self.page.wait_for_timeout(200)
            except:
                pass

    async def _get_top_skill_suggestion(self) -> str:
        """Get the text of the top skill suggestion from Workday dropdown."""
        try:
            # First, look for the dropdown/suggestion container
            dropdown_containers = [
                '[role="listbox"]',  # Standard dropdown
                '[data-automation-id*="searchDropdown"]',
                '[data-automation-id*="dropdown"]',
                '[class*="dropdown"]',
                '[class*="suggestion"]',
                '[class*="menu"]'
            ]
            
            # Try to find suggestions within dropdown containers first
            for container_selector in dropdown_containers:
                try:
                    container = self.page.locator(container_selector).first
                    if await container.is_visible(timeout=1000):
                        # Look for options within this container
                        option_selectors = [
                            '[role="option"]:first-child',
                            '[data-automation-id="promptOption"]:first-child',
                            'div:first-child',
                            'li:first-child',
                            '*[class*="option"]:first-child'
                        ]
                        
                        for option_selector in option_selectors:
                            try:
                                suggestion = container.locator(option_selector).first
                                if await suggestion.is_visible(timeout=500):
                                    text = await suggestion.inner_text()
                                    if text and text.strip():
                                        logger.debug(f"Found suggestion in container '{container_selector}': '{text.strip()}'")
                                        return text.strip()
                            except Exception:
                                continue
                except Exception:
                    continue
            
            # Fallback: Look for suggestions anywhere on page (but be more specific)
            fallback_selectors = [
                '[role="option"]:visible:first',
                '[data-automation-id="promptOption"]:visible:first',
                '.css-xy5u20:visible:first'
            ]
            
            for selector in fallback_selectors:
                try:
                    suggestions = self.page.locator(selector)
                    if await suggestions.count() > 0:
                        first_suggestion = suggestions.first
                        if await first_suggestion.is_visible(timeout=500):
                            text = await first_suggestion.inner_text()
                            if text and text.strip():
                                # Make sure it's not a previously added skill tag
                                if not await self._is_existing_skill_tag(text.strip()):
                                    logger.debug(f"Found fallback suggestion: '{text.strip()}'")
                                    return text.strip()
                except Exception:
                    continue
            
            logger.debug("No valid skill suggestions found")
            return ""
            
        except Exception as e:
            logger.debug(f"Error getting top skill suggestion: {e}")
            return ""

    async def _is_existing_skill_tag(self, text: str) -> bool:
        """Check if the text is from an existing skill tag (not a dropdown suggestion)."""
        try:
            # Look for skill tags/chips that might contain this text
            tag_selectors = [
                f'[class*="tag"]:has-text("{text}")',
                f'[class*="chip"]:has-text("{text}")',
                f'[class*="selected"]:has-text("{text}")',
                f'[data-automation-id*="selectedItem"]:has-text("{text}")'
            ]
            
            for selector in tag_selectors:
                if await self.page.locator(selector).count() > 0:
                    return True
            return False
        except Exception:
            return False

    def _is_skill_match(self, intended_skill: str, option_text: str) -> bool:
        """Check if an option text is a good match for the intended skill."""
        intended_lower = intended_skill.lower().strip()
        option_lower = option_text.lower().strip()
        
        # Exact match
        if intended_lower == option_lower:
            return True
        
        # Check if option contains the intended skill
        if intended_lower in option_lower:
            return True
        
        # Check if intended skill contains the option (for abbreviations)
        if option_lower in intended_lower:
            return True
        
        return False

    def _is_good_skill_match(self, intended_skill: str, suggestion: str) -> bool:
        """Check if a suggestion is a good match for the intended skill (80% similarity or contains word)."""
        intended_lower = intended_skill.lower().strip()
        suggestion_lower = suggestion.lower().strip()
        
        # Exact match
        if intended_lower == suggestion_lower:
            return True
        
        # Check if suggestion contains the intended skill word
        if intended_lower in suggestion_lower:
            return True
        
        # Check if intended skill contains the suggestion (for abbreviations)
        if suggestion_lower in intended_lower:
            return True
        
        # Calculate similarity percentage using simple character overlap
        similarity = self._calculate_string_similarity(intended_lower, suggestion_lower)
        
        logger.debug(f"Skill similarity: '{intended_skill}' vs '{suggestion}' = {similarity:.1%}")
        
        # Return True if similarity is 80% or higher
        return similarity >= 0.8

    def _calculate_string_similarity(self, str1: str, str2: str) -> float:
        """Calculate string similarity using a simple overlap method."""
        try:
            if not str1 or not str2:
                return 0.0
            
            # Remove special characters and split into words
            import re
            words1 = set(re.findall(r'\w+', str1.lower()))
            words2 = set(re.findall(r'\w+', str2.lower()))
            
            if not words1 or not words2:
                return 0.0
            
            # Calculate Jaccard similarity (intersection over union)
            intersection = len(words1.intersection(words2))
            union = len(words1.union(words2))
            
            return intersection / union if union > 0 else 0.0
            
        except Exception:
            # Fallback to character-based similarity
            if str1 == str2:
                return 1.0
            
            # Simple character overlap
            common_chars = sum(1 for c in str1 if c in str2)
            return common_chars / max(len(str1), len(str2)) if max(len(str1), len(str2)) > 0 else 0.0

    def _extract_relevant_skills_from_profile(self, profile: Dict[str, Any]) -> List[str]:
        """Extract relevant skills from profile for multiselect fields."""
        skills = []
        skills_data = profile.get('skills', {})
        
        if isinstance(skills_data, dict):
            # Extract from all skill categories
            for category, skill_list in skills_data.items():
                if isinstance(skill_list, list):
                    skills.extend(skill_list)
        elif isinstance(skills_data, list):
            skills = skills_data
        
        # Also include programming languages, frameworks, and tools as they're commonly asked
        for key in ['programming_languages', 'frameworks', 'tools', 'technical_skills']:
            if key in profile and isinstance(profile[key], list):
                skills.extend(profile[key])
        
        # Remove duplicates and empty values
        skills = list(set([skill.strip() for skill in skills if skill and skill.strip()]))
        
        # Limit to reasonable number (Workday usually shows top matches anyway)
        return skills[:10]

    async def _handle_workday_dropdown(self, element: Locator, value: str) -> None:
        """Handle Workday dropdown selection."""
        try:
            # Ensure element is in view and stable before clicking (with timeout to prevent hanging)
            try:
                await element.scroll_into_view_if_needed(timeout=3000)
                await self.page.wait_for_timeout(300)
            except Exception as scroll_error:
                logger.debug(f"Scroll into view failed or timed out: {scroll_error}, continuing anyway")
            
            # Click to open the Workday dropdown
            await element.click(timeout=5000)
            await self.page.wait_for_timeout(300)  # Wait for dropdown to open
            
            # Look for dropdown options in Workday-style containers
            option_selectors = [
                f'[role="option"]:has-text("{value}")',
                f'[role="listbox"] [role="option"]:has-text("{value}")',
                f'div[role="option"]:has-text("{value}")',
                f'li[role="option"]:has-text("{value}")',
                f'*[class*="option"]:has-text("{value}")'
            ]
            
            option_found = False
            for selector in option_selectors:
                try:
                    # Wait for options to appear
                    await self.page.wait_for_selector(selector, timeout=3000)
                    options = await self.page.locator(selector).all()
                    
                    if options:
                        logger.debug(f"🔍 Found {len(options)} Workday options with selector '{selector}'")
                        for option in options:
                            try:
                                option_text = await option.text_content()
                                if option_text:
                                    option_text = option_text.strip()
                                    # Try exact match first
                                    if value.lower() == option_text.lower():
                                        await option.click(timeout=3000)
                                        logger.info(f"✅ Selected Workday option '{option_text}' (exact match)")
                                        option_found = True
                                        break
                                    # Try partial match if exact fails
                                    elif value.lower() in option_text.lower() or option_text.lower() in value.lower():
                                        await option.click(timeout=3000)
                                        logger.info(f"✅ Selected Workday option '{option_text}' (partial match)")
                                        option_found = True
                                        break
                            except Exception as e:
                                logger.debug(f"Error clicking Workday option: {e}")
                                continue
                        
                        if option_found:
                            break
                            
                except Exception as e:
                    logger.debug(f"Workday option selector '{selector}' failed: {e}")
                    continue
            
            if not option_found:
                logger.warning(f"Standard Workday logic failed for '{value}', extracting options for AI selection")
                # Extract all available options for AI to choose from
                dropdown_options = await self._extract_visible_dropdown_options()
                
                if dropdown_options:
                    logger.info(f"🤖 Found {len(dropdown_options)} Workday options, using AI to select best match")
                    # Close dropdown first
                    try:
                        await self.page.keyboard.press("Escape")
                        await self.page.wait_for_timeout(200)
                    except Exception:
                        pass
                    
                    # Use AI to select the best option
                    from components.brains.gemini_field_mapper import GeminiFieldMapper
                    field_mapper = GeminiFieldMapper()
                    
                    ai_result = await field_mapper.select_best_dropdown_option_from_list(
                        target_value=value,
                        available_options=dropdown_options,
                        profile=None  # Profile will be passed from higher level if available
                    )
                    
                    if ai_result and ai_result.get('best_option_text'):
                        best_option = ai_result['best_option_text']
                        logger.info(f"🧠 AI selected: '{best_option}' for Workday dropdown")
                        
                        # Reopen dropdown and try to select AI's choice
                        await element.click(timeout=5000)
                        await self.page.wait_for_timeout(300)
                        
                        # Try to find and click the AI-suggested option
                        for selector in option_selectors:
                            try:
                                options = await self.page.locator(selector).all()
                                for option in options:
                                    option_text = await option.text_content()
                                    if option_text and option_text.strip().lower() == best_option.lower():
                                        await option.click(timeout=3000)
                                        logger.info(f"✅ Selected AI-suggested Workday option: '{best_option}'")
                                        option_found = True
                                        break
                                if option_found:
                                    break
                            except Exception:
                                continue
                
                # If AI also failed, close dropdown
                if not option_found:
                    logger.warning(f"❌ Could not find matching Workday option for '{value}' even with AI")
                    try:
                        await element.press("Escape")
                    except Exception:
                        pass
                    
        except Exception as e:
            logger.error(f"❌ Error handling Workday dropdown: {e}")

    async def _handle_greenhouse_dropdown(self, element: Locator, value: str) -> None:
        """Handle Greenhouse-style dropdown selection."""
        try:
            # Ensure element is in view and stable before clicking (with timeout to prevent hanging)
            try:
                await element.scroll_into_view_if_needed(timeout=3000)
                await self.page.wait_for_timeout(200)
            except Exception as scroll_error:
                logger.debug(f"Scroll into view failed or timed out: {scroll_error}, continuing anyway")

            # For Greenhouse dropdowns, find the clickable control container (not the hidden input)
            # The hidden input has aria-hidden="true", we need the visible control
            clickable_element = element
            try:
                parent = element.locator('..')
                # Look for the visible control container
                control_selectors = [
                    '[class*="control"]',
                    '.select__control',
                    '[class*="select"]',
                    'div[role="combobox"]'
                ]
                for selector in control_selectors:
                    control = parent.locator(selector).first
                    if await control.count() > 0:
                        # Verify it's visible and clickable
                        try:
                            if await control.is_visible(timeout=1000):
                                clickable_element = control
                                logger.debug(f"🏢 Found clickable Greenhouse control with {selector}")
                                break
                        except:
                            continue
            except Exception as e:
                logger.debug(f"Could not find Greenhouse control container, using element directly: {e}")

            # Click to open the dropdown
            try:
                await clickable_element.click(timeout=5000)
            except Exception as click_error:
                # If normal click fails, try with force
                logger.debug(f"Normal click failed, trying with force: {click_error}")
                await clickable_element.click(timeout=5000, force=True)

            await self.page.wait_for_timeout(800)  # Wait for dropdown to open
            
            # Look for dropdown options in various possible locations
            option_selectors = [
                '[role="option"]',
                '.select__option',
                '.remix-css-option',
                '[class*="option"]',
                'div[role="option"]',
                'li[role="option"]'
            ]
            
            option_found = False
            for selector in option_selectors:
                try:
                    # Wait for options to appear before trying to interact
                    await self.page.wait_for_selector(selector, timeout=3000)
                    options = await self.page.locator(selector).all()
                    if options:
                        logger.debug(f"🔍 Found {len(options)} options with selector '{selector}'")

                        # Try exact text matching FIRST (most reliable)
                        value_lower = value.lower()
                        for option in options:
                            try:
                                option_text = await option.text_content()
                                if option_text and option_text.strip().lower() == value_lower:
                                    await option.click(timeout=3000, force=True)
                                    logger.info(f"✅ Exact match: '{option_text.strip()}'")
                                    option_found = True
                                    break
                            except Exception:
                                continue
                        
                        # If exact match didn't work, try partial matching with strict validation
                        if not option_found:
                            for option in options:
                                try:
                                    option_text = await option.text_content()
                                    if option_text:
                                        option_text = option_text.strip()
                                        option_text_lower = option_text.lower()
                                        
                                        # Partial match WITH length similarity check to avoid "United States +1" matching "United States of America"
                                        if value_lower in option_text_lower or option_text_lower in value_lower:
                                            # Require similar lengths (within 2x) to avoid phone codes matching countries
                                            len_ratio = max(len(option_text), len(value)) / min(len(option_text), len(value))
                                            if len_ratio < 2.5:  # Not too different in length
                                                await option.click(timeout=3000, force=True)
                                                logger.info(f"✅ Partial match: '{option_text}' for '{value}'")
                                                option_found = True
                                                break
                                        
                                        # For years, match digits
                                        elif value.isdigit() and value in option_text:
                                            await option.click(timeout=3000, force=True)
                                            logger.info(f"✅ Year match: '{option_text}' for '{value}'")
                                            option_found = True
                                            break
                                except Exception as e:
                                    logger.debug(f"Error processing option: {e}")
                                    continue
                    
                    if option_found:
                        break
                except Exception:
                    # If this selector doesn't work, try the next one
                    continue
            
            if not option_found:
                logger.warning(f"Standard logic failed for '{value}', extracting options for intelligent selection")
                
                # Dropdown should still be open from earlier, extract options now
                dropdown_options = await self._extract_visible_dropdown_options()
                
                if dropdown_options and len(dropdown_options) > 0:
                    option_texts = [opt['text'] for opt in dropdown_options]
                    logger.info(f"📋 Found {len(option_texts)} options, trying intelligent selection")
                    
                    # TRY INTELLIGENT PROFILE-BASED SELECTION FIRST
                    from components.executors.intelligent_dropdown_selector import IntelligentDropdownSelector
                    selector_instance = IntelligentDropdownSelector()
                    
                    # Try to get field label for context
                    field_label = await self._get_field_label(element) if hasattr(self, '_get_field_label') else ""
                    
                    intelligent_choice = selector_instance.select_from_options(
                        question=field_label or "",
                        options=option_texts,
                        profile=None,  # Will be passed from higher level
                        target_value=value
                    )
                    
                    if intelligent_choice:
                        logger.info(f"🎯 Intelligent selector chose: '{intelligent_choice}'")
                        best_option = intelligent_choice
                    else:
                        # FALLBACK TO AI if intelligent selector couldn't decide
                        logger.info(f"🤖 Intelligent selector failed, using AI")
                        from components.brains.gemini_field_mapper import GeminiFieldMapper
                        field_mapper = GeminiFieldMapper()
                        
                        ai_result = await field_mapper.select_best_dropdown_option_from_list(
                            target_value=value,
                            available_options=dropdown_options,
                            profile=None  # Profile will be passed from higher level if available
                        )
                        
                        if ai_result and ai_result.get('best_option_text'):
                            best_option = ai_result['best_option_text']
                            logger.info(f"🧠 AI selected: '{best_option}'")
                        else:
                            best_option = None
                    
                    # Try to click the selected option
                    if best_option:
                        for selector in option_selectors:
                            try:
                                options = await self.page.locator(selector).all()
                                for option in options:
                                    option_text = await option.text_content()
                                    if option_text and option_text.strip().lower() == best_option.lower():
                                        await option.click(timeout=3000, force=True)
                                        logger.info(f"✅ Selected: '{best_option}'")
                                        option_found = True
                                        break
                                if option_found:
                                    break
                            except Exception:
                                continue
                else:
                    logger.warning(f"Could not extract dropdown options (found {len(dropdown_options) if dropdown_options else 0}), dropdown might not be open")
                
                # If AI also failed, try typing as last resort
                if not option_found:
                    logger.warning(f"AI selection also failed, trying typing as final fallback")
                    try:
                        await element.click(timeout=3000, force=True)
                        await element.clear()
                        await element.fill(value)
                        await element.press("Enter")
                        logger.info(f"Typed '{value}' into Greenhouse dropdown as final fallback")
                    except Exception as fallback_error:
                        logger.error(f"Even fallback typing failed: {fallback_error}")
                
        except Exception as e:
            logger.error(f"❌ Greenhouse dropdown selection failed: {e}")
            raise

    async def _extract_visible_dropdown_options(self) -> List[Dict[str, str]]:
        """Extract all visible dropdown options from the page."""
        options = []
        try:
            # Look for visible dropdown options using common selectors
            option_selectors = [
                '[role="option"]',
                '.select__option',
                '.dropdown-option',
                '[class*="option"]',
                'li[role="option"]',
                'div[role="option"]'
            ]

            for selector in option_selectors:
                try:
                    option_elements = await self.page.locator(selector).all()
                    for option in option_elements:
                        # Only process visible options (use short timeout to avoid hanging)
                        try:
                            if await option.is_visible(timeout=200):
                                text = await option.text_content()
                                if text and text.strip():
                                    # Try to get the value attribute if available
                                    value = await option.get_attribute('data-value') or await option.get_attribute('value')
                                    options.append({
                                        'text': text.strip(),
                                        'value': value or text.strip()
                                    })
                        except Exception:
                            # Skip options that timeout on visibility check
                            continue
                except Exception:
                    continue

            # Remove duplicates based on text
            seen_texts = set()
            unique_options = []
            for opt in options:
                if opt['text'] not in seen_texts:
                    seen_texts.add(opt['text'])
                    unique_options.append(opt)

            logger.debug(f"Extracted {len(unique_options)} unique dropdown options from page")
            return unique_options

        except Exception as e:
            logger.debug(f"Error extracting dropdown options: {e}")
            return []

    async def _ai_assisted_dropdown_selection(self, element: Locator, value: str, dropdown_options: List[Dict[str, str]] = None, profile: Optional[Dict[str, Any]] = None) -> None:
        """Use AI to help select dropdown options when standard methods fail."""
        try:
            logger.info(f"🤖 Using AI to help select dropdown option '{value}'")

            # Use Gemini to select the best option from the available options
            from components.brains.gemini_field_mapper import GeminiFieldMapper
            field_mapper = GeminiFieldMapper()

            if dropdown_options is None:
                dropdown_options = []

            # Get AI to select the best option from the list
            ai_result = await field_mapper.select_best_dropdown_option_from_list(
                target_value=value,
                available_options=dropdown_options,
                profile=profile
            )
            
            if ai_result and ai_result.get('best_option_text'):
                best_option = ai_result['best_option_text']
                confidence = ai_result.get('confidence', 0)
                reason = ai_result.get('reason', 'No reason provided')
                
                logger.info(f"🧠 AI selected option '{best_option}' (confidence: {confidence:.2f}) - {reason}")
                
                # Try to find and click the AI-suggested option
                if await self._try_click_option_by_text(best_option):
                    logger.info(f"✅ Successfully selected AI-suggested option '{best_option}'")
                    return
                else:
                    logger.warning(f"⚠️ Could not click AI-suggested option '{best_option}'")
            else:
                logger.warning("AI could not select a suitable option from the available choices")
            
        except Exception as e:
            logger.error(f"❌ AI-assisted dropdown selection failed: {e}")
        
        # Final fallback: just type the value
        try:
            await element.fill(value)
            await element.press("Enter")
            logger.info(f"Final fallback: typed '{value}' into dropdown")
        except Exception as final_e:
            logger.error(f"❌ All dropdown selection methods failed: {final_e}")
            raise

    async def _try_click_option_by_text(self, option_text: str) -> bool:
        """Try to find and click an option by its text content."""
        try:
            # Look for options with various selectors
            option_selectors = [
                f'[role="option"]:has-text("{option_text}")',
                f'.select__option:has-text("{option_text}")',
                f'.remix-css-option:has-text("{option_text}")',
                f'[class*="option"]:has-text("{option_text}")',
                f'div[role="option"]:has-text("{option_text}")',
                f'li[role="option"]:has-text("{option_text}")'
            ]

            for selector in option_selectors:
                try:
                    option = self.page.locator(selector).first
                    if await option.is_visible(timeout=1000):
                        await option.click()
                        return True
                except Exception:
                    continue

            return False

        except Exception as e:
            logger.debug(f"Error clicking option by text '{option_text}': {e}")
            return False

    # ========== Verification Helper Methods ==========

    async def _verify_file_upload(self, element: Locator) -> bool:
        """Verify that a file was successfully uploaded."""
        try:
            # Check if the file input has files
            files_count = await element.evaluate('el => el.files ? el.files.length : 0')
            if files_count > 0:
                return True

            # Alternative: Check for upload confirmation indicators
            confirmation_patterns = [
                "text=/uploaded/i",
                "text=/attached/i",
                "[class*='uploaded']",
                "[class*='file-name']"
            ]

            for pattern in confirmation_patterns:
                try:
                    if await self.page.locator(pattern).count() > 0:
                        return True
                except Exception:
                    continue

            return False
        except Exception as e:
            logger.debug(f"File upload verification error: {e}")
            return False

    async def _verify_dropdown_selection(self, element: Locator, expected_value: str) -> tuple[bool, str]:
        """Verify dropdown selection. Returns (success, actual_value)."""
        try:
            await self.page.wait_for_timeout(200)  # Wait for selection to settle

            # Special handling for Greenhouse dropdowns - check the visible display value
            if await self._is_greenhouse_style_dropdown(element):
                try:
                    # Greenhouse dropdowns show selected value in a sibling div with class containing "value" or "placeholder"
                    parent = element.locator('..')
                    # Try multiple selectors for the display element
                    display_selectors = [
                        '[class*="singleValue"]',
                        '[class*="value"]',
                        '[class*="placeholder"]',
                        '.select__single-value',
                        'div[data-value]'
                    ]

                    selected_value = None
                    for selector in display_selectors:
                        try:
                            display_element = parent.locator(selector).first
                            if await display_element.count() > 0:
                                selected_value = await display_element.text_content()
                                if selected_value:
                                    selected_value = selected_value.strip()
                                    if selected_value:  # Not empty
                                        logger.debug(f"🏢 Greenhouse dropdown verification: found '{selected_value}' via {selector}")
                                        break
                        except Exception:
                            continue

                    if selected_value:
                        # Exact match
                        if selected_value == expected_value:
                            return (True, selected_value)
                        # Case-insensitive match
                        if selected_value.lower() == expected_value.lower():
                            return (True, selected_value)
                        # Partial match (Greenhouse often shows country codes like "United States +1" when we want "United States of America")
                        if expected_value.lower() in selected_value.lower() or selected_value.lower() in expected_value.lower():
                            logger.debug(f"🏢 Greenhouse dropdown partial match: expected '{expected_value}', got '{selected_value}'")
                            return (True, selected_value)
                        # For country codes, check if the country name is in the beginning
                        if ' ' in selected_value:
                            country_part = selected_value.split('+')[0].strip()  # "United States +1" -> "United States"
                            if expected_value.lower().startswith(country_part.lower()) or country_part.lower() in expected_value.lower():
                                logger.debug(f"🏢 Greenhouse country code match: expected '{expected_value}', got '{selected_value}'")
                                return (True, selected_value)

                        return (False, selected_value)
                except Exception as gh_error:
                    logger.debug(f"Greenhouse-specific verification failed: {gh_error}")
                    # Fall through to standard verification

            # Standard dropdown verification
            try:
                selected_value = await element.input_value()
            except Exception:
                # For non-input elements, try getting text content
                try:
                    selected_value = await element.text_content()
                    if selected_value:
                        selected_value = selected_value.strip()
                except Exception:
                    return (False, "")

            if not selected_value:
                return (False, "")

            # Exact match
            if selected_value == expected_value:
                return (True, selected_value)

            # Case-insensitive match
            if selected_value.lower() == expected_value.lower():
                return (True, selected_value)

            # Partial match (for complex dropdown values)
            if expected_value.lower() in selected_value.lower() or selected_value.lower() in expected_value.lower():
                logger.debug(f"Dropdown partial match: expected '{expected_value}', got '{selected_value}'")
                return (True, selected_value)

            return (False, selected_value)

        except Exception as e:
            logger.debug(f"Dropdown verification error: {e}")
            return (False, "")

    async def _verify_button_group_selection(self, element: Locator, expected_value: Any) -> bool:
        """Verify button group selection (e.g., Ashby buttons)."""
        try:
            await self.page.wait_for_timeout(200)

            # Get parent container
            parent = element.locator('xpath=..')
            buttons = await parent.locator('button').all()

            target_value = str(expected_value).strip().lower()

            # Check which button is selected (usually has aria-pressed="true" or special class)
            for button in buttons:
                try:
                    button_text = await button.inner_text()
                    if button_text.strip().lower() == target_value:
                        # Check if this button is selected
                        aria_pressed = await button.get_attribute('aria-pressed')
                        button_class = await button.get_attribute('class') or ''

                        if aria_pressed == 'true' or 'selected' in button_class or 'active' in button_class:
                            return True
                except Exception:
                    continue

            return False

        except Exception as e:
            logger.debug(f"Button group verification error: {e}")
            return False

    async def _verify_checkbox_state(self, element: Locator, expected_value: Any) -> bool:
        """Verify checkbox/radio button state."""
        try:
            await self.page.wait_for_timeout(200)

            is_checked = await element.is_checked()
            expected_checked = str(expected_value).lower() in ['true', 'yes', '1', 'on', 'checked']

            return is_checked == expected_checked

        except Exception as e:
            logger.debug(f"Checkbox verification error: {e}")
            return False