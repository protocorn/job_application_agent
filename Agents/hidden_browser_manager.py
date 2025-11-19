"""
Hidden Browser Manager - Keep browser alive in background for instant resume

This is the SIMPLE approach for beta launch:
- Browser stays open (minimized/hidden)
- No complex state saving
- 100% accurate resume
- Works within same session
"""

import json
import os
import logging
from typing import Optional, Dict, Any
from datetime import datetime

logger = logging.getLogger(__name__)


class HiddenBrowserManager:
    """Manages hidden browser sessions for instant resume"""
    
    def __init__(self, storage_dir: str = "hidden_browsers"):
        self.storage_dir = storage_dir
        self.active_sessions_file = os.path.join(storage_dir, "active_sessions.json")
        
        # Create storage directory
        os.makedirs(storage_dir, exist_ok=True)
        
        # Load active sessions
        self.active_sessions = self._load_active_sessions()
    
    def _load_active_sessions(self) -> Dict[str, Any]:
        """Load list of active hidden browser sessions"""
        try:
            if os.path.exists(self.active_sessions_file):
                with open(self.active_sessions_file, 'r') as f:
                    return json.load(f)
            return {}
        except Exception as e:
            logger.error(f"Failed to load active sessions: {e}")
            return {}
    
    def _save_active_sessions(self):
        """Save list of active sessions"""
        try:
            with open(self.active_sessions_file, 'w') as f:
                json.dump(self.active_sessions, f, indent=2)
        except Exception as e:
            logger.error(f"Failed to save active sessions: {e}")
    
    async def hide_browser(self, session_id: str, page, browser, playwright, 
                          job_url: str, company: str = "", progress: int = 0,
                          reason: str = "Human intervention required"):
        """
        Hide browser window and keep it running in background
        
        Args:
            session_id: Unique session identifier
            page: Playwright page object
            browser: Playwright browser object
            playwright: Playwright instance
            job_url: Job application URL
            company: Company name
            progress: Completion percentage
            reason: Why browser was hidden
        
        Returns:
            Dict with session info
        """
        try:
            logger.info(f"🔄 Hiding browser for session {session_id}")
            
            # Get CDP (Chrome DevTools Protocol) endpoint for reconnection (optional for beta)
            cdp_endpoint = None
            try:
                # Try to get CDP endpoint for future reconnection feature
                if hasattr(browser, 'contexts') and len(browser.contexts) > 0:
                    context = browser.contexts[0]
                    if hasattr(context, '_impl_obj') and hasattr(context._impl_obj, '_connection'):
                        cdp_endpoint = getattr(context._impl_obj._connection, 'url', None)
                logger.debug(f"CDP endpoint: {cdp_endpoint or 'Not available (not needed for beta)'}")
            except Exception as e:
                logger.debug(f"Could not get CDP endpoint: {e} (not critical)")
            
            # Minimize the browser window (make it less intrusive)
            try:
                await page.evaluate("""
                    () => {
                        // Try to minimize the window
                        if (window.minimize) {
                            window.minimize();
                        } else {
                            // Fallback: move window off-screen (less ideal but works)
                            window.moveTo(-2000, -2000);
                        }
                    }
                """)
                logger.debug("Browser window minimized/hidden")
            except Exception as e:
                logger.debug(f"Could not minimize window: {e} (browser will stay visible)")
            
            # Store session info
            session_info = {
                "session_id": session_id,
                "job_url": job_url,
                "company": company,
                "progress": progress,
                "reason": reason,
                "hidden_at": datetime.now().isoformat(),
                "status": "hidden",
                "cdp_endpoint": cdp_endpoint,
                "current_url": page.url
            }
            
            self.active_sessions[session_id] = session_info
            self._save_active_sessions()
            
            logger.info(f"✅ Browser hidden for session {session_id}")
            logger.info(f"📍 Current URL: {page.url}")
            logger.info(f"📊 Progress: {progress}%")
            
            return session_info
            
        except Exception as e:
            logger.error(f"Failed to hide browser: {e}")
            return None
    
    async def unhide_browser(self, session_id: str, page) -> bool:
        """
        Unhide browser window and bring it to foreground
        
        Args:
            session_id: Session to unhide
            page: Playwright page object
        
        Returns:
            True if successfully unhidden
        """
        try:
            if session_id not in self.active_sessions:
                logger.error(f"Session {session_id} not found in active sessions")
                return False
            
            logger.info(f"🔄 Unhiding browser for session {session_id}")
            
            # Restore window position and bring to front
            await page.evaluate("""
                () => {
                    // Move window back to visible area
                    window.moveTo(100, 100);
                    
                    // Try to focus the window
                    window.focus();
                    
                    // Try to restore if minimized
                    if (window.restore) {
                        window.restore();
                    }
                }
            """)
            
            # Update session status
            self.active_sessions[session_id]["status"] = "active"
            self.active_sessions[session_id]["resumed_at"] = datetime.now().isoformat()
            self._save_active_sessions()
            
            logger.info(f"✅ Browser unhidden for session {session_id}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to unhide browser: {e}")
            return False
    
    def get_active_sessions(self) -> Dict[str, Any]:
        """Get all active hidden browser sessions"""
        return self.active_sessions
    
    def remove_session(self, session_id: str):
        """Remove session from active sessions (after browser closed)"""
        if session_id in self.active_sessions:
            del self.active_sessions[session_id]
            self._save_active_sessions()
            logger.info(f"Removed session {session_id} from active sessions")
    
    def cleanup_stale_sessions(self):
        """Clean up sessions older than 24 hours"""
        try:
            from datetime import datetime, timedelta
            
            stale_sessions = []
            for session_id, info in self.active_sessions.items():
                hidden_at = datetime.fromisoformat(info["hidden_at"])
                age = datetime.now() - hidden_at
                
                if age > timedelta(hours=24):
                    stale_sessions.append(session_id)
            
            for session_id in stale_sessions:
                logger.info(f"Removing stale session {session_id} (>24 hours old)")
                self.remove_session(session_id)
                
        except Exception as e:
            logger.error(f"Failed to cleanup stale sessions: {e}")

