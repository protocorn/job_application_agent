"""
Pattern Recorder - Records and updates learned field mapping patterns

This component records successful field mappings to the field_label_patterns database,
enabling the agent to learn from experience and reduce AI API calls over time.

Privacy: Never records actual field values, only labels and profile field mappings.
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


class PatternRecorder:
    """
    Records and updates learned field mapping patterns.

    Responsibility:
    - Record new field label → profile field mappings
    - Update existing patterns (increment counts, recalculate confidence)
    - Filter sensitive fields (privacy protection)
    - Calculate confidence scores
    """

    # Privacy: Never record patterns for these sensitive fields
    EXCLUDED_PATTERNS = [
        r'ssn',
        r'social.*security',
        r'tax.*id',
        r'credit.*card',
        r'cvv',
        r'password',
        r'pin',
        r'salary.*expectation',
        r'desired.*salary',
        r'expected.*salary',
        r'compensation.*expectation',
        r'date.*birth',
        r'\bdob\b',
        r'bank.*account',
        r'routing.*number',
        r'account.*number',
    ]

    # Initial confidence for new patterns
    INITIAL_CONFIDENCE = 0.85

    def __init__(self):
        """Initialize recorder with database connection."""
        self._init_database()
        self._compile_exclusion_patterns()

    def _init_database(self):
        """Initialize database connection."""
        try:
            DB_HOST = os.getenv('DB_HOST', 'localhost')
            DB_PORT = os.getenv('DB_PORT', '5432')
            DB_NAME = os.getenv('DB_NAME', 'job_agent_db')
            DB_USER = os.getenv('DB_USER', 'postgres')
            DB_PASSWORD = os.getenv('DB_PASSWORD', '')

            encoded_password = quote_plus(DB_PASSWORD)
            DATABASE_URL = f"postgresql://{DB_USER}:{encoded_password}@{DB_HOST}:{DB_PORT}/{DB_NAME}"

            self.engine = create_engine(DATABASE_URL, pool_pre_ping=True)
            self.SessionLocal = sessionmaker(bind=self.engine)
            logger.info("PatternRecorder: Database connection initialized")

        except Exception as e:
            logger.error(f"PatternRecorder: Failed to initialize database: {e}")
            self.engine = None
            self.SessionLocal = None

    def _compile_exclusion_patterns(self):
        """Compile regex patterns for faster matching."""
        self.exclusion_regexes = [
            re.compile(pattern, re.IGNORECASE)
            for pattern in self.EXCLUDED_PATTERNS
        ]

    async def record_pattern(
        self,
        field_label: str,
        profile_field: str,
        field_category: str,
        success: bool = True,
        user_id: Optional[str] = None
    ) -> bool:
        """
        Record or update a field mapping pattern.

        Args:
            field_label: The visible label of the form field
            profile_field: The profile field it maps to (e.g., "veteran_status")
            field_category: Type of field (dropdown, text_input, etc.)
            success: Whether the mapping was successful
            user_id: Optional user ID for attribution

        Returns:
            True if pattern was recorded, False if skipped or failed
        """
        if not self.engine or not self.SessionLocal:
            logger.warning("PatternRecorder: Database not initialized, skipping recording")
            return False

        # Privacy check: Skip sensitive fields
        if not self._should_record(field_label):
            logger.debug(
                f"PatternRecorder: Skipping sensitive field '{field_label}' "
                "(privacy exclusion)"
            )
            return False

        # Normalize label
        normalized_label = self._normalize_label(field_label)

        try:
            session = self.SessionLocal()

            # Check if pattern already exists
            existing = session.execute(text("""
                SELECT id, success_count, failure_count, occurrence_count
                FROM field_label_patterns
                WHERE field_label_normalized = :label
                  AND profile_field = :profile_field
            """), {
                'label': normalized_label,
                'profile_field': profile_field
            }).first()

            if existing:
                # Update existing pattern
                pattern_id = existing[0]
                # Handle NULL values from database
                new_success_count = (existing[1] or 0) + (1 if success else 0)
                new_failure_count = (existing[2] or 0) + (0 if success else 1)
                new_occurrence_count = (existing[3] or 0) + 1
                new_confidence = self._calculate_confidence(
                    new_success_count,
                    new_failure_count,
                    new_occurrence_count
                )

                session.execute(text("""
                    UPDATE field_label_patterns
                    SET occurrence_count = :occurrences,
                        success_count = :successes,
                        failure_count = :failures,
                        confidence_score = :confidence,
                        last_seen = NOW()
                    WHERE id = :id
                """), {
                    'id': pattern_id,
                    'occurrences': new_occurrence_count,
                    'successes': new_success_count,
                    'failures': new_failure_count,
                    'confidence': new_confidence
                })

                session.commit()
                session.close()

                logger.info(
                    f"PatternRecorder: Updated pattern '{field_label}' → {profile_field} "
                    f"(confidence: {new_confidence:.2f}, occurrences: {new_occurrence_count}, "
                    f"success: {success})"
                )

            else:
                # Insert new pattern
                initial_confidence = self.INITIAL_CONFIDENCE if success else 0.0

                session.execute(text("""
                    INSERT INTO field_label_patterns
                    (field_label_normalized, field_label_raw, profile_field, field_category,
                     confidence_score, occurrence_count, success_count, failure_count,
                     created_by_user_id, source)
                    VALUES (:label_norm, :label_raw, :profile_field, :category,
                            :confidence, 1, :success_count, :failure_count,
                            :user_id, 'gemini_ai')
                """), {
                    'label_norm': normalized_label,
                    'label_raw': field_label,
                    'profile_field': profile_field,
                    'category': field_category,
                    'confidence': initial_confidence,
                    'success_count': 1 if success else 0,
                    'failure_count': 0 if success else 1,
                    'user_id': user_id
                })

                session.commit()
                session.close()

                logger.info(
                    f"PatternRecorder: Created new pattern '{field_label}' → {profile_field} "
                    f"(confidence: {initial_confidence:.2f}, success: {success})"
                )

            return True

        except Exception as e:
            logger.error(f"PatternRecorder: Failed to record pattern: {e}")
            try:
                session.rollback()
                session.close()
            except:
                pass
            return False

    def record_pattern_sync(
        self,
        field_label: str,
        profile_field: str,
        field_category: str,
        success: bool = True,
        user_id: Optional[str] = None
    ) -> bool:
        """
        Synchronous version of record_pattern for non-async contexts.

        See record_pattern() for parameter documentation.
        """
        if not self.engine or not self.SessionLocal:
            logger.warning("PatternRecorder: Database not initialized, skipping recording")
            return False

        # Privacy check
        if not self._should_record(field_label):
            logger.debug(
                f"PatternRecorder: Skipping sensitive field '{field_label}' "
                "(privacy exclusion)"
            )
            return False

        normalized_label = self._normalize_label(field_label)

        try:
            session = self.SessionLocal()

            # Check if pattern exists
            existing = session.execute(text("""
                SELECT id, success_count, failure_count, occurrence_count
                FROM field_label_patterns
                WHERE field_label_normalized = :label
                  AND profile_field = :profile_field
            """), {
                'label': normalized_label,
                'profile_field': profile_field
            }).first()

            if existing:
                # Update
                pattern_id = existing[0]
                # Handle NULL values from database
                new_success_count = (existing[1] or 0) + (1 if success else 0)
                new_failure_count = (existing[2] or 0) + (0 if success else 1)
                new_occurrence_count = (existing[3] or 0) + 1
                new_confidence = self._calculate_confidence(
                    new_success_count,
                    new_failure_count,
                    new_occurrence_count
                )

                session.execute(text("""
                    UPDATE field_label_patterns
                    SET occurrence_count = :occurrences,
                        success_count = :successes,
                        failure_count = :failures,
                        confidence_score = :confidence,
                        last_seen = NOW()
                    WHERE id = :id
                """), {
                    'id': pattern_id,
                    'occurrences': new_occurrence_count,
                    'successes': new_success_count,
                    'failures': new_failure_count,
                    'confidence': new_confidence
                })

                session.commit()
                logger.info(
                    f"PatternRecorder: Updated pattern (sync) '{field_label}' → {profile_field}"
                )

            else:
                # Insert
                initial_confidence = self.INITIAL_CONFIDENCE if success else 0.0

                session.execute(text("""
                    INSERT INTO field_label_patterns
                    (field_label_normalized, field_label_raw, profile_field, field_category,
                     confidence_score, occurrence_count, success_count, failure_count,
                     created_by_user_id, source)
                    VALUES (:label_norm, :label_raw, :profile_field, :category,
                            :confidence, 1, :success_count, :failure_count,
                            :user_id, 'gemini_ai')
                """), {
                    'label_norm': normalized_label,
                    'label_raw': field_label,
                    'profile_field': profile_field,
                    'category': field_category,
                    'confidence': initial_confidence,
                    'success_count': 1 if success else 0,
                    'failure_count': 0 if success else 1,
                    'user_id': user_id
                })

                session.commit()
                logger.info(
                    f"PatternRecorder: Created new pattern (sync) '{field_label}' → {profile_field}"
                )

            session.close()
            return True

        except Exception as e:
            logger.error(f"PatternRecorder: Failed to record pattern (sync): {e}")
            try:
                session.rollback()
                session.close()
            except:
                pass
            return False

    def _should_record(self, field_label: str) -> bool:
        """
        Check if field label should be recorded (privacy filter).

        Returns:
            True if safe to record, False if sensitive field
        """
        label_lower = field_label.lower()

        for regex in self.exclusion_regexes:
            if regex.search(label_lower):
                return False

        return True

    def _normalize_label(self, label: str) -> str:
        """
        Normalize field label for consistent storage and matching.

        Examples:
            "First Name*" → "first name"
            "E-mail:" → "e mail"
            "Phone #" → "phone"
        """
        # Convert to lowercase
        normalized = label.lower()

        # Remove punctuation and special characters
        normalized = re.sub(r'[^a-z0-9\s]', '', normalized)

        # Normalize whitespace
        normalized = re.sub(r'\s+', ' ', normalized).strip()

        return normalized

    def _calculate_confidence(
        self,
        success_count: int,
        failure_count: int,
        occurrence_count: int
    ) -> float:
        """
        Calculate confidence score for a pattern.

        Formula:
            base = success_count / (success_count + failure_count)
            frequency_boost = min(0.1, occurrence_count / 100)
            final = min(0.99, base + frequency_boost)

        Examples:
            - 10 successes, 0 failures, 10 occurrences → 0.99 (highly trusted)
            - 8 successes, 2 failures, 5 occurrences → 0.85 (trusted)
            - 5 successes, 5 failures, 3 occurrences → 0.50 (low confidence)

        Returns:
            Confidence score between 0.0 and 0.99
        """
        total_attempts = success_count + failure_count

        if total_attempts == 0:
            return 0.0

        # Base confidence from success rate
        base_confidence = success_count / total_attempts

        # Frequency boost (max +0.1 for very frequent patterns)
        frequency_boost = min(0.1, occurrence_count / 100)

        # Final confidence (capped at 0.99)
        final_confidence = min(0.99, base_confidence + frequency_boost)

        return round(final_confidence, 2)

    def get_pattern_stats(self) -> dict:
        """
        Get statistics about learned patterns (useful for monitoring).

        Returns:
            Dictionary with pattern statistics
        """
        if not self.engine or not self.SessionLocal:
            return {'error': 'Database not initialized'}

        try:
            session = self.SessionLocal()

            stats_query = text("""
                SELECT
                    COUNT(*) as total_patterns,
                    COUNT(*) FILTER (WHERE confidence_score >= 0.85) as high_confidence,
                    COUNT(*) FILTER (WHERE confidence_score >= 0.70 AND confidence_score < 0.85) as medium_confidence,
                    COUNT(*) FILTER (WHERE confidence_score < 0.70) as low_confidence,
                    SUM(occurrence_count) as total_occurrences,
                    AVG(confidence_score) as avg_confidence,
                    MAX(occurrence_count) as max_occurrences
                FROM field_label_patterns
            """)

            result = session.execute(stats_query).first()
            session.close()

            return {
                'total_patterns': result[0] or 0,
                'high_confidence_patterns': result[1] or 0,
                'medium_confidence_patterns': result[2] or 0,
                'low_confidence_patterns': result[3] or 0,
                'total_occurrences': result[4] or 0,
                'average_confidence': float(result[5]) if result[5] else 0.0,
                'max_pattern_occurrences': result[6] or 0
            }

        except Exception as e:
            logger.error(f"PatternRecorder: Failed to get stats: {e}")
            return {'error': str(e)}
