"""
Integration Patch for Systematic Resume Tailoring
This file contains the code changes needed for resume_tailoring_agent.py
"""

# ============================================================
# PATCH 1: Add imports at top of file (after existing imports)
# ============================================================

PATCH_1_IMPORTS = """
# Systematic tailoring imports
from systematic_tailoring_complete import (
    run_systematic_tailoring,
    recover_from_overflow_if_needed
)
from mimikree_cache import get_cached_mimikree_data, cache_mimikree_data
from improved_char_calc import extract_font_metrics_from_doc, calculate_char_limits, estimate_visual_lines
"""

# ============================================================
# PATCH 2: Add Mimikree caching (around line 3073)
# ============================================================

# FIND this code:
OLD_MIMIKREE_INTEGRATION = """
if enable_mimikree_integration:
    print("\\n🔗 Mimikree Integration Starting...")
    print("="*60)

    # Step 1: Authenticate with Mimikree
    print("Step 1: Authenticating with Mimikree...")
    mimikree_integration = MimikreeIntegration()
"""

# REPLACE with:
NEW_MIMIKREE_INTEGRATION = """
if enable_mimikree_integration:
    print("\\n🔗 Mimikree Integration Starting...")
    print("="*60)

    # Check cache first
    cached_mimikree = get_cached_mimikree_data(job_description)

    if cached_mimikree:
        print("✅ Using cached Mimikree data (skipping API calls)")
        mimikree_data = cached_mimikree.get('formatted_data', '')
        mimikree_responses = cached_mimikree.get('responses', {})
    else:
        # Step 1: Authenticate with Mimikree
        print("Step 1: Authenticating with Mimikree...")
        mimikree_integration = MimikreeIntegration()
"""

# ============================================================
# PATCH 3: Cache Mimikree responses (after Mimikree completes)
# ============================================================

# FIND this code (around line 3100):
OLD_MIMIKREE_COMPLETE = """
        mimikree_data = format_mimikree_for_resume(responses)

        # Save to file for debugging
        mimikree_debug_file = f"../Resumes/mimikree_data_{uuid.uuid4().hex[:8]}.txt"
"""

# REPLACE with:
NEW_MIMIKREE_COMPLETE = """
        mimikree_data = format_mimikree_for_resume(responses)

        # Cache the Mimikree data for future runs
        cache_data = {
            'formatted_data': mimikree_data,
            'responses': dict(zip(questions, responses)),
            'timestamp': time.time()
        }
        cache_mimikree_data(job_description, cache_data)

        mimikree_responses = dict(zip(questions, responses))

        # Save to file for debugging
        mimikree_debug_file = f"../Resumes/mimikree_data_{uuid.uuid4().hex[:8]}.txt"
"""

# ============================================================
# PATCH 4: Replace iterative tailoring with systematic approach
# ============================================================

# FIND the call to iterative_tailor_with_verification (around line 3144):
OLD_ITERATIVE_CALL = """
        # Start iterative tailoring
        result = iterative_tailor_with_verification(
            docs_service,
            drive_service,
            copied_doc_id,
            original_doc_id,
            original_resume_text,
            original_resume_text_plain,
            job_description,
            line_metadata,
            line_budget,
            keywords,
            mimikree_data=mimikree_data,
            max_iterations=3
        )
"""

# REPLACE with:
NEW_SYSTEMATIC_CALL = """
        # Run systematic two-phase tailoring
        print("🚀 Starting systematic tailoring (Phase 1 + Phase 2)...")

        # Phase 1 & 2: Validation + Editing
        systematic_results = run_systematic_tailoring(
            job_description=job_description,
            job_keywords=keywords.get('prioritized_keywords', [])[:10],
            line_metadata=line_metadata,
            resume_text=original_resume_text_plain,
            mimikree_responses=mimikree_responses if enable_mimikree_integration else {},
            mimikree_formatted_data=mimikree_data if enable_mimikree_integration else ""
        )

        # Apply the replacements
        replacements_to_apply = systematic_results['all_replacements']

        if replacements_to_apply:
            print(f"\\n📝 Applying {len(replacements_to_apply)} replacements to document...")

            # Apply using existing apply_tailoring_changes function
            # Convert to expected format
            formatted_replacements = []
            for repl in replacements_to_apply:
                formatted_replacements.append({
                    'original_text': repl['old_text'],
                    'tailored_text': repl['new_text'],
                    'type': repl.get('type', 'systematic_edit')
                })

            # Apply changes
            success = apply_replacements_batch(
                docs_service,
                copied_doc_id,
                formatted_replacements,
                line_metadata
            )

            if success:
                print("✅ All replacements applied successfully")
            else:
                print("⚠️  Some replacements may have failed")

        # Check for page overflow
        print("\\n📏 Checking page count...")
        current_page_count = get_actual_page_count(drive_service, copied_doc_id)
        original_page_count = get_actual_page_count(drive_service, original_doc_id)

        if current_page_count > original_page_count:
            print(f"⚠️  OVERFLOW: {current_page_count} pages (target: {original_page_count})")
            print("🔄 Running one-time overflow recovery...")

            # Refresh line metadata
            current_line_metadata = extract_document_structure(docs_service, copied_doc_id)

            # Run overflow recovery
            recovery_results = recover_from_overflow_if_needed(
                line_metadata=current_line_metadata,
                keywords=systematic_results['phase1_results']['feasible_keywords'],
                current_pages=current_page_count,
                target_pages=original_page_count
            )

            # Apply recovery replacements
            if recovery_results['replacements']:
                recovery_formatted = []
                for repl in recovery_results['replacements']:
                    recovery_formatted.append({
                        'original_text': repl['old_text'],
                        'tailored_text': repl['new_text'],
                        'type': 'overflow_recovery'
                    })

                apply_replacements_batch(
                    docs_service,
                    copied_doc_id,
                    recovery_formatted,
                    current_line_metadata
                )

                # Check again
                final_page_count = get_actual_page_count(drive_service, copied_doc_id)

                if final_page_count <= original_page_count:
                    print(f"✅ Overflow recovered! Now {final_page_count} page(s)")
                else:
                    print(f"⚠️  Still {final_page_count} pages - manual review needed")
        else:
            print(f"✅ Document is {current_page_count} page(s) (within limit)")
            final_page_count = current_page_count

        # Final validation
        print("\\n🔍 Running final validation...")
        final_text = read_structural_elements_plain(
            docs_service.documents().get(documentId=copied_doc_id).execute()['body']['content']
        )

        # Run ATS check
        from resume_tailoring_agent import ATSOptimizer
        ats_optimizer = ATSOptimizer()
        final_ats = ats_optimizer.analyze_ats_compatibility(
            final_text,
            systematic_results['phase1_results']['feasible_keywords']
        )

        print(f"\\n{'='*60}")
        print("✅ SYSTEMATIC TAILORING COMPLETE")
        print(f"{'='*60}")
        print(f"   Match Rate: {systematic_results['phase1_results']['match_percentage']:.1f}%")
        print(f"   Keywords Used: {len(systematic_results['phase1_results']['feasible_keywords'])}/{len(keywords.get('prioritized_keywords', [])[:10])}")
        print(f"   Final ATS Score: {final_ats['ats_score']:.1f}/100")
        print(f"   Max Possible: {systematic_results['phase1_results']['max_possible_ats_score']}/100")
        print(f"   Final Pages: {final_page_count}")
        print(f"{'='*60}")

        result = {
            'success': True,
            'final_page_count': final_page_count,
            'ats_score': final_ats['ats_score'],
            'systematic_results': systematic_results
        }
"""

# ============================================================
# PATCH 5: Helper function for batch replacements
# ============================================================

NEW_HELPER_FUNCTION = """
def apply_replacements_batch(docs_service, document_id, replacements, line_metadata):
    \"\"\"
    Apply a batch of text replacements to the document.

    Args:
        docs_service: Google Docs service
        document_id: Document ID
        replacements: List of dicts with 'original_text' and 'tailored_text'
        line_metadata: Current line metadata

    Returns:
        bool: Success status
    \"\"\"
    try:
        # Use existing apply_tailoring_changes logic
        # Group replacements by type and apply

        requests = []
        for repl in replacements:
            # Create replaceAllText request
            requests.append({
                'replaceAllText': {
                    'containsText': {
                        'text': repl['original_text'],
                        'matchCase': True
                    },
                    'replaceText': repl['tailored_text']
                }
            })

        if requests:
            # Execute in batches of 20
            batch_size = 20
            for i in range(0, len(requests), batch_size):
                batch = requests[i:i+batch_size]
                docs_service.documents().batchUpdate(
                    documentId=document_id,
                    body={'requests': batch}
                ).execute()

        return True
    except Exception as e:
        print(f"⚠️  Error applying replacements: {e}")
        return False
"""

# ============================================================
# INSTRUCTIONS
# ============================================================

INTEGRATION_INSTRUCTIONS = """
HOW TO APPLY THIS PATCH:

1. Open resume_tailoring_agent.py

2. Add PATCH_1_IMPORTS after the existing imports (around line 15)

3. Find the Mimikree integration code (around line 3073) and replace:
   - Replace OLD_MIMIKREE_INTEGRATION with NEW_MIMIKREE_INTEGRATION

4. Find where Mimikree data is formatted (around line 3100) and replace:
   - Replace OLD_MIMIKREE_COMPLETE with NEW_MIMIKREE_COMPLETE

5. Find the iterative_tailor_with_verification call (around line 3144) and replace:
   - Replace OLD_ITERATIVE_CALL with NEW_SYSTEMATIC_CALL

6. Add NEW_HELPER_FUNCTION somewhere before main() (around line 3000)

7. Save the file

TESTING:
Run: python resume_tailoring_agent.py

Expected output:
- Phase 1: Keyword validation with feasibility analysis
- Phase 2: Section-wise systematic editing
- One-time overflow recovery if needed
- Final ATS score matching prediction

BENEFITS:
✅ No wasteful iterations
✅ Clear keyword feasibility upfront
✅ Mimikree caching (200s → 5s on repeat runs)
✅ Systematic section-by-section editing
✅ One-time overflow recovery only if needed
"""

if __name__ == "__main__":
    print(INTEGRATION_INSTRUCTIONS)
