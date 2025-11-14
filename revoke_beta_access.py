"""
Script to revoke beta access from all non-admin users
This forces all users except admins to request beta access again
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
ADMIN_EMAILS = os.getenv('ADMIN_EMAILS', '').split(',')

if not DB_PASSWORD:
    raise ValueError("DB_PASSWORD environment variable is required")

encoded_password = quote_plus(DB_PASSWORD)
DATABASE_URL = f"postgresql://{DB_USER}:{encoded_password}@{DB_HOST}:{DB_PORT}/{DB_NAME}"

engine = create_engine(DATABASE_URL)

def revoke_beta_access():
    """Revoke beta access from all non-admin users"""
    with engine.connect() as conn:
        try:
            print("=" * 60)
            print("Revoke Beta Access from Non-Admin Users")
            print("=" * 60)

            # Build admin email list for SQL
            admin_emails_cleaned = [email.strip() for email in ADMIN_EMAILS if email.strip()]

            if not admin_emails_cleaned:
                print("\n[WARNING] No admin emails found in ADMIN_EMAILS")
                return

            print(f"\nProtected admin emails: {', '.join(admin_emails_cleaned)}")
            print()

            # Count how many users will be affected
            placeholders = ','.join([f":admin{i}" for i in range(len(admin_emails_cleaned))])
            params = {f'admin{i}': email for i, email in enumerate(admin_emails_cleaned)}

            count_query = f"""
                SELECT COUNT(*) as count
                FROM public.users
                WHERE email NOT IN ({placeholders})
                AND beta_access_approved = TRUE
            """

            result = conn.execute(text(count_query), params)
            count = result.fetchone()[0]

            if count == 0:
                print("[INFO] No non-admin users with beta access found.")
                return

            print(f"[INFO] Found {count} non-admin user(s) with beta access")
            print()

            response = input(f"Revoke beta access from {count} user(s)? (yes/no): ").strip().lower()

            if response not in ['yes', 'y']:
                print("\n[CANCELLED] No changes were made.")
                return

            # Revoke beta access from non-admin users
            revoke_query = f"""
                UPDATE public.users
                SET beta_access_approved = FALSE,
                    beta_access_requested = FALSE,
                    beta_request_date = NULL,
                    beta_approved_date = NULL,
                    beta_request_reason = NULL
                WHERE email NOT IN ({placeholders})
                AND beta_access_approved = TRUE
            """

            conn.execute(text(revoke_query), params)
            conn.commit()

            print()
            print("[SUCCESS] Beta access revoked from non-admin users!")
            print(f"Revoked access from {count} user(s)")
            print("\nThese users will need to:")
            print("1. Log out and log back in")
            print("2. Request beta access via the form")
            print("3. Wait for your approval")
            print("\n" + "=" * 60)

        except Exception as e:
            print(f"\n[ERROR] Failed to revoke beta access: {e}")
            conn.rollback()
            raise

if __name__ == "__main__":
    revoke_beta_access()
