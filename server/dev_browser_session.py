"""
Development Browser Session Manager

For local Windows development where VNC isn't available.
Provides browser state information and screenshots as fallback.
"""

import logging
import uuid
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)


class DevBrowserSession:
    """Manages browser sessions for local development (Windows)"""
    
    def __init__(self):
        self.sessions: Dict[str, Dict[str, Any]] = {}
        
    def register_session(self, session_id: str, job_url: str, user_id: str, 
                        current_url: str, screenshot_path: str = None):
        """Register a browser session (browser is actually kept open by agent)"""
        self.sessions[session_id] = {
            'session_id': session_id,
            'job_url': job_url,
            'user_id': user_id,
            'current_url': current_url,
            'screenshot_path': screenshot_path,
            'status': 'ready_for_review',
            'vnc_enabled': False,  # Not true VNC, just browser kept open
            'dev_mode': True
        }
        
        logger.info(f"📝 Registered dev browser session: {session_id}")
        
    def get_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        """Get session info"""
        return self.sessions.get(session_id)
    
    def close_session(self, session_id: str):
        """Remove session"""
        if session_id in self.sessions:
            del self.sessions[session_id]
            logger.info(f"🗑️ Closed dev session: {session_id}")


# Global instance
dev_browser_session = DevBrowserSession()

