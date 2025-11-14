"""
Script to approve beta access for the admin user
Run this to give yourself access so you can approve others
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

if not ADMIN_EMAILS or ADMIN_EMAILS == ['']:
    raise ValueError("ADMIN_EMAILS environment variable is required. Set it in your .env file.")

encoded_password = quote_plus(DB_PASSWORD)
DATABASE_URL = f"postgresql://{DB_USER}:{encoded_password}@{DB_HOST}:{DB_PORT}/{DB_NAME}"

engine = create_engine(DATABASE_URL)

def approve_admin():
    """Approve beta access for admin users"""
    with engine.connect() as conn:
        try:
            print("=" * 60)
            print("Admin Beta Access Approval")
            print("=" * 60)
            print(f"\nAdmin emails from .env: {', '.join(ADMIN_EMAILS)}")
            print()

            for admin_email in ADMIN_EMAILS:
                admin_email = admin_email.strip()
                if not admin_email:
                    continue

                # Check if user exists
                result = conn.execute(
                    text("SELECT id, email, first_name, last_name, beta_access_approved FROM public.users WHERE email = :email"),
                    {"email": admin_email}
                )
                user = result.fetchone()

                if not user:
                    print(f"[WARNING] Admin user not found: {admin_email}")
                    print(f"          Please create an account with this email first.")
                    continue

                if user[4]:  # beta_access_approved
                    print(f"[INFO] Admin already has beta access: {admin_email}")
                    continue

                # Approve admin user
                conn.execute(
                    text("""
                        UPDATE public.users
                        SET beta_access_approved = TRUE,
                            beta_approved_date = NOW()
                        WHERE email = :email
                    """),
                    {"email": admin_email}
                )
                conn.commit()

                print(f"[SUCCESS] Beta access approved for: {admin_email}")
                print(f"          Name: {user[2]} {user[3]}")

            print("\n" + "=" * 60)
            print("Done! You can now log in and access the admin dashboard.")
            print("=" * 60)

        except Exception as e:
            print(f"\n[ERROR] Failed to approve admin: {e}")
            conn.rollback()
            raise

if __name__ == "__main__":
    approve_admin()
