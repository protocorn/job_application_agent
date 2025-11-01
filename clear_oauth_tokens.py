"""
Quick script to clear OAuth tokens from the database.
This is needed when OAuth scopes change.
"""
import sys
import os

# Add the server directory to the path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'server'))

from database_config import SessionLocal, User

def clear_all_oauth_tokens():
    """Clear all Google OAuth tokens from the database"""
    db = SessionLocal()
    try:
        # Get all users
        users = db.query(User).all()
        count = 0

        for user in users:
            if user.google_refresh_token or user.google_access_token:
                print(f"Clearing tokens for user {user.id} ({user.email})")
                user.google_refresh_token = None
                user.google_access_token = None
                user.google_token_expiry = None
                user.google_account_email = None
                count += 1

        db.commit()
        print(f"\n✓ Successfully cleared OAuth tokens for {count} user(s)")
        print("Users will need to reconnect their Google accounts with the new scopes.")

    except Exception as e:
        db.rollback()
        print(f"✗ Error clearing tokens: {e}")
    finally:
        db.close()

if __name__ == "__main__":
    print("=" * 60)
    print("Google OAuth Token Clearer")
    print("=" * 60)
    print("\nThis will clear all Google OAuth tokens from the database.")
    print("Users will need to reconnect their Google accounts.")
    print("\nThis is necessary when OAuth scopes change.")
    print("=" * 60)

    response = input("\nProceed? (yes/no): ").strip().lower()

    if response == 'yes':
        clear_all_oauth_tokens()
    else:
        print("Cancelled.")
