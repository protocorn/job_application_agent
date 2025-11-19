"""
Copy profile data from one user to another
"""

import sys
from pathlib import Path

# Add parent directory to path
sys.path.append(str(Path(__file__).parent.parent))

from database_config import SessionLocal, User, UserProfile
from uuid import UUID
import uuid

def copy_profile(source_user_id: str, target_user_id: str):
    """Copy profile from source user to target user"""

    db = SessionLocal()

    try:
        # Convert to UUID
        source_uuid = UUID(source_user_id)
        target_uuid = UUID(target_user_id)

        # Get source user and profile
        source_user = db.query(User).filter(User.id == source_uuid).first()
        if not source_user:
            print(f"ERROR: Source user {source_user_id} not found")
            return False

        source_profile = db.query(UserProfile).filter(UserProfile.user_id == source_uuid).first()
        if not source_profile:
            print(f"ERROR: Source user {source_user.email} does not have a profile")
            return False

        # Get target user
        target_user = db.query(User).filter(User.id == target_uuid).first()
        if not target_user:
            print(f"ERROR: Target user {target_user_id} not found")
            return False

        # Check if target already has a profile
        existing_profile = db.query(UserProfile).filter(UserProfile.user_id == target_uuid).first()
        if existing_profile:
            print(f"WARNING: Target user {target_user.email} already has a profile")
            response = input("Do you want to overwrite it? [yes/no]: ").strip().lower()
            if response != 'yes':
                print("Cancelled.")
                return False
            db.delete(existing_profile)
            db.commit()

        print(f"\n{'='*80}")
        print(f"Copying profile:")
        print(f"  FROM: {source_user.email} ({source_user.first_name} {source_user.last_name})")
        print(f"  TO:   {target_user.email} ({target_user.first_name} {target_user.last_name})")
        print(f"{'='*80}\n")

        # Create new profile for target user
        new_profile = UserProfile(
            id=uuid.uuid4(),
            user_id=target_uuid,

            # Copy all profile fields
            resume_url=source_profile.resume_url,
            cover_letter_template=source_profile.cover_letter_template,
            date_of_birth=source_profile.date_of_birth,
            gender=source_profile.gender,
            nationality=source_profile.nationality,
            preferred_language=source_profile.preferred_language,
            phone=source_profile.phone,
            address=source_profile.address,
            city=source_profile.city,
            state=source_profile.state,
            zip_code=source_profile.zip_code,
            country=source_profile.country,
            country_code=source_profile.country_code,
            state_code=source_profile.state_code,

            # Social links
            linkedin=source_profile.linkedin,
            github=source_profile.github,
            portfolio=source_profile.portfolio if hasattr(source_profile, 'portfolio') else None,
            other_links=source_profile.other_links,

            # Education & Experience (JSONB)
            education=source_profile.education,
            work_experience=source_profile.work_experience,
            skills=source_profile.skills,
            projects=source_profile.projects,
            certifications=source_profile.certifications if hasattr(source_profile, 'certifications') else [],
            languages=source_profile.languages if hasattr(source_profile, 'languages') else [],

            # Additional info
            summary=source_profile.summary if hasattr(source_profile, 'summary') else None,
            disabilities=source_profile.disabilities if hasattr(source_profile, 'disabilities') else [],
            veteran_status=source_profile.veteran_status if hasattr(source_profile, 'veteran_status') else None,
            visa_status=source_profile.visa_status if hasattr(source_profile, 'visa_status') else None,
            visa_sponsorship=source_profile.visa_sponsorship if hasattr(source_profile, 'visa_sponsorship') else None,
            preferred_location=source_profile.preferred_location if hasattr(source_profile, 'preferred_location') else [],

            availability=source_profile.availability if hasattr(source_profile, 'availability') else None,
            work_authorization=source_profile.work_authorization if hasattr(source_profile, 'work_authorization') else None,
            willing_to_relocate=source_profile.willing_to_relocate if hasattr(source_profile, 'willing_to_relocate') else False,
            willing_to_travel=source_profile.willing_to_travel if hasattr(source_profile, 'willing_to_travel') else False,
            expected_salary=source_profile.expected_salary if hasattr(source_profile, 'expected_salary') else None,
            notice_period=source_profile.notice_period if hasattr(source_profile, 'notice_period') else None,

            job_preferences=source_profile.job_preferences if hasattr(source_profile, 'job_preferences') else {}
        )

        db.add(new_profile)
        db.commit()

        print("SUCCESS: Profile copied successfully!")
        print(f"\nProfile details:")
        print(f"  Phone: {new_profile.phone}")
        print(f"  LinkedIn: {new_profile.linkedin}")
        print(f"  GitHub: {new_profile.github}")
        print(f"  Resume URL: {new_profile.resume_url}")
        print(f"  Education entries: {len(new_profile.education) if new_profile.education else 0}")
        print(f"  Work experience entries: {len(new_profile.work_experience) if new_profile.work_experience else 0}")
        print(f"\n{'='*80}\n")

        return True

    except Exception as e:
        print(f"ERROR: {e}")
        import traceback
        traceback.print_exc()
        db.rollback()
        return False

    finally:
        db.close()


if __name__ == "__main__":
    print("\n" + "="*80)
    print("COPY PROFILE DATA BETWEEN USERS")
    print("="*80)

    # Default: Copy from chordiasahil24@gmail.com to chordiasahil2412@gmail.com
    source_id = "de18962e-29c6-4227-9b0e-28287fdbef3e"  # chordiasahil24@gmail.com
    target_id = "033b8626-a468-48fc-9601-fdaec6f0fee9"  # chordiasahil2412@gmail.com

    print(f"\nSource User ID: {source_id}")
    print(f"Target User ID: {target_id}")
    print("\nThis will copy the profile data from source to target user.")
    print("="*80 + "\n")

    response = input("Proceed? [yes/no]: ").strip().lower()
    if response != 'yes':
        print("Cancelled.")
        sys.exit(0)

    success = copy_profile(source_id, target_id)
    sys.exit(0 if success else 1)
