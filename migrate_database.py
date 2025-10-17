"""
Database Migration Script
Adds new columns to user_profiles and job_listings tables
"""

import os
import sys
from sqlalchemy import text, inspect
from dotenv import load_dotenv

# Add current directory to path
sys.path.append(os.path.dirname(__file__))

from database_config import engine, SessionLocal
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def column_exists(table_name, column_name):
    """Check if a column exists in a table"""
    inspector = inspect(engine)
    columns = [col['name'] for col in inspector.get_columns(table_name)]
    return column_name in columns

def migrate_user_profiles():
    """Add new columns to user_profiles table"""
    db = SessionLocal()
    try:
        logger.info("Migrating user_profiles table...")

        migrations = [
            # Job Search Preferences
            ("open_to_remote", "ALTER TABLE user_profiles ADD COLUMN open_to_remote BOOLEAN DEFAULT FALSE"),
            ("open_to_anywhere", "ALTER TABLE user_profiles ADD COLUMN open_to_anywhere BOOLEAN DEFAULT FALSE"),
            ("preferred_cities", "ALTER TABLE user_profiles ADD COLUMN preferred_cities JSON"),
            ("preferred_states", "ALTER TABLE user_profiles ADD COLUMN preferred_states JSON"),

            # Salary & Experience Preferences
            ("minimum_salary", "ALTER TABLE user_profiles ADD COLUMN minimum_salary INTEGER"),
            ("maximum_salary", "ALTER TABLE user_profiles ADD COLUMN maximum_salary INTEGER"),
            ("salary_currency", "ALTER TABLE user_profiles ADD COLUMN salary_currency VARCHAR DEFAULT 'USD'"),
            ("years_of_experience", "ALTER TABLE user_profiles ADD COLUMN years_of_experience INTEGER"),
            ("desired_job_types", "ALTER TABLE user_profiles ADD COLUMN desired_job_types JSON"),
            ("desired_experience_levels", "ALTER TABLE user_profiles ADD COLUMN desired_experience_levels JSON"),
        ]

        for column_name, sql in migrations:
            if not column_exists('user_profiles', column_name):
                logger.info(f"Adding column: {column_name}")
                db.execute(text(sql))
                db.commit()
                logger.info(f"✓ Added {column_name}")
            else:
                logger.info(f"✓ Column {column_name} already exists")

        logger.info("user_profiles migration complete!")

    except Exception as e:
        logger.error(f"Error migrating user_profiles: {e}")
        db.rollback()
        raise
    finally:
        db.close()

def migrate_job_listings():
    """Add new columns to job_listings table"""
    db = SessionLocal()
    try:
        logger.info("Migrating job_listings table...")

        migrations = [
            # Additional job metadata
            ("job_type", "ALTER TABLE job_listings ADD COLUMN job_type VARCHAR"),
            ("experience_level", "ALTER TABLE job_listings ADD COLUMN experience_level VARCHAR"),
            ("is_remote", "ALTER TABLE job_listings ADD COLUMN is_remote BOOLEAN DEFAULT FALSE"),
            ("salary_min", "ALTER TABLE job_listings ADD COLUMN salary_min INTEGER"),
            ("salary_max", "ALTER TABLE job_listings ADD COLUMN salary_max INTEGER"),
            ("salary_currency", "ALTER TABLE job_listings ADD COLUMN salary_currency VARCHAR"),

            # Relevance scoring
            ("relevance_score", "ALTER TABLE job_listings ADD COLUMN relevance_score INTEGER DEFAULT 0"),
            ("user_id", "ALTER TABLE job_listings ADD COLUMN user_id INTEGER REFERENCES users(id)"),
        ]

        for column_name, sql in migrations:
            if not column_exists('job_listings', column_name):
                logger.info(f"Adding column: {column_name}")
                db.execute(text(sql))
                db.commit()
                logger.info(f"✓ Added {column_name}")
            else:
                logger.info(f"✓ Column {column_name} already exists")

        logger.info("job_listings migration complete!")

    except Exception as e:
        logger.error(f"Error migrating job_listings: {e}")
        db.rollback()
        raise
    finally:
        db.close()

def main():
    """Run all migrations"""
    logger.info("=" * 60)
    logger.info("Starting Database Migration")
    logger.info("=" * 60)

    try:
        # Test connection
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        logger.info("✓ Database connection successful")

        # Run migrations
        migrate_user_profiles()
        print()  # Empty line for readability
        migrate_job_listings()

        logger.info("=" * 60)
        logger.info("✓ All migrations completed successfully!")
        logger.info("=" * 60)

    except Exception as e:
        logger.error("=" * 60)
        logger.error(f"✗ Migration failed: {e}")
        logger.error("=" * 60)
        sys.exit(1)

if __name__ == "__main__":
    main()
