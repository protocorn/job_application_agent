#!/usr/bin/env python3
"""
Resume Hidden Browser - Simple Beta Version

This script shows and resumes hidden browser sessions.
Browser stays alive in background - this just makes it visible again!
"""

import asyncio
import sys
import os
from datetime import datetime

# Add parent directory to path
sys.path.append(os.path.join(os.path.dirname(__file__), '..'))

from Agents.hidden_browser_manager import HiddenBrowserManager


class ResumeHiddenInterface:
    """Simple interface to resume hidden browser sessions"""
    
    def __init__(self):
        self.manager = HiddenBrowserManager()
        
    def display_sessions(self):
        """Display all active hidden browser sessions"""
        sessions = self.manager.get_active_sessions()
        
        print("\n" + "="*80)
        print("🧊 HIDDEN BROWSER SESSIONS (BETA)")
        print("="*80)
        
        if not sessions:
            print("\n❌ No hidden browser sessions found.")
            print("\n💡 Tips:")
            print("   - Run: python Agents/job_application_agent_test.py --links 'url'")
            print("   - Wait for agent to pause at human intervention")
            print("   - Browser will be hidden automatically")
            print("   - Come back here to resume!")
            print("\n" + "="*80 + "\n")
            return None
        
        print(f"\nFound {len(sessions)} hidden browser session(s):\n")
        
        session_list = list(sessions.items())
        for i, (session_id, info) in enumerate(session_list, 1):
            hidden_at = datetime.fromisoformat(info['hidden_at'])
            age = datetime.now() - hidden_at
            
            # Format age
            if age.total_seconds() < 3600:
                age_str = f"{int(age.total_seconds() / 60)} minutes ago"
            elif age.total_seconds() < 86400:
                age_str = f"{age.total_seconds() / 3600:.1f} hours ago"
            else:
                age_str = f"{age.total_seconds() / 86400:.1f} days ago"
            
            print(f"[{i}] 🏢 {info.get('company', 'Unknown Company')}")
            print(f"    📍 URL: {info.get('job_url', 'Unknown')[:60]}...")
            print(f"    📊 Progress: {info.get('progress', 0)}%")
            print(f"    ⏰ Hidden: {age_str}")
            print(f"    💬 Reason: {info.get('reason', 'Unknown')}")
            print(f"    🆔 Session ID: {session_id[:20]}...")
            print()
        
        print("="*80 + "\n")
        return session_list
    
    async def resume_session(self, session_id: str, session_info: dict):
        """Resume a hidden browser session"""
        print(f"\n🔄 Resuming session...")
        print(f"📋 Session ID: {session_id}")
        print(f"📍 URL: {session_info['current_url']}")
        print(f"📊 Progress: {session_info.get('progress', 0)}%")
        print("\n" + "="*80)
        
        # Try to find and restore the browser window using Windows API
        print("\n🔍 Attempting to restore browser window...")
        
        try:
            import subprocess
            
            # Try to find Chrome/Chromium windows and restore them
            powershell_script = """
            Add-Type @"
                using System;
                using System.Runtime.InteropServices;
                public class Win32 {
                    [DllImport("user32.dll")]
                    public static extern bool ShowWindow(IntPtr hWnd, int nCmdShow);
                    [DllImport("user32.dll")]
                    public static extern bool SetForegroundWindow(IntPtr hWnd);
                }
"@
            Get-Process | Where-Object {$_.ProcessName -match 'chrome|msedge'} | ForEach-Object {
                [Win32]::ShowWindow($_.MainWindowHandle, 9)  # SW_RESTORE = 9
                [Win32]::SetForegroundWindow($_.MainWindowHandle)
            }
            """
            
            result = subprocess.run(
                ["powershell", "-Command", powershell_script],
                capture_output=True,
                text=True,
                timeout=5
            )
            
            if result.returncode == 0:
                print("✅ Attempted to restore browser windows")
                print("💡 Check your screen - browser should now be visible!")
            else:
                print("⚠️  Could not auto-restore window")
                print(f"   Error: {result.stderr[:100]}")
                print("\n💡 Manual steps:")
                print("   1. Press Alt+Tab to find browser window")
                print("   2. Look for Chrome/Chromium in taskbar")
                print("   3. Click on it to bring to front")
                
        except Exception as e:
            print(f"⚠️  Auto-restore not available: {e}")
            print("\n💡 To continue your application:")
            print("   1. Find the browser window (it should still be open)")
            print("   2. Press Alt+Tab or check taskbar for Chrome/Chromium")
            print("   3. Click on it to bring it to focus")
            print("   4. Complete the application")
            print("   5. Submit when ready!")
        
        print("\n" + "="*80 + "\n")
        
        # Mark as resumed
        session_info['status'] = 'resumed'
        session_info['resumed_at'] = datetime.now().isoformat()
        self.manager.active_sessions[session_id] = session_info
        self.manager._save_active_sessions()
        
        print("✅ Session marked as resumed!")
        print(f"💡 Browser window should be visible in your taskbar.")
        print(f"\nPress Enter to exit this script...")
        input()
    
    def run(self):
        """Main interactive loop"""
        print("\n" + "="*80)
        print(" "*25 + "🧊 RESUME HIDDEN BROWSER (BETA)")
        print("="*80)
        print("\nThis beta version helps you find your hidden browser sessions.")
        print("Browser windows stay alive - you just need to bring them to focus!")
        print("="*80)
        
        while True:
            session_list = self.display_sessions()
            
            if not session_list:
                break
            
            print("Options:")
            print(f"  - Enter a number (1-{len(session_list)}) to resume that session")
            print(f"  - Enter 'q' to quit")
            print()
            
            choice = input("Your choice: ").strip().lower()
            
            if choice == 'q':
                print("\n👋 Goodbye!")
                break
            
            try:
                idx = int(choice) - 1
                if 0 <= idx < len(session_list):
                    session_id, session_info = session_list[idx]
                    asyncio.run(self.resume_session(session_id, session_info))
                    
                    # Ask if they want to continue
                    cont = input("\n🔄 Resume another session? (y/n): ").strip().lower()
                    if cont != 'y':
                        print("\n👋 Goodbye!")
                        break
                else:
                    print(f"❌ Invalid choice. Please enter 1-{len(session_list)}")
            except ValueError:
                print("❌ Invalid input. Please enter a number or 'q'")


def main():
    """Entry point"""
    interface = ResumeHiddenInterface()
    interface.run()


if __name__ == "__main__":
    main()

