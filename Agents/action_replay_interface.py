#!/usr/bin/env python3
"""
Action Replay Interface for Job Application Agent

This script allows users to:
1. View all recorded job application sessions
2. Select a session to replay
3. Watch the form being filled automatically in real-time
"""

import asyncio
import os
import sys
import json
import logging
from datetime import datetime
from typing import List, Dict, Any
from playwright.async_api import async_playwright

# Add parent directory to path
sys.path.append(os.path.join(os.path.dirname(__file__), '..'))

from Agents.components.session.session_manager import SessionManager, ApplicationSession
from Agents.components.action_recorder import ActionReplay, ActionStep
from logging_config import setup_file_logging

logger = logging.getLogger(__name__)


class ActionReplayInterface:
    """Interactive interface for replaying recorded job application sessions"""
    
    def __init__(self, sessions_dir: str = "sessions"):
        self.sessions_dir = sessions_dir
        self.session_manager = SessionManager(storage_dir=sessions_dir)
        
    def get_replayable_sessions(self) -> List[ApplicationSession]:
        """Get all sessions that have recorded actions"""
        all_sessions = self.session_manager.get_all_sessions()
        
        # Filter sessions that have action history
        replayable = []
        for session in all_sessions:
            if session.action_history and len(session.action_history) > 0:
                replayable.append(session)
            else:
                # Check if action log file exists
                action_log_file = os.path.join(
                    self.session_manager.action_logs_dir, 
                    f"actions_{session.session_id}.json"
                )
                if os.path.exists(action_log_file):
                    replayable.append(session)
        
        return replayable
    
    def display_sessions(self, sessions: List[ApplicationSession]):
        """Display sessions in a formatted table"""
        print("\n" + "="*100)
        print("📼 AVAILABLE SESSIONS FOR REPLAY")
        print("="*100)
        
        if not sessions:
            print("\n❌ No sessions with recorded actions found.")
            print("💡 Run a job application first to record actions.")
            return
        
        print(f"\n{'#':<4} {'Created':<20} {'Company':<25} {'Status':<20} {'Actions':<10} {'Progress':<10}")
        print("-"*100)
        
        for i, session in enumerate(sessions, 1):
            created_time = datetime.fromtimestamp(session.created_at).strftime('%Y-%m-%d %H:%M:%S')
            company = (session.company[:22] + '...') if len(session.company) > 25 else session.company
            if not company:
                # Extract company from URL
                company = self._extract_company_from_url(session.job_url)
            
            status = session.status
            action_count = len(session.action_history)
            progress = f"{session.completion_percentage:.0f}%"
            
            # Color-code status
            status_emoji = {
                'completed': '✅',
                'needs_attention': '⚠️',
                'in_progress': '🔄',
                'frozen': '❄️',
                'failed': '❌',
                'requires_authentication': '🔐'
            }.get(status, '❓')
            
            print(f"{i:<4} {created_time:<20} {company:<25} {status_emoji} {status:<17} {action_count:<10} {progress:<10}")
        
        print("-"*100)
        print(f"\nTotal: {len(sessions)} replayable sessions")
        print("="*100 + "\n")
    
    def _extract_company_from_url(self, url: str) -> str:
        """Extract company name from job URL"""
        try:
            # Common job board patterns
            if 'greenhouse.io' in url:
                parts = url.split('/')
                for i, part in enumerate(parts):
                    if 'boards' in part and i + 1 < len(parts):
                        return parts[i + 1].replace('-', ' ').title()
            elif 'lever.co' in url:
                parts = url.split('/')
                if len(parts) > 3:
                    return parts[3].replace('-', ' ').title()
            elif 'myworkdayjobs.com' in url:
                # Extract from subdomain
                domain = url.split('//')[1].split('.')[0]
                return domain.replace('-', ' ').title()
            
            # Fallback to domain name
            domain = url.split('//')[1].split('/')[0].split('.')[0]
            return domain.title()
        except:
            return "Unknown"
    
    def display_session_details(self, session: ApplicationSession):
        """Display detailed information about a specific session"""
        print("\n" + "="*100)
        print("📋 SESSION DETAILS")
        print("="*100)
        
        print(f"\n🆔 Session ID: {session.session_id}")
        print(f"🏢 Company: {session.company or self._extract_company_from_url(session.job_url)}")
        print(f"💼 Job Title: {session.job_title or 'N/A'}")
        print(f"🔗 Job URL: {session.job_url}")
        print(f"📊 Status: {session.status}")
        print(f"📈 Progress: {session.completion_percentage:.0f}%")
        print(f"🕐 Created: {datetime.fromtimestamp(session.created_at).strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"🕐 Last Updated: {datetime.fromtimestamp(session.last_updated).strftime('%Y-%m-%d %H:%M:%S')}")
        
        # Action breakdown
        if session.action_history:
            action_types = {}
            successful_actions = 0
            failed_actions = 0
            
            for action in session.action_history:
                action_type = action.get('type', 'unknown')
                action_types[action_type] = action_types.get(action_type, 0) + 1
                
                if action.get('success', False):
                    successful_actions += 1
                else:
                    failed_actions += 1
            
            print(f"\n📝 Recorded Actions:")
            print(f"   Total: {len(session.action_history)}")
            print(f"   ✅ Successful: {successful_actions}")
            print(f"   ❌ Failed: {failed_actions}")
            
            print(f"\n📊 Action Breakdown:")
            for action_type, count in sorted(action_types.items(), key=lambda x: x[1], reverse=True):
                emoji = {
                    'navigate': '🧭',
                    'fill_field': '✏️',
                    'enhanced_field_fill': '✨',
                    'click': '👆',
                    'select_option': '📋',
                    'upload_file': '📄',
                    'wait': '⏱️'
                }.get(action_type, '❓')
                print(f"   {emoji} {action_type}: {count}")
            
            # Show first few actions
            print(f"\n🎬 Preview (first 5 actions):")
            for i, action in enumerate(session.action_history[:5], 1):
                action_type = action.get('type', 'unknown')
                field_label = action.get('field_label', '')
                value = action.get('value', '')
                url = action.get('url', '')
                success = '✓' if action.get('success', False) else '✗'
                
                if action_type == 'navigate':
                    print(f"   {i}. {success} {action_type}: {url}")
                elif action_type in ['fill_field', 'enhanced_field_fill']:
                    value_preview = (value[:30] + '...') if len(value) > 30 else value
                    print(f"   {i}. {success} {action_type}: {field_label} = {value_preview}")
                elif action_type == 'click':
                    element_text = action.get('element_text', '')
                    print(f"   {i}. {success} {action_type}: {element_text or field_label}")
                else:
                    print(f"   {i}. {success} {action_type}")
        
        print("\n" + "="*100 + "\n")
    
    async def replay_session(self, session: ApplicationSession, slow_mode: bool = False):
        """Replay a session's actions in a visible browser"""
        print(f"\n🎬 Starting replay for session: {session.session_id}")
        print(f"🌐 Job URL: {session.job_url}")
        
        # Ensure action history is loaded
        if not session.action_history:
            action_log_file = os.path.join(
                self.session_manager.action_logs_dir, 
                f"actions_{session.session_id}.json"
            )
            if os.path.exists(action_log_file):
                with open(action_log_file, 'r', encoding='utf-8') as f:
                    action_data = json.load(f)
                session.action_history = action_data.get('actions', [])
        
        if not session.action_history:
            print("❌ No actions to replay!")
            return False
        
        print(f"📝 Found {len(session.action_history)} actions to replay")
        
        # Convert to ActionStep objects
        actions = [ActionStep.from_dict(action_data) for action_data in session.action_history]
        successful_actions = [action for action in actions if action.success]
        
        print(f"✅ Replaying {len(successful_actions)} successful actions")
        print(f"\n{'='*100}")
        print("🎭 Opening browser in visible mode...")
        print(f"{'='*100}\n")
        
        # Start Playwright with visible browser
        playwright = await async_playwright().start()
        try:
            # Use slow_mo for better visibility if requested
            slow_mo_ms = 500 if slow_mode else 100
            
            browser = await playwright.chromium.launch(
                headless=False,  # Always visible
                slow_mo=slow_mo_ms
            )
            context = await browser.new_context()
            page = await context.new_page()
            
            # Create replay instance
            action_replay = ActionReplay(page)
            
            # Progress callback
            def progress_callback(current, total, action_type, description):
                progress_pct = (current / total) * 100
                print(f"⏩ [{current}/{total}] ({progress_pct:.0f}%) {action_type}: {description}")
            
            # Replay actions
            print("🎬 Starting action replay...\n")
            success = await action_replay.replay_actions(
                successful_actions, 
                stop_at_failure=False,
                progress_callback=progress_callback
            )
            
            print(f"\n{'='*100}")
            if success:
                print("✅ Replay completed successfully!")
            else:
                print("⚠️ Replay completed with some errors (see above)")
            print(f"{'='*100}\n")
            
            print("👀 Browser will stay open for review...")
            print("💡 You can now:")
            print("   - Review the filled form")
            print("   - Make manual corrections if needed")
            print("   - Submit the application manually")
            print("\n🔒 Press Enter when you're done to close the browser...")
            
            # Wait for user input
            await asyncio.get_event_loop().run_in_executor(None, input)
            
            await browser.close()
            return success
            
        except Exception as e:
            logger.error(f"Error during replay: {e}")
            print(f"\n❌ Error during replay: {e}")
            return False
        finally:
            await playwright.stop()
    
    def run_interactive(self):
        """Run the interactive replay interface"""
        print("\n" + "="*100)
        print(" "*35 + "🎬 ACTION REPLAY INTERFACE")
        print("="*100)
        print("\nThis tool allows you to replay previously recorded job application sessions.")
        print("The browser will open and you'll see the form being filled automatically.")
        print("="*100)
        
        # Get replayable sessions
        sessions = self.get_replayable_sessions()
        
        if not sessions:
            self.display_sessions(sessions)
            return
        
        while True:
            # Display sessions
            self.display_sessions(sessions)
            
            # Get user choice
            print("Options:")
            print("  - Enter a number (1-{}) to replay that session".format(len(sessions)))
            print("  - Enter 'd' followed by number (e.g., 'd3') to view details")
            print("  - Enter 'q' to quit")
            print()
            
            choice = input("Your choice: ").strip().lower()
            
            if choice == 'q':
                print("\n👋 Goodbye!")
                break
            
            # Check for details command
            if choice.startswith('d'):
                try:
                    session_num = int(choice[1:])
                    if 1 <= session_num <= len(sessions):
                        self.display_session_details(sessions[session_num - 1])
                        input("\nPress Enter to continue...")
                    else:
                        print(f"❌ Invalid session number. Choose between 1 and {len(sessions)}")
                except ValueError:
                    print("❌ Invalid input. Use format: d<number> (e.g., d3)")
                continue
            
            # Check for replay command
            try:
                session_num = int(choice)
                if 1 <= session_num <= len(sessions):
                    selected_session = sessions[session_num - 1]
                    
                    # Show details before replay
                    self.display_session_details(selected_session)
                    
                    # Confirm replay
                    print("🎬 Replay options:")
                    print("  - Press Enter to replay at normal speed")
                    print("  - Enter 's' for slow mode (500ms between actions)")
                    print("  - Enter 'c' to cancel")
                    
                    replay_choice = input("\nYour choice: ").strip().lower()
                    
                    if replay_choice == 'c':
                        print("❌ Replay cancelled")
                        continue
                    
                    slow_mode = (replay_choice == 's')
                    
                    # Run replay
                    asyncio.run(self.replay_session(selected_session, slow_mode=slow_mode))
                    
                    # Ask if user wants to continue
                    continue_choice = input("\n🔄 Replay another session? (y/n): ").strip().lower()
                    if continue_choice != 'y':
                        print("\n👋 Goodbye!")
                        break
                else:
                    print(f"❌ Invalid session number. Choose between 1 and {len(sessions)}")
            except ValueError:
                print("❌ Invalid input. Please enter a number, details command (d<number>), or 'q' to quit")


def main():
    """Main entry point"""
    import argparse
    
    # Set up logging
    log_file = setup_file_logging(log_level=logging.INFO, console_logging=True)
    logger.info(f"Action Replay Interface starting. Logs: {log_file}")
    
    parser = argparse.ArgumentParser(
        description="Replay recorded job application sessions",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Run in interactive mode (default)
  python action_replay_interface.py
  
  # Specify custom sessions directory
  python action_replay_interface.py --sessions-dir /path/to/sessions
  
  # Replay a specific session directly
  python action_replay_interface.py --session-id abc123 --slow
        """
    )
    
    parser.add_argument(
        "--sessions-dir",
        type=str,
        default="sessions",
        help="Path to sessions directory (default: sessions)"
    )
    
    parser.add_argument(
        "--session-id",
        type=str,
        default=None,
        help="Replay a specific session ID directly (skip interactive menu)"
    )
    
    parser.add_argument(
        "--slow",
        action="store_true",
        help="Use slow mode for replay (500ms between actions)"
    )
    
    args = parser.parse_args()
    
    # Create interface
    interface = ActionReplayInterface(sessions_dir=args.sessions_dir)
    
    # Direct replay mode
    if args.session_id:
        session = interface.session_manager.get_session(args.session_id)
        if not session:
            print(f"❌ Session {args.session_id} not found")
            sys.exit(1)
        
        interface.display_session_details(session)
        asyncio.run(interface.replay_session(session, slow_mode=args.slow))
    else:
        # Interactive mode
        interface.run_interactive()


if __name__ == "__main__":
    main()

