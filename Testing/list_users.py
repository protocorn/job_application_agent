"""
List all users in the database to find the correct user ID
"""

import sys
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent / "Agents"))

sys.path.append(str(Path(__file__).parent.parent))
from database_config import SessionLocal, User

def list_all_users():
    """List all users with their IDs and basic info"""
    db = SessionLocal()
    try:
        users = db.query(User).order_by(User.created_at.desc()).all()

        if not users:
            print("❌ No users found in database")
            return

        print("\n" + "="*80)
        print("USERS IN DATABASE")
        print("="*80)

        for i, user in enumerate(users, 1):
            print(f"\n{i}. User ID: {user.id}")
            print(f"   Name: {user.first_name} {user.last_name}")
            print(f"   Email: {user.email}")
            print(f"   Created: {user.created_at}")
            print("-"*80)

        print(f"\nTotal users: {len(users)}")
        print("\nTo use a specific user, run:")
        print("  python Testing/run_agent_with_tracking.py --links <URL> --user-id <UUID> --headful --keep-open --slowmo 20")
        print("\nExample:")
        print(f"  python Testing/run_agent_with_tracking.py --links <URL> --user-id \"{users[0].id}\" --headful --keep-open --slowmo 20")
        print("="*80 + "\n")

    except Exception as e:
        print(f"❌ Error listing users: {e}")
    finally:
        db.close()


if __name__ == "__main__":
    list_all_users()
