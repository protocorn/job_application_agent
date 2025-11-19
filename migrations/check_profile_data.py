"""
Check what data exists in user_profiles table
"""

import sys
from pathlib import Path

# Add parent directory to path
sys.path.append(str(Path(__file__).parent.parent))

from database_config import SessionLocal, User, UserProfile
from sqlalchemy import text
import json

def check_profile_data():
    """Check what data exists in user_profiles"""
    db = SessionLocal()

    try:
        print("\n" + "="*80)
        print("CHECKING USER_PROFILES DATA")
        print("="*80)

        # Check table structure
        print("\n1. Checking table structure...")
        result = db.execute(text("""
            SELECT column_name, data_type
            FROM information_schema.columns
            WHERE table_name = 'user_profiles'
            ORDER BY ordinal_position;
        """))

        columns = result.fetchall()
        print(f"Found {len(columns)} columns:")
        for col in columns:
            print(f"  - {col[0]}: {col[1]}")

        # Check if backup table exists
        print("\n2. Checking for backup table...")
        backup_exists = db.execute(text("""
            SELECT EXISTS (
                SELECT FROM information_schema.tables
                WHERE table_schema = 'public'
                AND table_name = 'user_profiles_backup'
            );
        """)).scalar()

        print(f"Backup table exists: {backup_exists}")

        # Count rows
        print("\n3. Counting rows...")
        profile_count = db.query(UserProfile).count()
        user_count = db.query(User).count()

        print(f"Users: {user_count}")
        print(f"User Profiles: {profile_count}")

        # Show sample data
        print("\n4. Sample user profile data...")
        profiles = db.query(UserProfile).limit(3).all()

        for i, profile in enumerate(profiles, 1):
            print(f"\nProfile {i}:")
            print(f"  ID: {profile.id}")
            print(f"  User ID: {profile.user_id}")
            print(f"  Phone: {profile.phone or '(empty)'}")
            print(f"  LinkedIn: {profile.linkedin or '(empty)'}")
            print(f"  GitHub: {profile.github or '(empty)'}")
            print(f"  Resume URL: {profile.resume_url or '(empty)'}")

            if profile.education:
                print(f"  Education entries: {len(profile.education)}")
                for j, edu in enumerate(profile.education[:2], 1):
                    print(f"    {j}. {edu.get('degree', '')} at {edu.get('institution', '')}")
            else:
                print(f"  Education: (empty)")

            if profile.work_experience:
                print(f"  Work experience entries: {len(profile.work_experience)}")
                for j, work in enumerate(profile.work_experience[:2], 1):
                    print(f"    {j}. {work.get('title', '')} at {work.get('company', '')}")
            else:
                print(f"  Work experience: (empty)")

        # Check backup data if it exists
        if backup_exists:
            print("\n5. Checking backup table data...")
            backup_count = db.execute(text("SELECT COUNT(*) FROM user_profiles_backup")).scalar()
            print(f"Backup profiles: {backup_count}")

            if backup_count > 0:
                print("\nSample from backup:")
                backup_sample = db.execute(text("""
                    SELECT user_id, phone, linkedin, github
                    FROM user_profiles_backup
                    LIMIT 3
                """)).fetchall()

                for i, row in enumerate(backup_sample, 1):
                    print(f"\n  Backup {i}:")
                    print(f"    User ID: {row[0]}")
                    print(f"    Phone: {row[1] or '(empty)'}")
                    print(f"    LinkedIn: {row[2] or '(empty)'}")
                    print(f"    GitHub: {row[3] or '(empty)'}")

        print("\n" + "="*80)
        print("DIAGNOSIS COMPLETE")
        print("="*80)

    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()

    finally:
        db.close()


if __name__ == "__main__":
    check_profile_data()
