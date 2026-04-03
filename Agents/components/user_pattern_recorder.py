"""
User Pattern Recorder

Records human fills and corrections into the user_field_overrides table.
This is the per-user counterpart to PatternRecorder (which writes to the
global field_label_patterns table).

Key differences from PatternRecorder:
  - Always requires user_id
  - Stores the actual value the human provided (field_value_cached)
  - Tracks source: 'human_fill' vs 'human_correction'
  - Optional site_domain scoping
  - Confidence formula weights human fills higher than AI fills

Production mode
---------------
In the production Launchway CLI package there is no local PostgreSQL.
UserPatternRecorder detects this automatically and routes all writes
through the Launchway API (POST /api/cli/user-field-overrides) using
the auth token stored in ~/.launchway/session.json.
"""

import json
import re
from pathlib import Path
from typing import Optional, List, Dict, Any
from datetime import datetime
from loguru import logger
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
import os
from dotenv import load_dotenv
from urllib.parse import quote_plus

load_dotenv()


class UserPatternRecorder:
    """
    Records per-user field overrides to user_field_overrides.

    Called by HumanFillTracker after every debounce flush.
    Also called directly when a user explicitly corrects an AI-provided value.
    """

    # Human fills start at higher confidence than AI-learned patterns
    # because humans are definitionally correct.
    INITIAL_CONFIDENCE_HUMAN_FILL       = 0.95
    INITIAL_CONFIDENCE_HUMAN_CORRECTION = 0.90

    # Never persist secrets or auth challenge answers in user_field_overrides.
    # This blocks common password/passcode/passphrase variants (including typos).
    SENSITIVE_LABEL_RE = re.compile(
        r"\b("
        r"password|passowrd|pasword|passwd|pwd|"
        r"confirm\s*password|re[-\s]*enter\s*password|"
        r"passphrase|paraphrase|"
        r"passcode|pin|otp|one\s*time\s*pass(code|word)|"
        r"verification\s*code|mfa|2fa|two\s*factor|auth(entication)?\s*code|"
        r"security\s*(code|answer|question)|secret"
        r")\b",
        re.IGNORECASE,
    )

    # Where the Launchway CLI stores the auth token
    _SESSION_FILE = Path.home() / ".launchway" / "session.json"

    def __init__(self):
        self._api_client = None   # lazy-loaded when DB is unavailable
        self._init_database()

    def _is_production(self) -> bool:
        """
        Returns True when running inside the production Launchway CLI package.
        In dev, RUN_MODE=Development is set in .env.
        In production the CLI never sets RUN_MODE, so it remains unset.
        """
        return os.getenv("RUN_MODE", "").strip().lower() != "development"

    def _init_database(self):
        if self._is_production():
            # Production: no local PostgreSQL — all writes go through the API.
            self.engine = None
            self.SessionLocal = None
            logger.debug("UserPatternRecorder: production mode — using API routing")
            return

        try:
            DB_HOST     = os.getenv('DB_HOST', 'localhost')
            DB_PORT     = os.getenv('DB_PORT', '5432')
            DB_NAME     = os.getenv('DB_NAME', 'job_agent_db')
            DB_USER     = os.getenv('DB_USER', 'postgres')
            DB_PASSWORD = os.getenv('DB_PASSWORD', '')

            encoded_password = quote_plus(DB_PASSWORD)
            DATABASE_URL = (
                f"postgresql://{DB_USER}:{encoded_password}"
                f"@{DB_HOST}:{DB_PORT}/{DB_NAME}"
            )

            self.engine = create_engine(DATABASE_URL, pool_pre_ping=True)
            self.SessionLocal = sessionmaker(bind=self.engine)
            logger.info("UserPatternRecorder: Database connection initialized")

        except Exception as e:
            logger.warning(
                f"UserPatternRecorder: DB init failed ({e}). "
                "Will route writes through the Launchway API."
            )
            self.engine = None
            self.SessionLocal = None

    # ---------------------------------------------------------------------- #
    #  API fallback (production / no-DB mode)                                 #
    # ---------------------------------------------------------------------- #

    def _get_api_client(self):
        """
        Lazily create a LaunchwayClient from the CLI session token.
        Returns None if no valid session is found.
        """
        if self._api_client is not None:
            return self._api_client
        try:
            if not self._SESSION_FILE.exists():
                return None
            session_data = json.loads(self._SESSION_FILE.read_text(encoding="utf-8"))
            token = session_data.get("token")
            if not token:
                return None
            # Import LaunchwayClient from the launchway package (always available in prod)
            from launchway.api_client import LaunchwayClient
            self._api_client = LaunchwayClient(token=token)
            logger.debug("UserPatternRecorder: API client initialised from session token")
            return self._api_client
        except Exception as e:
            logger.debug(f"UserPatternRecorder: Could not load API client: {e}")
            return None

    def _record_via_api(self, payload: dict) -> bool:
        """Send a single override to the Launchway API."""
        client = self._get_api_client()
        if client is None:
            logger.warning("UserPatternRecorder: No API client available, override lost")
            return False
        try:
            result = client.save_user_field_overrides([payload])
            saved = result.get("saved", 0)
            if saved > 0:
                logger.info(
                    f"UserPatternRecorder (API): saved '{payload.get('field_label_raw', '')}'"
                )
            return saved > 0
        except Exception as e:
            logger.error(f"UserPatternRecorder: API save failed: {e}")
            return False

    # ---------------------------------------------------------------------- #
    #  Public API                                                              #
    # ---------------------------------------------------------------------- #

    async def record_human_fill(
        self,
        field_label: str,
        field_value: str,
        field_category: str,
        source: str,                   # 'human_fill' | 'human_correction'
        was_ai_attempted: bool = True,
        user_id: Optional[str] = None,
        profile_field: Optional[str] = None,  # if known; None is fine
        site_domain: Optional[str] = None,
    ) -> bool:
        """
        Record or update a human-provided field fill.

        Args:
            field_label    : Visible label of the field ("Current Job Title")
            field_value    : Value the human typed/selected ("Software Engineer")
            field_category : Field type ("text_input", "dropdown", etc.)
            source         : 'human_fill' or 'human_correction'
            was_ai_attempted: Was the AI tried and failed before human intervened?
            user_id        : UUID string of the user
            profile_field  : Profile field path if known (e.g. "work_experience[0].title")
            site_domain    : e.g. 'greenhouse.io' — scope override to this site only

        Returns:
            True if saved, False if skipped/failed
        """
        return self._record_sync(
            field_label=field_label,
            field_value=field_value,
            field_category=field_category,
            source=source,
            was_ai_attempted=was_ai_attempted,
            user_id=user_id,
            profile_field=profile_field,
            site_domain=site_domain,
            success=True,
        )

    def record_human_fill_sync(
        self,
        field_label: str,
        field_value: str,
        field_category: str,
        source: str,
        was_ai_attempted: bool = True,
        user_id: Optional[str] = None,
        profile_field: Optional[str] = None,
        site_domain: Optional[str] = None,
    ) -> bool:
        """Synchronous version — use from non-async contexts."""
        return self._record_sync(
            field_label=field_label,
            field_value=field_value,
            field_category=field_category,
            source=source,
            was_ai_attempted=was_ai_attempted,
            user_id=user_id,
            profile_field=profile_field,
            site_domain=site_domain,
            success=True,
        )

    # ---------------------------------------------------------------------- #
    #  Internal                                                                #
    # ---------------------------------------------------------------------- #

    def _record_sync(
        self,
        field_label: str,
        field_value: str,
        field_category: str,
        source: str,
        was_ai_attempted: bool,
        user_id: Optional[str],
        profile_field: Optional[str],
        site_domain: Optional[str],
        success: bool,
    ) -> bool:
        if not self._should_record(field_label):
            logger.warning(
                f"UserPatternRecorder: Skipping sensitive field '{field_label}'"
            )
            return False

        normalized_site_domain = (site_domain or "").strip().lower() or None

        if not self.engine or not self.SessionLocal:
            # Production / no-DB mode: route through the Launchway API
            if not user_id:
                logger.warning("UserPatternRecorder: user_id required, skipping")
                return False
            payload = {
                "field_label_normalized": self._normalize_label(field_label),
                "field_label_raw":        field_label,
                "field_value_cached":     field_value,
                "field_category":         field_category,
                "source":                 source,
                "was_ai_attempted":       was_ai_attempted,
                "confidence_score":       (
                    self.INITIAL_CONFIDENCE_HUMAN_FILL
                    if source == "human_fill"
                    else self.INITIAL_CONFIDENCE_HUMAN_CORRECTION
                ),
                "site_domain":    normalized_site_domain,
                "profile_field":  profile_field,
            }
            return self._record_via_api(payload)

        if not user_id:
            logger.warning("UserPatternRecorder: user_id is required, skipping")
            return False

    def _should_record(self, field_label: str) -> bool:
        """
        Return False for sensitive/auth fields so secrets are never persisted.
        """
        if not field_label:
            return False
        return self.SENSITIVE_LABEL_RE.search(field_label) is None

        normalized_label = self._normalize_label(field_label)

        initial_confidence = (
            self.INITIAL_CONFIDENCE_HUMAN_FILL
            if source == 'human_fill'
            else self.INITIAL_CONFIDENCE_HUMAN_CORRECTION
        )

        try:
            session = self.SessionLocal()

            existing = session.execute(text("""
                SELECT id, success_count, failure_count, occurrence_count, confidence_score
                FROM user_field_overrides
                WHERE user_id = :user_id
                  AND LOWER(field_label_normalized) = LOWER(:label)
                  AND LOWER(COALESCE(site_domain, '')) = LOWER(COALESCE(:site_domain, ''))
            """), {
                'user_id':     user_id,
                'label':       normalized_label,
                'site_domain': normalized_site_domain,
            }).first()

            if existing:
                row_id           = existing[0]
                new_success      = (existing[1] or 0) + (1 if success else 0)
                new_failure      = (existing[2] or 0) + (0 if success else 1)
                new_occurrences  = (existing[3] or 0) + 1
                new_confidence   = self._calculate_confidence(new_success, new_failure, new_occurrences)

                session.execute(text("""
                    UPDATE user_field_overrides
                    SET field_value_cached  = :value,
                        field_category      = :category,
                        profile_field       = COALESCE(:profile_field, profile_field),
                        source              = :source,
                        occurrence_count    = :occurrences,
                        success_count       = :successes,
                        failure_count       = :failures,
                        confidence_score    = :confidence,
                        last_seen           = NOW()
                    WHERE id = :id
                """), {
                    'id':           row_id,
                    'value':        field_value,
                    'category':     field_category,
                    'profile_field': profile_field,
                    'source':       source,
                    'occurrences':  new_occurrences,
                    'successes':    new_success,
                    'failures':     new_failure,
                    'confidence':   new_confidence,
                })

                logger.info(
                    f"UserPatternRecorder: Updated '{field_label}' "
                    f"(user={user_id[:8]}, confidence={new_confidence:.2f}, "
                    f"occurrences={new_occurrences})"
                )

            else:
                session.execute(text("""
                    INSERT INTO user_field_overrides
                    (user_id, field_label_normalized, field_label_raw,
                     profile_field, field_value_cached, field_category,
                     source, was_ai_attempted, confidence_score,
                     occurrence_count, success_count, failure_count,
                     site_domain, created_at, last_seen)
                    VALUES
                    (:user_id, :label_norm, :label_raw,
                     :profile_field, :value, :category,
                     :source, :was_ai_attempted, :confidence,
                     1, :success_count, :failure_count,
                     :site_domain, NOW(), NOW())
                """), {
                    'user_id':          user_id,
                    'label_norm':       normalized_label,
                    'label_raw':        field_label,
                    'profile_field':    profile_field,
                    'value':            field_value,
                    'category':         field_category,
                    'source':           source,
                    'was_ai_attempted': was_ai_attempted,
                    'confidence':       initial_confidence,
                    'success_count':    1 if success else 0,
                    'failure_count':    0 if success else 1,
                    'site_domain':      normalized_site_domain,
                })

                logger.info(
                    f"UserPatternRecorder: Created new override '{field_label}' = '{field_value[:30]}' "
                    f"(user={user_id[:8]}, source={source})"
                )

            session.commit()
            session.close()
            return True

        except Exception as e:
            logger.error(f"UserPatternRecorder: Failed to record '{field_label}': {e}")
            try:
                session.rollback()
                session.close()
            except Exception:
                pass
            return False

    def _normalize_label(self, label: str) -> str:
        normalized = label.lower()
        normalized = re.sub(r'[^a-z0-9\s]', '', normalized)
        normalized = re.sub(r'\s+', ' ', normalized).strip()
        return normalized

    def _calculate_confidence(
        self,
        success_count: int,
        failure_count: int,
        occurrence_count: int,
    ) -> float:
        total = success_count + failure_count
        if total == 0:
            return 0.0
        base = success_count / total
        # Human fills get a bigger frequency boost (max +0.05) to stay above AI patterns
        freq_boost = min(0.05, occurrence_count / 200)
        return round(min(0.99, base + freq_boost), 2)

    def get_user_stats(self, user_id: str) -> dict:
        """Return override statistics for a user (for monitoring/UI)."""
        if not self.engine or not self.SessionLocal:
            return {'error': 'Database not initialized'}

        try:
            session = self.SessionLocal()
            result = session.execute(text("""
                SELECT
                    COUNT(*)                                               AS total,
                    COUNT(*) FILTER (WHERE source = 'human_fill')         AS fills,
                    COUNT(*) FILTER (WHERE source = 'human_correction')   AS corrections,
                    SUM(occurrence_count)                                  AS total_uses,
                    AVG(confidence_score)                                  AS avg_confidence
                FROM user_field_overrides
                WHERE user_id = :user_id
            """), {'user_id': user_id}).first()
            session.close()

            return {
                'total_overrides':     result[0] or 0,
                'human_fills':         result[1] or 0,
                'human_corrections':   result[2] or 0,
                'total_uses':          result[3] or 0,
                'average_confidence':  float(result[4]) if result[4] else 0.0,
            }

        except Exception as e:
            logger.error(f"UserPatternRecorder: Failed to get stats: {e}")
            return {'error': str(e)}
