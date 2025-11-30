"""
Enhanced Generic Form Filler V2 with:
- Single-attempt strategy per field (deterministic → AI → skip)
- Field validation after each attempt
- Improved field detection (skip invalid elements)
- Better Greenhouse dropdown matching
- Final Gemini review before submission
"""
import asyncio
import re
from typing import Any, Dict, List, Optional, Tuple, Set
from playwright.async_api import Page, Frame
from loguru import logger

from components.executors.field_interactor_v2 import FieldInteractorV2
from components.executors.deterministic_field_mapper import DeterministicFieldMapper, FieldMappingConfidence
from components.executors.learned_patterns_mapper import LearnedPatternsMapper
from components.brains.gemini_field_mapper import GeminiFieldMapper
from components.exceptions.field_exceptions import RequiresHumanInputError
from components.state.field_completion_tracker import FieldCompletionTracker
from components.validators.field_value_validator import FieldValueValidator
from components.pattern_recorder import PatternRecorder


class FieldAttemptTracker:
    """Tracks which methods have been attempted for each field."""

    def __init__(self):
        self.attempts: Dict[str, Set[str]] = {}  # field_id -> {method names}
        self.needs_human: Set[str] = set()  # field_ids that AI determined need human input

    def has_attempted(self, field_id: str, method: str) -> bool:
        """Check if method was already attempted for this field."""
        return method in self.attempts.get(field_id, set())

    def mark_attempted(self, field_id: str, method: str):
        """Mark that method was attempted for this field."""
        if field_id not in self.attempts:
            self.attempts[field_id] = set()
        self.attempts[field_id].add(method)

    def mark_needs_human(self, field_id: str):
        """Mark that AI determined this field needs human input."""
        self.needs_human.add(field_id)
        # Also mark AI as attempted to avoid re-asking
        self.mark_attempted(field_id, 'ai')

    def requires_human_input(self, field_id: str) -> bool:
        """Check if field was flagged as needing human input."""
        return field_id in self.needs_human

    def get_next_method(self, field_id: str) -> Optional[str]:
        """Get next method to try for this field."""
        # If already flagged as needing human input, no more methods to try
        if field_id in self.needs_human:
            return None

        attempted = self.attempts.get(field_id, set())

        # Strategy order: deterministic → learned_pattern → AI → skip
        if 'deterministic' not in attempted:
            return 'deterministic'
        elif 'learned_pattern' not in attempted:
            return 'learned_pattern'
        elif 'ai' not in attempted:
            return 'ai'
        else:
            return None  # All methods exhausted


class GenericFormFillerV2Enhanced:
    """
    Enhanced form filler with single-attempt strategy and final validation:
    1. Try deterministic mapping first
    2. Validate the field
    3. If empty and not tried AI, try AI
    4. Validate again
    5. If still empty, skip field
    6. Final Gemini review of all inputs before submission
    """

    MAX_ITERATIONS = 5
    DYNAMIC_CONTENT_WAIT_MS = 1000

    def __init__(self, page: Page | Frame, action_recorder=None, user_id=None):
        self.page = page
        self.action_recorder = action_recorder
        self.user_id = user_id
        self.interactor = FieldInteractorV2(page, action_recorder)
        self.deterministic_mapper = DeterministicFieldMapper()
        self.learned_mapper = LearnedPatternsMapper()  # NEW: Tier 2 - Learned patterns
        self.ai_mapper = GeminiFieldMapper()
        self.pattern_recorder = PatternRecorder()  # NEW: Records AI successes
        self.completion_tracker = FieldCompletionTracker()
        self.attempt_tracker = FieldAttemptTracker()

    async def fill_form(self, profile: Dict[str, Any]) -> Dict[str, Any]:
        """
        Fill form with enhanced strategy and validation.
        """
        logger.info("🚀 Starting enhanced form filling with single-attempt strategy...")

        current_url = self.page.url
        self.completion_tracker.set_current_page(current_url)

        result = {
            "success": False,
            "total_fields_filled": 0,
            "iterations": 0,
            "fields_by_method": {"deterministic": 0, "learned_pattern": 0, "ai": 0},
            "errors": [],
            "requires_human": [],
            "skipped_fields": [],
            "filled_fields": {}  # field_label -> value (for final review)
        }

        # Keep track of last detected fields for correction mechanism
        last_detected_fields = []

        # Iterative filling loop
        for iteration in range(self.MAX_ITERATIONS):
            result["iterations"] = iteration + 1
            logger.info(f"📝 Form filling iteration {iteration + 1}/{self.MAX_ITERATIONS}")

            # Step 0: Try to upload resume if not already done (first iteration only)
            if iteration == 0:
                resume_path = profile.get('resume_path')
                if resume_path:
                    logger.info(f"📄 Attempting resume upload: {resume_path}")
                    upload_success = await self.interactor.upload_resume_if_present(resume_path)
                    if upload_success:
                        logger.info("✅ Resume uploaded successfully")
                    else:
                        logger.debug("⏭️ No resume upload field found or upload skipped")

            # Step 1: Detect fields (NO option extraction - fill immediately!)
            all_fields = await self.interactor.get_all_form_fields(extract_options=False)
            last_detected_fields = all_fields  # Save for correction mechanism
            logger.info(f"🔍 Detected {len(all_fields)} fields (fast mode - no pre-extraction)")

            # Step 2: Consolidate radio button groups (so we don't send duplicates to Gemini)
            all_fields = await self._consolidate_radio_groups(all_fields)
            logger.info(f"🔗 After radio grouping: {len(all_fields)} fields")
            
            # Step 2.5: Consolidate checkbox groups (same logic - group related checkboxes)
            all_fields = await self._consolidate_checkbox_groups(all_fields)
            logger.info(f"🔗 After checkbox grouping: {len(all_fields)} fields")

            # Step 3: Clean fields (remove invalid ones)
            valid_fields = await self._clean_detected_fields(all_fields)
            logger.info(f"✅ {len(valid_fields)} valid fields after cleaning")

            # Step 3: Filter out completed fields
            unfilled_fields = self._filter_unfilled_fields(valid_fields)
            logger.info(f"📊 {len(unfilled_fields)} fields remain to fill")

            if not unfilled_fields:
                logger.info("✅ All valid fields processed!")
                result["success"] = True
                break

            # Step 4: Process each field with single-attempt strategy
            iteration_filled = await self._process_fields_with_strategy(
                unfilled_fields, profile, result
            )

            if iteration_filled == 0:
                logger.warning("⚠️ No progress made in this iteration")
                break

            # Step 5: Wait for dynamic content
            await self.page.wait_for_timeout(self.DYNAMIC_CONTENT_WAIT_MS)

        # Step 6: Final Gemini review and correction before submission
        if result["filled_fields"]:
            logger.info("🤖 Performing final Gemini review...")
            review_result = await self._final_gemini_review(result["filled_fields"], profile)
            result["gemini_review"] = review_result

            if not review_result.get("approved", False):
                logger.warning(f"⚠️ Gemini flagged issues: {review_result.get('issues', [])}")

                # Step 7: Attempt to correct flagged issues
                logger.info("🔧 Attempting to correct Gemini-flagged issues...")
                corrections_made = await self._correct_gemini_issues(
                    review_result.get('issues', []),
                    result["filled_fields"],
                    last_detected_fields,
                    profile,
                    result
                )

                if corrections_made > 0:
                    logger.info(f"✅ Corrected {corrections_made} issues - performing re-review...")
                    # Re-review after corrections
                    review_result = await self._final_gemini_review(result["filled_fields"], profile)
                    result["gemini_review"] = review_result

                    if review_result.get("approved", False):
                        logger.info("✅ Form approved after corrections")
                        result["success"] = True
                    else:
                        logger.warning("⚠️ Issues remain after correction attempts")
                        result["success"] = False
                else:
                    logger.warning("⚠️ Could not automatically correct flagged issues")
                    result["success"] = False

        # Final summary
        result["total_fields_filled"] = len(result["filled_fields"])
        logger.info(f"🏁 Form filling completed: {result['total_fields_filled']} fields filled in {result['iterations']} iterations")
        logger.info(f"📊 Methods used: {result['fields_by_method']['deterministic']} deterministic, {result['fields_by_method']['learned_pattern']} learned patterns, {result['fields_by_method']['ai']} AI")

        # Log AI call reduction
        ai_reduction = 0
        if result['fields_by_method']['learned_pattern'] > 0:
            total_mapped = result['fields_by_method']['learned_pattern'] + result['fields_by_method']['ai']
            if total_mapped > 0:
                ai_reduction = (result['fields_by_method']['learned_pattern'] / total_mapped) * 100
                logger.info(f"💡 AI call reduction: {ai_reduction:.1f}% (learned patterns used instead of AI)")

        logger.info(f"⏭️ Skipped {len(result['skipped_fields'])} fields after all attempts")

        # Step 8: Look for Next/Continue button and click it (but never Submit)
        next_button_clicked = await self._try_click_next_button()
        result["next_button_clicked"] = next_button_clicked

        return result

    async def _consolidate_radio_groups(self, fields: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Consolidate radio buttons into groups so Gemini sees ONE field per question, not multiple.
        
        Before: 5 separate fields for "When do you expect to graduate?" (one per option)
        After: 1 field with question and all 5 options
        
        Args:
            fields: List of all detected fields
        
        Returns:
            Updated list with radio groups consolidated
        """
        try:
            radio_groups = {}  # key: name attribute, value: list of radio buttons
            non_radio_fields = []
            
            for field in fields:
                if field.get('field_category') == 'radio':
                    field_name = field.get('name', '')
                    if field_name:
                        if field_name not in radio_groups:
                            radio_groups[field_name] = []
                        radio_groups[field_name].append(field)
                    else:
                        # Radio without name - treat as individual field
                        non_radio_fields.append(field)
                else:
                    # Not a radio button - keep as is
                    non_radio_fields.append(field)
            
            # Create one consolidated field per radio group
            for field_name, group_fields in radio_groups.items():
                if len(group_fields) == 0:
                    continue
                
                # Use the first field as the base
                first_field = group_fields[0]
                
                # Get the question (all radios in group should have same question)
                question = first_field.get('field_question', first_field.get('label', ''))
                
                # Get all options from all radio buttons in the group
                all_options = []
                seen_option_texts = set()
                
                for radio_field in group_fields:
                    # IMPORTANT: Use option_label ONLY, not label (label has been overwritten with the question)
                    option_label = radio_field.get('option_label', '')
                    radio_id = radio_field.get('id', '')
                    radio_value = radio_field.get('name', '')
                    
                    # Debug: Check if option_label is missing
                    if not option_label:
                        logger.warning(f"⚠️  Radio button missing option_label! ID={radio_id}, name={radio_value}")
                        # Try to extract from options list as fallback
                        radio_options = radio_field.get('options', [])
                        if radio_options:
                            # Find the option for this specific radio button
                            for opt in radio_options:
                                if isinstance(opt, dict) and opt.get('id') == radio_id:
                                    option_label = opt.get('text', '')
                                    break
                    
                    # Avoid duplicates
                    if option_label and option_label not in seen_option_texts:
                        all_options.append({
                            'text': option_label,
                            'value': radio_value,
                            'id': radio_id,
                            'element': radio_field.get('element')  # Keep reference to actual element
                        })
                        seen_option_texts.add(option_label)
                    elif not option_label:
                        logger.warning(f"⚠️  Could not determine option label for radio button ID={radio_id}")
                
                # If we didn't get options from option_label, use the options list
                if not all_options:
                    all_options = first_field.get('options', [])
                
                # Create consolidated field
                consolidated = {
                    'element': first_field['element'],  # Representative element (we'll find the right one when filling)
                    'label': question,  # Use the question as the label
                    'field_question': question,
                    'field_category': 'radio_group',  # Mark as group
                    'options': all_options,
                    'name': field_name,
                    'id': first_field.get('id', ''),
                    'stable_id': f"radio_group:{field_name}",
                    'required': first_field.get('required', False),
                    'placeholder': '',
                    'individual_radios': group_fields,  # Keep all individual radio fields for filling
                    'input_type': 'radio',
                    'tag_name': 'input',
                    'is_dropdown': False,
                    'is_filled': first_field.get('is_filled', False),
                    'element_index': first_field.get('element_index', 0)
                }
                
                non_radio_fields.append(consolidated)
                
                logger.debug(f"📻 Consolidated radio group: '{question}' with options: {[opt['text'] for opt in all_options]}")
            
            logger.info(f"🔗 Consolidated {len(radio_groups)} radio groups from {sum(len(g) for g in radio_groups.values())} individual buttons")
            
            return non_radio_fields
        
        except Exception as e:
            logger.error(f"Error consolidating radio groups: {e}")
            return fields
    
    async def _consolidate_checkbox_groups(self, fields: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Consolidate checkboxes into groups by their shared question.
        
        Unlike radio buttons (same name), checkboxes often have different names but share a question.
        We group them by:
        1. Shared question text
        2. Shared ID prefix pattern
        
        Args:
            fields: List of all fields
        
        Returns:
            Updated list with checkbox groups consolidated
        """
        try:
            # Import QuestionExtractor
            from components.executors.question_extractor import QuestionExtractor
            
            checkbox_fields = []
            non_checkbox_fields = []
            
            # Separate checkboxes from other fields
            for field in fields:
                if field.get('field_category') == 'checkbox':
                    checkbox_fields.append(field)
                else:
                    non_checkbox_fields.append(field)
            
            if not checkbox_fields:
                return fields
            
            # Extract checkbox elements
            checkbox_elements = [f['element'] for f in checkbox_fields]
            
            # Use QuestionExtractor to group checkboxes intelligently
            extractor = QuestionExtractor(self.page)
            grouped_checkboxes = await extractor.group_checkboxes_by_question(checkbox_elements)
            
            logger.info(f"☑️  Grouped {len(checkbox_fields)} checkboxes into {len(grouped_checkboxes)} groups")
            
            # Convert grouped checkboxes to field format
            for group_idx, group in enumerate(grouped_checkboxes):
                question = group.get('question', '')
                checkboxes_data = group.get('checkboxes', [])
                
                if len(checkboxes_data) == 0:
                    continue
                
                # If it's a single checkbox with a good question, keep it as individual
                if len(checkboxes_data) == 1 and question:
                    # Single checkbox (e.g., terms & conditions, work authorization yes/no)
                    cb_data = checkboxes_data[0]
                    
                    # Find the original field data
                    matching_field = None
                    for cf in checkbox_fields:
                        if cf.get('id') == cb_data.get('id') or cf.get('name') == cb_data.get('name'):
                            matching_field = cf
                            break
                    
                    if matching_field:
                        # Update label to use the question
                        if question and len(question) > len(matching_field.get('label', '')):
                            matching_field['label'] = question
                            matching_field['field_question'] = question
                        non_checkbox_fields.append(matching_field)
                    
                elif len(checkboxes_data) > 1:
                    # Multiple checkboxes - create a consolidated group
                    first_cb = checkboxes_data[0]
                    
                    # Build options list
                    all_options = []
                    for cb_data in checkboxes_data:
                        all_options.append({
                            'text': cb_data.get('label', cb_data.get('name', '')),
                            'name': cb_data.get('name', ''),
                            'id': cb_data.get('id', ''),
                            'value': cb_data.get('value', '')
                        })
                    
                    # Find original field elements
                    individual_checkboxes = []
                    for cb_data in checkboxes_data:
                        for cf in checkbox_fields:
                            if cf.get('id') == cb_data.get('id') or cf.get('name') == cb_data.get('name'):
                                individual_checkboxes.append(cf)
                                break
                    
                    if individual_checkboxes:
                        first_field = individual_checkboxes[0]
                        
                        # Create consolidated checkbox group
                        consolidated = {
                            'element': first_field['element'],
                            'label': question or f"Checkbox Group {group_idx + 1}",
                            'field_question': question,
                            'field_category': 'checkbox_group',
                            'options': all_options,
                            'name': first_field.get('name', ''),
                            'id': first_field.get('id', ''),
                            'stable_id': f"checkbox_group:{first_cb.get('name', group_idx)}",
                            'required': first_field.get('required', False),
                            'placeholder': '',
                            'individual_checkboxes': individual_checkboxes,
                            'input_type': 'checkbox',
                            'tag_name': 'input',
                            'is_dropdown': False,
                            'is_filled': False,
                            'element_index': first_field.get('element_index', 0)
                        }
                        
                        non_checkbox_fields.append(consolidated)
                        logger.debug(f"☑️  Consolidated checkbox group: '{question}' ({len(checkboxes_data)} options)")
                else:
                    # Empty group - shouldn't happen
                    pass
            
            return non_checkbox_fields
        
        except Exception as e:
            logger.error(f"Error consolidating checkbox groups: {e}")
            import traceback
            logger.debug(traceback.format_exc())
            return fields

    async def _clean_detected_fields(self, fields: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Remove invalid fields from detection:
        - Empty labels
        - Listbox elements (dropdown options, not inputs)
        - Hidden elements
        - Already disabled elements
        """
        cleaned = []

        for field in fields:
            label = field.get('label', '').strip()
            field_category = field.get('field_category', '')
            stable_id = field.get('stable_id', '')

            # Skip fields with empty labels (unless they have a valid ID)
            if not label and not stable_id:
                logger.debug(f"⏭️ Skipping field with no label and no ID")
                continue

            # Skip listbox elements (they are dropdown options, not form inputs)
            if 'listbox' in stable_id.lower() or 'listbox' in field_category.lower():
                logger.debug(f"⏭️ Skipping listbox element: {label}")
                continue

            # Skip if element has role="listbox" (dropdown menu, not input)
            try:
                element = field.get('element')
                if element:
                    role = await element.get_attribute('role')
                    if role == 'listbox':
                        logger.debug(f"⏭️ Skipping role=listbox element: {label}")
                        continue
            except:
                pass

            # Skip hidden or disabled fields
            if field.get('is_hidden') or field.get('is_disabled'):
                logger.debug(f"⏭️ Skipping hidden/disabled field: {label}")
                continue

            cleaned.append(field)

        return cleaned

    async def _process_fields_with_strategy(
        self,
        fields: List[Dict[str, Any]],
        profile: Dict[str, Any],
        result: Dict[str, Any]
    ) -> int:
        """
        BATCH STRATEGY: Process all fields efficiently:
        Phase 1: Try deterministic on ALL fields
        Phase 1.5: Try learned patterns on remaining fields
        Phase 2: Collect fields needing AI help
        Phase 3: Make ONE batch Gemini call for all AI fields
        Phase 4: Apply all AI responses
        """
        filled_count = 0

        # PHASE 1: Try deterministic on all fields first
        logger.info("📋 Phase 1: Attempting deterministic mapping for all fields...")
        fields_needing_learned = []

        for field in fields:
            field_id = self._get_field_id(field)
            field_label = field.get('label', 'Unknown')

            # Skip if all methods exhausted
            next_method = self.attempt_tracker.get_next_method(field_id)
            if not next_method:
                if field_label not in [f['field'] for f in result['skipped_fields']]:
                    result['skipped_fields'].append({
                        "field": field_label,
                        "reason": "All strategies attempted, field still empty"
                    })
                continue

            # Try deterministic if not yet attempted
            if next_method == 'deterministic':
                success = await self._try_deterministic(field, profile, result)
                self.attempt_tracker.mark_attempted(field_id, 'deterministic')

                if success:
                    filled_count += 1
                    continue  # Success, move to next field
                else:
                    # Deterministic failed - try learned patterns next
                    fields_needing_learned.append(field)

            # If learned_pattern is next method, add to learned batch
            elif next_method == 'learned_pattern':
                fields_needing_learned.append(field)

        # PHASE 1.5: Try learned patterns on remaining fields
        fields_needing_ai = []
        if fields_needing_learned:
            logger.info(f"🧠 Phase 1.5: Attempting learned pattern matching for {len(fields_needing_learned)} fields...")
            for field in fields_needing_learned:
                field_id = self._get_field_id(field)
                field_label = field.get('label', 'Unknown')

                success = await self._try_learned_pattern(field, profile, result)
                self.attempt_tracker.mark_attempted(field_id, 'learned_pattern')

                if success:
                    filled_count += 1
                    continue  # Success, move to next field
                else:
                    # Learned pattern failed - add to AI batch
                    if not self.attempt_tracker.has_attempted(field_id, 'ai'):
                        fields_needing_ai.append(field)

        # PHASE 2 & 3: Batch AI processing
        if fields_needing_ai:
            logger.info(f"🤖 Phase 2: Batch processing {len(fields_needing_ai)} fields with Gemini...")
            ai_filled = await self._try_ai_batch(fields_needing_ai, profile, result)
            filled_count += ai_filled
        
        return filled_count

    async def _try_deterministic(
        self,
        field: Dict[str, Any],
        profile: Dict[str, Any],
        result: Dict[str, Any]
    ) -> bool:
        """Try to fill field with deterministic mapping."""
        field_label = field.get('label', 'Unknown')

        try:
            # Map field deterministically
            mapping = self.deterministic_mapper.map_field(
                field_label,
                field.get('field_category', 'text_input'),
                profile
            )

            if not mapping or mapping.confidence.value < 0.5:
                logger.debug(f"⏭️ No deterministic mapping for '{field_label}'")
                return False

            # Get fresh element
            element = await self._get_fresh_element(field)
            if not element:
                return False

            # Validate and clean the value before filling
            cleaned_value = FieldValueValidator.validate_and_clean(
                mapping.value,
                field_label,
                field.get('field_category', 'text_input')
            )

            # Prepare field data (no pre-extracted options in fast mode)
            field_data = {
                'element': element,
                'label': field_label,
                'field_category': field.get('field_category', 'text_input'),
                'stable_id': field.get('stable_id', '')
            }
            
            # Include group data for radio_group and checkbox_group
            if field.get('field_category') == 'radio_group':
                field_data['individual_radios'] = field.get('individual_radios', [])
            elif field.get('field_category') == 'checkbox_group':
                field_data['individual_checkboxes'] = field.get('individual_checkboxes', [])

            # Fill the field with cleaned value
            fill_result = await self.interactor.fill_field(field_data, cleaned_value, profile)

            if fill_result['success']:
                # Trust the fill result - it already verified success
                logger.info(f"✅ Deterministic: '{field_label}' = '{mapping.value}'")
                result["fields_by_method"]["deterministic"] += 1
                result["filled_fields"][field_label] = mapping.value

                field_id = self._get_field_id(field)
                self.completion_tracker.mark_field_completed(field_id, field_label, mapping.value)
                return True
            else:
                logger.debug(f"⏭️ Deterministic fill failed for '{field_label}'")
                return False

        except Exception as e:
            logger.error(f"❌ Error in deterministic attempt for '{field_label}': {e}")
            return False

    async def _try_learned_pattern(
        self,
        field: Dict[str, Any],
        profile: Dict[str, Any],
        result: Dict[str, Any]
    ) -> bool:
        """Try to fill field using learned patterns from database."""
        field_label = field.get('label', 'Unknown')

        try:
            # Query learned patterns
            learned_pattern = self.learned_mapper.map_field(
                field_label,
                field.get('field_category', 'text_input'),
                profile
            )

            if not learned_pattern:
                logger.debug(f"⏭️ No learned pattern for '{field_label}'")
                return False

            # Get value from profile using the learned profile field
            value = self.learned_mapper.get_profile_value(profile, learned_pattern.profile_field)

            if not value:
                logger.debug(
                    f"⏭️ Learned pattern found '{field_label}' → {learned_pattern.profile_field}, "
                    f"but no value in profile"
                )
                # Record failure to reduce confidence
                await self.pattern_recorder.record_pattern(
                    field_label,
                    learned_pattern.profile_field,
                    field.get('field_category', 'text_input'),
                    success=False,
                    user_id=self.user_id
                )
                return False

            # Get fresh element
            element = await self._get_fresh_element(field)
            if not element:
                return False

            # Validate and clean the value
            cleaned_value = FieldValueValidator.validate_and_clean(
                value,
                field_label,
                field.get('field_category', 'text_input')
            )

            # Prepare field data
            field_data = {
                'element': element,
                'label': field_label,
                'field_category': field.get('field_category', 'text_input'),
                'stable_id': field.get('stable_id', '')
            }

            # Include group data for radio_group and checkbox_group
            if field.get('field_category') == 'radio_group':
                field_data['individual_radios'] = field.get('individual_radios', [])
            elif field.get('field_category') == 'checkbox_group':
                field_data['individual_checkboxes'] = field.get('individual_checkboxes', [])

            # Fill the field
            fill_result = await self.interactor.fill_field(field_data, cleaned_value, profile)

            if fill_result['success']:
                logger.info(
                    f"✅ Learned Pattern: '{field_label}' = '{cleaned_value}' "
                    f"(from {learned_pattern.profile_field}, confidence: {learned_pattern.confidence_score:.2f})"
                )
                result["fields_by_method"]["learned_pattern"] += 1
                result["filled_fields"][field_label] = cleaned_value

                field_id = self._get_field_id(field)
                self.completion_tracker.mark_field_completed(field_id, field_label, cleaned_value)

                # Record successful reuse to boost confidence
                await self.pattern_recorder.record_pattern(
                    field_label,
                    learned_pattern.profile_field,
                    field.get('field_category', 'text_input'),
                    success=True,
                    user_id=self.user_id
                )
                return True
            else:
                logger.debug(f"⏭️ Learned pattern fill failed for '{field_label}'")
                # Record failure to reduce confidence
                await self.pattern_recorder.record_pattern(
                    field_label,
                    learned_pattern.profile_field,
                    field.get('field_category', 'text_input'),
                    success=False,
                    user_id=self.user_id
                )
                return False

        except Exception as e:
            logger.error(f"❌ Error in learned pattern attempt for '{field_label}': {e}")
            return False

    async def _try_ai_batch(
        self,
        fields: List[Dict[str, Any]],
        profile: Dict[str, Any],
        result: Dict[str, Any]
    ) -> int:
        """
        Batch process multiple fields with ONE Gemini API call.
        This reduces API calls from N to 1 per batch.
        """
        if not fields:
            return 0
        
        filled_count = 0
        
        try:
            # Make ONE batch Gemini call for all fields
            logger.info(f"🧠 Making batch Gemini call for {len(fields)} fields...")
            ai_mappings = await self.ai_mapper.map_fields_to_profile(fields, profile)
            logger.info(f"✅ Received {len(ai_mappings)} mappings from Gemini")
            
            # Apply each mapping
            for field in fields:
                field_id = self._get_field_id(field)
                field_label = field.get('label', 'Unknown')

                if field_id not in ai_mappings:
                    logger.debug(f"⏭️ No AI mapping for '{field_label}'")
                    # Mark as attempted since AI tried but couldn't map it
                    self.attempt_tracker.mark_attempted(field_id, 'ai')
                    result['skipped_fields'].append({
                        "field": field_label,
                        "reason": "AI did not provide mapping"
                    })
                    continue

                mapping_data = ai_mappings[field_id]
                mapping_type = mapping_data.get('type', 'simple')

                # Check if needs human input
                if mapping_type == 'needs_human_input':
                    # Mark as needing human input to avoid re-asking AI but don't skip entirely
                    self.attempt_tracker.mark_needs_human(field_id)
                    result["requires_human"].append({
                        "field": field_label,
                        "reason": mapping_data.get('reason', 'AI determined needs human input')
                    })
                    continue

                # Mark AI as attempted only when we actually try to fill the field
                self.attempt_tracker.mark_attempted(field_id, 'ai')
                
                # Handle MANUAL fields (essays, motivation questions) - generate AI content
                if mapping_type == 'manual':
                    logger.info(f"✍️ Generating AI content for essay field: '{field_label}'")
                    
                    # Determine max length based on field type
                    field_category = field.get('field_category', 'text_input')
                    max_length = 1000 if field_category == 'textarea' else 300
                    
                    # Extract job context from profile if available
                    job_context = {
                        'job_title': profile.get('target_job_title', ''),
                        'company': profile.get('target_company', ''),
                        'job_description': profile.get('job_description', '')
                    }
                    
                    # Generate AI-written response
                    generated_text = await self.ai_mapper.generate_text_field_response(
                        field_label=field_label,
                        field_type=field_category,
                        profile=profile,
                        job_context=job_context if any(job_context.values()) else None,
                        max_length=max_length
                    )
                    
                    if not generated_text:
                        logger.warning(f"⚠️ Failed to generate text for '{field_label}'")
                        result['skipped_fields'].append({
                            "field": field_label,
                            "reason": "AI text generation failed"
                        })
                        continue
                    
                    value = generated_text
                elif mapping_type in ['multiselect', 'multiselect_skills']:
                    # Get value for multiselect fields (list of options)
                    value = mapping_data.get('value')
                    if not value or (isinstance(value, list) and len(value) == 0):
                        logger.debug(f"⏭️ No value for multiselect '{field_label}'")
                        result['skipped_fields'].append({
                            "field": field_label,
                            "reason": "AI returned empty multiselect value"
                        })
                        continue
                    # Value is already a list, keep it as is
                else:
                    # Get value for simple/dropdown fields
                    value = mapping_data.get('value')
                    if not value:
                        logger.debug(f"⏭️ No value for '{field_label}'")
                        result['skipped_fields'].append({
                            "field": field_label,
                            "reason": "AI returned empty value"
                        })
                        continue
                
                # Validate and clean the value before filling
                cleaned_value = FieldValueValidator.validate_and_clean(
                    value,
                    field_label,
                    field.get('field_category', 'text_input')
                )

                # Get fresh element
                element = await self._get_fresh_element(field)
                if not element:
                    logger.debug(f"⏭️ Could not get fresh element for '{field_label}'")
                    continue

                # Prepare field data (fast mode - no pre-extracted options)
                field_data = {
                    'element': element,
                    'label': field_label,
                    'field_category': field.get('field_category', 'text_input'),
                    'stable_id': field.get('stable_id', '')
                }
                
                # Include group data for radio_group and checkbox_group
                if field.get('field_category') == 'radio_group':
                    field_data['individual_radios'] = field.get('individual_radios', [])
                elif field.get('field_category') == 'checkbox_group':
                    field_data['individual_checkboxes'] = field.get('individual_checkboxes', [])

                # Fill the field with cleaned value
                # CRITICAL: Check if this is a file upload but AI provided long text (essay/cover letter)
                if field.get('field_category') == 'file_upload' and len(str(cleaned_value)) > 100 and not str(cleaned_value).lower().endswith(('.pdf', '.doc', '.docx', '.txt')):
                    logger.info(f"📄 Detected text content for file upload '{field_label}'. Creating temporary file...")
                    try:
                        import tempfile
                        import os
                        
                        # Create temporary file with the content
                        # Use field label for filename if possible
                        safe_label = "".join([c for c in field_label if c.isalnum() or c in (' ', '_', '-')]).strip()[:30] or "document"
                        temp_file = tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.txt', prefix=f"{safe_label}_")
                        temp_file.write(str(cleaned_value))
                        temp_file.close()
                        
                        logger.info(f"✅ Created temporary file: {temp_file.name}")
                        cleaned_value = temp_file.name
                        
                        # Track for cleanup if agent has tracker
                        if hasattr(self, 'created_files'):
                            self.created_files.append(temp_file.name)
                            
                    except Exception as e:
                        logger.error(f"Failed to create temp file for text content: {e}")
                        result['skipped_fields'].append({
                            "field": field_label,
                            "reason": "Failed to convert AI text to file"
                        })
                        continue

                fill_result = await self.interactor.fill_field(field_data, cleaned_value, profile)

                if fill_result['success']:
                    # Log differently for generated text vs mapped values
                    if mapping_type == 'manual':
                        # Truncate long text for logging
                        display_value = value[:100] + '...' if len(value) > 100 else value
                        logger.info(f"✅ AI Generated: '{field_label}' = '{display_value}'")
                    else:
                        logger.info(f"✅ AI Batch: '{field_label}' = '{value}'")

                    result["fields_by_method"]["ai"] += 1
                    result["filled_fields"][field_label] = value
                    self.completion_tracker.mark_field_completed(field_id, field_label, value)
                    filled_count += 1

                    # NEW: Record successful AI mapping as learned pattern (except manual/essay fields)
                    if mapping_type not in ['manual', 'needs_human_input']:
                        # Try to infer profile_field from the mapping
                        profile_field = mapping_data.get('profile_field')
                        if profile_field:
                            await self.pattern_recorder.record_pattern(
                                field_label,
                                profile_field,
                                field.get('field_category', 'text_input'),
                                success=True,
                                user_id=self.user_id
                            )
                            logger.debug(f"📝 Recorded pattern: '{field_label}' → {profile_field}")
                else:
                    logger.debug(f"⏭️ AI batch fill failed for '{field_label}'")
                    result['skipped_fields'].append({
                        "field": field_label,
                        "reason": f"AI provided value but fill failed: {fill_result.get('error', 'Unknown')}"
                    })
            
            return filled_count
            
        except Exception as e:
            logger.error(f"❌ Error in batch AI processing: {e}")
            # Mark all fields as attempted even if batch failed
            for field in fields:
                field_id = self._get_field_id(field)
                self.attempt_tracker.mark_attempted(field_id, 'ai')
            return 0

    async def _try_ai(
        self,
        field: Dict[str, Any],
        profile: Dict[str, Any],
        result: Dict[str, Any]
    ) -> bool:
        """Try to fill field with AI mapping (single field - used by final review)."""
        field_label = field.get('label', 'Unknown')

        try:
            # Get AI mapping
            field_id = self._get_field_id(field)
            ai_mappings = await self.ai_mapper.map_fields_to_profile([field], profile)

            if field_id not in ai_mappings:
                logger.debug(f"⏭️ No AI mapping for '{field_label}'")
                return False

            mapping_data = ai_mappings[field_id]
            mapping_type = mapping_data.get('type', 'simple')

            # Check if needs human input
            if mapping_type == 'needs_human_input':
                result["requires_human"].append({
                    "field": field_label,
                    "reason": mapping_data.get('reason', 'AI determined needs human input')
                })
                return False

            # Get value
            value = mapping_data.get('value')
            if not value:
                return False

            # Get fresh element
            element = await self._get_fresh_element(field)
            if not element:
                return False

            # Prepare field data
            field_data = {
                'element': element,
                'label': field_label,
                'field_category': field.get('field_category', 'text_input'),
                'stable_id': field.get('stable_id', '')
            }

            # Fill the field
            fill_result = await self.interactor.fill_field(field_data, value, profile)

            if fill_result['success']:
                # Trust the fill result - it already verified success
                logger.info(f"✅ AI: '{field_label}' = '{value}'")
                result["fields_by_method"]["ai"] += 1
                result["filled_fields"][field_label] = value

                self.completion_tracker.mark_field_completed(field_id, field_label, value)
                return True
            else:
                logger.debug(f"⏭️ AI fill failed for '{field_label}'")
                return False

        except Exception as e:
            logger.error(f"❌ Error in AI attempt for '{field_label}': {e}")
            return False

    async def _validate_field(
        self,
        element: Any,
        expected_value: str,
        field_category: str
    ) -> bool:
        """
        Validate that field was actually filled.
        Returns True if field contains expected value, False if empty.
        """
        try:
            await asyncio.sleep(0.3)  # Wait for value to settle

            if 'dropdown' in field_category:
                # For dropdowns, check selected value or input value
                actual = await element.input_value() or await element.text_content()
            elif field_category == 'checkbox':
                # For checkboxes, check if checked
                return await element.is_checked()
            else:
                # For text inputs
                actual = await element.input_value()

            # Check if field is not empty
            if actual and actual.strip():
                return True
            else:
                logger.debug(f"Field validation: expected something, got empty")
                return False

        except Exception as e:
            logger.debug(f"Validation error: {e}")
            return False

    async def _correct_gemini_issues(
        self,
        issues: List[str],
        filled_fields: Dict[str, str],
        all_fields: List[Dict[str, Any]],
        profile: Dict[str, Any],
        result: Dict[str, Any]
    ) -> int:
        """
        Parse Gemini's flagged issues and attempt to correct them.

        Returns:
            int: Number of successfully corrected issues
        """
        corrections_made = 0

        try:
            # Use Gemini to parse issues and suggest corrections
            from google import genai
            import os
            import json

            client = genai.Client(api_key=os.getenv('GOOGLE_API_KEY'))

            issues_text = "\n".join([f"- {issue}" for issue in issues])
            filled_list = "\n".join([f"- {label}: {value}" for label, value in filled_fields.items()])

            # Use the comprehensive profile context (same as AI gets during field filling and review)
            from components.brains.gemini_field_mapper import GeminiFieldMapper
            field_mapper = GeminiFieldMapper()
            comprehensive_profile_context = field_mapper._create_profile_context(profile, context_type="correction")

            prompt = f"""
You identified these issues with a job application form:
{issues_text}

Current filled fields:
{filled_list}

{comprehensive_profile_context}

For EACH issue, provide a correction. Respond in JSON format:
{{
  "corrections": [
    {{
      "field_name": "exact field name from filled fields",
      "current_value": "current incorrect value",
      "corrected_value": "what it should be",
      "reason": "why this correction is needed"
    }}
  ]
}}

IMPORTANT:
- Only include fields that actually need correction
- Use EXACT field names as they appear in "Current filled fields"
- For "Phone Extension" with full phone number, set corrected_value to empty string "" (not the extension)
- For redundant duplicate fields, mark them for removal by setting corrected_value to ""
- For graduation date fields:
  * If graduation date is in the FUTURE relative to Current Date, the person IS currently enrolled
  * Provide the actual graduation date from education data (e.g., "May 2025", "December 2025")
  * DO NOT use "No" or "I am not currently enrolled" when graduation is in the future
- For LinkedIn URLs, ensure they start with "https://www.linkedin.com/in/"
"""

            response = client.models.generate_content(
                model="gemini-2.0-flash",
                contents=prompt,
                config={"response_mime_type": "application/json"}
            )

            corrections_data = json.loads(response.text)
            corrections_list = corrections_data.get('corrections', [])

            if not corrections_list:
                logger.info("🤷 Gemini could not suggest specific corrections")
                return 0

            logger.info(f"📝 Gemini suggested {len(corrections_list)} corrections")

            # Apply each correction
            for correction in corrections_list:
                field_name = correction.get('field_name')
                corrected_value = correction.get('corrected_value')
                reason = correction.get('reason', 'No reason provided')

                if not field_name or field_name not in filled_fields:
                    logger.debug(f"⏭️ Skipping correction for unknown field: {field_name}")
                    continue

                # Find the field definition
                field_def = None
                for field in all_fields:
                    if field.get('label') == field_name:
                        field_def = field
                        break

                if not field_def:
                    logger.debug(f"⏭️ Could not find field definition for: {field_name}")
                    continue

                # Apply correction
                logger.info(f"🔧 Correcting '{field_name}': '{filled_fields[field_name]}' → '{corrected_value}'")
                logger.info(f"   Reason: {reason}")

                # Get fresh element
                element = await self._get_fresh_element(field_def)
                if not element:
                    logger.warning(f"⚠️ Could not get element for '{field_name}'")
                    continue

                # Clear field first
                try:
                    await element.clear()
                    await asyncio.sleep(0.2)
                except:
                    pass

                # Fill with corrected value (or leave empty if corrected_value is "")
                if corrected_value:
                    field_data = {
                        'element': element,
                        'label': field_name,
                        'field_category': field_def.get('field_category', 'text_input'),
                        'stable_id': field_def.get('stable_id', '')
                    }

                    fill_result = await self.interactor.fill_field(field_data, corrected_value, profile)

                    if fill_result['success']:
                        filled_fields[field_name] = corrected_value
                        corrections_made += 1
                        logger.info(f"✅ Corrected '{field_name}' successfully")
                    else:
                        logger.warning(f"⚠️ Failed to correct '{field_name}'")
                else:
                    # Empty the field (for duplicates/errors)
                    filled_fields[field_name] = ""
                    corrections_made += 1
                    logger.info(f"✅ Cleared '{field_name}' successfully")

            return corrections_made

        except Exception as e:
            logger.error(f"Error correcting Gemini issues: {e}")
            return 0

    async def _final_gemini_review(
        self,
        filled_fields: Dict[str, str],
        profile: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Final Gemini review of all filled fields before submission.
        Returns: {"approved": bool, "issues": List[str]}
        """
        try:
            from google import genai
            import os

            client = genai.Client(api_key=os.getenv('GOOGLE_API_KEY'))

            # Create review prompt
            filled_list = "\n".join([f"- {label}: {value}" for label, value in filled_fields.items()])

            # Use the comprehensive profile context from field mapper (same as AI gets during field filling)
            from components.brains.gemini_field_mapper import GeminiFieldMapper
            field_mapper = GeminiFieldMapper()
            comprehensive_profile_context = field_mapper._create_profile_context(profile, context_type="final_review")

            prompt = f"""
You are reviewing a job application form that has been filled out. Please verify that the inputs make sense and are appropriate.

{comprehensive_profile_context}

Filled Fields:
{filled_list}

Review Requirements:
1. Check if field values match the profile data
2. Look for any obvious errors or inconsistencies
3. Verify that dropdown selections make sense
4. Check that required information is present

Respond in JSON format:
{{
  "approved": true/false,
  "issues": ["issue1", "issue2", ...],
  "confidence": 0.0-1.0
}}

If everything looks good, set approved=true with empty issues list.
If there are problems, set approved=false and list specific issues.
"""

            response = client.models.generate_content(
                model="gemini-2.0-flash",
                contents=prompt,
                config={"response_mime_type": "application/json"}
            )

            import json
            review_result = json.loads(response.text)

            if review_result.get("approved"):
                logger.info(f"✅ Gemini approved form with confidence: {review_result.get('confidence', 0)}")
            else:
                logger.warning(f"⚠️ Gemini flagged issues: {review_result.get('issues', [])}")

            return review_result

        except Exception as e:
            logger.error(f"Error in Gemini review: {e}")
            # Default to approved if review fails
            return {"approved": True, "issues": [], "confidence": 0.5}

    def _filter_unfilled_fields(self, fields: List[Dict[str, Any]]) -> List[Dict]:
        """Filter out fields that are already completed."""
        unfilled = []

        for field in fields:
            field_id = self._get_field_id(field)
            field_label = field.get('label', 'Unknown')

            # Check if already completed
            if self.completion_tracker.should_skip_field(field_id, field_label):
                continue

            # Check if pre-filled
            if field.get('is_filled'):
                continue

            unfilled.append(field)

        return unfilled

    async def _get_fresh_element(self, field: Dict[str, Any]) -> Optional[Any]:
        """Get fresh element reference using stable_id."""
        stable_id = field.get('stable_id', '')

        if not stable_id:
            return field.get('element')

        try:
            if stable_id.startswith('id:'):
                element_id = stable_id[3:]
                # Use attribute selector to handle IDs with special characters (dots, colons, etc.)
                return self.page.locator(f'[id="{element_id}"]').first
            elif stable_id.startswith('name:'):
                name = stable_id[5:]
                return self.page.locator(f'[name="{name}"]').first
            elif stable_id.startswith('aria_label:'):
                aria_label = stable_id[11:]
                return self.page.locator(f'[aria-label="{aria_label}"]').first
            else:
                return field.get('element')
        except Exception as e:
            logger.debug(f"Error getting fresh element: {e}")
            return field.get('element')

    def _get_field_id(self, field: Dict[str, Any]) -> str:
        """Get unique field identifier."""
        return field.get('stable_id') or field.get('id') or field.get('name') or f"field_{hash(field.get('label', ''))}"

    def _merge_cached_options(self, current_fields: List[Dict], cached_fields: List[Dict]) -> List[Dict]:
        """Merge cached dropdown options into current field detection."""
        # Build lookup of cached options by stable_id
        cached_options = {}
        for cached in cached_fields:
            field_id = self._get_field_id(cached)
            if cached.get('options'):
                cached_options[field_id] = cached['options']

        # Merge options into current fields
        for field in current_fields:
            field_id = self._get_field_id(field)
            if field_id in cached_options and not field.get('options'):
                field['options'] = cached_options[field_id]

        return current_fields

    async def _try_click_next_button(self) -> bool:
        """
        Try to find and click a Next/Continue button (but never Submit).

        Returns:
            bool: True if a button was clicked, False otherwise
        """
        try:
            logger.info("🔍 Looking for Next/Continue button...")

            # CRITICAL: Patterns that indicate SUBMIT buttons (NEVER click these)
            submit_patterns = [
                r'\bsubmit\b',
                r'\bapply\b',
                r'\bsend\s+application\b',
                r'\bfinish\b',
                r'\bcomplete\s+application\b',
                r'\breview\s+and\s+submit\b',
                r'\bconfirm\s+and\s+submit\b',
            ]

            # Safe patterns for Next/Continue buttons (OK to click)
            next_patterns = [
                r'\bnext\b',
                r'\bcontinue\b',
                r'\bproceed\b',
                r'\bgo\s+to\s+next\b',
                r'\bsave\s+and\s+continue\b',
                r'\bsave\s+and\s+next\b',
                r'\bsave\s*&\s*continue\b',  # "Save & Continue"
                r'\bsave\s*&\s*next\b',  # "Save & Next"
                r'\barrow\s+right\b',
                r'^>\s*$',  # Just an arrow
                r'^→\s*$',  # Unicode arrow
                r'\bnext\s+step\b',
                r'\bnext\s+page\b',
            ]

            # Find all buttons on the page
            all_buttons = await self.page.locator('button, input[type="button"], input[type="submit"], a[role="button"]').all()

            logger.debug(f"Found {len(all_buttons)} total buttons on the page")
            found_buttons = []  # Track all visible buttons for debugging

            for button in all_buttons:
                try:
                    # Check if button is visible
                    if not await button.is_visible():
                        continue

                    # Get button text/label
                    button_text = await button.text_content() or ""
                    aria_label = await button.get_attribute('aria-label') or ""
                    button_type = await button.get_attribute('type') or ""
                    combined_text = f"{button_text} {aria_label}".lower().strip()

                    if not combined_text:
                        continue

                    # Track visible buttons for debugging
                    found_buttons.append(combined_text)

                    # SAFETY CHECK: Never click submit buttons
                    is_submit = any(re.search(pattern, combined_text, re.IGNORECASE)
                                   for pattern in submit_patterns)

                    if is_submit:
                        logger.debug(f"⛔ Skipping SUBMIT button: '{combined_text}'")
                        continue

                    # Check if it's a Next/Continue button
                    is_next = any(re.search(pattern, combined_text, re.IGNORECASE)
                                 for pattern in next_patterns)

                    if is_next:
                        logger.info(f"✅ Found Next/Continue button: '{combined_text}'")

                        # Click the button
                        await button.click(timeout=5000)
                        logger.info(f"🎯 Clicked Next/Continue button successfully")

                        # Wait for page to load
                        await self.page.wait_for_timeout(2000)

                        return True

                except Exception as e:
                    logger.debug(f"Error checking button: {e}")
                    continue

            # Log all found buttons for debugging
            if found_buttons:
                logger.info(f"ℹ️ No Next/Continue button found. Visible buttons on page: {found_buttons[:10]}")
            else:
                logger.info("ℹ️ No visible buttons found on this page")
            return False

        except Exception as e:
            logger.error(f"❌ Error looking for Next button: {e}")
            return False

    async def _final_gemini_checkpoint(
        self,
        unfilled_fields: List[Dict[str, Any]],
        skipped_fields: List[Dict[str, str]],
        visible_buttons: List[str],
        profile: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Final checkpoint before giving up - ask Gemini if there's anything we can still do.

        Args:
            unfilled_fields: Fields that weren't filled
            skipped_fields: Fields that were skipped with reasons
            visible_buttons: List of visible button texts on page
            profile: User profile data

        Returns:
            {
                "can_progress": bool,
                "confidence": float,
                "instructions": {...},
                "green_signal": bool
            }
        """
        try:
            import google.generativeai as genai
            import base64

            logger.info("🔍 Final Gemini checkpoint - analyzing if we can progress further...")

            # Take screenshot for visual analysis
            # Handle both Page and Frame objects
            try:
                if hasattr(self.page, 'screenshot'):
                    screenshot = await self.page.screenshot()
                else:
                    # If it's a frame, get the page from it
                    page = self.page.page
                    screenshot = await page.screenshot()
                screenshot_b64 = base64.b64encode(screenshot).decode()
            except Exception as e:
                logger.warning(f"Could not take screenshot: {e}")
                screenshot_b64 = None

            # Prepare unfilled fields summary
            unfilled_summary = []
            for field in unfilled_fields[:10]:  # Limit to 10 for token efficiency
                unfilled_summary.append({
                    "label": field.get("label", "Unknown"),
                    "category": field.get("field_category", "unknown"),
                    "stable_id": field.get("stable_id", "")
                })

            # Prepare context
            from components.brains.gemini_field_mapper import GeminiFieldMapper
            field_mapper = GeminiFieldMapper()
            profile_context = field_mapper._create_profile_context(profile, context_type="final_checkpoint")

            unfilled_text = "\n".join([f"- {f['label']} ({f['category']})" for f in unfilled_summary]) if unfilled_summary else "None"
            skipped_text = "\n".join([f"- {s['field']}: {s['reason']}" for s in skipped_fields[:10]]) if skipped_fields else "None"
            buttons_text = "\n".join([f"- {btn}" for btn in visible_buttons[:15]]) if visible_buttons else "None"

            prompt = f"""
You are a final checkpoint analyzer for a job application form filling agent. The agent is about to give up, but first you need to determine if there's ANYTHING we can still do to progress.

{profile_context}

CURRENT SITUATION:
Unfilled Fields:
{unfilled_text}

Skipped Fields:
{skipped_text}

Visible Buttons on Page:
{buttons_text}

CRITICAL QUESTIONS:
1. Can ANY of the unfilled/skipped fields be filled using the profile data?
   - Look for phone numbers, names, emails, addresses, etc. that match profile
   - Can we infer values? (e.g., "Are you 18+?" → Yes if has work experience)

2. Is there a button we should click to progress?
   - Look for buttons that might continue the form (even if not labeled "Next")
   - Could "Review", "Save", or other buttons move us forward?
   - NEVER suggest clicking Submit/Apply/Finish buttons

3. Should we wait for dynamic content to load?

4. Or should we give GREEN SIGNAL (truly nothing more can be done)?

Respond in JSON format:
{{
  "can_progress": true/false,
  "confidence": 0.0-1.0,
  "green_signal": true/false,
  "instructions": {{
    "action": "fill_field" | "click_button" | "wait" | "stop",
    "details": {{
      "field_label": "field to fill (if action=fill_field)",
      "value": "value to use from profile (if action=fill_field)",
      "button_text": "button to click (if action=click_button)",
      "wait_ms": 3000,
      "reasoning": "why this action will help progress"
    }}
  }}
}}

If GREEN SIGNAL (nothing can be done), set:
- can_progress: false
- green_signal: true
- action: "stop"
"""

            model = genai.GenerativeModel("gemini-2.0-flash")
            response = model.generate_content([
                prompt,
                {
                    "mime_type": "image/png",
                    "data": screenshot_b64
                }
            ])

            # Parse response
            import json
            response_text = response.text

            # Extract JSON from markdown code blocks if present
            json_match = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', response_text, re.DOTALL)
            if json_match:
                response_text = json_match.group(1)

            result = json.loads(response_text)

            if result.get("green_signal"):
                logger.info("✅ Gemini GREEN SIGNAL: No more actions possible, safe to stop")
            elif result.get("can_progress"):
                logger.info(f"🚀 Gemini suggests we can progress: {result.get('instructions', {}).get('details', {}).get('reasoning', 'No reason provided')}")
            else:
                logger.warning("⚠️ Gemini uncertain - defaulting to stop")

            return result

        except Exception as e:
            logger.error(f"❌ Final checkpoint failed: {e}")
            # Default to green signal if checkpoint fails
            return {
                "can_progress": False,
                "confidence": 0.0,
                "green_signal": True,
                "instructions": {"action": "stop", "details": {"reasoning": "Checkpoint failed, defaulting to stop"}}
            }
