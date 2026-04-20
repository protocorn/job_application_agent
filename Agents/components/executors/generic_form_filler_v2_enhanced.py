"""
Enhanced Generic Form Filler V2 with:
- Single-attempt strategy per field (deterministic → AI → skip)
- Field validation after each attempt
- Improved field detection (skip invalid elements)
- Better Greenhouse dropdown matching
- Final Gemini review before submission
"""
import asyncio
import os
import re
from typing import Any, Dict, List, Optional, Set
from playwright.async_api import Page, Frame
from loguru import logger

from components.executors.field_interactor_v2 import FieldInteractorV2
from components.executors.deterministic_field_mapper import DeterministicFieldMapper
from components.executors.learned_patterns_mapper import LearnedPatternsMapper
from components.executors.semantic_field_mapper import SemanticFieldMapper
from components.brains.gemini_field_mapper import GeminiFieldMapper
from components.exceptions.field_exceptions import RequiresHumanInputError
from components.state.field_completion_tracker import FieldCompletionTracker
from components.validators.field_value_validator import FieldValueValidator
from components.pattern_recorder import PatternRecorder
from components.user_pattern_recorder import UserPatternRecorder


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
    _CACHED_OVERRIDE_ALLOWED_CATEGORIES = {
        "text_input",
        "textarea",
        "dropdown",
        "selection",
        "radio_group",
        "checkbox_group",
    }

    def __init__(self, page: Page | Frame, action_recorder=None, user_id=None, full_auto_mode: bool = False):
        self.page = page
        self.action_recorder = action_recorder
        self.user_id = user_id
        self.interactor = FieldInteractorV2(page, action_recorder)
        self.deterministic_mapper = DeterministicFieldMapper()
        self.learned_mapper = LearnedPatternsMapper(user_id=str(user_id) if user_id else None)  # Tier 2 - Learned patterns (user overrides first)
        self.semantic_mapper = SemanticFieldMapper()        # Tier 3 - Local semantic similarity (before Gemini)
        self.ai_mapper = GeminiFieldMapper()
        self.pattern_recorder = PatternRecorder()        # Records AI successes → global table
        self.user_pattern_recorder = UserPatternRecorder()  # Records human fills → per-user table

        # Pre-load successful DB labels as extra anchors for the semantic mapper.
        # This means "First Name" (seen in the DB) becomes part of the embedding index
        # for first_name, so novel phrasings are compared against real-world examples.
        self._load_db_anchors_into_semantic_mapper()

        # Load user-specific cached answers for Gemini context enrichment.
        # These are values the user previously typed that are NOT in the standard profile
        # (e.g. "Supervisor Name", "Reason for Leaving", "Secondary Citizenship").
        # Passed to Gemini so it can fill similar fields on future forms.
        self._user_gemini_context: Dict[str, str] = self._load_user_gemini_context()

        self.completion_tracker = FieldCompletionTracker()
        self.attempt_tracker = FieldAttemptTracker()
        self.pre_filled_values = {}  # Track original values of fields before agent touches them
        self.gemini_flagged_fields = set()  # Track fields flagged as incorrect by Gemini reviewer
        self.full_auto_mode = full_auto_mode  # 100% auto-apply: no human input prompts

        # AI-fill lock: once AI fills a field label on a page, it is NEVER sent to AI again.
        # Keyed by base page URL → set of lowercase-stripped field labels.
        # More stable than stable_id which can change after DOM re-renders (React, Workday, etc.)
        self._ai_filled_labels: dict = {}  # {page_key: {normalized_label, ...}}

        # Failure-exhaustion lock: after MAX_FIELD_FAILURES consecutive failures for the
        # same field label+category, we stop retrying it (it most likely needs human input).
        self._field_failure_counts: dict = {}  # {page_key: {fingerprint: int}}
        self._MAX_FIELD_FAILURES = 3

    # ── Semantic mapper: DB anchor loading ────────────────────────────────

    def _load_db_anchors_into_semantic_mapper(self) -> None:
        """
        Load successful field_label_raw values from the DB and add them as
        extra anchors for the semantic mapper.  This runs synchronously at
        startup (one small SQL query) and is fast (<50 ms).

        Effect: the semantic model's embedding index now includes all real-world
        label phrasings the system has already seen, making future fuzzy matches
        more accurate without any DB lookup at fill-time.
        """
        if not SemanticFieldMapper.is_available():
            return
        try:
            from sqlalchemy import create_engine, text as sa_text
            from urllib.parse import quote_plus
            import os

            DB_HOST     = os.getenv('DB_HOST', 'localhost')
            DB_PORT     = os.getenv('DB_PORT', '5432')
            DB_NAME     = os.getenv('DB_NAME', 'job_agent_db')
            DB_USER     = os.getenv('DB_USER', 'postgres')
            DB_PASSWORD = os.getenv('DB_PASSWORD', '')
            encoded_pw  = quote_plus(DB_PASSWORD)
            url = f"postgresql://{DB_USER}:{encoded_pw}@{DB_HOST}:{DB_PORT}/{DB_NAME}"

            engine = create_engine(url, pool_pre_ping=True)
            with engine.connect() as conn:
                rows = conn.execute(sa_text("""
                    SELECT profile_field, field_label_raw
                    FROM field_label_patterns
                    WHERE confidence_score >= 0.70
                      AND occurrence_count >= 2
                      AND profile_field IS NOT NULL
                      AND field_label_raw IS NOT NULL
                    ORDER BY occurrence_count DESC
                    LIMIT 500
                """)).fetchall()

            db_anchors: Dict[str, List[str]] = {}
            for profile_field, label_raw in rows:
                db_anchors.setdefault(profile_field, []).append(label_raw)

            if db_anchors:
                self.semantic_mapper.add_db_anchors(db_anchors)
                logger.info(
                    f"SemanticFieldMapper: Pre-loaded DB anchors for "
                    f"{len(db_anchors)} profile fields."
                )

        except Exception as e:
            logger.debug(f"SemanticFieldMapper: Could not load DB anchors: {e}")

    def _load_user_gemini_context(self) -> Dict[str, Any]:
        """
        Load ALL user_field_overrides entries into an in-memory pool.

        This includes BOTH:
          - profile_field IS NULL  → unmappable, always needs Gemini context
          - profile_field IS NOT NULL → mapped by profiler, but may be absent
            from the user's actual profile (orphaned after backfill).  Detected
            and included at call-time when the profile is available.

        Pool format: {field_label_raw: {'value': str, 'profile_field': str|None}}

        At call-time, _select_relevant_user_context(fields, profile) excludes
        entries where get_profile_value(profile, profile_field) returns a value
        — those are handled directly by _try_learned_pattern and don't need
        to be re-sent to Gemini.
        """
        if not self.user_id:
            return {}

        try:
            from sqlalchemy import create_engine, text as sa_text
            from urllib.parse import quote_plus

            DB_HOST     = os.getenv('DB_HOST', 'localhost')
            DB_PORT     = os.getenv('DB_PORT', '5432')
            DB_NAME     = os.getenv('DB_NAME', 'job_agent_db')
            DB_USER     = os.getenv('DB_USER', 'postgres')
            DB_PASSWORD = os.getenv('DB_PASSWORD', '')
            encoded_pw  = quote_plus(DB_PASSWORD)
            url = f"postgresql://{DB_USER}:{encoded_pw}@{DB_HOST}:{DB_PORT}/{DB_NAME}"

            engine = create_engine(url, pool_pre_ping=True)
            with engine.connect() as conn:
                rows = conn.execute(sa_text("""
                    SELECT field_label_raw, field_value_cached, profile_field
                    FROM user_field_overrides
                    WHERE user_id              = :uid
                      AND field_value_cached   IS NOT NULL
                      AND field_value_cached   <> ''
                      AND field_category       NOT IN ('file_upload')
                      AND confidence_score     >= 0.80
                    ORDER BY occurrence_count DESC, confidence_score DESC
                    LIMIT 200
                """), {'uid': str(self.user_id)}).fetchall()

            pool = {
                row[0]: {'value': row[1], 'profile_field': row[2]}
                for row in rows
            }
            if pool:
                null_count    = sum(1 for v in pool.values() if v['profile_field'] is None)
                mapped_count  = len(pool) - null_count
                logger.info(
                    f"GeminiContextLoader: Pool loaded — {len(pool)} entries "
                    f"({null_count} unmapped, {mapped_count} profile-mapped) "
                    f"for user {self.user_id}"
                )
            return pool

        except Exception as e:
            logger.debug(f"GeminiContextLoader: Could not load user context: {e}")
            return {}

    # SIMILARITY THRESHOLD: how semantically close a context entry must be to
    # at least one field in the current batch to be included in the prompt.
    # 0.55 is intentionally lower than the field-mapping threshold (0.72) because
    # here we want recall (don't miss a useful hint) not precision (don't fill wrong).
    _CONTEXT_SIMILARITY_THRESHOLD = 0.55

    # Maximum context entries per Gemini call — keeps prompt size bounded.
    _MAX_CONTEXT_ENTRIES = 15

    def _select_relevant_user_context(
        self,
        fields: List[Dict[str, Any]],
        profile: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, str]:
        """
        From the full user-context pool, return only the entries that Gemini
        actually needs — i.e. entries where the profile cannot supply the value.

        Step 1 — Profile exclusion (Flaw 1 fix)
        ----------------------------------------
        For every pool entry that has a profile_field set, check whether the
        current profile already has a value for that field.  If it does,
        _try_learned_pattern will fill it directly — no need to send it to
        Gemini as a hint.  If the profile is missing the value (orphaned after
        profiler backfill), include it in the context so Gemini can use it.

        Step 2 — Semantic relevance filter
        ------------------------------------
        If the surviving pool is still larger than _MAX_CONTEXT_ENTRIES, use
        the already-loaded sentence-embedding model to keep only the entries
        whose label is semantically close to at least one field in this batch.

        Args:
            fields:  The list of form fields being sent to Gemini this batch.
            profile: The user's profile dict for this run (used in Step 1).

        Returns:
            Filtered {label: value} dict for the Gemini prompt.
        """
        raw_pool = self._user_gemini_context
        if not raw_pool:
            return {}

        # ── Step 1: exclude entries the profile can already satisfy ──────────
        pool: Dict[str, str] = {}
        for label, entry in raw_pool.items():
            pf    = entry.get('profile_field')
            value = entry.get('value', '')

            if pf and profile:
                profile_val = self.learned_mapper.get_profile_value(profile, pf)
                if profile_val:
                    # Profile has this value → _try_learned_pattern handles it,
                    # no need to include in Gemini context.
                    continue
                # Profile does NOT have the value → orphaned entry, include it.
                pool[label] = value
            else:
                # profile_field is None → always include (unmappable field)
                pool[label] = value

        if not pool:
            return {}

        # ── Step 2: small pool → send as-is ──────────────────────────────────
        if len(pool) <= self._MAX_CONTEXT_ENTRIES:
            return pool

        # ── Step 3: semantic relevance filter ────────────────────────────────
        try:
            import numpy as np
            from components.executors.semantic_field_mapper import SemanticFieldMapper

            if not SemanticFieldMapper.is_available() or SemanticFieldMapper._model is None:
                return dict(list(pool.items())[:self._MAX_CONTEXT_ENTRIES])

            model = SemanticFieldMapper._model

            field_labels   = [f.get('label', '') for f in fields if f.get('label')]
            context_labels = list(pool.keys())

            if not field_labels:
                return dict(list(pool.items())[:self._MAX_CONTEXT_ENTRIES])

            all_labels = field_labels + context_labels
            embeddings = model.encode(
                all_labels,
                normalize_embeddings=True,
                show_progress_bar=False,
            )

            field_vecs   = embeddings[:len(field_labels)]
            context_vecs = embeddings[len(field_labels):]

            sim_matrix = context_vecs @ field_vecs.T
            max_sims   = sim_matrix.max(axis=1)

            scored = sorted(
                [(context_labels[i], float(max_sims[i]))
                 for i in range(len(context_labels))
                 if max_sims[i] >= self._CONTEXT_SIMILARITY_THRESHOLD],
                key=lambda x: x[1],
                reverse=True,
            )

            selected = {label: pool[label] for label, _ in scored[:self._MAX_CONTEXT_ENTRIES]}

            logger.debug(
                f"GeminiContextLoader: {len(raw_pool)} pool → "
                f"{len(pool)} after profile exclusion → "
                f"{len(selected)} after semantic filter"
            )
            return selected

        except Exception as e:
            logger.debug(f"GeminiContextLoader: Semantic filter failed ({e}), using top-N")
            return dict(list(pool.items())[:self._MAX_CONTEXT_ENTRIES])

    # ── AI-fill label lock helpers ─────────────────────────────────────────

    def _page_key(self) -> str:
        """Stable page identifier - strip query params so Workday-style URLs are consistent."""
        url = self.page.url
        return url.split("?")[0] if "?" in url else url

    def _lock_ai_filled(self, field_label: str, field_category: str = "") -> None:
        """Permanently record that AI has filled this field on the current page.

        Uses a composite fingerprint of ``label|category`` so two fields that happen
        to share the same label but are of different types (e.g. a text input and a
        dropdown both labelled "State") are tracked independently.
        """
        key = self._page_key()
        if key not in self._ai_filled_labels:
            self._ai_filled_labels[key] = set()
        fingerprint = f"{field_label.lower().strip()}|{field_category.lower().strip()}"
        self._ai_filled_labels[key].add(fingerprint)
        logger.info(f"🔒 AI-fill lock set: '{field_label}' [{field_category}] - will not be re-filled on this page")

    def _is_locked_by_ai(self, field_label: str, field_category: str = "") -> bool:
        """Return True if AI has already filled this field on the current page."""
        fingerprint = f"{field_label.lower().strip()}|{field_category.lower().strip()}"
        return fingerprint in self._ai_filled_labels.get(self._page_key(), set())

    # ── Failure-exhaustion lock helpers ────────────────────────────────────

    def _record_field_failure(self, field_label: str, field_category: str = "") -> int:
        """Increment failure count for this field. Returns the new count."""
        key = self._page_key()
        if key not in self._field_failure_counts:
            self._field_failure_counts[key] = {}
        fingerprint = f"{field_label.lower().strip()}|{field_category.lower().strip()}"
        self._field_failure_counts[key][fingerprint] = (
            self._field_failure_counts[key].get(fingerprint, 0) + 1
        )
        count = self._field_failure_counts[key][fingerprint]
        if count >= self._MAX_FIELD_FAILURES:
            logger.warning(
                f"🚫 Field '{field_label}' [{field_category}] exhausted after "
                f"{count} failures - will not retry"
            )
        return count

    def _is_exhausted(self, field_label: str, field_category: str = "") -> bool:
        """Return True if this field has failed too many times and should be skipped."""
        fingerprint = f"{field_label.lower().strip()}|{field_category.lower().strip()}"
        count = self._field_failure_counts.get(self._page_key(), {}).get(fingerprint, 0)
        return count >= self._MAX_FIELD_FAILURES

    # ── main fill loop ────────────────────────────────────────────────────

    async def fill_form(self, profile: Dict[str, Any]) -> Dict[str, Any]:
        """
        Fill form with enhanced strategy and validation.
        """
        logger.info("🚀 Starting enhanced form filling with single-attempt strategy...")

        # Start the debug reporter for this fill_form call
        try:
            import fill_debug_reporter as _fdr
            _reporter = _fdr.start_report()
        except Exception:
            _reporter = None

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
                    print(f"  Uploading resume: {os.path.basename(resume_path)}...")
                    logger.info(f"📄 Attempting resume upload: {resume_path}")
                    upload_success = await self.interactor.upload_resume_if_present(resume_path)
                    if upload_success:
                        print("  ✓ Resume uploaded successfully")
                        logger.info("✅ Resume uploaded successfully")
                    else:
                        print("  [WARN] Resume upload: no file-upload control found on this page")
                        logger.warning("⚠️ Resume upload skipped - no matching upload control found on this page")

            # Step 1: Detect fields (NO option extraction - fill immediately!)
            all_fields = await self.interactor.get_all_form_fields(extract_options=False)
            last_detected_fields = all_fields  # Save for correction mechanism
            logger.info(f"🔍 Detected {len(all_fields)} fields (fast mode - no pre-extraction)")

            # Step 1.5: Capture pre-filled values on FIRST iteration only
            if iteration == 0:
                await self._capture_pre_filled_values(all_fields)

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

        # Step 6: Lightweight heuristic cross-check (no Gemini call)
        # The Gemini review + correction cycle was replaced by a capture-filter
        # call in HumanFillTracker.  Here we do a fast, zero-cost sanity check
        # to catch the most common fill mistake: a URL value in a non-URL field
        # or a plain-text value in a URL field.
        if result["filled_fields"]:
            self._heuristic_fill_check(result)

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

        # Step 7.5: FINAL RE-SCAN for newly appeared conditional fields
        logger.info("🔄 Performing final scan for any new/conditional fields...")
        new_fields_filled = await self._final_rescan_and_fill(profile, result)
        if new_fields_filled > 0:
            logger.info(f"✅ Filled {new_fields_filled} additional fields from final re-scan")

        # Step 7.7: Handle legal disclaimer / terms & conditions checkboxes
        # (custom KnockoutJS span-based and native checkbox fallback)
        legal_handled = await self.handle_legal_disclaimer_checkboxes()
        if legal_handled > 0:
            logger.info(f"✅ Handled {legal_handled} legal disclaimer checkbox(es)")
            result["legal_disclaimer_handled"] = legal_handled

        # Step 8: Look for Next/Continue button and click it (but never Submit)
        next_button_clicked = await self._try_click_next_button()
        result["next_button_clicked"] = next_button_clicked

        # ── Debug report ──────────────────────────────────────────────────
        try:
            import fill_debug_reporter as _fdr
            rptr = _fdr.get_reporter()
            if rptr:
                # Stamp human-intervention fields
                for fh in result.get("requires_human", []):
                    sid = fh.get("stable_id", "") or fh.get("field", "")
                    rptr.record_human(sid, fh.get("field", sid), "")
                # Stamp skipped fields
                for sf in result.get("skipped_fields", []):
                    sid = sf.get("stable_id", "") or sf.get("field", "")
                    rptr.record_skip(sid, sf.get("field", sid), "",
                                     sf.get("reason", ""))
                debug_path = _fdr.finish_report(current_url)
                if debug_path:
                    logger.info(f"📋 Fill debug report saved: {debug_path}")
        except Exception:
            pass

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
                    radio_id = radio_field.get('id', '')
                    radio_value = radio_field.get('name', '')

                    # Priority order for option label:
                    # 1. explicit option_label key (set by some detectors)
                    # 2. label key - for Ashby/standard radios this IS the option text
                    #    (e.g. "Male", "Female") now that Method 1 label lookup is fixed
                    # 3. available_options list (legacy path)
                    option_label = radio_field.get('option_label', '').strip()

                    if not option_label:
                        # Fallback: use the field's 'label' if it looks like an option text
                        # (i.e. it is NOT just a raw GUID/name attribute value)
                        raw_label = radio_field.get('label', '').strip()
                        if raw_label and raw_label != radio_value:
                            option_label = raw_label

                    if not option_label:
                        # Last resort: scan available_options by radio id
                        for opt in radio_field.get('available_options', []):
                            if isinstance(opt, dict) and opt.get('id') == radio_id:
                                option_label = opt.get('text', '')
                                break

                    if not option_label:
                        logger.warning(f"⚠️  Could not determine option label for radio button ID={radio_id}")

                    # Avoid duplicates
                    if option_label and option_label not in seen_option_texts:
                        all_options.append({
                            'text': option_label,
                            'value': radio_value,
                            'id': radio_id,
                            'element': radio_field.get('element')
                        })
                        seen_option_texts.add(option_label)
                
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

    async def _capture_pre_filled_values(self, fields: List[Dict[str, Any]]) -> None:
        """
        Capture the current values of all fields before the agent starts filling them.
        This allows us to:
        1. Skip fields that already have values (unless Gemini flags them)
        2. Restore original values if correction attempts fail
        """
        logger.debug(f"📸 Capturing pre-filled values for {len(fields)} fields...")
        captured_count = 0
        
        for field in fields:
            try:
                field_label = field.get('label', 'Unknown')
                field_id = field.get('stable_id', field_label)
                field_category = field.get('field_category', 'text_input')
                element = field.get('element')
                
                if not element:
                    continue
                
                # Get current value based on field type
                current_value = None
                
                if field_category in ['checkbox', 'radio']:
                    try:
                        is_checked = await element.is_checked()
                        if is_checked:
                            current_value = "checked"
                    except:
                        pass
                        
                elif field_category == 'greenhouse_dropdown':
                    # Check Greenhouse dropdown display value
                    try:
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
                                    text = await display_element.text_content(timeout=500)
                                    if text and text.strip() and 'select' not in text.lower():
                                        current_value = text.strip()
                                        break
                            except:
                                continue
                    except:
                        pass
                        
                else:
                    # Standard text inputs, textareas, other dropdowns
                    try:
                        current_value = await element.input_value(timeout=500)
                    except:
                        pass
                
                # Store if field has a value
                if current_value and str(current_value).strip():
                    self.pre_filled_values[field_id] = {
                        'label': field_label,
                        'value': current_value,
                        'category': field_category
                    }
                    captured_count += 1
                    logger.debug(f"  ✓ Captured pre-filled '{field_label}' = '{current_value[:50]}...'")
                    
            except Exception as e:
                logger.debug(f"  ✗ Error capturing value for '{field.get('label', 'Unknown')}': {e}")
                continue
        
        if captured_count > 0:
            logger.info(f"📸 Captured {captured_count} pre-filled field values")

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

        # PHASE 1.5: Per-user override lookup (fast single-row DB query per field)
        # These are human-filled / human-corrected values — trusted immediately.
        fields_needing_semantic = []
        if fields_needing_learned:
            logger.info(
                f"🗂️  Phase 1.5: Checking user overrides for "
                f"{len(fields_needing_learned)} fields..."
            )
            for field in fields_needing_learned:
                field_id = self._get_field_id(field)
                success = await self._try_learned_pattern(field, profile, result)
                self.attempt_tracker.mark_attempted(field_id, 'learned_pattern')
                if success:
                    filled_count += 1
                else:
                    fields_needing_semantic.append(field)
        
        # PHASE 1.75: Semantic mapping (local model, ~5 ms/field, no API cost)
        # Handles paraphrases the DB has never seen before.
        # Successful matches are written back to the DB as exact patterns.
        fields_needing_ai = []
        if fields_needing_semantic and SemanticFieldMapper.is_available():
            logger.info(
                f"🔍 Phase 1.75: Semantic mapping for "
                f"{len(fields_needing_semantic)} fields..."
            )
            for field in fields_needing_semantic:
                success = await self._try_semantic(field, profile, result)
                if success:
                    filled_count += 1
                else:
                    fields_needing_ai.append(field)
        else:
            fields_needing_ai = fields_needing_semantic

        # PHASE 1.9: Silently skip optional file upload fields that have no content
        # configured in the user's profile. Sending these to AI results in
        # NEEDS_HUMAN_INPUT which forces human intervention — but cover letters,
        # writing samples, etc. are always optional fields.
        _OPTIONAL_FILE_LABEL_KEYWORDS = {
            'cover letter', 'covering letter', 'letter of interest',
            'writing sample', 'portfolio', 'work sample', 'additional attachment',
        }
        if fields_needing_ai:
            still_needing_ai = []
            for field in fields_needing_ai:
                label_lower = field.get('label', '').lower()
                category = field.get('field_category', '')
                is_optional_file = (
                    category == 'file_upload'
                    and any(kw in label_lower for kw in _OPTIONAL_FILE_LABEL_KEYWORDS)
                )
                if is_optional_file:
                    cover_val = (
                        (profile or {}).get('cover_letter') or
                        (profile or {}).get('cover_letter_path') or
                        (profile or {}).get('cover_letter_text') or ''
                    )
                    if not str(cover_val).strip():
                        logger.info(
                            f"⏭️ Skipping optional file field '{field.get('label')}' "
                            f"— no cover letter configured in profile"
                        )
                        result['skipped_fields'].append({
                            "field": field.get('label', 'Unknown'),
                            "reason": "Optional file field — not configured in profile"
                        })
                        continue
                still_needing_ai.append(field)
            fields_needing_ai = still_needing_ai

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

            _fid  = self._get_field_id(field)
            _flbl = field_label
            _fcat = field.get('field_category', '')

            if fill_result['success']:
                logger.info(f"✅ Deterministic: '{field_label}' = '{cleaned_value}'")
                result["fields_by_method"]["deterministic"] += 1
                result["filled_fields"][field_label] = cleaned_value
                self.completion_tracker.mark_field_completed(_fid, _flbl, cleaned_value)
                # Debug reporter
                try:
                    import fill_debug_reporter as _fdr
                    r = _fdr.get_reporter()
                    if r:
                        r.record_fill(_fid, _flbl, _fcat, "deterministic", cleaned_value,
                                      f"profile_field={mapping.profile_key}")
                except Exception:
                    pass
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
        """
        Try to fill field using learned patterns from database.

        Only fills when profile_field is mapped to a known profile key.
        Entries where profile_field is NULL are intentionally skipped here —
        they fall through to Gemini, which receives them as context hints via
        _user_gemini_context.  This prevents cached answers to job-specific
        questions (e.g. "Relocate to Seattle? → Yes") from being blindly
        reused on a different job posting.

        Enhancement:
        For user overrides with a mapped profile_field, if that profile value is
        currently missing, we can fall back to the user-cached value (when safe).
        This prevents deadlocks where a reusable personal field was learned from
        human input but is absent in Launchway profile data.
        """
        field_label    = field.get('label', 'Unknown')
        field_category = field.get('field_category', 'text_input')

        try:
            learned_pattern = self.learned_mapper.map_field(
                field_label,
                field_category,
                profile
            )

            if not learned_pattern:
                logger.debug(f"⏭️ No learned pattern for '{field_label}'")
                return False

            using_cached_override_fallback = False
            value = None

            # If profile_field is unmapped (NULL), still allow direct reuse of a
            # user override cached value for safe categories. This is essential
            # for assisted auto-apply where users intentionally override fields
            # that are not represented in Launchway profile schema.
            if not learned_pattern.profile_field:
                if (
                    learned_pattern.source == "user_override"
                    and learned_pattern.cached_value
                    and self._can_reuse_cached_override(field_category)
                ):
                    value = learned_pattern.cached_value
                    using_cached_override_fallback = True
                    logger.info(
                        f"♻️ Using cached user override for unmapped field '{field_label}'"
                    )
                else:
                    logger.debug(
                        f"⏭️ User override for '{field_label}' has no profile_field mapping — "
                        f"skipping direct fill, will surface as Gemini context hint"
                    )
                    return False
            else:
                value = self.learned_mapper.get_profile_value(
                    profile, learned_pattern.profile_field
                )

            if not value:
                # If this came from a user override and we have a cached value,
                # use it when the field category is safely reusable.
                if (
                    learned_pattern.source == "user_override"
                    and learned_pattern.cached_value
                    and self._can_reuse_cached_override(field_category)
                ):
                    value = learned_pattern.cached_value
                    using_cached_override_fallback = True
                    logger.info(
                        f"♻️ Using cached user override for '{field_label}' "
                        f"(profile missing '{learned_pattern.profile_field}')"
                    )
                else:
                    logger.debug(
                        f"⏭️ Learned pattern found '{field_label}' → "
                        f"{learned_pattern.profile_field}, but no value in profile"
                    )
                    if learned_pattern.source == "global":
                        await self.pattern_recorder.record_pattern(
                            field_label,
                            learned_pattern.profile_field,
                            field_category,
                            success=False,
                            user_id=self.user_id,
                        )
                    return False

            if not value:
                logger.debug(
                    f"⏭️ Learned pattern found '{field_label}' → "
                    f"{learned_pattern.profile_field}, but no value in profile"
                )
                if learned_pattern.source == "global":
                    await self.pattern_recorder.record_pattern(
                        field_label,
                        learned_pattern.profile_field,
                        field_category,
                        success=False,
                        user_id=self.user_id,
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
                field_category
            )

            # Prepare field data
            field_data = {
                'element': element,
                'label': field_label,
                'field_category': field_category,
                'stable_id': field.get('stable_id', '')
            }

            # Include group data for radio_group and checkbox_group
            if field_category == 'radio_group':
                field_data['individual_radios'] = field.get('individual_radios', [])
            elif field_category == 'checkbox_group':
                field_data['individual_checkboxes'] = field.get('individual_checkboxes', [])

            # Fill the field
            fill_result = await self.interactor.fill_field(field_data, cleaned_value, profile)

            if fill_result['success']:
                if using_cached_override_fallback:
                    source_desc = (
                        f"cached user override "
                        f"(profile missing {learned_pattern.profile_field})"
                    )
                else:
                    source_desc = (
                        f"from {learned_pattern.profile_field}"
                        if learned_pattern.profile_field
                        else "cached human fill"
                    )
                logger.info(
                    f"✅ Learned Pattern: '{field_label}' = '{cleaned_value}' "
                    f"({source_desc}, confidence: {learned_pattern.confidence_score:.2f})"
                )
                result["fields_by_method"]["learned_pattern"] += 1
                result["filled_fields"][field_label] = cleaned_value

                field_id = self._get_field_id(field)
                self.completion_tracker.mark_field_completed(field_id, field_label, cleaned_value)

                # Record successful reuse to boost confidence (only for global patterns)
                if learned_pattern.profile_field and learned_pattern.source == "global":
                    await self.pattern_recorder.record_pattern(
                        field_label,
                        learned_pattern.profile_field,
                        field_category,
                        success=True,
                        user_id=self.user_id,
                    )
                return True
            else:
                logger.debug(f"⏭️ Learned pattern fill failed for '{field_label}'")
                if learned_pattern.profile_field and learned_pattern.source == "global":
                    await self.pattern_recorder.record_pattern(
                        field_label,
                        learned_pattern.profile_field,
                        field_category,
                        success=False,
                        user_id=self.user_id,
                    )
                return False

        except Exception as e:
            logger.error(f"❌ Error in learned pattern attempt for '{field_label}': {e}")
            return False

    def _can_reuse_cached_override(self, field_category: str) -> bool:
        """
        Allow cached user-override fallback only for reusable field categories.
        File uploads and unknown/custom categories should continue through AI/human.
        """
        return (field_category or "").lower().strip() in self._CACHED_OVERRIDE_ALLOWED_CATEGORIES

    async def _try_semantic(
        self,
        field: Dict[str, Any],
        profile: Dict[str, Any],
        result: Dict[str, Any],
    ) -> bool:
        """
        Try to fill field using local sentence-embedding similarity.

        Runs entirely on-device (~5 ms per field after model load).
        If a confident match is found, the mapping is recorded to the global
        DB so it becomes an exact match next time — no model call needed.
        """
        field_label   = field.get('label', 'Unknown')
        field_category = field.get('field_category', 'text_input')

        try:
            match = self.semantic_mapper.map_field(field_label)
            if not match:
                return False

            # Resolve the profile value
            value = self.learned_mapper.get_profile_value(profile, match.profile_field)
            if not value:
                logger.debug(
                    f"🔍 Semantic match '{field_label}' → {match.profile_field} "
                    f"(sim={match.confidence:.2f}), but no profile value"
                )
                return False

            element = await self._get_fresh_element(field)
            if not element:
                return False

            cleaned_value = FieldValueValidator.validate_and_clean(
                value, field_label, field_category
            )

            field_data = {
                'element':       element,
                'label':         field_label,
                'field_category': field_category,
                'stable_id':     field.get('stable_id', ''),
            }
            if field_category == 'radio_group':
                field_data['individual_radios'] = field.get('individual_radios', [])
            elif field_category == 'checkbox_group':
                field_data['individual_checkboxes'] = field.get('individual_checkboxes', [])

            fill_result = await self.interactor.fill_field(field_data, cleaned_value, profile)

            if fill_result['success']:
                logger.info(
                    f"✅ Semantic Match: '{field_label}' = '{cleaned_value}' "
                    f"(→ {match.profile_field}, similarity={match.confidence:.2f})"
                )
                result["fields_by_method"]["semantic"] = (
                    result["fields_by_method"].get("semantic", 0) + 1
                )
                result["filled_fields"][field_label] = cleaned_value

                field_id = self._get_field_id(field)
                self.completion_tracker.mark_field_completed(
                    field_id, field_label, cleaned_value
                )

                # Record to global DB → becomes exact match next run
                await self.pattern_recorder.record_pattern(
                    field_label,
                    match.profile_field,
                    field_category,
                    success=True,
                    user_id=self.user_id,
                )
                return True

            logger.debug(f"⏭️ Semantic fill failed for '{field_label}'")
            return False

        except Exception as e:
            logger.error(f"❌ Error in semantic attempt for '{field_label}': {e}")
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
            # Make ONE batch Gemini call for all fields, injecting only the
            # user-context entries that are relevant to this specific batch.
            relevant_ctx = self._select_relevant_user_context(fields, profile)
            logger.info(f"🧠 Making batch Gemini call for {len(fields)} fields...")
            if relevant_ctx:
                logger.info(
                    f"   Enriching prompt with {len(relevant_ctx)} relevant "
                    f"user-context entries (pool size: {len(self._user_gemini_context)})"
                )
            ai_mappings = await self.ai_mapper.map_fields_to_profile(
                fields, profile,
                full_auto_mode=self.full_auto_mode,
                user_context=relevant_ctx or None,
            )
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
                        
                        # Create clean filename: FirstName_LastName_CoverLetter.txt
                        first_name = profile.get('first_name', '').strip()
                        last_name = profile.get('last_name', '').strip()
                        
                        if first_name and last_name:
                            clean_first = "".join(c for c in first_name if c.isalnum())
                            clean_last = "".join(c for c in last_name if c.isalnum())
                            file_name = f"{clean_first}_{clean_last}_CoverLetter.txt"
                        else:
                            file_name = "CoverLetter.txt"
                        
                        # Create temp file with clean name
                        temp_dir = tempfile.gettempdir()
                        temp_file_path = os.path.join(temp_dir, file_name)
                        
                        with open(temp_file_path, 'w', encoding='utf-8') as f:
                            f.write(str(cleaned_value))
                            f.flush()  # Ensure all data is written
                            os.fsync(f.fileno())  # Force OS to write to disk
                        # File is now closed, but Windows may still hold the handle
                        
                        # Longer delay to ensure Windows releases the file handle completely
                        # Also wait for antivirus/file system to finish scanning
                        await asyncio.sleep(1.0)  # Increased from 0.5s to 1.0s
                        
                        logger.info(f"✅ Created temporary file: {file_name}")
                        cleaned_value = temp_file_path
                        
                        # Track for cleanup if agent has tracker
                        if hasattr(self, 'created_files'):
                            self.created_files.append(temp_file_path)
                            
                    except Exception as e:
                        logger.error(f"Failed to create temp file for text content: {e}")
                        result['skipped_fields'].append({
                            "field": field_label,
                            "reason": "Failed to convert AI text to file"
                        })
                        continue

                fill_result = await self.interactor.fill_field(field_data, cleaned_value, profile)

                if fill_result['success']:
                    if mapping_type == 'manual':
                        display_value = value[:100] + '...' if len(value) > 100 else value
                        logger.info(f"✅ AI Generated: '{field_label}' = '{display_value}'")
                    else:
                        logger.info(f"✅ AI Batch: '{field_label}' = '{value}'")

                    result["fields_by_method"]["ai"] += 1
                    result["filled_fields"][field_label] = value
                    self.completion_tracker.mark_field_completed(field_id, field_label, value)
                    self._lock_ai_filled(field_label, field.get('field_category', ''))
                    filled_count += 1
                    # Debug reporter
                    try:
                        import fill_debug_reporter as _fdr
                        r = _fdr.get_reporter()
                        if r:
                            r.record_fill(field_id, field_label,
                                          field.get('field_category', ''),
                                          "ai", value,
                                          f"type={mapping_type} profile_field={mapping_data.get('profile_field','?')}")
                    except Exception:
                        pass

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
                    self._record_field_failure(field_label, field.get('field_category', ''))
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
            field_id     = self._get_field_id(field)
            relevant_ctx = self._select_relevant_user_context([field], profile)
            ai_mappings  = await self.ai_mapper.map_fields_to_profile(
                [field], profile,
                full_auto_mode=self.full_auto_mode,
                user_context=relevant_ctx or None,
            )

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
                self._lock_ai_filled(field_label, field.get('field_category', ''))
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
You previously flagged these issues with a job application form:
{issues_text}

Current filled fields:
{filled_list}

{comprehensive_profile_context}

CRITICAL RULES - YOU MUST FOLLOW THESE EXACTLY:
1. The profile is ABSOLUTE TRUTH. A correction is ONLY valid if the filled value directly contradicts a specific value stated in the profile.

2. ONLY suggest a correction when you can point to a specific profile field that has a DIFFERENT value than what was filled.

Allowed correction types (with examples):
- Profile says first_name="John" but form filled "Jane" → correct to "John"
- Profile says email="john@gmail.com" but form filled "wrong@email.com" → correct to profile email
- Graduation date field: if graduation date is in the FUTURE, person IS currently enrolled; provide actual date from education data
- LinkedIn URLs: ensure they start with "https://www.linkedin.com/in/"
- Phone Extension field filled with a full phone number → correct to empty string ""

Respond in JSON format:
{{
  "corrections": [
    {{
      "field_name": "exact field name from filled fields",
      "current_value": "current value in form",
      "corrected_value": "the exact value from the profile",
      "reason": "Profile field X says Y but form has Z"
    }}
  ]
}}

If none of the flagged issues represent a true contradiction with the profile, return an empty corrections list.
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

                # Mark field as Gemini-flagged (allows re-filling even if pre-filled)
                field_id = self._get_field_id(field_def)
                self.gemini_flagged_fields.add(field_id)
                
                # Store original value before correction attempt (for restoration if correction fails)
                original_value = filled_fields.get(field_name)

                # Get fresh element
                element = await self._get_fresh_element(field_def)
                if not element:
                    logger.warning(f"⚠️ Could not get element for '{field_name}'")
                    # Restore original value since we couldn't even get the element
                    if original_value:
                        filled_fields[field_name] = original_value
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
                        # Correction failed - restore original value
                        logger.warning(f"⚠️ Failed to correct '{field_name}' - restoring original value")
                        if original_value:
                            filled_fields[field_name] = original_value
                            logger.info(f"↩️ Restored '{field_name}' to original value: '{original_value[:50]}...'")
                else:
                    # Empty the field (for duplicates/errors)
                    filled_fields[field_name] = ""
                    corrections_made += 1
                    logger.info(f"✅ Cleared '{field_name}' successfully")

            return corrections_made

        except Exception as e:
            logger.error(f"Error correcting Gemini issues: {e}")
            return 0

    def _heuristic_fill_check(self, result: Dict[str, Any]) -> None:
        """
        Zero-cost sanity check on filled fields.  Replaces the removed Gemini
        review cycle for the most common fill mistake: a URL ending up in a
        non-URL field, or plain text in a URL field.

        Logs warnings only — does not attempt corrections (those would require
        a Gemini call we intentionally eliminated).
        """
        url_re      = re.compile(r'https?://', re.IGNORECASE)
        url_labels  = re.compile(r'\b(linkedin|github|portfolio|website|url|link)\b', re.IGNORECASE)
        city_labels = re.compile(r'\b(city|town|municipality)\b', re.IGNORECASE)
        state_labels = re.compile(r'\b(state|province|region)\b', re.IGNORECASE)

        issues = []
        for label, value in result.get("filled_fields", {}).items():
            val_is_url = bool(url_re.search(str(value)))
            label_wants_url  = bool(url_labels.search(label))
            label_wants_city  = bool(city_labels.search(label))
            label_wants_state = bool(state_labels.search(label))

            if label_wants_url and not val_is_url:
                issues.append(f"'{label}' expected a URL but got: '{str(value)[:50]}'")
            elif (label_wants_city or label_wants_state) and val_is_url:
                issues.append(f"'{label}' expected a location but got a URL: '{str(value)[:50]}'")

        if issues:
            logger.warning(f"⚠️ Heuristic fill check flagged {len(issues)} potential issues:")
            for issue in issues:
                logger.warning(f"   {issue}")
            result["fill_warnings"] = issues
        else:
            logger.info("✅ Heuristic fill check passed")

    async def _final_gemini_review(
        self,
        filled_fields: Dict[str, str],
        profile: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        DEPRECATED — replaced by _heuristic_fill_check (no Gemini call) +
        HumanFillTracker._filter_captures_with_gemini (runs at flush time,
        sends no personal data).

        Kept to avoid breaking any external callers; returns approved=True
        immediately without making any API call.
        """
        logger.debug("_final_gemini_review: skipped (replaced by heuristic check)")
        return {"approved": True, "issues": []}
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
You are reviewing a completed job application form. Your job is to verify two things:
  A. Each filled value matches the correct field in the applicant's profile.
  B. Each filled value is the RIGHT TYPE of data for that field label.

RULE SET:

1. VALUE-FIELD TYPE MISMATCH (most important - flag these):
   These are cases where a valid profile value ended up in the WRONG field:
   - Any field whose label contains "LinkedIn", "LinkedIn Profile", "LinkedIn URL" → must contain a LinkedIn URL (e.g. linkedin.com/in/...). If it contains a city, name, state, or any non-URL text → FLAG IT.
   - Any field whose label contains "GitHub", "Github", "Portfolio", "Website", "URL", "Link" → must contain a URL or empty. If it contains a city, state, or plain text → FLAG IT.
   - Any field whose label is "City" or "Town" → must contain a city name, NOT a URL or date → flag if URL found.
   - Any field whose label is "State" or "Province" → must contain a state name/code, NOT a URL → flag if URL found.
   - Any essay / long-answer field (label contains "tell us", "describe", "why", "what", "ideal", "start date", "experience", "internship", "feedback", "skill") → must contain a meaningful sentence or paragraph. If it contains only a URL, a city name, a state code, or a single unrelated word → FLAG IT.

2. VALUE CONTRADICTS PROFILE (also flag):
   - Profile says name is "John Smith" but form filled "Jane Smith" → flag it.
   - Profile says email is "john@gmail.com" but form filled "jane@yahoo.com" → flag it.

3. DO NOT FLAG (these are fine):
   - Formatting differences (phone without dashes, URL with/without https, etc.)
   - Missing fields - only check what IS filled.
   - Values that seem unusual but are directly in the profile (Indian nationality + US phone, visa + work auth both Yes, etc.).

{comprehensive_profile_context}

Filled Fields (label → value):
{filled_list}

Respond in JSON format:
{{
  "approved": true/false,
  "issues": ["field_label: 'filled_value' - reason (expected: what it should be)"],
  "confidence": 0.0-1.0
}}

Set approved=false if ANY field has a value of the wrong type for its label, or if a value directly contradicts the profile.
If everything looks correct, set approved=true with empty issues list.
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
        """
        Filter out fields that are already completed or pre-filled.
        UNLESS they've been flagged by Gemini as incorrect.
        """
        unfilled = []

        for field in fields:
            field_id = self._get_field_id(field)
            field_label = field.get('label', 'Unknown')

            field_category = field.get('field_category', '')

            # AI-fill lock: if AI already filled this field on this page, never retry it.
            if self._is_locked_by_ai(field_label, field_category):
                logger.info(f"🔒 Skipping AI-locked field: '{field_label}' [{field_category}]")
                continue

            # Exhaustion lock: skip fields that have failed too many times already.
            if self._is_exhausted(field_label, field_category):
                logger.info(f"🚫 Skipping exhausted field: '{field_label}' [{field_category}]")
                continue

            # Check if already completed by agent in previous iteration (stable_id based)
            if self.completion_tracker.should_skip_field(field_id, field_label):
                continue

            # Check if pre-filled (before agent started) and NOT flagged by Gemini
            if field_id in self.pre_filled_values and field_id not in self.gemini_flagged_fields:
                pre_filled_data = self.pre_filled_values[field_id]
                logger.info(f"⏭️ Skipping pre-filled field: '{field_label}' = '{pre_filled_data['value'][:50]}...'")
                continue

            # Legacy check for pre-filled flag (from field detection)
            if field.get('is_filled') and field_id not in self.gemini_flagged_fields:
                continue


            unfilled.append(field)

        return unfilled

    async def _get_fresh_element(self, field: Dict[str, Any]) -> Optional[Any]:
        """
        Re-locate the DOM element after a potential React/Workday re-render.

        Uses a waterfall of strategies ordered from most to least stable:
          1. stable_id prefix  (name: / aria_label: / placeholder: / id:)
          2. raw field attributes as fallback (name, aria_label, placeholder, id)
          3. position_index - nth visible form field captured at scan time
          4. original stale element reference - absolute last resort
        """
        _input_sel = (
            'input:not([type="hidden"]):not([type="submit"]):not([type="button"]):visible,'
            ' select:visible, textarea:visible'
        )

        async def _try_locator(selector: str):
            try:
                loc = self.page.locator(selector).first
                if await loc.count() > 0:
                    return loc
            except Exception:
                pass
            return None

        try:
            stable_id = field.get('stable_id', '')

            # ── Phase 1: parse stable_id prefix ──────────────────────────────
            if stable_id.startswith('ashby_yesno:'):
                # Ashby Yes/No: the element is the _yesno_ container div, NOT the
                # hidden checkbox.  _get_fresh_element must return the container so
                # _fill_ashby_yesno can find the Yes/No buttons inside it.
                name_val = stable_id[12:]
                el = await _try_locator(
                    f'[class*="_yesno_"]:has(input[type="checkbox"][name="{name_val}"])'
                )
                if el:
                    return el
                # Fallback: any _yesno_ container on the page
                el = await _try_locator('[class*="_yesno_"]')
                if el:
                    return el

            elif stable_id.startswith('name:'):
                name_val = stable_id[5:]
                el = await _try_locator(f'[name="{name_val}"]')
                if el:
                    return el

            elif stable_id.startswith('aria_label:'):
                al_val = stable_id[11:]
                el = await _try_locator(f'[aria-label="{al_val}"]')
                if el:
                    return el

            elif stable_id.startswith('placeholder:'):
                ph_val = stable_id[12:]
                el = await _try_locator(f'[placeholder="{ph_val}"]')
                if el:
                    return el

            elif stable_id.startswith('id:'):
                id_val = stable_id[3:]
                el = await _try_locator(f'[id="{id_val}"]')
                if el:
                    return el

            # label_hash: and pos: prefixes can't be used directly for DOM lookup;
            # fall through to Phase 2.

            # ── Phase 2: raw field attribute waterfall ────────────────────────
            # Try each stable attribute in order (name is most stable; id is least).
            for attr, css_attr in [
                ('name',        'name'),
                ('aria_label',  'aria-label'),
                ('placeholder', 'placeholder'),
                ('id',          'id'),
            ]:
                val = field.get(attr, '')
                if val:
                    el = await _try_locator(f'[{css_attr}="{val}"]')
                    if el:
                        logger.debug(f"🔍 Re-located '{field.get('label', '?')}' via [{css_attr}]")
                        return el

            # ── Phase 3: position-based fallback ─────────────────────────────
            pos_idx = field.get('position_index')
            if pos_idx is not None:
                try:
                    all_inputs = await self.page.locator(_input_sel).all()
                    if pos_idx < len(all_inputs):
                        logger.debug(
                            f"🗂️ Re-located '{field.get('label', '?')}' by position index {pos_idx}"
                        )
                        return all_inputs[pos_idx]
                except Exception as e:
                    logger.debug(f"Position-based lookup failed: {e}")

            # ── Phase 4: original stale reference ────────────────────────────
            logger.debug(f"⚠️ Falling back to stale element ref for '{field.get('label', '?')}'")
            return field.get('element')

        except Exception as e:
            logger.debug(f"Error in _get_fresh_element: {e}")
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

    async def _final_rescan_and_fill(self, profile: Dict[str, Any], result: Dict[str, Any]) -> int:
        """
        Perform a final scan for any new/conditional fields that appeared after filling.
        Some forms show additional fields based on previous answers (conditional logic).
        
        Returns:
            int: Number of new fields filled
        """
        try:
            filled_count = 0
            
            # Re-detect all fields on the page
            all_fields = await self.interactor.get_all_form_fields(extract_options=False)
            logger.debug(f"🔍 Final re-scan detected {len(all_fields)} total fields")
            
            # Consolidate groups
            all_fields = await self._consolidate_radio_groups(all_fields)
            all_fields = await self._consolidate_checkbox_groups(all_fields)
            
            # Clean fields
            valid_fields = await self._clean_detected_fields(all_fields)
            
            # Filter to only NEW unfilled fields (not in our completion tracker)
            new_fields = []
            for field in valid_fields:
                field_id = self._get_field_id(field)
                field_label = field.get('label', 'Unknown Field')
                # Check if field should be skipped (already completed)
                if not self.completion_tracker.should_skip_field(field_id, field_label):
                    new_fields.append(field)
            
            if not new_fields:
                logger.info("✅ No new fields detected in final re-scan")
                return 0
            
            logger.info(f"🆕 Found {len(new_fields)} NEW fields in final re-scan! Filling them now...")
            
            # Fill the new fields using the same strategy
            filled_count = await self._process_fields_with_strategy(
                new_fields, profile, result
            )
            
            return filled_count
            
        except Exception as e:
            logger.error(f"❌ Error during final re-scan: {e}")
            return 0

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

            # Find all clickable button-like controls on the page.
            # Include generic role=button because many ATS UIs use div/span wrappers.
            all_buttons = await self.page.locator(
                'button, input[type="button"], input[type="submit"], a[role="button"], [role="button"]'
            ).all()

            logger.debug(f"Found {len(all_buttons)} total buttons on the page")
            found_buttons = []  # Track all visible buttons for debugging

            for button in all_buttons:
                try:
                    # Check if button is visible
                    if not await button.is_visible():
                        continue

                    # Skip disabled controls early
                    is_enabled = await button.is_enabled()
                    if not is_enabled:
                        continue
                    aria_disabled = (await button.get_attribute('aria-disabled') or "").lower().strip()
                    if aria_disabled == "true":
                        continue

                    # Collect multiple text sources. Many ATS controls are input[type=submit]
                    # where visible text lives in value/title/data-* attrs, not textContent.
                    button_text = await button.text_content() or ""
                    try:
                        if not button_text.strip():
                            button_text = await button.inner_text() or ""
                    except Exception:
                        pass
                    aria_label = await button.get_attribute('aria-label') or ""
                    value_attr = await button.get_attribute('value') or ""
                    title_attr = await button.get_attribute('title') or ""
                    data_automation = await button.get_attribute('data-automation-id') or ""
                    data_testid = await button.get_attribute('data-testid') or ""
                    button_type = await button.get_attribute('type') or ""

                    combined_text = (
                        f"{button_text} {aria_label} {value_attr} {title_attr} "
                        f"{data_automation} {data_testid} {button_type}"
                    ).lower().strip()

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

                        # Click the button with a small fallback chain
                        await button.scroll_into_view_if_needed(timeout=1500)
                        try:
                            await button.click(timeout=5000)
                        except Exception:
                            try:
                                await button.dispatch_event('click')
                            except Exception:
                                await button.click(force=True, timeout=5000)
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
            from gemini_compat import genai
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

    # ── Legal disclaimer / terms & conditions ─────────────────────────────

    async def handle_legal_disclaimer_checkboxes(self) -> int:
        """
        Detect and accept legal disclaimer / terms & conditions checkboxes.

        Handles two patterns:

        Pattern 1 - Oracle HCM / Taleo KnockoutJS custom checkbox:
            <span class="apply-flow-input-checkbox__button"
                  data-bind="click: toggleAccepted,
                             css: {'apply-flow-input-checkbox__button--checked': legalDisclaimer.isAccepted}">
            </span>
            The button is a plain <span>; checked state is tracked by the
            ``--checked`` CSS modifier class, not by a native checked attribute.

        Pattern 2 - Generic native checkbox near legal/agreement text:
            Any <input type="checkbox"> whose associated label or surrounding
            text contains terms like "terms", "agree", "legal", "privacy policy",
            "disclaimer", "acknowledge", or "consent".

        Returns:
            Number of checkboxes that were accepted (already-accepted ones are
            counted so the caller knows the page is in a good state).
        """
        handled = 0

        try:
            # ── Pattern 1: Oracle HCM / Taleo apply-flow custom checkbox ──
            apply_flow_buttons = await self.page.locator(
                '.apply-flow-input-checkbox__button'
            ).all()

            for button in apply_flow_buttons:
                try:
                    btn_class = await button.get_attribute('class') or ''
                    already_checked = 'apply-flow-input-checkbox__button--checked' in btn_class

                    if already_checked:
                        logger.debug('☑️  Legal disclaimer already accepted (apply-flow pattern)')
                        handled += 1
                        continue

                    if not await button.is_visible():
                        continue

                    await button.click()
                    await self.page.wait_for_timeout(300)

                    # Verify the click registered
                    updated_class = await button.get_attribute('class') or ''
                    if 'apply-flow-input-checkbox__button--checked' in updated_class:
                        logger.info('✅ Accepted legal disclaimer (apply-flow / Oracle HCM pattern)')
                    else:
                        logger.warning('⚠️  Clicked apply-flow legal checkbox but --checked class not set')

                    handled += 1

                except Exception as e:
                    logger.debug(f'apply-flow legal disclaimer click error: {e}')

            if handled:
                return handled  # Pattern 1 succeeded - no need for generic fallback

            # ── Pattern 2: Generic native checkbox near legal keywords ──
            LEGAL_KEYWORDS = [
                'terms', 'conditions', 'i agree', 'privacy policy', 'legal',
                'disclaimer', 'acknowledge', 'consent', 'certify',
            ]

            checkboxes = await self.page.locator('input[type="checkbox"]').all()
            for cb in checkboxes:
                try:
                    if await cb.is_checked():
                        # Already accepted - count it so caller knows it's handled
                        label_text = await self._get_checkbox_context_text(cb)
                        if any(kw in label_text for kw in LEGAL_KEYWORDS):
                            handled += 1
                        continue

                    if not await cb.is_visible():
                        continue

                    label_text = await self._get_checkbox_context_text(cb)

                    if any(kw in label_text for kw in LEGAL_KEYWORDS):
                        await cb.check()
                        await self.page.wait_for_timeout(300)
                        logger.info(
                            f'✅ Checked legal agreement checkbox (generic pattern): '
                            f'"{label_text[:80]}"'
                        )
                        handled += 1

                except Exception as e:
                    logger.debug(f'Generic legal checkbox error: {e}')

        except Exception as e:
            logger.error(f'handle_legal_disclaimer_checkboxes error: {e}')

        return handled

    async def _get_checkbox_context_text(self, cb) -> str:
        """Return lowercase surrounding text for a checkbox element."""
        try:
            cb_id = await cb.get_attribute('id')
            if cb_id:
                label_el = self.page.locator(f'label[for="{cb_id}"]').first
                if await label_el.count() > 0:
                    return (await label_el.inner_text()).lower()

            # Walk up to the nearest container that has visible text
            return (await cb.evaluate(
                '''el => {
                    const container = el.closest("label, li, td, div, span");
                    return container ? container.innerText : "";
                }'''
            )).lower()
        except Exception:
            return ''

