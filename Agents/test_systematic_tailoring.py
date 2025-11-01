"""
Quick Test Script for Systematic Tailoring
Run this to verify the integration works before running the full agent
"""

import sys
from pathlib import Path


def check_environment():
    """Check if all required files and dependencies exist."""
    print("="*60)
    print("SYSTEMATIC TAILORING - PRE-FLIGHT CHECK")
    print("="*60)
    print()

    checks_passed = 0
    checks_total = 0

    # Check Python version
    checks_total += 1
    print(f"1. Python version: {sys.version.split()[0]}", end=" ")
    if sys.version_info >= (3, 8):
        print("✅")
        checks_passed += 1
    else:
        print("❌ (Need Python 3.8+)")

    # Check required module files
    required_files = {
        "systematic_tailoring_complete.py": "Core systematic tailoring logic",
        "mimikree_cache.py": "Mimikree response caching",
        "space_borrowing.py": "Space borrowing logic",
        "improved_char_calc.py": "Font-aware character calculation",
        "resume_tailoring_agent.py": "Main agent file"
    }

    for i, (file, desc) in enumerate(required_files.items(), 2):
        checks_total += 1
        print(f"{i}. {file}", end=" ")
        if Path(file).exists():
            print("✅")
            checks_passed += 1
        else:
            print(f"❌ ({desc})")

    # Check imports
    checks_total += 1
    print(f"{len(required_files) + 2}. Import test", end=" ")
    try:
        from google import genai
        from googleapiclient.discovery import build
        print("✅")
        checks_passed += 1
    except ImportError as e:
        print(f"❌ (Missing: {e.name})")

    # Check environment variables
    import os
    checks_total += 1
    print(f"{len(required_files) + 3}. Environment variables", end=" ")
    has_api_key = bool(os.getenv('GOOGLE_API_KEY') or os.getenv('GEMINI_API_KEY'))
    if has_api_key:
        print("✅")
        checks_passed += 1
    else:
        print("❌ (Need GOOGLE_API_KEY or GEMINI_API_KEY)")

    print()
    print("="*60)
    print(f"RESULTS: {checks_passed}/{checks_total} checks passed")
    print("="*60)

    if checks_passed == checks_total:
        print("✅ All checks passed! Ready to integrate.")
        print()
        print("Next steps:")
        print("  1. Run: python apply_systematic_tailoring.py")
        print("  2. Run: python resume_tailoring_agent.py")
        return True
    else:
        print("❌ Some checks failed. Please fix the issues above.")
        return False


def test_systematic_modules():
    """Test that systematic modules can be imported and work."""
    print()
    print("="*60)
    print("MODULE FUNCTIONALITY TEST")
    print("="*60)
    print()

    # Test 1: Mimikree caching
    print("1. Testing Mimikree caching...", end=" ")
    try:
        from mimikree_cache import get_job_description_hash, cache_mimikree_data, get_cached_mimikree_data

        test_job = "Test job description for data scientist"
        test_hash = get_job_description_hash(test_job)

        # Cache some data
        cache_mimikree_data(test_job, {'test': 'data'})

        # Retrieve it
        cached = get_cached_mimikree_data(test_job)

        if cached and cached.get('test') == 'data':
            print("✅")
        else:
            print("❌ (Cache retrieval failed)")
    except Exception as e:
        print(f"❌ ({str(e)[:50]})")

    # Test 2: Space borrowing
    print("2. Testing space borrowing logic...", end=" ")
    try:
        from space_borrowing import calculate_relevance_scores

        test_lines = [
            {
                'text': 'Improved data quality by 95% using Python',
                'line_number': 1,
                'char_buffer': 10,
                'visual_lines': 1
            },
            {
                'text': 'Assisted in various team projects',
                'line_number': 2,
                'char_buffer': 50,
                'visual_lines': 1
            }
        ]

        scored = calculate_relevance_scores(
            test_lines,
            ['Data quality', 'Python'],
            "Job description about data quality"
        )

        if scored[0]['relevance_score'] > scored[1]['relevance_score']:
            print("✅")
        else:
            print("❌ (Relevance scoring incorrect)")
    except Exception as e:
        print(f"❌ ({str(e)[:50]})")

    # Test 3: Character calculation
    print("3. Testing character calculation...", end=" ")
    try:
        from improved_char_calc import calculate_avg_char_width

        width_tnr = calculate_avg_char_width('Times New Roman', 11)
        width_arial = calculate_avg_char_width('Arial', 11)

        if width_tnr < width_arial:  # Times New Roman is more compact
            print("✅")
        else:
            print("❌ (Font width calculation incorrect)")
    except Exception as e:
        print(f"❌ ({str(e)[:50]})")

    # Test 4: Systematic tailoring import
    print("4. Testing systematic tailoring import...", end=" ")
    try:
        from systematic_tailoring_complete import (
            KeywordValidatorComplete,
            SystematicEditorComplete,
            OverflowRecoveryComplete
        )
        print("✅")
    except Exception as e:
        print(f"❌ ({str(e)[:50]})")

    print()
    print("="*60)
    print("✅ Module tests complete!")
    print("="*60)


def main():
    """Run all pre-flight checks."""
    if check_environment():
        test_systematic_modules()

        print()
        print("🚀 READY TO LAUNCH!")
        print()
        print("Run the integration script:")
        print("  python apply_systematic_tailoring.py")
        print()
        print("Or read the documentation:")
        print("  README_SYSTEMATIC_TAILORING.md")
    else:
        print()
        print("Please fix the issues above before proceeding.")
        sys.exit(1)


if __name__ == "__main__":
    main()
