"""
Migration: Convert user_profiles.id from Integer to UUID (Fixed Version)

This migration automatically detects existing columns and migrates them properly.

Run with: python migrations/migrate_user_profiles_to_uuid_fixed.py
"""

import sys
import os
from pathlib import Path

# Add parent directory to path
sys.path.append(str(Path(__file__).parent.parent))

from database_config import SessionLocal, engine
from sqlalchemy import text
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def get_existing_columns():
    """Get list of columns that exist in the current user_profiles table"""
    connection = engine.connect()

    try:
        result = connection.execute(text("""
            SELECT column_name
            FROM information_schema.columns
            WHERE table_name = 'user_profiles_backup'
            AND table_schema = 'public'
            ORDER BY ordinal_position;
        """))

        columns = [row[0] for row in result.fetchall()]
        logger.info(f"Found {len(columns)} columns in backup table")
        return columns

    finally:
        connection.close()


def migrate_user_profiles_to_uuid():
    """Migrate user_profiles table to use UUID primary key"""

    logger.info("Starting migration: user_profiles.id → UUID")

    # Create a connection
    connection = engine.connect()

    try:
        # Start transaction
        trans = connection.begin()

        # Check if backup already exists from previous failed attempt
        check_backup = connection.execute(text("""
            SELECT EXISTS (
                SELECT FROM information_schema.tables
                WHERE table_schema = 'public'
                AND table_name = 'user_profiles_backup'
            );
        """)).scalar()

        if check_backup:
            logger.info("Found existing backup from previous attempt, using it...")
        else:
            logger.info("Step 1: Creating backup of user_profiles table...")
            connection.execute(text("""
                CREATE TABLE IF NOT EXISTS user_profiles_backup AS
                SELECT * FROM user_profiles;
            """))
            logger.info("✓ Backup created: user_profiles_backup")

        # Get existing columns from backup
        existing_columns = get_existing_columns()
        logger.info(f"Existing columns: {', '.join(existing_columns)}")

        logger.info("Step 2: Dropping existing user_profiles table...")
        connection.execute(text("DROP TABLE IF EXISTS user_profiles CASCADE;"))
        logger.info("✓ Table dropped")

        logger.info("Step 3: Creating new user_profiles table with UUID primary key...")
        connection.execute(text("""
            CREATE TABLE user_profiles (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                user_id UUID UNIQUE NOT NULL REFERENCES users(id) ON DELETE CASCADE,

                -- Basic Information
                resume_url VARCHAR,
                cover_letter_template TEXT,
                date_of_birth VARCHAR,
                gender VARCHAR,
                nationality VARCHAR,
                preferred_language VARCHAR,
                phone VARCHAR,
                address TEXT,
                city VARCHAR,
                state VARCHAR,
                zip_code VARCHAR,
                country VARCHAR,
                country_code VARCHAR,
                state_code VARCHAR,

                -- Professional Links
                linkedin VARCHAR,
                github VARCHAR,
                portfolio VARCHAR,
                other_links JSONB DEFAULT '[]'::jsonb,

                -- Education (JSONB array)
                education JSONB DEFAULT '[]'::jsonb,

                -- Work Experience (JSONB array)
                work_experience JSONB DEFAULT '[]'::jsonb,

                -- Skills (JSONB array)
                skills JSONB DEFAULT '[]'::jsonb,

                -- Projects (JSONB array)
                projects JSONB DEFAULT '[]'::jsonb,

                -- Certifications (JSONB array)
                certifications JSONB DEFAULT '[]'::jsonb,

                -- Languages (JSONB array)
                languages JSONB DEFAULT '[]'::jsonb,

                -- Additional Information
                availability VARCHAR,
                work_authorization VARCHAR,
                willing_to_relocate BOOLEAN DEFAULT false,
                willing_to_travel BOOLEAN DEFAULT false,
                expected_salary VARCHAR,
                notice_period VARCHAR,

                -- Preferences
                job_preferences JSONB DEFAULT '{}'::jsonb,

                -- Timestamps
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """))
        logger.info("✓ New table created with UUID primary key")

        logger.info("Step 4: Restoring data from backup...")

        # Build column lists based on what exists in backup
        # Remove 'id' from existing columns since we'll generate new UUIDs
        restore_columns = [col for col in existing_columns if col != 'id']

        # Build the INSERT statement dynamically
        column_list = ', '.join(restore_columns)
        select_list = ', '.join([
            f"COALESCE({col}, '[]'::jsonb)" if col in ['other_links', 'education', 'work_experience', 'skills', 'projects', 'certifications', 'languages'] and col in restore_columns
            else f"COALESCE({col}, '{{}}'::jsonb)" if col == 'job_preferences' and col in restore_columns
            else col
            for col in restore_columns
        ])

        insert_query = f"""
            INSERT INTO user_profiles ({column_list})
            SELECT {select_list}
            FROM user_profiles_backup;
        """

        logger.info(f"Restoring columns: {', '.join(restore_columns)}")
        connection.execute(text(insert_query))

        row_count = connection.execute(text("SELECT COUNT(*) FROM user_profiles")).scalar()
        logger.info(f"✓ Restored {row_count} user profiles")

        logger.info("Step 5: Creating indexes...")
        connection.execute(text("""
            CREATE INDEX idx_user_profiles_user_id ON user_profiles(user_id);
        """))
        logger.info("✓ Indexes created")

        # Commit transaction
        trans.commit()
        logger.info("✓ Transaction committed")

        logger.info("\n" + "="*60)
        logger.info("MIGRATION SUCCESSFUL!")
        logger.info("="*60)
        logger.info(f"✓ user_profiles table now uses UUID primary key")
        logger.info(f"✓ {row_count} profiles migrated successfully")
        logger.info(f"✓ Backup table available: user_profiles_backup")
        logger.info("\nTo remove backup table (ONLY after verifying everything works):")
        logger.info("  DROP TABLE user_profiles_backup;")
        logger.info("="*60 + "\n")

    except Exception as e:
        logger.error(f"Migration failed: {e}")
        logger.error("Rolling back transaction...")
        trans.rollback()

        logger.info("\nAttempting to restore from backup...")
        try:
            # Restore from backup if it exists
            check_backup = connection.execute(text("""
                SELECT EXISTS (
                    SELECT FROM information_schema.tables
                    WHERE table_schema = 'public'
                    AND table_name = 'user_profiles_backup'
                );
            """)).scalar()

            if check_backup:
                connection.execute(text("DROP TABLE IF EXISTS user_profiles;"))
                connection.execute(text("""
                    CREATE TABLE user_profiles AS SELECT * FROM user_profiles_backup;
                """))
                connection.execute(text("""
                    ALTER TABLE user_profiles ADD PRIMARY KEY (id);
                """))
                logger.info("✓ Restored from backup")
            else:
                logger.error("No backup table found!")

        except Exception as restore_error:
            logger.error(f"Failed to restore from backup: {restore_error}")
            logger.error("MANUAL INTERVENTION REQUIRED!")

        raise

    finally:
        connection.close()


def verify_migration():
    """Verify the migration was successful"""
    logger.info("\nVerifying migration...")

    connection = engine.connect()

    try:
        # Check column type
        result = connection.execute(text("""
            SELECT column_name, data_type
            FROM information_schema.columns
            WHERE table_name = 'user_profiles'
            AND column_name IN ('id', 'user_id')
            ORDER BY column_name;
        """))

        columns = result.fetchall()
        logger.info("\nColumn types:")
        for col in columns:
            logger.info(f"  {col[0]}: {col[1]}")

        # Check row count
        count = connection.execute(text("SELECT COUNT(*) FROM user_profiles")).scalar()
        logger.info(f"\nTotal profiles: {count}")

        # Check a sample
        sample = connection.execute(text("""
            SELECT id, user_id
            FROM user_profiles
            LIMIT 1;
        """)).fetchone()

        if sample:
            logger.info(f"\nSample row:")
            logger.info(f"  id (UUID): {sample[0]}")
            logger.info(f"  user_id (UUID): {sample[1]}")

        logger.info("\n✓ Migration verification complete")

    finally:
        connection.close()


if __name__ == "__main__":
    print("\n" + "="*60)
    print("USER_PROFILES TABLE MIGRATION TO UUID (FIXED)")
    print("="*60)
    print("\nThis will:")
    print("  1. Use existing backup or create new one")
    print("  2. Drop and recreate user_profiles with UUID primary key")
    print("  3. Restore all data from backup (only existing columns)")
    print("  4. Verify the migration")
    print("\nWARNING: This modifies your database schema!")
    print("="*60)

    response = input("\nDo you want to proceed? [yes/no]: ").strip().lower()

    if response != 'yes':
        print("Migration cancelled.")
        sys.exit(0)

    try:
        migrate_user_profiles_to_uuid()
        verify_migration()

        print("\n" + "="*60)
        print("MIGRATION COMPLETED SUCCESSFULLY!")
        print("="*60)
        print("\nNext steps:")
        print("  1. Test your application thoroughly")
        print("  2. Verify all user profiles work correctly")
        print("  3. Once confirmed, drop the backup table:")
        print("     DROP TABLE user_profiles_backup;")
        print("="*60 + "\n")

    except Exception as e:
        print("\n" + "="*60)
        print("MIGRATION FAILED!")
        print("="*60)
        print(f"Error: {e}")
        print("\nThe backup table (user_profiles_backup) should still exist.")
        print("Your original data should be restored.")
        print("="*60 + "\n")
        sys.exit(1)
