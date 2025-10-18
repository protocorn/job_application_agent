"""
Enhanced Generic Form Filler V2 with:
- Single-attempt strategy per field (deterministic → AI → skip)
- Field validation after each attempt
- Improved field detection (skip invalid elements)
- Better Greenhouse dropdown matching
- Final Gemini review before submission
"""
import asyncio
from typing import Any, Dict, List, Optional, Tuple, Set
from playwright.async_api import Page, Frame
from loguru import logger

from components.executors.field_interactor_v2 import FieldInteractorV2
from components.executors.deterministic_field_mapper import DeterministicFieldMapper, FieldMappingConfidence
from components.brains.gemini_field_mapper import GeminiFieldMapper
from components.exceptions.field_exceptions import RequiresHumanInputError
from components.state.field_completion_tracker import FieldCompletionTracker


class FieldAttemptTracker:
    """Tracks which methods have been attempted for each field."""

    def __init__(self):
        self.attempts: Dict[str, Set[str]] = {}  # field_id -> {method names}

    def has_attempted(self, field_id: str, method: str) -> bool:
        """Check if method was already attempted for this field."""
        return method in self.attempts.get(field_id, set())

    def mark_attempted(self, field_id: str, method: str):
        """Mark that method was attempted for this field."""
        if field_id not in self.attempts:
            self.attempts[field_id] = set()
        self.attempts[field_id].add(method)

    def get_next_method(self, field_id: str) -> Optional[str]:
        """Get next method to try for this field."""
        attempted = self.attempts.get(field_id, set())

        # Strategy order: deterministic → AI → skip
        if 'deterministic' not in attempted:
            return 'deterministic'
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

    def __init__(self, page: Page | Frame, action_recorder=None):
        self.page = page
        self.action_recorder = action_recorder
        self.interactor = FieldInteractorV2(page, action_recorder)
        self.deterministic_mapper = DeterministicFieldMapper()
        self.ai_mapper = GeminiFieldMapper()
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
            "fields_by_method": {"deterministic": 0, "ai": 0},
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

            # Step 2: Clean fields (remove invalid ones)
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
        logger.info(f"📊 Methods used: {result['fields_by_method']['deterministic']} deterministic, {result['fields_by_method']['ai']} AI")
        logger.info(f"⏭️ Skipped {len(result['skipped_fields'])} fields after all attempts")

        return result

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
        Phase 2: Collect fields needing AI help
        Phase 3: Make ONE batch Gemini call for all AI fields
        Phase 4: Apply all AI responses
        """
        filled_count = 0
        
        # PHASE 1: Try deterministic on all fields first
        logger.info("📋 Phase 1: Attempting deterministic mapping for all fields...")
        fields_needing_ai = []
        
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
                    # Deterministic failed - add to AI batch
                    if not self.attempt_tracker.has_attempted(field_id, 'ai'):
                        fields_needing_ai.append(field)
            
            # If AI is next method, add to batch
            elif next_method == 'ai':
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

            # Prepare field data (no pre-extracted options in fast mode)
            field_data = {
                'element': element,
                'label': field_label,
                'field_category': field.get('field_category', 'text_input'),
                'stable_id': field.get('stable_id', '')
            }

            # Fill the field
            fill_result = await self.interactor.fill_field(field_data, mapping.value, profile)

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
                
                # Mark AI as attempted
                self.attempt_tracker.mark_attempted(field_id, 'ai')
                
                if field_id not in ai_mappings:
                    logger.debug(f"⏭️ No AI mapping for '{field_label}'")
                    result['skipped_fields'].append({
                        "field": field_label,
                        "reason": "AI did not provide mapping"
                    })
                    continue
                
                mapping_data = ai_mappings[field_id]
                mapping_type = mapping_data.get('type', 'simple')
                
                # Check if needs human input
                if mapping_type == 'needs_human_input':
                    result["requires_human"].append({
                        "field": field_label,
                        "reason": mapping_data.get('reason', 'AI determined needs human input')
                    })
                    continue
                
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
                
                # Fill the field
                fill_result = await self.interactor.fill_field(field_data, value, profile)
                
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
                return self.page.locator(f'#{element_id}').first
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
