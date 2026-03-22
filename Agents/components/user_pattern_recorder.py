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
"""

import re
from typing import Optional
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

    def __init__(self):
        self._init_database()

    def _init_database(self):
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
            logger.error(f"UserPatternRecorder: Failed to initialize database: {e}")
            self.engine = None
            self.SessionLocal = None

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
        if not self.engine or not self.SessionLocal:
            logger.warning("UserPatternRecorder: Database not initialized, skipping")
            return False

        if not user_id:
            logger.warning("UserPatternRecorder: user_id is required, skipping")
            return False

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
                  AND field_label_normalized = :label
                  AND (site_domain = :site_domain OR (site_domain IS NULL AND :site_domain IS NULL))
            """), {
                'user_id':     user_id,
                'label':       normalized_label,
                'site_domain': site_domain,
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
                    'site_domain':      site_domain,
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
