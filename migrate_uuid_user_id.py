"""
Migration script to convert user IDs from sequential INTEGER to UUID
This migration ensures better security by preventing user enumeration

Steps:
1. Add a new UUID column to users table
2. Generate UUIDs for all existing users
3. Update all foreign key references in related tables
4. Drop old integer columns and constraints
5. Rename UUID columns to be the primary keys
"""

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
import os
from dotenv import load_dotenv
from urllib.parse import quote_plus
import uuid

load_dotenv()

# Database Configuration
DB_HOST = os.getenv('DB_HOST', 'localhost')
DB_PORT = os.getenv('DB_PORT', '5432')
DB_NAME = os.getenv('DB_NAME', 'job_agent_db')
DB_USER = os.getenv('DB_USER', 'postgres')
DB_PASSWORD = os.getenv('DB_PASSWORD')

if not DB_PASSWORD:
    raise ValueError("DB_PASSWORD environment variable is required")

encoded_password = quote_plus(DB_PASSWORD)
DATABASE_URL = f"postgresql://{DB_USER}:{encoded_password}@{DB_HOST}:{DB_PORT}/{DB_NAME}"

engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

def run_migration():
    """Execute the migration to convert user IDs to UUIDs"""
    db = SessionLocal()

    try:
        print("Starting UUID migration...")

        # Step 1: Enable UUID extension in PostgreSQL
        print("\n1. Enabling UUID extension...")
        db.execute(text("""
            CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
        """))
        db.commit()
        print("   ✓ UUID extension enabled")

        # Step 2: Add UUID columns to users table
        print("\n2. Adding UUID column to users table...")
        db.execute(text("""
            ALTER TABLE public.users
            ADD COLUMN IF NOT EXISTS uuid_id UUID DEFAULT uuid_generate_v4() UNIQUE NOT NULL;
        """))
        db.commit()
        print("   ✓ UUID column added to users table")

        # Step 3: Add UUID columns to all related tables
        print("\n3. Adding UUID columns to related tables...")

        # UserProfile
        db.execute(text("""
            ALTER TABLE public.user_profiles
            ADD COLUMN IF NOT EXISTS user_uuid UUID;
        """))

        # JobApplication
        db.execute(text("""
            ALTER TABLE public.job_applications
            ADD COLUMN IF NOT EXISTS user_uuid UUID;
        """))

        # ActionHistory
        db.execute(text("""
            ALTER TABLE public.action_history
            ADD COLUMN IF NOT EXISTS user_uuid UUID;
        """))

        # BetaFeedback
        db.execute(text("""
            ALTER TABLE public.beta_feedback
            ADD COLUMN IF NOT EXISTS user_uuid UUID;
        """))

        db.commit()
        print("   ✓ UUID columns added to related tables")

        # Step 4: Populate UUID columns in related tables
        print("\n4. Populating UUID columns in related tables...")

        db.execute(text("""
            UPDATE public.user_profiles
            SET user_uuid = users.uuid_id
            FROM public.users
            WHERE user_profiles.user_id = users.id;
        """))

        db.execute(text("""
            UPDATE public.job_applications
            SET user_uuid = users.uuid_id
            FROM public.users
            WHERE job_applications.user_id = users.id;
        """))

        db.execute(text("""
            UPDATE public.action_history
            SET user_uuid = users.uuid_id
            FROM public.users
            WHERE action_history.user_id = users.id;
        """))

        db.execute(text("""
            UPDATE public.beta_feedback
            SET user_uuid = users.uuid_id
            FROM public.users
            WHERE beta_feedback.user_id = users.id;
        """))

        db.commit()
        print("   ✓ UUID columns populated in related tables")

        # Step 5: Drop old foreign key constraints
        print("\n5. Dropping old foreign key constraints...")

        # Get all foreign key constraint names
        result = db.execute(text("""
            SELECT
                tc.constraint_name,
                tc.table_name
            FROM information_schema.table_constraints tc
            WHERE tc.constraint_type = 'FOREIGN KEY'
            AND tc.table_schema = 'public'
            AND tc.constraint_name LIKE '%user_id%'
            OR tc.constraint_name LIKE '%users_id%';
        """))

        constraints = result.fetchall()
        for constraint in constraints:
            constraint_name, table_name = constraint
            print(f"   Dropping constraint {constraint_name} from {table_name}...")
            db.execute(text(f"""
                ALTER TABLE public.{table_name}
                DROP CONSTRAINT IF EXISTS {constraint_name};
            """))

        db.commit()
        print("   ✓ Old foreign key constraints dropped")

        # Step 6: Drop old integer ID columns from related tables
        print("\n6. Dropping old integer user_id columns...")

        db.execute(text("""
            ALTER TABLE public.user_profiles
            DROP COLUMN IF EXISTS user_id;
        """))

        db.execute(text("""
            ALTER TABLE public.job_applications
            DROP COLUMN IF EXISTS user_id;
        """))

        db.execute(text("""
            ALTER TABLE public.action_history
            DROP COLUMN IF EXISTS user_id;
        """))

        db.execute(text("""
            ALTER TABLE public.beta_feedback
            DROP COLUMN IF EXISTS user_id;
        """))

        db.commit()
        print("   ✓ Old integer user_id columns dropped")

        # Step 7: Rename UUID columns to user_id
        print("\n7. Renaming UUID columns to user_id...")

        db.execute(text("""
            ALTER TABLE public.user_profiles
            RENAME COLUMN user_uuid TO user_id;
        """))

        db.execute(text("""
            ALTER TABLE public.job_applications
            RENAME COLUMN user_uuid TO user_id;
        """))

        db.execute(text("""
            ALTER TABLE public.action_history
            RENAME COLUMN user_uuid TO user_id;
        """))

        db.execute(text("""
            ALTER TABLE public.beta_feedback
            RENAME COLUMN user_uuid TO user_id;
        """))

        db.commit()
        print("   ✓ UUID columns renamed to user_id")

        # Step 8: Add NOT NULL constraints to user_id columns
        print("\n8. Adding NOT NULL constraints...")

        db.execute(text("""
            ALTER TABLE public.user_profiles
            ALTER COLUMN user_id SET NOT NULL;
        """))

        db.execute(text("""
            ALTER TABLE public.job_applications
            ALTER COLUMN user_id SET NOT NULL;
        """))

        db.execute(text("""
            ALTER TABLE public.action_history
            ALTER COLUMN user_id SET NOT NULL;
        """))

        db.execute(text("""
            ALTER TABLE public.beta_feedback
            ALTER COLUMN user_id SET NOT NULL;
        """))

        db.commit()
        print("   ✓ NOT NULL constraints added")

        # Step 9: Drop old primary key and add new UUID primary key to users table
        print("\n9. Updating users table primary key...")

        db.execute(text("""
            ALTER TABLE public.users
            DROP CONSTRAINT IF EXISTS users_pkey;
        """))

        db.execute(text("""
            ALTER TABLE public.users
            DROP COLUMN IF EXISTS id;
        """))

        db.execute(text("""
            ALTER TABLE public.users
            RENAME COLUMN uuid_id TO id;
        """))

        db.execute(text("""
            ALTER TABLE public.users
            ADD PRIMARY KEY (id);
        """))

        db.commit()
        print("   ✓ Users table primary key updated to UUID")

        # Step 10: Add new foreign key constraints with UUID
        print("\n10. Adding new foreign key constraints...")

        db.execute(text("""
            ALTER TABLE public.user_profiles
            ADD CONSTRAINT user_profiles_user_id_fkey
            FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;
        """))

        db.execute(text("""
            ALTER TABLE public.job_applications
            ADD CONSTRAINT job_applications_user_id_fkey
            FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;
        """))

        db.execute(text("""
            ALTER TABLE public.action_history
            ADD CONSTRAINT action_history_user_id_fkey
            FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;
        """))

        db.execute(text("""
            ALTER TABLE public.beta_feedback
            ADD CONSTRAINT beta_feedback_user_id_fkey
            FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;
        """))

        db.commit()
        print("   ✓ New foreign key constraints added")

        # Step 11: Add indexes for performance
        print("\n11. Adding indexes...")

        db.execute(text("""
            CREATE INDEX IF NOT EXISTS idx_user_profiles_user_id
            ON public.user_profiles(user_id);
        """))

        db.execute(text("""
            CREATE INDEX IF NOT EXISTS idx_job_applications_user_id
            ON public.job_applications(user_id);
        """))

        db.execute(text("""
            CREATE INDEX IF NOT EXISTS idx_action_history_user_id
            ON public.action_history(user_id);
        """))

        db.execute(text("""
            CREATE INDEX IF NOT EXISTS idx_beta_feedback_user_id
            ON public.beta_feedback(user_id);
        """))

        db.commit()
        print("   ✓ Indexes added")

        # Step 12: Add unique constraint to user_profiles.user_id
        print("\n12. Adding unique constraints...")

        db.execute(text("""
            ALTER TABLE public.user_profiles
            ADD CONSTRAINT user_profiles_user_id_unique
            UNIQUE (user_id);
        """))

        db.execute(text("""
            ALTER TABLE public.beta_feedback
            ADD CONSTRAINT beta_feedback_user_id_unique
            UNIQUE (user_id);
        """))

        db.commit()
        print("   ✓ Unique constraints added")

        print("\n✅ Migration completed successfully!")
        print("\nNOTE: Make sure to update your application code to:")
        print("1. Use UUID type for user_id in all models")
        print("2. Update JWT token to use UUID instead of int")
        print("3. Update all queries that filter by user_id")
        print("4. Update job_queue.py to handle UUID user_id")

    except Exception as e:
        db.rollback()
        print(f"\n❌ Migration failed: {e}")
        raise
    finally:
        db.close()

if __name__ == "__main__":
    print("=" * 80)
    print("UUID Migration Script - Convert User IDs from INTEGER to UUID")
    print("=" * 80)
    print("\nWARNING: This will modify your database schema.")
    print("Make sure you have a backup before proceeding!")

    confirm = input("\nDo you want to continue? (yes/no): ")

    if confirm.lower() == 'yes':
        run_migration()
    else:
        print("Migration cancelled.")
