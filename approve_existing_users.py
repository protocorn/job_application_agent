"""
Script to auto-approve beta access for all existing users
Run this once after implementing the beta access system
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

def approve_existing_users():
    """Approve beta access for all existing users"""
    with engine.connect() as conn:
        try:
            print("Approving beta access for all existing users...")

            # Check how many users will be affected
            result = conn.execute(text("""
                SELECT COUNT(*) as count
                FROM public.users
                WHERE beta_access_approved IS NULL OR beta_access_approved = FALSE
            """))
            count = result.fetchone()[0]

            if count == 0:
                print("\n[INFO] No users need approval. All users already have beta access!")
                return

            print(f"Found {count} user(s) to approve")

            # Approve all existing users
            conn.execute(text("""
                UPDATE public.users
                SET beta_access_approved = TRUE,
                    beta_approved_date = NOW()
                WHERE beta_access_approved IS NULL OR beta_access_approved = FALSE
            """))
            conn.commit()

            print("\n[SUCCESS] All existing users have been approved for beta access!")
            print(f"Approved {count} user(s)")

        except Exception as e:
            print(f"\n[ERROR] Failed to approve users: {e}")
            conn.rollback()
            raise

if __name__ == "__main__":
    print("=" * 60)
    print("Beta Access Auto-Approval Script")
    print("=" * 60)
    print("\nThis script will approve beta access for ALL existing users.")
    print("New users will still need to request beta access.\n")

    response = input("Do you want to continue? (yes/no): ").strip().lower()

    if response in ['yes', 'y']:
        approve_existing_users()
    else:
        print("\n[CANCELLED] No changes were made.")
