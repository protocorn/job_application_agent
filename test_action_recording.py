#!/usr/bin/env python3
"""
Test script to verify the action recording and replay functionality
"""

import asyncio
import os
import sys
import json
import tempfile
from playwright.async_api import async_playwright

# Add the project directory to the path
sys.path.append(os.path.dirname(__file__))
sys.path.append(os.path.join(os.path.dirname(__file__), 'Agents'))

from Agents.components.action_recorder import ActionRecorder, ActionReplay
from Agents.components.session.session_manager import SessionManager


async def test_basic_action_recording():
    """Test basic action recording functionality"""
    print("🧪 Testing basic action recording...")

    # Create temporary directory for testing
    with tempfile.TemporaryDirectory() as temp_dir:
        print(f"📁 Using temp directory: {temp_dir}")

        # Initialize session manager
        session_manager = SessionManager(temp_dir)

        # Test action recorder
        session_id = "test_session_123"
        initial_url = "https://example.com"

        action_recorder = session_manager.start_action_recording(session_id, initial_url)

        # Simulate some actions
        action_recorder.record_navigation("https://example.com/jobs", success=True)
        action_recorder.record_click("button.apply-btn", "Apply Button", success=True)
        action_recorder.record_field_fill("input[name='email']", "test@example.com", "Email", "text_input", success=True)
        action_recorder.record_field_fill("input[name='name']", "John Doe", "Full Name", "text_input", success=True)
        action_recorder.record_select_option("select[name='experience']", "2-3 years", "Experience Level", success=True)

        # Stop recording and save
        success = session_manager.stop_action_recording(session_id, save_to_session=True)
        print(f"✅ Action recording stopped and saved: {success}")

        # Check if session has actions
        session = session_manager.get_session(session_id)
        if session and session.action_history:
            print(f"✅ Session has {len(session.action_history)} recorded actions")

            # Print action summary
            for i, action in enumerate(session.action_history):
                print(f"  {i+1}. {action['type']}: {action.get('field_label', action.get('url', action.get('selector', 'unknown')))}")
        else:
            print("❌ No actions found in session")
            return False

        print("✅ Basic action recording test passed!")
        return True


async def test_action_replay():
    """Test action replay functionality"""
    print("\n🧪 Testing action replay...")

    playwright = await async_playwright().start()
    try:
        # Launch browser
        browser = await playwright.chromium.launch(headless=True)
        context = await browser.new_context()
        page = await context.new_page()

        # Create test actions
        from Agents.components.action_recorder import ActionStep
        import time

        test_actions = [
            ActionStep(
                type="navigate",
                timestamp=time.time(),
                url="https://example.com",
                success=True
            ),
            ActionStep(
                type="wait",
                timestamp=time.time(),
                value="1000",
                success=True
            )
        ]

        # Test action replay
        action_replay = ActionReplay(page)
        success = await action_replay.replay_actions(test_actions, stop_at_failure=False)

        print(f"✅ Action replay completed: {success}")

        # Check if we're on the right page
        current_url = page.url
        print(f"🌐 Current URL after replay: {current_url}")

        if "example.com" in current_url:
            print("✅ Navigation replay worked correctly")
        else:
            print("❌ Navigation replay failed")
            return False

        await browser.close()
        print("✅ Action replay test passed!")
        return True

    except Exception as e:
        print(f"❌ Action replay test failed: {e}")
        return False
    finally:
        await playwright.stop()


async def test_session_persistence():
    """Test session persistence and loading"""
    print("\n🧪 Testing session persistence...")

    with tempfile.TemporaryDirectory() as temp_dir:
        print(f"📁 Using temp directory: {temp_dir}")

        # Create first session manager instance
        session_manager1 = SessionManager(temp_dir)

        # Create a test session
        session = session_manager1.create_session("https://test-job.com", "Test Job", "Test Company")
        session_id = session.session_id

        # Add some action history manually
        session.action_history = [
            {
                "type": "navigate",
                "timestamp": 1234567890,
                "url": "https://test-job.com",
                "success": True
            },
            {
                "type": "fill_field",
                "timestamp": 1234567891,
                "selector": "input[name='email']",
                "value": "test@example.com",
                "field_label": "Email",
                "success": True
            }
        ]

        # Save the session
        session_manager1.save_sessions()
        print(f"✅ Session {session_id} saved with {len(session.action_history)} actions")

        # Create new session manager instance (simulating app restart)
        session_manager2 = SessionManager(temp_dir)

        # Load the session
        loaded_session = session_manager2.get_session(session_id)

        if loaded_session:
            print(f"✅ Session loaded successfully")
            print(f"📋 Job URL: {loaded_session.job_url}")
            print(f"🎬 Actions: {len(loaded_session.action_history)}")

            if len(loaded_session.action_history) == 2:
                print("✅ Action history preserved correctly")
                return True
            else:
                print(f"❌ Expected 2 actions, got {len(loaded_session.action_history)}")
                return False
        else:
            print("❌ Failed to load session")
            return False


async def main():
    """Run all tests"""
    print("🚀 Starting Action Recording System Tests\n")

    tests = [
        ("Basic Action Recording", test_basic_action_recording),
        ("Action Replay", test_action_replay),
        ("Session Persistence", test_session_persistence),
    ]

    results = []
    for test_name, test_func in tests:
        print(f"{'='*50}")
        print(f"Running: {test_name}")
        print(f"{'='*50}")

        try:
            result = await test_func()
            results.append((test_name, result))
            status = "✅ PASSED" if result else "❌ FAILED"
            print(f"\n{test_name}: {status}")
        except Exception as e:
            print(f"\n❌ {test_name}: FAILED with exception: {e}")
            results.append((test_name, False))

    # Summary
    print(f"\n{'='*50}")
    print("TEST SUMMARY")
    print(f"{'='*50}")

    passed = sum(1 for _, result in results if result)
    total = len(results)

    for test_name, result in results:
        status = "✅ PASSED" if result else "❌ FAILED"
        print(f"{test_name}: {status}")

    print(f"\nPassed: {passed}/{total}")

    if passed == total:
        print("🎉 All tests passed! Action recording system is working correctly.")
    else:
        print("⚠️ Some tests failed. Check the output above for details.")

    return passed == total


if __name__ == "__main__":
    success = asyncio.run(main())
    sys.exit(0 if success else 1)