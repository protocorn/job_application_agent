"""
VNC Session Manager

Manages multiple concurrent VNC browser sessions for different job applications.
Each session gets its own virtual display and VNC port.
"""

import logging
import asyncio
from typing import Dict, Optional, Any
from datetime import datetime, timedelta
from sqlalchemy.orm import Session
from database_config import SessionLocal, VNCSession

from .browser_vnc_coordinator import BrowserVNCCoordinator

logger = logging.getLogger(__name__)


class VNCSessionManager:
    """Manages multiple concurrent VNC browser sessions"""
    
    def __init__(self, base_port: int = 5900, max_sessions: int = 10):
        """
        Initialize VNC session manager
        
        Args:
            base_port: Starting port for VNC servers (5900, 5901, 5902, etc.)
            max_sessions: Maximum number of concurrent sessions
        """
        self.base_port = base_port
        self.max_sessions = max_sessions
        self.sessions: Dict[str, Dict[str, Any]] = {}
        self.port_allocations = set()  # Track allocated ports
        
        # Try to recover sessions from DB on startup?
        # We probably need an explicit 'recover_sessions()' call to avoid blocking init
        
    def _allocate_port(self) -> Optional[int]:
        """Allocate next available VNC port"""
        for i in range(self.max_sessions):
            port = self.base_port + i
            if port not in self.port_allocations:
                self.port_allocations.add(port)
                return port
        return None
    
    def _free_port(self, port: int):
        """Free a VNC port"""
        if port in self.port_allocations:
            self.port_allocations.remove(port)
    
    async def create_session(self, session_id: str, job_url: str, user_id: str, resume_path: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """
        Create a new VNC browser session

        Args:
            session_id: Unique session identifier
            job_url: Job application URL
            user_id: User who owns this session
            resume_path: Optional path to user's resume PDF

        Returns:
            Dict with VNC session info or None if failed
        """
        try:
            # Check if we're at max capacity
            if len(self.sessions) >= self.max_sessions:
                logger.error(f"Max VNC sessions reached ({self.max_sessions})")
                return None

            # Allocate port
            vnc_port = self._allocate_port()
            if not vnc_port:
                logger.error("No available VNC ports")
                return None

            logger.info(f"🆕 Creating VNC session {session_id} on port {vnc_port} for user {user_id}")
            logger.info(f"📍 Job URL: {job_url}")

            # Create VNC coordinator with user and session IDs for file isolation
            # Pass job_url to enable app mode and tab restrictions
            coordinator = BrowserVNCCoordinator(
                display_width=1920,
                display_height=1080,
                vnc_port=vnc_port,
                user_id=user_id,
                session_id=session_id,
                resume_path=resume_path,
                job_url=job_url  # IMPORTANT: This enables app mode and security restrictions
            )
            
            # Start VNC environment with retry logic and exponential backoff
            max_retries = 3
            base_delay = 2.0  # seconds
            success = False
            
            for attempt in range(max_retries):
                try:
                    logger.info(f"🚀 Starting VNC environment (attempt {attempt + 1}/{max_retries})...")
                    success = await coordinator.start()
                    
                    if success:
                        logger.info(f"✅ VNC environment started successfully on attempt {attempt + 1}")
                        break
                    else:
                        logger.warning(f"⚠️ VNC start returned False (attempt {attempt + 1}/{max_retries})")
                        
                        # Clean up failed attempt
                        try:
                            await coordinator.stop()
                        except:
                            pass
                        
                        if attempt < max_retries - 1:
                            # Exponential backoff
                            delay = base_delay * (2 ** attempt)
                            logger.info(f"⏳ Waiting {delay}s before retry...")
                            await asyncio.sleep(delay)
                        
                except Exception as e:
                    logger.error(f"❌ Exception during VNC start (attempt {attempt + 1}/{max_retries}): {e}")
                    
                    # Clean up failed attempt
                    try:
                        await coordinator.stop()
                    except:
                        pass
                    
                    if attempt < max_retries - 1:
                        # Exponential backoff
                        delay = base_delay * (2 ** attempt)
                        logger.info(f"⏳ Waiting {delay}s before retry...")
                        await asyncio.sleep(delay)
                    else:
                        logger.error(f"❌ All retry attempts exhausted for session {session_id}")
            
            if not success:
                logger.error(f"❌ Failed to start VNC session {session_id} after {max_retries} attempts")
                self._free_port(vnc_port)
                return None
            
            # Store session info in memory
            session_info = {
                "session_id": session_id,
                "job_url": job_url,
                "user_id": user_id,
                "vnc_port": vnc_port,
                "vnc_url": coordinator.get_vnc_url(),
                "coordinator": coordinator,
                "created_at": datetime.now(),
                "status": "active",
                "page": coordinator.get_page()
            }
            
            self.sessions[session_id] = session_info
            
            # Persist to Database
            try:
                db = SessionLocal()
                db_session = VNCSession(
                    id=session_id,
                    user_id=user_id,
                    job_url=job_url,
                    vnc_port=vnc_port,
                    status="active"
                )
                db.add(db_session)
                db.commit()
                db.close()
                logger.info(f"💾 Persisted VNC session {session_id} to DB")
            except Exception as e:
                logger.error(f"Failed to persist session to DB: {e}")
            
            logger.info(f"✅ VNC session {session_id} created successfully")
            logger.info(f"   VNC Port: {vnc_port}")
            logger.info(f"   Display: {coordinator.virtual_display.display}")
            
            return {
                "session_id": session_id,
                "vnc_port": vnc_port,
                "vnc_url": f"localhost:{vnc_port}",
                "status": "ready",
                "created_at": session_info["created_at"].isoformat()
            }
            
        except Exception as e:
            logger.error(f"Error creating VNC session: {e}")
            if vnc_port:
                self._free_port(vnc_port)
            return None
    
    async def close_session(self, session_id: str) -> bool:
        """
        Close a VNC session and cleanup resources
        
        Args:
            session_id: Session to close
            
        Returns:
            True if closed successfully
        """
        try:
            if session_id not in self.sessions:
                logger.warning(f"Session {session_id} not found")
                return False
            
            session = self.sessions[session_id]
            logger.info(f"🛑 Closing VNC session {session_id}")
            
            # Stop VNC coordinator
            coordinator = session.get("coordinator")
            if coordinator:
                await coordinator.stop()
            
            # Free port
            vnc_port = session.get("vnc_port")
            if vnc_port:
                self._free_port(vnc_port)
            
            # Remove from sessions
            if session_id in self.sessions:
                del self.sessions[session_id]
            
            # Remove from DB
            try:
                db = SessionLocal()
                db.query(VNCSession).filter(VNCSession.id == session_id).delete()
                db.commit()
                db.close()
                logger.info(f"🗑️ Removed VNC session {session_id} from DB")
            except Exception as e:
                logger.error(f"Failed to remove session from DB: {e}")
            
            logger.info(f"✅ VNC session {session_id} closed")
            return True
            
        except Exception as e:
            logger.error(f"Error closing VNC session: {e}")
            return False
    
    def get_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        """Get session information"""
        return self.sessions.get(session_id)
    
    def get_user_sessions(self, user_id: str) -> list:
        """Get all sessions for a specific user"""
        return [
            {
                "session_id": sid,
                "job_url": info["job_url"],
                "vnc_port": info["vnc_port"],
                "status": info["status"],
                "created_at": info["created_at"].isoformat()
            }
            for sid, info in self.sessions.items()
            if info["user_id"] == user_id
        ]
    
    def get_all_sessions(self) -> list:
        """Get all active sessions"""
        return [
            {
                "session_id": sid,
                "user_id": info["user_id"],
                "job_url": info["job_url"],
                "vnc_port": info["vnc_port"],
                "status": info["status"],
                "created_at": info["created_at"].isoformat()
            }
            for sid, info in self.sessions.items()
        ]
    
    async def cleanup_old_sessions(self, max_age_hours: int = 24):
        """Cleanup sessions older than max_age_hours"""
        try:
            now = datetime.now()
            to_remove = []
            
            for session_id, info in self.sessions.items():
                age = now - info["created_at"]
                if age > timedelta(hours=max_age_hours):
                    to_remove.append(session_id)
            
            for session_id in to_remove:
                logger.info(f"🧹 Cleaning up old session {session_id}")
                await self.close_session(session_id)
            
            if to_remove:
                logger.info(f"✅ Cleaned up {len(to_remove)} old sessions")
                
        except Exception as e:
            logger.error(f"Error during cleanup: {e}")


    async def recover_sessions(self):
        """
        Recover active sessions from database after server restart.
        This is called when the server starts up.
        """
        logger.info("🔄 Checking for VNC sessions to recover...")
        try:
            db = SessionLocal()
            # Find sessions that were 'active' when the server died
            # (and are not too old, e.g. created in last 24h)
            cutoff = datetime.utcnow() - timedelta(hours=24)
            active_sessions = db.query(VNCSession).filter(
                VNCSession.status == "active",
                VNCSession.created_at > cutoff
            ).all()
            
            recovered_count = 0
            
            for db_session in active_sessions:
                session_id = db_session.id
                job_url = db_session.job_url
                user_id = str(db_session.user_id)
                
                logger.info(f"♻️ Recovering session {session_id} for {job_url}")
                
                # Re-create the session (spin up new VNC/Browser)
                # Note: create_session handles port allocation
                new_session = await self.create_session(session_id, job_url, user_id)
                
                if new_session:
                    recovered_count += 1
                    # Note: We don't need to update DB status because create_session sets it to 'active'
                    # effectively "refreshing" the session record
                else:
                    # If recovery failed, mark as failed in DB so we don't try forever
                    db_session.status = "failed_recovery"
                    db.commit()
            
            db.close()
            
            if recovered_count > 0:
                logger.info(f"✅ Successfully recovered {recovered_count} VNC sessions")
            else:
                logger.info("No sessions needed recovery")
                
        except Exception as e:
            logger.error(f"Error recovering sessions: {e}")


# Global VNC session manager instance
vnc_session_manager = VNCSessionManager(base_port=5900, max_sessions=10)

