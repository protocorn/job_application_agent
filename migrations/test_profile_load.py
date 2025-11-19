"""
Test if profile loading works after UUID migration
"""

import sys
from pathlib import Path

# Add parent directory to path
sys.path.append(str(Path(__file__).parent.parent))
sys.path.append(str(Path(__file__).parent.parent / "Agents"))

from agent_profile_service import AgentProfileService

def test_profile_load():
    """Test loading profile with UUID"""

    # Test with Sahil Chordia's account (chordiasahil24@gmail.com)
    user_id = "de18962e-29c6-4227-9b0e-28287fdbef3e"

    print(f"\n{'='*80}")
    print(f"Testing Profile Load for User ID: {user_id}")
    print(f"{'='*80}\n")

    try:
        profile = AgentProfileService.get_profile_by_user_id(user_id)

        if not profile:
            print("FAIL: Profile not found!")
            return False

        print("SUCCESS: Profile loaded successfully!")
        print(f"\nProfile Data:")
        print(f"  Phone: {profile.get('phone', 'N/A')}")
        print(f"  LinkedIn: {profile.get('linkedin', 'N/A')}")
        print(f"  GitHub: {profile.get('github', 'N/A')}")
        print(f"  Resume URL: {profile.get('resume_url', 'N/A')}")

        education = profile.get('education', [])
        print(f"\n  Education ({len(education)} entries):")
        for i, edu in enumerate(education[:2], 1):
            print(f"    {i}. {edu.get('degree', '')} at {edu.get('institution', '')}")

        work_exp = profile.get('work_experience', [])
        print(f"\n  Work Experience ({len(work_exp)} entries):")
        for i, work in enumerate(work_exp[:2], 1):
            print(f"    {i}. {work.get('title', '')} at {work.get('company', '')}")

        print(f"\n{'='*80}")
        print("PASS: PROFILE LOADING TEST PASSED!")
        print(f"{'='*80}\n")
        return True

    except Exception as e:
        print(f"ERROR: Error loading profile: {e}")
        import traceback
        traceback.print_exc()
        return False


if __name__ == "__main__":
    success = test_profile_load()
    sys.exit(0 if success else 1)
