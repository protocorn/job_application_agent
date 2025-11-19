"""
Show which users have profiles and which don't
"""

import sys
from pathlib import Path

# Add parent directory to path
sys.path.append(str(Path(__file__).parent.parent))

from database_config import SessionLocal, User, UserProfile

def show_users_with_profiles():
    """Show all users and indicate which have profiles"""
    db = SessionLocal()

    try:
        users = db.query(User).order_by(User.created_at.desc()).all()
        profiles = {p.user_id: p for p in db.query(UserProfile).all()}

        print("\n" + "="*100)
        print("USERS AND THEIR PROFILES")
        print("="*100)

        users_with_profiles = []
        users_without_profiles = []

        for user in users:
            profile = profiles.get(user.id)
            if profile:
                users_with_profiles.append({
                    'user': user,
                    'profile': profile
                })
            else:
                users_without_profiles.append(user)

        print(f"\nUSERS WITH PROFILES ({len(users_with_profiles)}):")
        print("-"*100)
        for item in users_with_profiles:
            user = item['user']
            profile = item['profile']
            print(f"\nUser ID: {user.id}")
            print(f"Name: {user.first_name} {user.last_name}")
            print(f"Email: {user.email}")
            print(f"Profile ID: {profile.id}")
            print(f"Phone: {profile.phone or 'N/A'}")
            print(f"LinkedIn: {profile.linkedin or 'N/A'}")
            print(f"GitHub: {profile.github or 'N/A'}")
            print(f"Resume URL: {profile.resume_url or 'N/A'}")

            education = profile.education if profile.education else []
            work_exp = profile.work_experience if profile.work_experience else []
            print(f"Education entries: {len(education)}")
            print(f"Work experience entries: {len(work_exp)}")

        print(f"\n\nUSERS WITHOUT PROFILES ({len(users_without_profiles)}):")
        print("-"*100)
        for user in users_without_profiles:
            print(f"\nUser ID: {user.id}")
            print(f"Name: {user.first_name} {user.last_name}")
            print(f"Email: {user.email}")
            print("Status: NO PROFILE - needs profile creation")

        print("\n" + "="*100)
        print("SUMMARY")
        print("="*100)
        print(f"Total users: {len(users)}")
        print(f"Users with profiles: {len(users_with_profiles)}")
        print(f"Users without profiles: {len(users_without_profiles)}")

        if users_with_profiles:
            print("\nTo use a user with profile:")
            user = users_with_profiles[0]['user']
            print(f'  python Testing/run_agent_with_tracking.py --links <URL> --user-id "{user.id}" --headful --keep-open')

        print("="*100 + "\n")

    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()

    finally:
        db.close()


if __name__ == "__main__":
    show_users_with_profiles()
