"""
Test script for Pattern Learning System

This script tests the core components:
1. PatternRecorder - Recording and updating patterns
2. LearnedPatternsMapper - Querying learned patterns
3. Integration - Full workflow
"""
import asyncio
from Agents.components.pattern_recorder import PatternRecorder
from Agents.components.executors.learned_patterns_mapper import LearnedPatternsMapper
from loguru import logger


async def test_pattern_recording():
    """Test recording patterns to database."""
    print("\n" + "="*60)
    print("TEST 1: Pattern Recording")
    print("="*60)

    recorder = PatternRecorder()

    # Test 1: Record a new pattern
    print("\n1. Recording new pattern: 'Have you served in military?' -> veteran_status")
    success = await recorder.record_pattern(
        field_label="Have you served in the military?",
        profile_field="veteran_status",
        field_category="dropdown",
        success=True,
        user_id=None
    )
    print(f"   Result: {'[OK] Success' if success else '[FAIL] Failed'}")

    # Test 2: Update existing pattern (should increment occurrence_count)
    print("\n2. Recording same pattern again (should update)")
    success = await recorder.record_pattern(
        field_label="Have you served in the military?",
        profile_field="veteran_status",
        field_category="dropdown",
        success=True,
        user_id=None
    )
    print(f"   Result: {'[OK] Success' if success else '[FAIL] Failed'}")

    # Test 3: Record a failure (should reduce confidence)
    print("\n3. Recording failure for same pattern")
    success = await recorder.record_pattern(
        field_label="Have you served in the military?",
        profile_field="veteran_status",
        field_category="dropdown",
        success=False,
        user_id=None
    )
    print(f"   Result: {'[OK] Success' if success else '[FAIL] Failed'}")

    # Test 4: Privacy filter test
    print("\n4. Testing privacy filter (should skip)")
    success = await recorder.record_pattern(
        field_label="What is your SSN?",
        profile_field="ssn",
        field_category="text_input",
        success=True,
        user_id=None
    )
    print(f"   Result: {'[OK] Correctly skipped' if not success else '[FAIL] Should have been skipped!'}")

    # Get stats
    print("\n5. Getting pattern statistics")
    stats = recorder.get_pattern_stats()
    print(f"   Total patterns: {stats.get('total_patterns', 0)}")
    print(f"   High confidence: {stats.get('high_confidence_patterns', 0)}")
    print(f"   Average confidence: {stats.get('average_confidence', 0):.2f}")

    return True


async def test_pattern_retrieval():
    """Test retrieving learned patterns."""
    print("\n" + "="*60)
    print("TEST 2: Pattern Retrieval")
    print("="*60)

    mapper = LearnedPatternsMapper()
    profile = {
        "first_name": "John",
        "last_name": "Doe",
        "veteran_status": "No"
    }

    # Test 1: Exact match
    print("\n1. Testing exact match: 'Have you served in the military?'")
    pattern = mapper.map_field(
        field_label="Have you served in the military?",
        field_category="dropdown",
        profile=profile
    )
    if pattern:
        print(f"   [OK] Found pattern:")
        print(f"      Profile field: {pattern.profile_field}")
        print(f"      Confidence: {pattern.confidence_score:.2f}")
        print(f"      Occurrences: {pattern.occurrence_count}")
    else:
        print(f"   [FAIL] No pattern found")

    # Test 2: Variation with different punctuation
    print("\n2. Testing label variation: 'Have you served in military'")
    pattern = mapper.map_field(
        field_label="Have you served in military",  # No question mark
        field_category="dropdown",
        profile=profile
    )
    if pattern:
        print(f"   [OK] Found pattern: {pattern.profile_field}")
    else:
        print(f"   [FAIL] No pattern found")

    # Test 3: Fuzzy match (if pg_trgm is enabled)
    print("\n3. Testing fuzzy match: 'military service'")
    pattern = mapper.map_field(
        field_label="Military service?",
        field_category="dropdown",
        profile=profile
    )
    if pattern:
        print(f"   [OK] Found fuzzy pattern: {pattern.profile_field} (confidence: {pattern.confidence_score:.2f})")
    else:
        print(f"   [INFO] No fuzzy match (may need pg_trgm extension or higher similarity)")

    # Test 4: Get value from profile
    print("\n4. Testing profile value extraction")
    if pattern:
        value = mapper.get_profile_value(profile, pattern.profile_field)
        print(f"   Value from profile['{pattern.profile_field}']: {value}")

    # Test 5: Cache stats
    print("\n5. Cache statistics")
    cache_stats = mapper.get_cache_stats()
    print(f"   Total entries: {cache_stats['total_entries']}")
    print(f"   Active entries: {cache_stats['active_entries']}")

    return True


async def test_full_workflow():
    """Test complete workflow: record -> retrieve -> use."""
    print("\n" + "="*60)
    print("TEST 3: Full Workflow Simulation")
    print("="*60)

    recorder = PatternRecorder()
    mapper = LearnedPatternsMapper()

    profile = {
        "first_name": "Alice",
        "last_name": "Smith",
        "email": "alice@example.com",
        "phone": "555-1234"
    }

    # Simulate AI learning from first application
    print("\n1. Simulating first application (AI learns)")
    print("   AI fills 'Email Address' -> email")
    await recorder.record_pattern("Email Address", "email", "email_input", True)
    print("   AI fills 'Phone Number' -> phone")
    await recorder.record_pattern("Phone Number", "phone", "tel_input", True)
    print("   [OK] Patterns recorded")

    # Simulate second application (using learned patterns)
    print("\n2. Simulating second application (using learned patterns)")

    print("   Looking up 'Email Address'...")
    pattern = mapper.map_field("Email Address", "email_input", profile)
    if pattern:
        value = mapper.get_profile_value(profile, pattern.profile_field)
        print(f"   [OK] Used learned pattern: {pattern.profile_field} = '{value}'")
        print(f"      (Saved 1 AI API call!)")

    print("   Looking up 'Phone Number'...")
    pattern = mapper.map_field("Phone Number", "tel_input", profile)
    if pattern:
        value = mapper.get_profile_value(profile, pattern.profile_field)
        print(f"   [OK] Used learned pattern: {pattern.profile_field} = '{value}'")
        print(f"      (Saved 1 AI API call!)")

    # Update with success
    print("\n3. Recording successful reuse (boosts confidence)")
    await recorder.record_pattern("Email Address", "email", "email_input", True)
    await recorder.record_pattern("Phone Number", "phone", "tel_input", True)

    # Check updated confidence
    print("\n4. Checking updated confidence scores")
    pattern = mapper.map_field("Email Address", "email_input", profile)
    if pattern:
        print(f"   Email pattern confidence: {pattern.confidence_score:.2f} (occurrences: {pattern.occurrence_count})")

    return True

async def main():
    """Run all tests."""
    print("\n[TEST] PATTERN LEARNING SYSTEM - TEST SUITE")
    print("="*60)

    try:
        # Test 1: Recording
        await test_pattern_recording()

        # Test 2: Retrieval
        await test_pattern_retrieval()

        # Test 3: Full workflow
        await test_full_workflow()

        print("\n" + "="*60)
        print("[OK] ALL TESTS COMPLETED")
        print("="*60)
        print("\nNotes:")
        print("- Check database for recorded patterns")
        print("- Fuzzy matching requires pg_trgm extension")
        print("- Seed data should be visible in pattern stats")

    except Exception as e:
        logger.error(f"Test failed: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(main())
