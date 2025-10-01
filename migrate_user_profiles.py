#!/usr/bin/env python3
"""
Migration script to update user_profiles table schema
"""

import sys
import os
sys.path.append(os.path.dirname(__file__))

from database_config import engine, Base, UserProfile
from sqlalchemy import text
import logging

def migrate_user_profiles():
    """Drop and recreate user_profiles table with new schema"""

    print("Starting user_profiles table migration...")

    try:
        with engine.begin() as conn:
            # Drop existing user_profiles table
            print("Dropping existing user_profiles table...")
            conn.execute(text("DROP TABLE IF EXISTS user_profiles CASCADE"))

            # Recreate with new schema
            print("Creating new user_profiles table with updated schema...")
            UserProfile.__table__.create(engine)

            print("Migration completed successfully!")
            print("New user_profiles table created with the following fields:")

            # Show new table structure
            result = conn.execute(text("SELECT column_name, data_type FROM information_schema.columns WHERE table_name = 'user_profiles' ORDER BY ordinal_position"))
            for row in result:
                print(f"   - {row[0]}: {row[1]}")

    except Exception as e:
        print(f"Migration failed: {e}")
        return False

    return True

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    success = migrate_user_profiles()
    if success:
        print("\nDatabase migration completed successfully!")
        print("You can now use the profile system with the full schema.")
    else:
        print("\nMigration failed. Please check the error messages above.")
        sys.exit(1)