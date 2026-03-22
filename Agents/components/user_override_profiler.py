"""
User Override Profiler

Background task that runs at agent startup to backfill `profile_field` for
entries in `user_field_overrides` that still have `profile_field IS NULL`.

Why this matters
----------------
When the human-fill tracker records a field, it captures the label and value
but *does not* know which profile field the label corresponds to.  E.g.:

    field_label_normalized = "home phone number"
    field_value_cached     = "(240) 610-1453"
    profile_field          = NULL   ← needs filling

Without `profile_field`, the agent uses the cached string verbatim every run.
That is fine for job-specific fields ("Supervisor Name", "Reason for Leaving")
but wrong for profile-mapped fields like phone, city, GPA — because the cached
string never updates when the user changes their profile.

The profiler fixes this by:
  1. Querying entries where profile_field IS NULL
  2. Trying SemanticFieldMapper first (fast, local, no API cost)
  3. Falling back to Gemini for anything the model can't confidently map
  4. Writing the discovered profile_field back to the DB

Fields the profiler intentionally leaves unmapped (NULL forever):
  - Supervisor name/position, reason for leaving, responsibilities — these are
    job-specific and have no generic profile equivalent.  The cached value is
    the correct answer.
  - Ambiguous date fields like "MM/YYYY" — context-dependent.
"""

import re
import os
import asyncio
from typing import Dict, List, Optional, Any
from loguru import logger
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from urllib.parse import quote_plus
from dotenv import load_dotenv

load_dotenv()


# Profile fields that semantic mapping should NOT assign even if similarity
# is high — they are context-dependent and the cached value is the right answer.
SKIP_MAPPING_LABELS = {
    "mmyyyy", "mm/yyyy", "responsibilities", "duties",
    "reason for leaving", "reason for leaving required",
    "supervisor name", "supervisor name required",
    "supervisor position", "supervisor position required",
    "manager name", "manager name required",
    "manager position", "manager position required",
    "company description", "job description",
    "address line 2optional", "address line 2",
}

# Words that signal a label is specific to THIS job/company/location.
# A label containing any of these should never be permanently mapped to a
# profile field because the answer may differ per posting.
_JOB_SPECIFIC_PHRASES = re.compile(
    r'\b(our|this role|this position|this company|this opportunity|this job|'
    r'this team|this office|the role|the position|the company|the team|'
    r'the job|the opportunity|here at|with us)\b',
    re.IGNORECASE,
)

# Proper-noun location pattern: a preposition followed by a Capitalised word.
# e.g. "relocate to Seattle", "work in Austin", "based at London"
# Catches "in/at/to/for/near [Capital]" but not "in the US" (short stop-words).
_LOCATION_PREP_RE = re.compile(
    r'\b(?:in|at|to|for|near|from|within)\s+([A-Z][a-z]{2,})',
)

# Minimum label length for location check — short labels like "State" or
# "City" are fine to map; only flag multi-word labels with location context.
_LOCATION_CHECK_MIN_WORDS = 4


def _normalize(label: str) -> str:
    return re.sub(r'\s+', ' ', re.sub(r'[^a-z0-9\s]', '', label.lower())).strip()


def _is_job_specific(label: str) -> bool:
    """
    Return True if the label contains language that makes it specific to a
    particular job posting, company, or location — meaning permanently mapping
    it to a profile field would be unsafe.

    Rules
    -----
    1. Contains a "this role / our team / the company" type phrase.
    2. Contains a location preposition + proper noun in a multi-word label
       (e.g. "relocate to Austin", "work in Seattle", "on-site in London").
    """
    if _JOB_SPECIFIC_PHRASES.search(label):
        return True

    word_count = len(label.split())
    if word_count >= _LOCATION_CHECK_MIN_WORDS and _LOCATION_PREP_RE.search(label):
        return True

    return False


class UserOverrideProfiler:
    """
    Runs once at agent startup (or on-demand) to backfill profile_field for
    user_field_overrides entries that have profile_field = NULL.
    """

    # Higher threshold than real-time mapping (0.72) because the profiler
    # writes permanently to the DB.  A confident match is required.
    # Increased from 0.72 to 0.78 to reduce chance of overmatching job-specific
    # questions that partially overlap with generic profile field concepts.
    SEMANTIC_THRESHOLD = 0.78

    # Gemini fallback: only call API if there are >=1 unmapped entry that
    # semantic couldn't confidently classify.
    USE_GEMINI_FALLBACK = True

    def __init__(self):
        self._init_db()
        self._semantic_mapper = None

    def _init_db(self):
        try:
            DB_HOST     = os.getenv('DB_HOST', 'localhost')
            DB_PORT     = os.getenv('DB_PORT', '5432')
            DB_NAME     = os.getenv('DB_NAME', 'job_agent_db')
            DB_USER_DB  = os.getenv('DB_USER', 'postgres')
            DB_PASSWORD = os.getenv('DB_PASSWORD', '')
            encoded_pw  = quote_plus(DB_PASSWORD)
            url = f"postgresql://{DB_USER_DB}:{encoded_pw}@{DB_HOST}:{DB_PORT}/{DB_NAME}"
            self.engine = create_engine(url, pool_pre_ping=True)
            self.SessionLocal = sessionmaker(bind=self.engine)
        except Exception as e:
            logger.error(f"UserOverrideProfiler: DB init failed: {e}")
            self.engine = None
            self.SessionLocal = None

    # ---------------------------------------------------------------------- #
    #  Public entry point                                                      #
    # ---------------------------------------------------------------------- #

    async def run(self, user_id: Optional[str] = None) -> int:
        """
        Backfill profile_field for unmapped user override entries.

        Args:
            user_id: Restrict to a specific user; None means process all users.

        Returns:
            Number of entries that got a profile_field assigned.
        """
        if not self.engine:
            return 0

        entries = self._fetch_unmapped(user_id)
        if not entries:
            logger.info("UserOverrideProfiler: No unmapped entries to process.")
            return 0

        logger.info(
            f"UserOverrideProfiler: Found {len(entries)} entries with null "
            f"profile_field — attempting to backfill..."
        )

        updates: Dict[int, str] = {}  # {id: profile_field}

        # ── Pass 1: semantic (fast, local) ──────────────────────────────────
        unresolved = []
        semantic   = self._get_semantic_mapper()

        for entry in entries:
            label_norm = entry['field_label_normalized']
            label_raw  = entry['field_label_raw']

            # Skip labels on the explicit deny-list
            if _normalize(label_raw) in SKIP_MAPPING_LABELS or label_norm in SKIP_MAPPING_LABELS:
                logger.debug(
                    f"UserOverrideProfiler: Skipping '{label_raw}' "
                    f"(on deny-list, cached value is correct)"
                )
                continue

            # Skip labels that contain job/location-specific language —
            # permanently mapping these to a profile field is unsafe
            if _is_job_specific(label_raw):
                logger.debug(
                    f"UserOverrideProfiler: Skipping '{label_raw}' "
                    f"(job/location-specific, will remain as Gemini context hint)"
                )
                continue

            if semantic:
                match = semantic.map_field(label_raw)
                if match and match.confidence >= self.SEMANTIC_THRESHOLD:
                    updates[entry['id']] = match.profile_field
                    logger.info(
                        f"UserOverrideProfiler: '{label_raw}' → "
                        f"'{match.profile_field}' "
                        f"(semantic, sim={match.confidence:.2f})"
                    )
                    continue

            unresolved.append(entry)

        # ── Pass 2: Gemini fallback for remaining entries ────────────────────
        if unresolved and self.USE_GEMINI_FALLBACK:
            gemini_updates = await self._resolve_with_gemini(unresolved)
            updates.update(gemini_updates)

        # ── Write updates ────────────────────────────────────────────────────
        if updates:
            self._write_updates(updates)
            logger.info(
                f"UserOverrideProfiler: Backfilled profile_field for "
                f"{len(updates)} entries."
            )

        skipped = len(entries) - len(updates) - len(
            [e for e in entries
             if _normalize(e['field_label_raw']) in SKIP_MAPPING_LABELS
             or e['field_label_normalized'] in SKIP_MAPPING_LABELS]
        )
        if skipped > 0:
            logger.debug(
                f"UserOverrideProfiler: {skipped} entries left unmapped "
                f"(job-specific / low confidence — cached value will be used)."
            )

        return len(updates)

    # ---------------------------------------------------------------------- #
    #  DB helpers                                                              #
    # ---------------------------------------------------------------------- #

    def _fetch_unmapped(self, user_id: Optional[str]) -> List[Dict]:
        """Return user_field_overrides rows where profile_field IS NULL."""
        try:
            params: Dict[str, Any] = {}
            user_clause = ""
            if user_id:
                user_clause = "AND user_id = :user_id"
                params['user_id'] = user_id

            with self.engine.connect() as conn:
                rows = conn.execute(text(f"""
                    SELECT id,
                           user_id,
                           field_label_normalized,
                           field_label_raw,
                           field_value_cached,
                           field_category,
                           occurrence_count
                    FROM user_field_overrides
                    WHERE profile_field IS NULL
                      {user_clause}
                    ORDER BY occurrence_count DESC, id
                    LIMIT 200
                """), params).fetchall()

            return [
                {
                    'id':                     r[0],
                    'user_id':                r[1],
                    'field_label_normalized': r[2],
                    'field_label_raw':        r[3],
                    'field_value_cached':     r[4],
                    'field_category':         r[5],
                    'occurrence_count':       r[6],
                }
                for r in rows
            ]
        except Exception as e:
            logger.error(f"UserOverrideProfiler: fetch_unmapped failed: {e}")
            return []

    def _write_updates(self, updates: Dict[int, str]) -> None:
        """Bulk-write profile_field values back to the DB."""
        try:
            with self.engine.connect() as conn:
                for row_id, profile_field in updates.items():
                    conn.execute(text("""
                        UPDATE user_field_overrides
                        SET profile_field = :pf
                        WHERE id = :id
                    """), {'pf': profile_field, 'id': row_id})
                conn.commit()
        except Exception as e:
            logger.error(f"UserOverrideProfiler: write_updates failed: {e}")

    # ---------------------------------------------------------------------- #
    #  Semantic helper                                                         #
    # ---------------------------------------------------------------------- #

    def _get_semantic_mapper(self):
        if self._semantic_mapper is not None:
            return self._semantic_mapper

        try:
            from components.executors.semantic_field_mapper import SemanticFieldMapper
            if SemanticFieldMapper.is_available():
                self._semantic_mapper = SemanticFieldMapper()
                return self._semantic_mapper
        except Exception:
            pass
        return None

    # ---------------------------------------------------------------------- #
    #  Gemini fallback                                                         #
    # ---------------------------------------------------------------------- #

    async def _resolve_with_gemini(
        self, entries: List[Dict]
    ) -> Dict[int, str]:
        """
        Ask Gemini to map field labels → profile fields for entries that
        semantic couldn't confidently classify.

        Sends a single batched prompt to avoid N API calls.
        """
        if not entries:
            return {}

        # Build a reference list of valid profile fields for the prompt
        valid_fields = [
            "first_name", "last_name", "full_name", "email", "phone",
            "city", "state", "country", "zip_code", "address",
            "linkedin", "github", "portfolio", "veteran_status",
            "disability_status", "gender", "race", "ethnicity",
            "race_ethnicity", "nationality", "visa_status",
            "sponsorship_required", "willing_to_relocate",
            "years_of_experience", "current_title", "current_company",
            "highest_education", "degree", "major", "gpa",
            "cover_letter", "start_date", "notice_period",
            "salary_range", "preferred_name",
        ]

        lines = []
        for i, e in enumerate(entries):
            lines.append(
                f"{i+1}. label='{e['field_label_raw']}' "
                f"category={e['field_category']} "
                f"example_value='{e['field_value_cached'] or ''}'"
            )

        prompt = f"""You are mapping job-application form field labels to standardized profile fields.

Valid profile fields:
{', '.join(valid_fields)}

For each numbered field below, respond with ONLY:
  <number>: <profile_field>
or
  <number>: NULL   (if no profile field matches — e.g. job-specific questions)

Fields to map:
{chr(10).join(lines)}

Rules:
- Use exactly one of the valid profile fields above, or NULL.
- Use NULL for fields like "Supervisor Name", "Reason for Leaving", "Responsibilities",
  "Company Description", "MM/YYYY" date pickers, and other job-specific context.
- Use NULL if the label refers to a SPECIFIC location, company, or role
  (e.g. "Are you willing to relocate to Seattle?", "Do you have experience with
  our proprietary system?", "Can you work on-site in Austin 5 days/week?").
  These answers may differ per job posting and must not be permanently mapped.
- Do NOT invent new field names.
"""

        try:
            from gemini_compat import genai
            model  = genai.GenerativeModel("gemini-2.0-flash")
            config = genai.GenerationConfig(temperature=0.0, max_output_tokens=512)
            resp   = await asyncio.to_thread(
                model.generate_content, prompt, generation_config=config
            )
            text_out = resp.text.strip()
        except Exception as e:
            logger.error(f"UserOverrideProfiler: Gemini call failed: {e}")
            return {}

        # Parse the response
        updates: Dict[int, str] = {}
        for line in text_out.splitlines():
            line = line.strip()
            if not line or ':' not in line:
                continue
            parts = line.split(':', 1)
            try:
                idx_str    = parts[0].strip()
                field_val  = parts[1].strip().lower()
                idx        = int(idx_str) - 1
                if 0 <= idx < len(entries):
                    if field_val not in ('null', 'none', '') and field_val in valid_fields:
                        updates[entries[idx]['id']] = field_val
                        logger.info(
                            f"UserOverrideProfiler: (Gemini) "
                            f"'{entries[idx]['field_label_raw']}' → '{field_val}'"
                        )
            except (ValueError, IndexError):
                continue

        return updates
