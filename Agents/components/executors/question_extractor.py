"""
Question Extractor for Radio Buttons and Checkboxes

This module extracts the question/context associated with radio buttons and checkboxes
using UI/UX rules, HTML structure analysis, and accessibility patterns.

Key Principles:
1. Radio buttons and checkboxes are visual controls for answering questions
2. The question is usually in a parent container, preceding sibling, or associated label
3. Use ARIA attributes, fieldset/legend, and proximity analysis
4. Follow HTML/CSS best practices for form accessibility
"""
from typing import Dict, List, Optional, Any
from playwright.async_api import Page, Frame, Locator
from loguru import logger


class QuestionExtractor:
    """Extracts questions associated with form controls (radio, checkbox, select)."""
    
    def __init__(self, page: Page | Frame):
        self.page = page
    
    async def extract_question_for_field(self, element: Locator, field_category: str) -> Dict[str, Any]:
        """
        Extract the question/label and available options for a field.
        
        Args:
            element: The form field element (radio button, checkbox, select, etc.)
            field_category: Type of field ('radio', 'checkbox', 'dropdown', etc.)
        
        Returns:
            Dictionary with:
            - question: The question text associated with this field
            - option_label: The label for this specific option (for radio/checkbox)
            - all_options: List of all options for this question (for radio groups)
            - context: Additional context text
        """
        try:
            if field_category in ['radio', 'checkbox']:
                return await self._extract_radio_checkbox_question(element, field_category)
            elif 'dropdown' in field_category:
                return await self._extract_dropdown_question(element)
            else:
                return await self._extract_text_field_question(element)
        except Exception as e:
            logger.error(f"Error extracting question for field: {e}")
            return {
                'question': '',
                'option_label': '',
                'all_options': [],
                'context': ''
            }
    
    async def _extract_radio_checkbox_question(self, element: Locator, field_type: str) -> Dict[str, Any]:
        """
        Extract question and options for radio button or checkbox.
        
        Strategy:
        1. Get the name attribute to find all related radio buttons in the group
        2. Look for question text in:
           - <fieldset><legend>...</legend> (proper HTML forms)
           - Parent div/section with heading or label
           - Preceding sibling with question-like text
           - ARIA attributes (aria-labelledby, aria-describedby)
        3. Extract all option labels in the group
        """
        result = await element.evaluate('''
            (el, fieldType) => {
                // Helper: Get clean text from element
                function getCleanText(element) {
                    if (!element) return '';
                    let text = '';
                    for (const node of element.childNodes) {
                        if (node.nodeType === Node.TEXT_NODE) {
                            text += node.textContent;
                        } else if (node.nodeType === Node.ELEMENT_NODE) {
                            const tag = node.tagName.toLowerCase();
                            // Skip input elements but include their labels
                            if (tag === 'label' || tag === 'span' || tag === 'div' || tag === 'p') {
                                text += ' ' + node.textContent;
                            }
                        }
                    }
                    return text.trim().replace(/\\s+/g, ' ');
                }
                
                // Helper: Check if text looks like a question
                function looksLikeQuestion(text) {
                    if (!text || text.length < 5) return false;
                    // Questions often contain: ?, "are you", "do you", "have you", "will you", "select", etc.
                    const questionIndicators = [
                        '?', 'are you', 'do you', 'have you', 'will you', 'can you',
                        'please select', 'please indicate', 'please choose',
                        'which', 'what', 'when', 'where', 'how', 'why',
                        'select your', 'select the', 'indicate your', 'choose your'
                    ];
                    const lowerText = text.toLowerCase();
                    return questionIndicators.some(indicator => lowerText.includes(indicator));
                }
                
                // Step 1: Get this option's label
                let optionLabel = '';
                
                // Try label[for=id]
                if (el.id) {
                    const label = document.querySelector(`label[for="${el.id}"]`);
                    if (label) {
                        optionLabel = getCleanText(label);
                    }
                }
                
                // Try parent label
                if (!optionLabel) {
                    let parent = el.parentElement;
                    if (parent && parent.tagName.toLowerCase() === 'label') {
                        optionLabel = getCleanText(parent);
                    }
                }
                
                // Try aria-label
                if (!optionLabel) {
                    optionLabel = el.getAttribute('aria-label') || '';
                }
                
                // Try sibling text next to the input
                if (!optionLabel) {
                    const parent = el.parentElement;
                    if (parent) {
                        let siblingText = '';
                        for (const child of parent.childNodes) {
                            if (child.nodeType === Node.TEXT_NODE && child.textContent.trim()) {
                                siblingText += child.textContent.trim() + ' ';
                            } else if (child.nodeType === Node.ELEMENT_NODE && child !== el) {
                                const tag = child.tagName.toLowerCase();
                                if (['label', 'span'].includes(tag)) {
                                    siblingText += child.textContent.trim() + ' ';
                                }
                            }
                        }
                        if (siblingText.trim()) {
                            optionLabel = siblingText.trim();
                        }
                    }
                }
                
                // Step 2: Find the question text (group label)
                let questionText = '';
                let questionSource = 'unknown';
                
                // Strategy A: Fieldset + Legend (best practice HTML)
                let currentElement = el;
                for (let i = 0; i < 5; i++) {  // Search up to 5 levels
                    currentElement = currentElement.parentElement;
                    if (!currentElement) break;
                    
                    if (currentElement.tagName.toLowerCase() === 'fieldset') {
                        const legend = currentElement.querySelector('legend');
                        if (legend) {
                            questionText = getCleanText(legend);
                            questionSource = 'fieldset_legend';
                            break;
                        }
                    }
                }
                
                // Strategy B: ARIA labelledby/describedby
                if (!questionText) {
                    const labelledBy = el.getAttribute('aria-labelledby');
                    if (labelledBy) {
                        const labelElement = document.getElementById(labelledBy);
                        if (labelElement) {
                            const text = getCleanText(labelElement);
                            if (looksLikeQuestion(text)) {
                                questionText = text;
                                questionSource = 'aria_labelledby';
                            }
                        }
                    }
                }
                
                if (!questionText) {
                    const describedBy = el.getAttribute('aria-describedby');
                    if (describedBy) {
                        const descElement = document.getElementById(describedBy);
                        if (descElement) {
                            const text = getCleanText(descElement);
                            if (looksLikeQuestion(text)) {
                                questionText = text;
                                questionSource = 'aria_describedby';
                            }
                        }
                    }
                }
                
                // Strategy C: Look for question in parent container
                // Find the nearest parent container and look for heading/label elements before this input group
                if (!questionText) {
                    const fieldName = el.getAttribute('name') || '';
                    let container = el.parentElement;
                    
                    // Find a suitable container (usually a div, section, or form-group)
                    for (let i = 0; i < 5; i++) {
                        if (!container) break;
                        
                        // Check if container has multiple inputs with same name (indicates a radio group)
                        if (fieldName) {
                            const sameNameInputs = container.querySelectorAll(`input[name="${fieldName}"]`);
                            if (sameNameInputs.length > 1) {
                                // This is likely the container for the group
                                // Look for heading or label elements in this container
                                const headings = container.querySelectorAll('h1, h2, h3, h4, h5, h6, label, legend, div[class*="label"], div[class*="question"], span[class*="label"], span[class*="question"], p[class*="question"]');
                                
                                for (const heading of headings) {
                                    // Make sure the heading comes before the first input
                                    const headingY = heading.getBoundingClientRect().top;
                                    const firstInputY = sameNameInputs[0].getBoundingClientRect().top;
                                    
                                    if (headingY <= firstInputY) {
                                        const text = getCleanText(heading);
                                        if (text && text.length > 5 && text !== optionLabel) {
                                            questionText = text;
                                            questionSource = 'container_heading';
                                            break;
                                        }
                                    }
                                }
                                
                                if (questionText) break;
                            }
                        }
                        
                        container = container.parentElement;
                    }
                }
                
                // Strategy D: Look at preceding siblings for question text
                if (!questionText) {
                    let sibling = el.previousElementSibling;
                    let attempts = 0;
                    
                    while (sibling && attempts < 5) {
                        const text = getCleanText(sibling);
                        if (looksLikeQuestion(text) && text !== optionLabel) {
                            questionText = text;
                            questionSource = 'preceding_sibling';
                            break;
                        }
                        sibling = sibling.previousElementSibling;
                        attempts++;
                    }
                }
                
                // Strategy E: Parent's preceding siblings (question might be outside the immediate parent)
                if (!questionText) {
                    let parent = el.parentElement;
                    if (parent) {
                        let sibling = parent.previousElementSibling;
                        let attempts = 0;
                        
                        while (sibling && attempts < 3) {
                            const text = getCleanText(sibling);
                            if (looksLikeQuestion(text)) {
                                questionText = text;
                                questionSource = 'parent_preceding_sibling';
                                break;
                            }
                            sibling = sibling.previousElementSibling;
                            attempts++;
                        }
                    }
                }
                
                // Strategy F: Look for role="group" with aria-label
                if (!questionText) {
                    let parent = el.parentElement;
                    for (let i = 0; i < 5; i++) {
                        if (!parent) break;
                        if (parent.getAttribute('role') === 'group' || parent.getAttribute('role') === 'radiogroup') {
                            const ariaLabel = parent.getAttribute('aria-label');
                            if (ariaLabel && ariaLabel.length > 5) {
                                questionText = ariaLabel;
                                questionSource = 'role_group_aria_label';
                                break;
                            }
                        }
                        parent = parent.parentElement;
                    }
                }
                
                // Step 3: Find all options in this group
                const fieldName = el.getAttribute('name');
                const fieldId = el.getAttribute('id');
                let allOptions = [];
                
                if (fieldName && fieldType === 'radio') {
                    // For radio buttons, find all with same name
                    const radioGroup = document.querySelectorAll(`input[type="radio"][name="${fieldName}"]`);
                    for (const radio of radioGroup) {
                        let radioLabel = '';
                        
                        // Get label for each radio
                        if (radio.id) {
                            const label = document.querySelector(`label[for="${radio.id}"]`);
                            if (label) {
                                radioLabel = getCleanText(label);
                            }
                        }
                        
                        if (!radioLabel && radio.parentElement && radio.parentElement.tagName.toLowerCase() === 'label') {
                            radioLabel = getCleanText(radio.parentElement);
                        }
                        
                        if (!radioLabel) {
                            radioLabel = radio.getAttribute('aria-label') || radio.getAttribute('value') || '';
                        }
                        
                        if (radioLabel) {
                            allOptions.push({
                                text: radioLabel,
                                value: radio.getAttribute('value') || radioLabel,
                                id: radio.getAttribute('id') || '',
                                name: radio.getAttribute('name') || ''
                            });
                        }
                    }
                }
                
                // Return the extracted information
                return {
                    question: questionText,
                    questionSource: questionSource,
                    optionLabel: optionLabel,
                    allOptions: allOptions,
                    fieldName: fieldName || '',
                    fieldId: fieldId || '',
                    fieldType: fieldType
                };
            }
        ''', field_type)
        
        logger.debug(f"📋 Extracted question context:")
        logger.debug(f"   Question: {result['question']}")
        logger.debug(f"   Source: {result['questionSource']}")
        logger.debug(f"   This option: {result['optionLabel']}")
        logger.debug(f"   All options: {[opt['text'] for opt in result['allOptions']]}")
        
        return result
    
    async def _extract_dropdown_question(self, element: Locator) -> Dict[str, Any]:
        """Extract question for dropdown/select fields."""
        result = await element.evaluate('''
            (el) => {
                function getCleanText(element) {
                    if (!element) return '';
                    return element.textContent.trim().replace(/\\s+/g, ' ');
                }
                
                let questionText = '';
                let questionSource = 'unknown';
                
                // Try label[for=id]
                if (el.id) {
                    const label = document.querySelector(`label[for="${el.id}"]`);
                    if (label) {
                        questionText = getCleanText(label);
                        questionSource = 'label_for';
                    }
                }
                
                // Try aria-labelledby
                if (!questionText) {
                    const labelledBy = el.getAttribute('aria-labelledby');
                    if (labelledBy) {
                        const labelEl = document.getElementById(labelledBy);
                        if (labelEl) {
                            questionText = getCleanText(labelEl);
                            questionSource = 'aria_labelledby';
                        }
                    }
                }
                
                // Try aria-label
                if (!questionText) {
                    questionText = el.getAttribute('aria-label') || '';
                    if (questionText) questionSource = 'aria_label';
                }
                
                // Try preceding sibling
                if (!questionText) {
                    let sibling = el.previousElementSibling;
                    if (sibling) {
                        questionText = getCleanText(sibling);
                        if (questionText) questionSource = 'preceding_sibling';
                    }
                }
                
                // Try parent label
                if (!questionText) {
                    let parent = el.parentElement;
                    if (parent && parent.tagName.toLowerCase() === 'label') {
                        questionText = getCleanText(parent);
                        if (questionText) questionSource = 'parent_label';
                    }
                }
                
                return {
                    question: questionText,
                    questionSource: questionSource,
                    optionLabel: '',
                    allOptions: [],
                    fieldName: el.getAttribute('name') || '',
                    fieldId: el.getAttribute('id') || '',
                    fieldType: 'dropdown'
                };
            }
        ''')
        
        return result
    
    async def _extract_text_field_question(self, element: Locator) -> Dict[str, Any]:
        """Extract label/question for text input fields."""
        result = await element.evaluate('''
            (el) => {
                function getCleanText(element) {
                    if (!element) return '';
                    return element.textContent.trim().replace(/\\s+/g, ' ');
                }
                
                let questionText = '';
                let questionSource = 'unknown';
                
                // Try label[for=id]
                if (el.id) {
                    const label = document.querySelector(`label[for="${el.id}"]`);
                    if (label) {
                        questionText = getCleanText(label);
                        questionSource = 'label_for';
                    }
                }
                
                // Try aria-label
                if (!questionText) {
                    questionText = el.getAttribute('aria-label') || '';
                    if (questionText) questionSource = 'aria_label';
                }
                
                // Try placeholder as last resort
                if (!questionText) {
                    questionText = el.getAttribute('placeholder') || '';
                    if (questionText) questionSource = 'placeholder';
                }
                
                return {
                    question: questionText,
                    questionSource: questionSource,
                    optionLabel: '',
                    allOptions: [],
                    fieldName: el.getAttribute('name') || '',
                    fieldId: el.getAttribute('id') || '',
                    fieldType: 'text'
                };
            }
        ''')
        
        return result
    
    async def group_radio_buttons_by_question(self, all_fields: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Group radio buttons by their shared question, returning one entry per question.
        
        Args:
            all_fields: List of all detected form fields
        
        Returns:
            Updated list where radio button groups are consolidated into single entries
        """
        try:
            radio_groups = {}  # key: field_name, value: list of fields
            non_radio_fields = []
            
            for field in all_fields:
                if field.get('field_category') == 'radio':
                    field_name = field.get('name', '')
                    if field_name:
                        if field_name not in radio_groups:
                            radio_groups[field_name] = []
                        radio_groups[field_name].append(field)
                    else:
                        # Radio without name - treat as individual
                        non_radio_fields.append(field)
                else:
                    non_radio_fields.append(field)
            
            # Process radio groups - create one consolidated entry per group
            for field_name, group_fields in radio_groups.items():
                if len(group_fields) == 0:
                    continue
                
                # Extract question for the first field in group (they should all have same question)
                first_field = group_fields[0]
                question_data = await self.extract_question_for_field(
                    first_field['element'],
                    'radio'
                )
                
                # Create a consolidated field entry for this radio group
                consolidated_field = {
                    'element': first_field['element'],  # Use first radio as representative
                    'label': question_data['question'] or first_field.get('label', ''),
                    'field_category': 'radio_group',
                    'field_question': question_data['question'],
                    'options': question_data['allOptions'],
                    'name': field_name,
                    'id': first_field.get('id', ''),
                    'stable_id': f"radio_group:{field_name}",
                    'required': first_field.get('required', False),
                    'individual_radios': group_fields  # Keep reference to individual radios
                }
                
                non_radio_fields.append(consolidated_field)
                
                logger.info(f"📻 Grouped {len(group_fields)} radio buttons for question: '{question_data['question']}'")
                logger.info(f"    Options: {[opt['text'] for opt in question_data['allOptions']]}")
            
            return non_radio_fields
        
        except Exception as e:
            logger.error(f"Error grouping radio buttons: {e}")
            return all_fields
    
    async def group_checkboxes_by_question(self, checkboxes: List) -> List[Dict[str, Any]]:
        """
        Group checkboxes by their shared question/container.
        
        Unlike radio buttons (which share a name), checkboxes often have different names
        but belong to the same question. We group them by:
        1. Shared question text
        2. Shared parent container
        3. Similar ID patterns
        
        Args:
            checkboxes: List of checkbox elements
        
        Returns:
            List of grouped checkbox data with questions and all options
        """
        try:
            checkbox_data = []
            
            # Extract data for all checkboxes
            for cb in checkboxes:
                try:
                    cb_id = await cb.get_attribute('id')
                    cb_name = await cb.get_attribute('name')
                    cb_value = await cb.get_attribute('value')
                    
                    # Get label
                    cb_label = ''
                    if cb_id:
                        label = self.page.locator(f'label[for="{cb_id}"]').first
                        if await label.count() > 0:
                            cb_label = await label.text_content()
                            cb_label = cb_label.strip() if cb_label else ''
                    
                    # Try parent label if no label[for] found
                    if not cb_label:
                        parent = cb.locator('..')
                        parent_tag = await parent.evaluate('el => el.tagName.toLowerCase()')
                        if parent_tag == 'label':
                            cb_label = await parent.text_content()
                            cb_label = cb_label.strip() if cb_label else ''
                    
                    # Extract question context
                    question_context = await self.extract_question_for_field(cb, 'checkbox')
                    
                    checkbox_data.append({
                        'element': cb,
                        'id': cb_id,
                        'name': cb_name,
                        'value': cb_value,
                        'label': cb_label,
                        'question': question_context.get('question', ''),
                        'question_source': question_context.get('questionSource', ''),
                    })
                except Exception as e:
                    logger.debug(f"Error extracting checkbox data: {e}")
                    continue
            
            # Group by question text (case-insensitive)
            groups = {}
            ungrouped = []
            
            for cb_data in checkbox_data:
                question = cb_data['question'].strip()
                
                if question:
                    # Normalize question for grouping
                    question_key = question.lower().strip()
                    
                    if question_key not in groups:
                        groups[question_key] = {
                            'question': question,
                            'question_source': cb_data['question_source'],
                            'checkboxes': []
                        }
                    
                    groups[question_key]['checkboxes'].append(cb_data)
                else:
                    # No question found - check if it shares ID pattern with existing groups
                    grouped = False
                    
                    if cb_data['id']:
                        # Extract ID prefix (e.g., "eb2e6758-ba53-4985-a80c-488df61774dc_a63b6f87" from full ID)
                        id_parts = cb_data['id'].split('-')
                        if len(id_parts) >= 4:
                            id_prefix = '-'.join(id_parts[:4])  # First 4 parts of UUID
                            
                            # Try to find existing group with same ID prefix
                            for group_data in groups.values():
                                for existing_cb in group_data['checkboxes']:
                                    if existing_cb['id'] and existing_cb['id'].startswith(id_prefix):
                                        group_data['checkboxes'].append(cb_data)
                                        grouped = True
                                        break
                                if grouped:
                                    break
                    
                    if not grouped:
                        ungrouped.append(cb_data)
            
            # Convert to output format
            result = []
            
            # Add grouped checkboxes
            for idx, (question_key, group_data) in enumerate(groups.items(), 1):
                result.append({
                    'question': group_data['question'],
                    'question_source': group_data['question_source'],
                    'checkboxes': group_data['checkboxes'],
                    'group_id': f"checkbox_group_{idx}"
                })
            
            # Add ungrouped checkboxes as individual groups
            for cb_data in ungrouped:
                result.append({
                    'question': '',
                    'question_source': 'unknown',
                    'checkboxes': [cb_data],
                    'group_id': f"checkbox_single_{cb_data['name'] or cb_data['id']}"
                })
            
            return result
        
        except Exception as e:
            logger.error(f"Error grouping checkboxes: {e}")
            return []

