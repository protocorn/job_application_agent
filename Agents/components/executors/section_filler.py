import re
from typing import Dict, List, Optional, Any
from playwright.async_api import Page, Locator
from loguru import logger


class SectionFiller:
    """Fills education and work experience sections with profile data."""
    
    def __init__(self, page: Page):
        self.page = page
        
        # Field mapping patterns for education
        self.education_field_mapping = {
            'institution': [r'school', r'university', r'college', r'institution'],
            'degree': [r'degree', r'qualification', r'diploma', r'field of study', r'major', r'program', r'study'],
            'graduation_year': [r'graduation date', r'expected graduation', r'graduation year', r'completion date', r'end date', r'year'],
            'gpa': [r'gpa', r'grade', r'score', r'cgpa'],
            'location': [r'location', r'city', r'state', r'country']
        }
        
        # Field mapping patterns for work experience
        self.work_field_mapping = {
            'company': [r'company name', r'employer', r'organization', r'company'],
            'title': [r'job title', r'position', r'role', r'designation'],
            'start_date': [r'start date', r'from', r'beginning', r'joined'],
            'end_date': [r'end date', r'to', r'until', r'left', r'current'],
            'description': [r'description', r'responsibilities', r'duties', r'achievements', r'summary']
        }

    async def fill_education_section(self, education_data: List[Dict[str, Any]], section_info: Dict[str, Any]) -> bool:
        """Fill education section with profile data."""
        logger.info("🎓 Filling education section...")
        logger.info(f"📊 Section info: {section_info}")
        logger.info(f"📊 Education data: {education_data}")
        
        if not education_data:
            logger.warning("No education data provided")
            return False
            
        # If there are existing fields, try to fill them
        if section_info.get('fields'):
            logger.info(f"🔍 Found {len(section_info['fields'])} education fields to fill")
            filled_count = 0
            for field_info in section_info['fields']:
                field = field_info['element']
                label = field_info['label']
                logger.info(f"🎯 Trying to fill field: '{label}'")
                
                # Find matching education data
                matching_data = self._find_matching_education_data(label, education_data)
                logger.info(f"📝 Matching data for '{label}': {matching_data}")
                
                if matching_data:
                    success = await self._fill_field(field, matching_data, label)
                    if success:
                        filled_count += 1
                        logger.info(f"✅ Filled education field: {label}")
                    else:
                        logger.warning(f"❌ Failed to fill education field: {label}")
                else:
                    logger.warning(f"⚠️ No matching data found for field: {label}")
            
            if filled_count > 0:
                logger.info(f"✅ Filled {filled_count} education fields")
                return True
            else:
                logger.warning("❌ No education fields were filled")
        
        # If there's an add button, click it to add more education entries
        if section_info.get('add_button'):
            logger.info("➕ Clicking add education button...")
            add_button = section_info['add_button']['element']
            await add_button.click()
            await self.page.wait_for_timeout(1000)  # Wait for form to appear
            
            # Try to fill the new form that appeared
            return await self._fill_new_education_form(education_data[0])  # Fill with first education entry
        
        return False

    async def fill_work_experience_section(self, work_data: List[Dict[str, Any]], section_info: Dict[str, Any]) -> bool:
        """Fill work experience section with profile data."""
        logger.info("💼 Filling work experience section...")
        
        if not work_data:
            logger.warning("No work experience data provided")
            return False
            
        # If there are existing fields, try to fill them
        if section_info.get('fields'):
            filled_count = 0
            for field_info in section_info['fields']:
                field = field_info['element']
                label = field_info['label']
                
                # Find matching work data
                matching_data = self._find_matching_work_data(label, work_data)
                if matching_data:
                    success = await self._fill_field(field, matching_data, label)
                    if success:
                        filled_count += 1
                        logger.info(f"✅ Filled work field: {label}")
            
            if filled_count > 0:
                logger.info(f"✅ Filled {filled_count} work experience fields")
                return True
        
        # If there's an add button, click it to add more work entries
        if section_info.get('add_button'):
            logger.info("➕ Clicking add work experience button...")
            add_button = section_info['add_button']['element']
            await add_button.click()
            await self.page.wait_for_timeout(1000)  # Wait for form to appear
            
            # Try to fill the new form that appeared
            return await self._fill_new_work_form(work_data[0])  # Fill with first work entry
        
        return False

    def _find_matching_education_data(self, field_label: str, education_data: List[Dict[str, Any]]) -> Optional[str]:
        """Find the best matching education data for a field label (prioritizing most recent)."""
        field_label_lower = field_label.lower()
        logger.debug(f"🔍 Searching for match for field: '{field_label_lower}'")
        
        # Sort education data by graduation year (most recent first)
        sorted_education = self._sort_education_by_recency(education_data)
        
        # Sort patterns by specificity (longer patterns first)
        all_patterns = []
        for pattern_key, patterns in self.education_field_mapping.items():
            for pattern in patterns:
                all_patterns.append((pattern_key, pattern, len(pattern)))
        
        # Sort by pattern length (longest first) for more specific matching
        all_patterns.sort(key=lambda x: x[2], reverse=True)
        
        for pattern_key, pattern, _ in all_patterns:
            if re.search(pattern, field_label_lower, re.IGNORECASE):
                logger.debug(f"📍 Pattern '{pattern}' matches field '{field_label_lower}', looking for data key '{pattern_key}'")
                # Find the first (most recent) education entry that has this field
                for edu in sorted_education:
                    if pattern_key in edu and edu[pattern_key]:
                        logger.debug(f"✅ Found data: {pattern_key} = {edu[pattern_key]} (from {edu.get('graduation_year', 'unknown')})")
                        return str(edu[pattern_key])
                    else:
                        logger.debug(f"❌ No data found for key '{pattern_key}' in education entry: {edu.keys()}")
                return None
        
        logger.debug(f"❌ No pattern matched for field: '{field_label_lower}'")
        return None

    def _find_matching_work_data(self, field_label: str, work_data: List[Dict[str, Any]]) -> Optional[str]:
        """Find the best matching work data for a field label (prioritizing most recent)."""
        field_label_lower = field_label.lower()
        
        # Sort work data by recency (most recent first)
        sorted_work = self._sort_work_by_recency(work_data)
        
        for pattern_key, patterns in self.work_field_mapping.items():
            for pattern in patterns:
                if re.search(pattern, field_label_lower, re.IGNORECASE):
                    # Find the first (most recent) work entry that has this field
                    for work in sorted_work:
                        if pattern_key in work and work[pattern_key]:
                            logger.debug(f"✅ Found work data: {pattern_key} = {work[pattern_key]} (from {work.get('start_date', 'unknown')})")
                            return str(work[pattern_key])
        
        return None

    async def _fill_field(self, field: Locator, value: str, label: str) -> bool:
        """Fill a field with the given value."""
        try:
            field_type = await field.get_attribute('type') or 'text'
            tag_name = await field.evaluate('el => el.tagName.toLowerCase()')
            
            # PRIORITY 1: Check if this is a dropdown field (including Greenhouse-style)
            is_dropdown = await self._detect_if_dropdown(field, tag_name, field_type, label)
            
            if is_dropdown:
                logger.info(f"🔄 Detected dropdown field, using dropdown logic for: {label}")
                return await self._handle_dropdown_selection(field, value, label)
            
            # PRIORITY 2: Handle other field types
            if field_type in ['text', 'email', 'tel', 'url']:
                await field.fill(value)
            elif field_type == 'textarea':
                await field.fill(value)
            elif field_type == 'select':
                # Try to select by visible text first
                try:
                    await field.select_option(label=value)
                except:
                    # If that fails, try by value
                    await field.select_option(value=value)
            else:
                await field.fill(value)
            
            logger.debug(f"Filled {field_type} field '{label}' with: {value}")
            return True
            
        except Exception as e:
            logger.warning(f"Failed to fill field '{label}': {e}")
            return False

    async def _fill_new_education_form(self, education_data: Dict[str, Any]) -> bool:
        """Fill a new education form that appeared after clicking add button."""
        logger.info("📝 Filling new education form...")
        
        # Look for all input fields in the current context
        inputs = await self.page.locator('input, textarea, select').all()
        filled_count = 0
        
        for input_field in inputs:
            if await input_field.is_visible():
                label = await self._get_field_label(input_field)
                matching_data = self._find_matching_education_data(label, [education_data])
                
                if matching_data:
                    success = await self._fill_field(input_field, matching_data, label)
                    if success:
                        filled_count += 1
                        logger.info(f"✅ Filled new education field: {label}")
        
        return filled_count > 0

    async def _fill_new_work_form(self, work_data: Dict[str, Any]) -> bool:
        """Fill a new work form that appeared after clicking add button."""
        logger.info("📝 Filling new work form...")
        
        # Look for all input fields in the current context
        inputs = await self.page.locator('input, textarea, select').all()
        filled_count = 0
        
        for input_field in inputs:
            if await input_field.is_visible():
                label = await self._get_field_label(input_field)
                matching_data = self._find_matching_work_data(label, [work_data])
                
                if matching_data:
                    success = await self._fill_field(input_field, matching_data, label)
                    if success:
                        filled_count += 1
                        logger.info(f"✅ Filled new work field: {label}")
        
        return filled_count > 0

    async def _get_field_label(self, field: Locator) -> str:
        """Get the label text for a field element."""
        try:
            # Try to find associated label by 'for' attribute
            field_id = await field.get_attribute('id')
            if field_id:
                label = self.page.locator(f'label[for="{field_id}"]').first
                if await label.is_visible():
                    return await label.inner_text()
            
            # Try aria-label
            aria_label = await field.get_attribute('aria-label')
            if aria_label:
                return aria_label
                
            # Try placeholder
            placeholder = await field.get_attribute('placeholder')
            if placeholder:
                return placeholder
                
            return "Unknown field"
        except Exception:
            return "Unknown field"

    def _sort_education_by_recency(self, education_data: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Sort education data by graduation year (most recent first)."""
        try:
            # Sort by graduation_year in descending order (most recent first)
            sorted_data = sorted(education_data, key=lambda x: int(x.get('graduation_year', 0)), reverse=True)
            logger.debug(f"📅 Sorted education by recency: {[edu.get('graduation_year', 'unknown') for edu in sorted_data]}")
            return sorted_data
        except Exception as e:
            logger.debug(f"⚠️ Could not sort education data: {e}, using original order")
            return education_data

    def _sort_work_by_recency(self, work_data: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Sort work data by start date (most recent first)."""
        try:
            # Sort by start_date in descending order (most recent first)
            # Handle different date formats
            def get_sort_key(work):
                start_date = work.get('start_date', '')
                if not start_date:
                    return 0
                
                # Try to extract year from various date formats
                if isinstance(start_date, str):
                    # Extract 4-digit year from string
                    import re
                    year_match = re.search(r'(\d{4})', start_date)
                    if year_match:
                        return int(year_match.group(1))
                    
                    # Handle just year as string
                    if start_date.isdigit() and len(start_date) == 4:
                        return int(start_date)
                
                return 0
            
            sorted_data = sorted(work_data, key=get_sort_key, reverse=True)
            logger.debug(f"📅 Sorted work by recency: {[work.get('start_date', 'unknown') for work in sorted_data]}")
            return sorted_data
        except Exception as e:
            logger.debug(f"⚠️ Could not sort work data: {e}, using original order")
            return work_data
    
    async def _detect_if_dropdown(self, element: Locator, tag_name: str, field_type: str, label: str) -> bool:
        """Detect if an element is a dropdown (including Greenhouse-style)."""
        try:
            # Standard select element
            if tag_name == 'select':
                return True
            
            # Check for Greenhouse-style dropdown patterns
            if await self._is_greenhouse_style_dropdown(element):
                return True
            
            # Check for other custom dropdown implementations
            if await self._is_custom_dropdown_implementation(element):
                return True
            
            return False
            
        except Exception as e:
            logger.debug(f"Error detecting dropdown: {e}")
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
            
            return False
            
        except Exception as e:
            logger.debug(f"Error checking Greenhouse dropdown: {e}")
            return False
    
    async def _is_custom_dropdown_implementation(self, element: Locator) -> bool:
        """Check if element is a custom dropdown implementation."""
        try:
            # Check for common dropdown patterns
            parent = element.locator('..')
            
            # Check for dropdown indicators
            dropdown_indicators = [
                '[class*="dropdown"]',
                '[class*="select"]',
                '[role="combobox"]',
                '[aria-haspopup="true"]',
                '[aria-expanded]'
            ]
            
            for indicator in dropdown_indicators:
                if await parent.locator(indicator).count() > 0:
                    return True
            
            return False
            
        except Exception as e:
            logger.debug(f"Error checking custom dropdown: {e}")
            return False
    
    async def _handle_dropdown_selection(self, element: Locator, value: str, label: str) -> bool:
        """Handle dropdown selection for both standard and custom dropdowns."""
        try:
            # Try standard select first
            if await element.evaluate('el => el.tagName.toLowerCase()') == 'select':
                await element.select_option(label=value)
                logger.info(f"✅ Selected '{value}' from standard dropdown: {label}")
                return True
            
            # Handle custom dropdowns (like Greenhouse)
            return await self._select_custom_dropdown_option(element, value, label)
            
        except Exception as e:
            logger.warning(f"Failed to select dropdown option '{value}' for '{label}': {e}")
            return False
    
    async def _select_custom_dropdown_option(self, element: Locator, value: str, label: str) -> bool:
        """Select option from custom dropdown (like Greenhouse)."""
        try:
            # Click to open dropdown
            await element.click()
            await self.page.wait_for_timeout(1000)
            
            # Look for options
            option_selectors = [
                '[role="option"]',
                '.option',
                'li[role="option"]',
                'div[role="option"]'
            ]
            
            for selector in option_selectors:
                options = await self.page.locator(selector).all()
                if options:
                    for option in options:
                        option_text = await option.text_content()
                        if option_text:
                            # Try exact match first
                            if value.lower() == option_text.lower():
                                await option.click()
                                logger.info(f"✅ Selected custom option '{option_text}' for '{label}' (exact match)")
                                return True
                            # Try partial match
                            elif value.lower() in option_text.lower() or option_text.lower() in value.lower():
                                await option.click()
                                logger.info(f"✅ Selected custom option '{option_text}' for '{label}' (partial match)")
                                return True
                            # For graduation years, try to find the year in the option
                            elif value.isdigit() and value in option_text:
                                await option.click()
                                logger.info(f"✅ Selected custom option '{option_text}' for '{label}' (year match)")
                                return True
            
            logger.warning(f"Could not find option '{value}' in custom dropdown for '{label}'")
            return False
            
        except Exception as e:
            logger.warning(f"Failed to select custom dropdown option: {e}")
            return False
