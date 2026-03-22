"""
Database Migration: Add User Field Overrides Table

This migration adds the user_field_overrides table for per-user pattern learning.
It captures human fills (when AI fails) and user corrections, so the agent learns
each user's unique answers and style over time.

Two-tier lookup system:
  1. user_field_overrides  (per-user, highest priority)
  2. field_label_patterns  (global, fallback)
"""

from sqlalchemy import create_engine, text
import os
from dotenv import load_dotenv
from urllib.parse import quote_plus

load_dotenv()

DB_HOST = os.getenv('DB_HOST', 'localhost')
DB_PORT = os.getenv('DB_PORT', '5432')
DB_NAME = os.getenv('DB_NAME', 'job_agent_db')
DB_USER = os.getenv('DB_USER', 'postgres')
DB_PASSWORD = os.getenv('DB_PASSWORD', '')

encoded_password = quote_plus(DB_PASSWORD)
DATABASE_URL = f"postgresql://{DB_USER}:{encoded_password}@{DB_HOST}:{DB_PORT}/{DB_NAME}"

engine = create_engine(DATABASE_URL)


def migrate():
    print("=" * 60)
    print("DATABASE MIGRATION: Add User Field Overrides Table")
    print("=" * 60)

    with engine.connect() as conn:

        print("\nCreating user_field_overrides table...")
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS user_field_overrides (
                id                  SERIAL PRIMARY KEY,

                -- User association (required — this table is always per-user)
                user_id             UUID NOT NULL,

                -- Field identification
                field_label_normalized  TEXT NOT NULL,
                field_label_raw         TEXT NOT NULL,

                -- Mapping: what profile field does this label correspond to?
                -- NULL when AI couldn't identify the field; filled in later by background mapping.
                profile_field       VARCHAR(150),

                -- Cached value: the actual value the human typed/selected.
                -- Used directly when profile_field is NULL or when user wants to override
                -- the profile value for this specific site/label.
                field_value_cached  TEXT,

                field_category      VARCHAR(50),

                -- Source of this record
                source              VARCHAR(30) NOT NULL DEFAULT 'human_fill',
                -- 'human_fill'       : AI left field empty, human filled it
                -- 'human_correction' : AI filled field, human changed the value
                -- 'user_manual'      : user added it explicitly via UI

                -- Was AI attempted and failed before human stepped in?
                was_ai_attempted    BOOLEAN DEFAULT TRUE,

                -- Confidence & usage metrics
                confidence_score    NUMERIC(3,2) DEFAULT 0.90,
                occurrence_count    INTEGER DEFAULT 1,
                success_count       INTEGER DEFAULT 1,
                failure_count       INTEGER DEFAULT 0,

                -- Timestamps
                created_at          TIMESTAMP DEFAULT NOW(),
                last_seen           TIMESTAMP DEFAULT NOW(),
                last_used           TIMESTAMP,

                -- Optional: restrict this override to a specific site domain
                -- e.g. 'greenhouse.io' so it doesn't bleed onto other ATSes
                site_domain         VARCHAR(100),

                CONSTRAINT uq_user_field_override
                    UNIQUE (user_id, field_label_normalized, site_domain)
            );
        """))
        print("[OK] Created user_field_overrides table")

        print("\nCreating indexes...")
        conn.execute(text("""
            CREATE INDEX IF NOT EXISTS idx_ufo_user_label
                ON user_field_overrides (user_id, field_label_normalized);
        """))
        conn.execute(text("""
            CREATE INDEX IF NOT EXISTS idx_ufo_user_confidence
                ON user_field_overrides (user_id, confidence_score DESC, occurrence_count DESC);
        """))

        # Fuzzy index if pg_trgm is available
        try:
            conn.execute(text("""
                CREATE INDEX IF NOT EXISTS idx_ufo_label_trgm
                    ON user_field_overrides
                    USING gin(field_label_normalized gin_trgm_ops);
            """))
            print("[OK] Created fuzzy GIN index on field_label_normalized")
        except Exception:
            print("[WARNING] pg_trgm not available — fuzzy matching disabled for user overrides")

        conn.commit()
        print("[OK] Indexes created")

    print("\n" + "=" * 60)
    print("MIGRATION COMPLETED SUCCESSFULLY")
    print("=" * 60)
    print("\nNew table: user_field_overrides")
    print("  - Per-user field label → profile field mappings")
    print("  - Captures human fills and human corrections")
    print("  - Checked BEFORE global field_label_patterns")
    print("  - Optional site_domain scoping")
    return True


def rollback():
    print("=" * 60)
    print("ROLLING BACK: user_field_overrides")
    print("=" * 60)
    with engine.connect() as conn:
        conn.execute(text("DROP TABLE IF EXISTS user_field_overrides CASCADE;"))
        conn.commit()
    print("[OK] Dropped user_field_overrides table")


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "--rollback":
        rollback()
    else:
        migrate()
