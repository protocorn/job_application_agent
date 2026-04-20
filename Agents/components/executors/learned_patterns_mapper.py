"""
Learned Patterns Mapper - Uses global database of learned field mappings

This mapper queries the field_label_patterns database to reuse successful
field label → profile field mappings, reducing expensive AI API calls by 60-80%.

Lookup priority (highest → lowest):
  1. user_field_overrides  — per-user human fills / corrections (no occurrence minimum)
  2. field_label_patterns  — global AI-learned patterns (confidence ≥ 0.70, occurrences ≥ 2)
  3. Gemini AI             — fallback when no pattern exists

Privacy: Only uses labels and mappings, never stores actual field values.
"""
import re
from typing import Dict, Optional, Any
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from loguru import logger
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
import os
from dotenv import load_dotenv
from urllib.parse import quote_plus

load_dotenv()


@dataclass
class LearnedPattern:
    """Represents a learned field mapping pattern."""
    profile_field: str  # e.g., "veteran_status"
    field_category: str  # e.g., "dropdown"
    confidence_score: float  # 0.0 to 0.99
    occurrence_count: int  # Number of times seen
    last_used: Optional[datetime]
    pattern_id: int  # Database ID for tracking
    # For user overrides: the cached value the human typed (may be None)
    cached_value: Optional[str] = field(default=None)
    source: str = field(default="global")  # "global" | "user_override"


class LearnedPatternsMapper:
    """
    Maps form fields using learned patterns from the global database.

    Strategy:
    1. Normalize field label
    2. Check in-memory cache (5-min TTL)
    3. Query user_field_overrides first (per-user, no occurrence minimum)
    4. Query field_label_patterns (global, confidence >= 0.70 and occurrences >= 2)
    5. Return pattern or None (triggers Gemini AI fallback)
    """

    # Confidence threshold for auto-using global patterns
    MIN_CONFIDENCE = 0.70
    MIN_OCCURRENCES = 2

    # User overrides are always trusted if confidence >= this (human = correct by definition)
    MIN_CONFIDENCE_USER_OVERRIDE = 0.50

    # Fuzzy matching similarity threshold (pg_trgm)
    FUZZY_SIMILARITY_THRESHOLD = 0.75

    # Cache TTL (5 minutes)
    CACHE_TTL_SECONDS = 300

    def __init__(self, user_id: Optional[str] = None, site_domain: Optional[str] = None):
        """
        Initialize mapper with database connection and cache.

        Args:
            user_id:     When provided, user_field_overrides is checked first.
            site_domain: Optional domain for site-scoped user overrides (e.g. 'greenhouse.io').
        """
        self.user_id = user_id
        self.site_domain = site_domain
        self.pattern_cache = {}  # {cache_key: (pattern, timestamp)}
        self.cache_timestamps = {}
        self._init_database()

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
            logger.info("LearnedPatternsMapper: Database connection initialized")

        except Exception as e:
            logger.error(f"LearnedPatternsMapper: Failed to initialize database: {e}")
            self.engine = None
            self.SessionLocal = None

    def map_field(
        self,
        field_label: str,
        field_category: str,
        profile: Dict[str, Any],
        user_id: Optional[str] = None,
        site_domain: Optional[str] = None,
    ) -> Optional[LearnedPattern]:
        """
        Map a field label to a profile field using learned patterns.

        Lookup order:
          1. user_field_overrides  (if user_id provided) — no occurrence minimum
          2. field_label_patterns  (global)              — confidence ≥ 0.70, occurrences ≥ 2

        Args:
            field_label:   The visible label of the form field
            field_category: Type of field (dropdown, text_input, etc.)
            profile:       User profile dict (not used for mapping, kept for consistency)
            user_id:       Override the instance-level user_id for this call
            site_domain:   Override the instance-level site_domain for this call

        Returns:
            LearnedPattern if found with sufficient confidence, None otherwise
        """
        if not self.engine:
            return None

        effective_user_id    = user_id    or self.user_id
        effective_site_domain = site_domain or self.site_domain

        # Normalize label
        normalized_label = self._normalize_label(field_label)

        # Cache key includes user so per-user overrides don't bleed into global cache
        cache_key = f"{effective_user_id or 'global'}:{normalized_label}"

        # Check cache first
        cached_pattern = self._get_from_cache(cache_key)
        if cached_pattern is not None:
            logger.debug(f"LearnedPatternsMapper: Cache hit for '{field_label}'")
            return cached_pattern

        pattern = None

        # ── Priority 1: per-user overrides ──────────────────────────────────
        if effective_user_id:
            pattern = self._query_user_override(
                normalized_label, field_category, effective_user_id, effective_site_domain
            )
            if pattern:
                logger.info(
                    f"LearnedPatternsMapper: [USER OVERRIDE] '{field_label}' → "
                    f"{pattern.profile_field or '(cached value)'} "
                    f"(confidence: {pattern.confidence_score:.2f})"
                )

        # ── Priority 2: global learned patterns ─────────────────────────────
        if pattern is None:
            pattern = self._query_exact_match(normalized_label, field_category)

        if pattern is None:
            pattern = self._query_fuzzy_match(normalized_label, field_category)

        # Cache the result (even if None, to avoid repeated queries)
        if pattern:
            self._add_to_cache(cache_key, pattern)
            if pattern.source == "global":
                logger.info(
                    f"LearnedPatternsMapper: Mapped '{field_label}' → {pattern.profile_field} "
                    f"(confidence: {pattern.confidence_score:.2f}, occurrences: {pattern.occurrence_count})"
            )
        else:
            logger.debug(f"LearnedPatternsMapper: No pattern found for '{field_label}'")

        return pattern

    def _query_user_override(
        self,
        normalized_label: str,
        field_category: str,
        user_id: str,
        site_domain: Optional[str] = None,
    ) -> Optional[LearnedPattern]:
        """
        Query user_field_overrides for a per-user pattern.

        No occurrence minimum — a single human fill is trusted immediately.
        Tries exact match first, then fuzzy (if pg_trgm available).
        Site-domain-scoped rows are preferred over global rows (NULL site_domain).
        """
        if not self.SessionLocal:
            return None

        try:
            session = self.SessionLocal()

            result = session.execute(text("""
                SELECT
                    id,
                    profile_field,
                    field_category,
                    confidence_score,
                    occurrence_count,
                    last_used,
                    field_value_cached,
                    -- Prefer site-scoped rows over global rows
                    CASE WHEN site_domain = :site_domain THEN 1 ELSE 0 END AS scope_rank
                FROM user_field_overrides
                WHERE user_id = :user_id
                  AND field_label_normalized = :label
                  AND confidence_score >= :min_confidence
                  AND (site_domain = :site_domain OR site_domain IS NULL)
                ORDER BY scope_rank DESC, confidence_score DESC, occurrence_count DESC
                LIMIT 1
            """), {
                'user_id':      user_id,
                'label':        normalized_label,
                'site_domain':  site_domain,
                'min_confidence': self.MIN_CONFIDENCE_USER_OVERRIDE,
            }).first()

            if result:
                session.close()
                return LearnedPattern(
                    profile_field=result[1] or "",
                    field_category=result[2] or field_category,
                    confidence_score=float(result[3]),
                    occurrence_count=result[4],
                    last_used=result[5],
                    pattern_id=result[0],
                    cached_value=result[6],
                    source="user_override",
                )

            # Fuzzy fallback for user overrides
            try:
                result = session.execute(text("""
                    SELECT
                        id,
                        profile_field,
                        field_category,
                        confidence_score,
                        occurrence_count,
                        last_used,
                        field_value_cached,
                        similarity(field_label_normalized, :label) AS sim
                    FROM user_field_overrides
                    WHERE user_id = :user_id
                      AND similarity(field_label_normalized, :label) > :sim_threshold
                      AND confidence_score >= :min_confidence
                      AND (site_domain = :site_domain OR site_domain IS NULL)
                    ORDER BY sim DESC, confidence_score DESC
                    LIMIT 1
                """), {
                    'user_id':        user_id,
                    'label':          normalized_label,
                    'site_domain':    site_domain,
                    'sim_threshold':  self.FUZZY_SIMILARITY_THRESHOLD,
                    'min_confidence': self.MIN_CONFIDENCE_USER_OVERRIDE,
                }).first()

                if result:
                    logger.debug(
                        f"LearnedPatternsMapper: Fuzzy user override match "
                        f"(similarity: {result[7]:.2f})"
                    )
                    return LearnedPattern(
                        profile_field=result[1] or "",
                        field_category=result[2] or field_category,
                        confidence_score=float(result[3]),
                        occurrence_count=result[4],
                        last_used=result[5],
                        pattern_id=result[0],
                        cached_value=result[6],
                        source="user_override",
                    )
            except Exception:
                pass  # pg_trgm not available; that's fine

            session.close()
            return None

        except Exception as e:
            logger.error(f"LearnedPatternsMapper: User override query failed: {e}")
            return None

    def _normalize_label(self, label: str) -> str:
        """
        Normalize field label for consistent matching.

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

    def _query_exact_match(
        self,
        normalized_label: str,
        field_category: str
    ) -> Optional[LearnedPattern]:
        """
        Query database for exact normalized label match.

        Uses multi-factor ranking when multiple patterns exist:
        1. Exact field_category match (priority)
        2. High confidence score
        3. High occurrence count
        4. Recent usage
        """
        if not self.SessionLocal:
            return None

        try:
            session = self.SessionLocal()

            query = text("""
                SELECT
                    id,
                    profile_field,
                    field_category,
                    confidence_score,
                    occurrence_count,
                    last_used,
                    (
                        CASE WHEN field_category = :category THEN 10 ELSE 0 END +
                        (confidence_score::float * 5) +
                        (LN(occurrence_count + 1) * 2) +
                        COALESCE(EXTRACT(EPOCH FROM (NOW() - last_used)) / 86400 * -0.1, 0)
                    ) as rank_score
                FROM field_label_patterns
                WHERE field_label_normalized = :label
                  AND confidence_score >= :min_confidence
                  AND occurrence_count >= :min_occurrences
                ORDER BY rank_score DESC
                LIMIT 1
            """)

            result = session.execute(query, {
                'label': normalized_label,
                'category': field_category,
                'min_confidence': self.MIN_CONFIDENCE,
                'min_occurrences': self.MIN_OCCURRENCES
            }).first()

            session.close()

            if result:
                return LearnedPattern(
                    profile_field=result[1],
                    field_category=result[2],
                    confidence_score=float(result[3]),
                    occurrence_count=result[4],
                    last_used=result[5],
                    pattern_id=result[0],
                    source="global",
                )

            return None

        except Exception as e:
            logger.error(f"LearnedPatternsMapper: Exact match query failed: {e}")
            return None

    def _query_fuzzy_match(
        self,
        normalized_label: str,
        field_category: str
    ) -> Optional[LearnedPattern]:
        """
        Query database using fuzzy matching (pg_trgm).

        Similarity threshold: 0.75 (high precision to avoid incorrect matches)

        Examples of 0.75+ similarity:
            - "first name" ↔ "fname" (similar)
            - "first name" ↔ "last name" (NOT similar, different words)
        """
        if not self.SessionLocal:
            return None

        try:
            session = self.SessionLocal()

            query = text("""
                SELECT
                    id,
                    profile_field,
                    field_category,
                    confidence_score,
                    occurrence_count,
                    last_used,
                    similarity(field_label_normalized, :label) as sim,
                    (
                        similarity(field_label_normalized, :label) * 10 +
                        CASE WHEN field_category = :category THEN 5 ELSE 0 END +
                        (confidence_score::float * 3) +
                        (LN(occurrence_count + 1))
                    ) as rank_score
                FROM field_label_patterns
                WHERE similarity(field_label_normalized, :label) > :similarity_threshold
                  AND confidence_score >= :min_confidence
                  AND occurrence_count >= :min_occurrences
                ORDER BY rank_score DESC
                LIMIT 1
            """)

            result = session.execute(query, {
                'label': normalized_label,
                'category': field_category,
                'similarity_threshold': self.FUZZY_SIMILARITY_THRESHOLD,
                'min_confidence': self.MIN_CONFIDENCE,
                'min_occurrences': self.MIN_OCCURRENCES
            }).first()

            session.close()

            if result:
                logger.debug(
                    f"LearnedPatternsMapper: Fuzzy match found "
                    f"(similarity: {result[6]:.2f})"
                )
                return LearnedPattern(
                    profile_field=result[1],
                    field_category=result[2],
                    confidence_score=float(result[3]),
                    occurrence_count=result[4],
                    last_used=result[5],
                    pattern_id=result[0],
                    source="global",
                )

            return None

        except Exception as e:
            logger.warning(f"LearnedPatternsMapper: Fuzzy match query failed: {e}")
            logger.warning("  pg_trgm extension may not be enabled. Fuzzy matching disabled.")
            return None

    def _get_from_cache(self, normalized_label: str) -> Optional[LearnedPattern]:
        """Get pattern from in-memory cache if not expired."""
        if normalized_label not in self.pattern_cache:
            return None

        # Check if cache entry is expired
        cached_time = self.cache_timestamps.get(normalized_label)
        if not cached_time:
            return None

        age_seconds = (datetime.now() - cached_time).total_seconds()
        if age_seconds > self.CACHE_TTL_SECONDS:
            # Expired, remove from cache
            del self.pattern_cache[normalized_label]
            del self.cache_timestamps[normalized_label]
            return None

        return self.pattern_cache[normalized_label]

    def _add_to_cache(self, normalized_label: str, pattern: LearnedPattern):
        """Add pattern to in-memory cache."""
        self.pattern_cache[normalized_label] = pattern
        self.cache_timestamps[normalized_label] = datetime.now()

    # Combo fields: profile_field value → how to build the combined string.
    # Only add combos that are 100% unambiguous across every job form.
    COMBO_FIELDS: Dict[str, list] = {
        'full_name': ['first_name', 'last_name'],
    }

    def get_profile_value(self, profile: Dict[str, Any], profile_field: str) -> Optional[Any]:
        """
        Extract value from profile using the mapped profile_field.

        Handles:
          - Combo fields (full_name → "First Last")
          - Direct key access
          - Common key variations (spaces, title-case, no-underscore)

        Args:
            profile: User profile dictionary
            profile_field: Field name to extract (e.g., "first_name", "full_name")

        Returns:
            Value from profile, or None if not found
        """
        # ── Combo field handling ─────────────────────────────────────────────
        if profile_field in self.COMBO_FIELDS:
            parts = []
            for sub_field in self.COMBO_FIELDS[profile_field]:
                val = self.get_profile_value(profile, sub_field)
                if val:
                    parts.append(str(val).strip())
            if parts:
                return ' '.join(parts)
            logger.debug(
                f"LearnedPatternsMapper: Combo field '{profile_field}' "
                f"— no sub-fields resolved"
            )
            return None

        # ── Direct key access ────────────────────────────────────────────────
        if profile_field in profile:
            return profile[profile_field]

        # Try with spaces (e.g., "first_name" → "first name")
        spaced_key = profile_field.replace('_', ' ')
        if spaced_key in profile:
            return profile[spaced_key]

        # Try common variations
        variations = [
            profile_field.replace('_', ' ').title(),  # "first_name" → "First Name"
            profile_field.replace('_', ''),            # "first_name" → "firstname"
        ]

        for variation in variations:
            if variation in profile:
                return profile[variation]

        logger.debug(
            f"LearnedPatternsMapper: Profile field '{profile_field}' not found in profile"
        )
        return None

    def clear_cache(self):
        """Clear the in-memory cache (useful for testing)."""
        self.pattern_cache = {}
        self.cache_timestamps = {}
        logger.debug("LearnedPatternsMapper: Cache cleared")

    def get_cache_stats(self) -> Dict[str, int]:
        """Get cache statistics (useful for monitoring)."""
        total_entries = len(self.pattern_cache)
        expired_entries = sum(
            1 for label, timestamp in self.cache_timestamps.items()
            if (datetime.now() - timestamp).total_seconds() > self.CACHE_TTL_SECONDS
        )

        return {
            'total_entries': total_entries,
            'active_entries': total_entries - expired_entries,
            'expired_entries': expired_entries,
            'cache_ttl_seconds': self.CACHE_TTL_SECONDS
        }
