#!/usr/bin/env python3
"""
VNC Integration Test Script

Tests the complete VNC streaming setup locally before Railway deployment
"""

import asyncio
import sys
import os
import logging

# Setup paths
sys.path.append(os.path.join(os.path.dirname(__file__), 'Agents'))

from Agents.components.vnc import BrowserVNCCoordinator
from Agents.job_application_agent import run_links_with_refactored_agent
from Agents.components.session.session_manager import SessionManager

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def test_vnc_basic():
    """Test 1: Basic VNC infrastructure"""
    print("\n" + "="*80)
    print("TEST 1: Basic VNC Infrastructure")
    print("="*80 + "\n")
    
    try:
        coordinator = BrowserVNCCoordinator(vnc_port=5900)
        
        print("Starting VNC environment...")
        success = await coordinator.start()
        
        if success:
            print("✅ VNC environment started successfully!")
            print(f"   Display: {coordinator.virtual_display.display}")
            print(f"   VNC Port: {coordinator.vnc_port}")
            print(f"   VNC URL: {coordinator.get_vnc_url()}")
            print(f"\n📺 Status: {coordinator.get_status()}")
            
            # Test navigation
            page = coordinator.get_page()
            print(f"\n🌐 Testing browser navigation...")
            await page.goto("https://example.com")
            print(f"✅ Navigated to: {page.url}")
            
            print(f"\n💡 Connect VNC viewer to: localhost:5900")
            print(f"   You should see example.com in the browser!")
            print(f"\n⏸️  Keeping browser open for 30 seconds...")
            print(f"   (Press Ctrl+C to stop early)")
            
            try:
                await asyncio.sleep(30)
            except KeyboardInterrupt:
                print("\n👋 Interrupted by user")
            
            print(f"\n🛑 Stopping VNC environment...")
            await coordinator.stop()
            print("✅ Test 1 PASSED!")
            return True
        else:
            print("❌ Failed to start VNC environment")
            print("\n🔍 Troubleshooting:")
            print("   - Are you on Linux/WSL? (VNC requires Linux)")
            print("   - Is Xvfb installed? (sudo apt-get install xvfb)")
            print("   - Is x11vnc installed? (sudo apt-get install x11vnc)")
            return False
            
    except Exception as e:
        print(f"❌ Test 1 FAILED: {e}")
        import traceback
        traceback.print_exc()
        return False


async def test_vnc_with_agent():
    """Test 2: VNC with job application agent"""
    print("\n" + "="*80)
    print("TEST 2: VNC with Job Application Agent")
    print("="*80 + "\n")
    
    try:
        test_url = "https://boards.greenhouse.io/embed/job_app?token=test"
        
        print(f"Starting agent with VNC on test URL...")
        print(f"URL: {test_url}")
        
        session_manager = SessionManager(storage_dir="test_sessions")
        
        # Run agent with VNC mode
        vnc_info = await run_links_with_refactored_agent(
            links=[test_url],
            headless=False,  # Must be False for VNC
            keep_open=False,
            debug=False,
            hold_seconds=0,
            slow_mo_ms=500,  # Slow down to watch
            job_id="test-vnc-job",
            jobs_dict={},
            session_manager=session_manager,
            user_id="test-user",
            vnc_mode=True,  # ENABLE VNC!
            vnc_port=5901  # Use different port than test 1
        )
        
        if vnc_info:
            print(f"\n✅ Agent completed with VNC info:")
            print(f"   Session ID: {vnc_info.get('session_id')}")
            print(f"   VNC Port: {vnc_info.get('vnc_port')}")
            print(f"   VNC URL: {vnc_info.get('vnc_url')}")
            print(f"   Current URL: {vnc_info.get('current_url')}")
            
            print(f"\n💡 Connect VNC viewer to: localhost:{vnc_info.get('vnc_port')}")
            print(f"   You should see the job application form!")
            print("✅ Test 2 PASSED!")
            return True
        else:
            print("⚠️ Agent ran but no VNC info returned")
            print("   This is normal if agent completed without pausing")
            return True
            
    except Exception as e:
        print(f"❌ Test 2 FAILED: {e}")
        import traceback
        traceback.print_exc()
        return False


async def test_vnc_health_check():
    """Test 3: Test VNC health API endpoint"""
    print("\n" + "="*80)
    print("TEST 3: VNC Health Check API")
    print("="*80 + "\n")
    
    try:
        import requests
        
        print("Testing VNC health endpoint...")
        print("URL: http://localhost:5000/api/vnc/health")
        
        response = requests.get('http://localhost:5000/api/vnc/health', timeout=5)
        
        if response.status_code == 200:
            data = response.json()
            print(f"✅ VNC health check passed!")
            print(f"   Status: {data.get('status')}")
            print(f"   VNC Available: {data.get('vnc_available')}")
            print(f"   Active Sessions: {data.get('active_sessions')}")
            print(f"   Available Ports: {data.get('available_ports')}")
            print("✅ Test 3 PASSED!")
            return True
        else:
            print(f"❌ Health check failed with status {response.status_code}")
            return False
            
    except requests.exceptions.ConnectionError:
        print("⚠️ Could not connect to API server")
        print("   Make sure server is running: python server/api_server.py")
        return False
    except Exception as e:
        print(f"❌ Test 3 FAILED: {e}")
        return False


async def run_all_tests():
    """Run all tests in sequence"""
    print("\n" + "="*80)
    print(" "*25 + "VNC INTEGRATION TESTS")
    print("="*80)
    
    results = []
    
    # Test 1: Basic VNC
    print("\n📋 Running tests...")
    result1 = await test_vnc_basic()
    results.append(("Basic VNC Infrastructure", result1))
    
    # Test 2: VNC with Agent  
    if result1:  # Only run if test 1 passed
        result2 = await test_vnc_with_agent()
        results.append(("VNC with Agent", result2))
    
    # Test 3: Health API (requires server running)
    result3 = await test_vnc_health_check()
    results.append(("VNC Health API", result3))
    
    # Summary
    print("\n" + "="*80)
    print("TEST SUMMARY")
    print("="*80 + "\n")
    
    for test_name, result in results:
        status = "✅ PASSED" if result else "❌ FAILED"
        print(f"{status} - {test_name}")
    
    passed = sum(1 for _, result in results if result)
    total = len(results)
    
    print(f"\n📊 Results: {passed}/{total} tests passed")
    
    if passed == total:
        print("\n🎉 All tests passed! VNC integration is working!")
        print("\n🚀 Next steps:")
        print("   1. Deploy to Railway: railway up")
        print("   2. Test on Railway with real job URL")
        print("   3. Integrate frontend VNC viewer")
        print("   4. Launch beta!")
    else:
        print("\n⚠️ Some tests failed. Check errors above.")
        print("\n🔍 Common issues:")
        print("   - VNC requires Linux (use WSL on Windows)")
        print("   - Install: apt-get install xvfb x11vnc")
        print("   - Install: pip install flask-socketio websockify")
    
    print("\n" + "="*80 + "\n")


def main():
    """Main entry point"""
    import argparse
    
    parser = argparse.ArgumentParser(description="Test VNC integration")
    parser.add_argument('--test', choices=['basic', 'agent', 'health', 'all'], 
                       default='all', help='Which test to run')
    args = parser.parse_args()
    
    if args.test == 'basic':
        asyncio.run(test_vnc_basic())
    elif args.test == 'agent':
        asyncio.run(test_vnc_with_agent())
    elif args.test == 'health':
        asyncio.run(test_vnc_health_check())
    else:
        asyncio.run(run_all_tests())


if __name__ == "__main__":
    main()

