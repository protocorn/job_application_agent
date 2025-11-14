"""
Migration script to add beta access fields to users table
"""
import os
from sqlalchemy import create_engine, text
from dotenv import load_dotenv
from urllib.parse import quote_plus

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

def migrate():
    """Add beta access fields to users table"""
    with engine.connect() as conn:
        try:
            print("Starting migration to add beta access fields...")

            # Add beta access fields
            migrations = [
                "ALTER TABLE public.users ADD COLUMN IF NOT EXISTS beta_access_requested BOOLEAN DEFAULT FALSE",
                "ALTER TABLE public.users ADD COLUMN IF NOT EXISTS beta_access_approved BOOLEAN DEFAULT FALSE",
                "ALTER TABLE public.users ADD COLUMN IF NOT EXISTS beta_request_date TIMESTAMP",
                "ALTER TABLE public.users ADD COLUMN IF NOT EXISTS beta_approved_date TIMESTAMP",
                "ALTER TABLE public.users ADD COLUMN IF NOT EXISTS beta_request_reason TEXT"
            ]

            for migration_sql in migrations:
                print(f"Executing: {migration_sql}")
                conn.execute(text(migration_sql))
                conn.commit()

            print("\n[SUCCESS] Migration completed successfully!")
            print("\nBeta access fields added to users table:")
            print("  - beta_access_requested (boolean, default: false)")
            print("  - beta_access_approved (boolean, default: false)")
            print("  - beta_request_date (timestamp)")
            print("  - beta_approved_date (timestamp)")
            print("  - beta_request_reason (text)")

        except Exception as e:
            print(f"\n[ERROR] Migration failed: {e}")
            conn.rollback()
            raise

if __name__ == "__main__":
    migrate()
