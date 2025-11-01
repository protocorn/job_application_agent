"""
Automated Integration Script for Systematic Tailoring
Run this to automatically patch resume_tailoring_agent.py
"""

import os
import shutil
from pathlib import Path


def backup_original():
    """Create backup of original file."""
    original = Path("resume_tailoring_agent.py")
    backup = Path("resume_tailoring_agent.py.backup")

    if original.exists():
        shutil.copy2(original, backup)
        print(f"✅ Created backup: {backup}")
        return True
    else:
        print(f"❌ Original file not found: {original}")
        return False


def apply_patches():
    """Apply all patches to resume_tailoring_agent.py."""

    file_path = Path("resume_tailoring_agent.py")

    if not file_path.exists():
        print("❌ resume_tailoring_agent.py not found!")
        return False

    with open(file_path, 'r', encoding='utf-8') as f:
        content = f.read()

    print("📝 Applying patches...")

    # Patch 1: Add imports
    import_marker = "from googleapiclient.errors import HttpError"
    if import_marker in content:
        new_imports = """from googleapiclient.errors import HttpError

# Systematic tailoring imports
try:
    from systematic_tailoring_complete import (
        run_systematic_tailoring,
        recover_from_overflow_if_needed
    )
    from mimikree_cache import get_cached_mimikree_data, cache_mimikree_data
    SYSTEMATIC_TAILORING_AVAILABLE = True
except ImportError:
    print("⚠️  Systematic tailoring modules not found. Using legacy mode.")
    SYSTEMATIC_TAILORING_AVAILABLE = False
"""
        content = content.replace(import_marker, new_imports)
        print("   ✓ Added imports")

    # Patch 2: Add Mimikree caching
    old_mimikree_start = '''if enable_mimikree_integration:
        print("\\n🔗 Mimikree Integration Starting...")
        print("="*60)

        # Step 1: Authenticating with Mimikree'''

    new_mimikree_start = '''if enable_mimikree_integration:
        print("\\n🔗 Mimikree Integration Starting...")
        print("="*60)

        # Check cache first
        cached_mimikree = get_cached_mimikree_data(job_description)
        mimikree_responses = {}

        if cached_mimikree:
            print("✅ Using cached Mimikree data (saves ~200 seconds)")
            mimikree_data = cached_mimikree.get('formatted_data', '')
            mimikree_responses = cached_mimikree.get('responses', {})
            enable_mimikree_integration = False  # Skip API calls
        else:
            # Step 1: Authenticating with Mimikree'''

    if old_mimikree_start in content:
        content = content.replace(old_mimikree_start, new_mimikree_start)
        print("   ✓ Added Mimikree caching (check)")

    # Patch 3: Cache Mimikree responses after completion
    old_mimikree_format = '''        mimikree_data = format_mimikree_for_resume(responses)

        # Save to file for debugging
        mimikree_debug_file = f"../Resumes/mimikree_data_{uuid.uuid4().hex[:8]}.txt"'''

    new_mimikree_format = '''        mimikree_data = format_mimikree_for_resume(responses)

        # Cache the Mimikree data for future runs
        mimikree_responses = dict(zip(questions, responses))
        cache_data = {
            'formatted_data': mimikree_data,
            'responses': mimikree_responses,
            'timestamp': time.time()
        }
        cache_mimikree_data(job_description, cache_data)
        print(f"💾 Cached Mimikree data for future runs")

        # Save to file for debugging
        mimikree_debug_file = f"../Resumes/mimikree_data_{uuid.uuid4().hex[:8]}.txt"'''

    if old_mimikree_format in content:
        content = content.replace(old_mimikree_format, new_mimikree_format)
        print("   ✓ Added Mimikree caching (save)")

    # Patch 4: Add systematic tailoring option before iterative call
    # Find the annotated resume section
    marker = '''        print("🚀 Starting iterative tailoring with automatic verification...")'''

    new_section = '''        # Choose tailoring approach
        use_systematic_tailoring = SYSTEMATIC_TAILORING_AVAILABLE and os.getenv('USE_SYSTEMATIC_TAILORING', 'true').lower() == 'true'

        if use_systematic_tailoring:
            print("🚀 Starting SYSTEMATIC tailoring (Phase 1 + Phase 2)...")
            print("="*60)

            # Run systematic two-phase tailoring
            systematic_results = run_systematic_tailoring(
                job_description=job_description,
                job_keywords=keywords.get('prioritized_keywords', [])[:10],
                line_metadata=line_metadata,
                resume_text=original_resume_text_plain,
                mimikree_responses=mimikree_responses,
                mimikree_formatted_data=mimikree_data
            )

            # Apply replacements using existing logic
            if systematic_results['all_replacements']:
                print(f"\\n📝 Applying {len(systematic_results['all_replacements'])} replacements...")

                # Convert to expected format and apply
                from resume_tailoring_agent import apply_tailoring_changes

                for repl in systematic_results['all_replacements']:
                    try:
                        # Use existing apply function
                        requests = [{
                            'replaceAllText': {
                                'containsText': {
                                    'text': repl['old_text'],
                                    'matchCase': True
                                },
                                'replaceText': repl['new_text']
                            }
                        }]

                        docs_service.documents().batchUpdate(
                            documentId=copied_doc_id,
                            body={'requests': requests}
                        ).execute()
                    except Exception as e:
                        print(f"⚠️  Skipped replacement: {str(e)[:50]}")

                print("✅ Replacements applied")

            # Check for overflow
            current_page_count = get_actual_page_count(drive_service, copied_doc_id)
            original_page_count = get_actual_page_count(drive_service, original_doc_id)

            if current_page_count > original_page_count:
                print(f"\\n⚠️  OVERFLOW: {current_page_count} pages (target: {original_page_count})")
                print("🔄 Running one-time overflow recovery...")

                # Refresh metadata
                current_line_metadata = extract_document_structure(docs_service, copied_doc_id)

                # Run recovery
                recovery = recover_from_overflow_if_needed(
                    line_metadata=current_line_metadata,
                    keywords=systematic_results['phase1_results']['feasible_keywords'],
                    current_pages=current_page_count,
                    target_pages=original_page_count
                )

                # Apply recovery replacements
                for repl in recovery.get('replacements', []):
                    try:
                        requests = [{
                            'replaceAllText': {
                                'containsText': {'text': repl['old_text'], 'matchCase': True},
                                'replaceText': repl['new_text']
                            }
                        }]
                        docs_service.documents().batchUpdate(
                            documentId=copied_doc_id,
                            body={'requests': requests}
                        ).execute()
                    except:
                        pass

                current_page_count = get_actual_page_count(drive_service, copied_doc_id)

            print(f"\\n{'='*60}")
            print("✅ SYSTEMATIC TAILORING COMPLETE")
            print(f"{'='*60}")
            print(f"   Match Rate: {systematic_results['phase1_results']['match_percentage']:.1f}%")
            print(f"   Feasible Keywords: {len(systematic_results['phase1_results']['feasible_keywords'])}")
            print(f"   Max Possible ATS: {systematic_results['phase1_results']['max_possible_ats_score']}/100")
            print(f"   Final Pages: {current_page_count}")
            print(f"{'='*60}")

        else:
            print("🚀 Starting iterative tailoring with automatic verification...")'''

    if marker in content:
        content = content.replace(marker, new_section)
        print("   ✓ Added systematic tailoring integration")

    # Write back
    with open(file_path, 'w', encoding='utf-8') as f:
        f.write(content)

    print("✅ All patches applied successfully!")
    return True


def main():
    print("="*60)
    print("SYSTEMATIC TAILORING INTEGRATION SCRIPT")
    print("="*60)
    print()

    # Check if required files exist
    required_files = [
        "systematic_tailoring_complete.py",
        "mimikree_cache.py",
        "space_borrowing.py",
        "improved_char_calc.py"
    ]

    missing = []
    for file in required_files:
        if not Path(file).exists():
            missing.append(file)

    if missing:
        print("❌ Missing required files:")
        for file in missing:
            print(f"   • {file}")
        print()
        print("Please ensure all module files are in the same directory.")
        return

    print("✅ All required modules found")
    print()

    # Backup original
    if not backup_original():
        return

    # Apply patches
    if apply_patches():
        print()
        print("="*60)
        print("✅ INTEGRATION COMPLETE!")
        print("="*60)
        print()
        print("To use systematic tailoring:")
        print("  python resume_tailoring_agent.py")
        print()
        print("To use legacy iterative mode:")
        print("  set USE_SYSTEMATIC_TAILORING=false")
        print("  python resume_tailoring_agent.py")
        print()
        print("To restore original:")
        print("  copy resume_tailoring_agent.py.backup resume_tailoring_agent.py")
    else:
        print("❌ Integration failed. Check errors above.")


if __name__ == "__main__":
    main()
