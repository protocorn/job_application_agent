"""
VNC Connection Pool Manager

Manages a pool of VNC connections to prevent resource exhaustion
and improve connection reuse.
"""

import logging
import threading
import time
from typing import Dict, Optional, Set
from dataclasses import dataclass
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)


@dataclass
class VNCConnection:
    """Represents a VNC connection"""
    session_id: str
    vnc_port: int
    ws_port: int
    created_at: datetime
    last_accessed: datetime
    active_connections: int = 0
    max_connections: int = 5
    
    def is_available(self) -> bool:
        """Check if connection slot is available"""
        return self.active_connections < self.max_connections
    
    def is_expired(self, max_age_seconds: int = 3600) -> bool:
        """Check if connection has expired (default 1 hour)"""
        return (datetime.now() - self.created_at).total_seconds() > max_age_seconds
    
    def is_idle(self, idle_timeout_seconds: int = 300) -> bool:
        """Check if connection has been idle (default 5 minutes)"""
        return (
            self.active_connections == 0 and
            (datetime.now() - self.last_accessed).total_seconds() > idle_timeout_seconds
        )


class VNCConnectionPool:
    """
    Manages a pool of VNC connections with limits and automatic cleanup
    """
    
    def __init__(
        self,
        max_total_connections: int = 20,
        max_connections_per_session: int = 5,
        connection_timeout: int = 3600,  # 1 hour
        idle_timeout: int = 300,  # 5 minutes
        cleanup_interval: int = 60  # 1 minute
    ):
        self.max_total_connections = max_total_connections
        self.max_connections_per_session = max_connections_per_session
        self.connection_timeout = connection_timeout
        self.idle_timeout = idle_timeout
        self.cleanup_interval = cleanup_interval
        
        self.connections: Dict[str, VNCConnection] = {}
        self._lock = threading.RLock()
        
        # Port allocation tracking
        self.allocated_ports: Set[int] = set()
        self.next_vnc_port = 5900
        self.next_ws_port = 6900
        
        # Start cleanup thread
        self._cleanup_thread = threading.Thread(
            target=self._cleanup_loop,
            daemon=True,
            name="VNCPoolCleanup"
        )
        self._cleanup_thread.start()
        self._shutdown = threading.Event()
        
        logger.info(f"✅ VNC Connection Pool initialized (max: {max_total_connections})")
    
    def acquire_connection(self, session_id: str) -> Optional[VNCConnection]:
        """
        Acquire a connection slot for a session
        
        Args:
            session_id: VNC session ID
            
        Returns:
            VNCConnection if available, None if limit reached
        """
        with self._lock:
            # Check if session already exists
            if session_id in self.connections:
                conn = self.connections[session_id]
                
                if not conn.is_available():
                    logger.warning(
                        f"⚠️ Session {session_id} at max connections "
                        f"({conn.active_connections}/{conn.max_connections})"
                    )
                    return None
                
                conn.active_connections += 1
                conn.last_accessed = datetime.now()
                logger.debug(f"📊 Session {session_id}: {conn.active_connections} active connections")
                return conn
            
            # Check total connection limit
            total_connections = sum(c.active_connections for c in self.connections.values())
            if total_connections >= self.max_total_connections:
                logger.warning(
                    f"⚠️ Connection pool at capacity "
                    f"({total_connections}/{self.max_total_connections})"
                )
                return None
            
            # Create new connection
            vnc_port, ws_port = self._allocate_ports()
            if vnc_port is None or ws_port is None:
                logger.error("❌ Failed to allocate ports for new connection")
                return None
            
            conn = VNCConnection(
                session_id=session_id,
                vnc_port=vnc_port,
                ws_port=ws_port,
                created_at=datetime.now(),
                last_accessed=datetime.now(),
                active_connections=1,
                max_connections=self.max_connections_per_session
            )
            
            self.connections[session_id] = conn
            logger.info(
                f"✅ Created connection for session {session_id} "
                f"(VNC: {vnc_port}, WS: {ws_port})"
            )
            return conn
    
    def release_connection(self, session_id: str):
        """Release a connection slot"""
        with self._lock:
            if session_id in self.connections:
                conn = self.connections[session_id]
                conn.active_connections = max(0, conn.active_connections - 1)
                conn.last_accessed = datetime.now()
                logger.debug(
                    f"📊 Released connection for {session_id} "
                    f"({conn.active_connections} remaining)"
                )
    
    def remove_session(self, session_id: str) -> bool:
        """
        Forcefully remove a session and free its resources
        
        Args:
            session_id: Session to remove
            
        Returns:
            True if removed, False if not found
        """
        with self._lock:
            if session_id in self.connections:
                conn = self.connections[session_id]
                
                # Free ports
                self.allocated_ports.discard(conn.vnc_port)
                self.allocated_ports.discard(conn.ws_port)
                
                # Remove connection
                del self.connections[session_id]
                logger.info(f"🗑️ Removed session {session_id} from pool")
                return True
            
            return False
    
    def get_session_info(self, session_id: str) -> Optional[Dict]:
        """Get information about a session"""
        with self._lock:
            if session_id in self.connections:
                conn = self.connections[session_id]
                return {
                    'session_id': conn.session_id,
                    'vnc_port': conn.vnc_port,
                    'ws_port': conn.ws_port,
                    'active_connections': conn.active_connections,
                    'max_connections': conn.max_connections,
                    'created_at': conn.created_at.isoformat(),
                    'last_accessed': conn.last_accessed.isoformat(),
                    'is_available': conn.is_available()
                }
            return None
    
    def get_stats(self) -> Dict:
        """Get pool statistics"""
        with self._lock:
            total_active = sum(c.active_connections for c in self.connections.values())
            
            return {
                'total_sessions': len(self.connections),
                'total_active_connections': total_active,
                'max_total_connections': self.max_total_connections,
                'available_capacity': self.max_total_connections - total_active,
                'allocated_ports': len(self.allocated_ports),
                'sessions': [
                    {
                        'session_id': conn.session_id,
                        'active_connections': conn.active_connections,
                        'vnc_port': conn.vnc_port
                    }
                    for conn in self.connections.values()
                ]
            }
    
    def _allocate_ports(self) -> tuple[Optional[int], Optional[int]]:
        """Allocate VNC and WebSocket ports"""
        # Find available VNC port
        vnc_port = None
        for _ in range(100):  # Try up to 100 ports
            candidate = self.next_vnc_port
            self.next_vnc_port += 1
            if self.next_vnc_port > 5999:  # Reset range
                self.next_vnc_port = 5900
            
            if candidate not in self.allocated_ports:
                vnc_port = candidate
                self.allocated_ports.add(vnc_port)
                break
        
        # Find available WebSocket port
        ws_port = None
        for _ in range(100):
            candidate = self.next_ws_port
            self.next_ws_port += 1
            if self.next_ws_port > 6999:  # Reset range
                self.next_ws_port = 6900
            
            if candidate not in self.allocated_ports:
                ws_port = candidate
                self.allocated_ports.add(ws_port)
                break
        
        return vnc_port, ws_port
    
    def _cleanup_loop(self):
        """Background cleanup thread"""
        logger.info("🧹 VNC Connection Pool cleanup thread started")
        
        while not self._shutdown.is_set():
            try:
                time.sleep(self.cleanup_interval)
                self._cleanup_expired_connections()
            except Exception as e:
                logger.error(f"Error in cleanup loop: {e}")
    
    def _cleanup_expired_connections(self):
        """Remove expired and idle connections"""
        with self._lock:
            to_remove = []
            
            for session_id, conn in self.connections.items():
                # Remove expired connections
                if conn.is_expired(self.connection_timeout):
                    logger.info(f"🧹 Removing expired session {session_id}")
                    to_remove.append(session_id)
                    continue
                
                # Remove idle connections
                if conn.is_idle(self.idle_timeout):
                    logger.info(f"🧹 Removing idle session {session_id}")
                    to_remove.append(session_id)
                    continue
            
            # Remove sessions
            for session_id in to_remove:
                self.remove_session(session_id)
            
            if to_remove:
                logger.info(f"✅ Cleaned up {len(to_remove)} expired/idle sessions")
    
    def shutdown(self):
        """Shutdown the connection pool"""
        logger.info("🛑 Shutting down VNC Connection Pool...")
        self._shutdown.set()
        
        # Wait for cleanup thread
        if self._cleanup_thread.is_alive():
            self._cleanup_thread.join(timeout=5)
        
        # Clear all connections
        with self._lock:
            session_count = len(self.connections)
            self.connections.clear()
            self.allocated_ports.clear()
        
        logger.info(f"✅ VNC Connection Pool shutdown (cleared {session_count} sessions)")


# Global connection pool instance
_global_connection_pool: Optional[VNCConnectionPool] = None
_pool_lock = threading.Lock()


def get_connection_pool() -> VNCConnectionPool:
    """Get or create the global connection pool"""
    global _global_connection_pool
    
    with _pool_lock:
        if _global_connection_pool is None:
            _global_connection_pool = VNCConnectionPool(
                max_total_connections=20,
                max_connections_per_session=5,
                connection_timeout=3600,  # 1 hour
                idle_timeout=300  # 5 minutes
            )
        
        return _global_connection_pool


def shutdown_connection_pool():
    """Shutdown the global connection pool"""
    global _global_connection_pool
    
    with _pool_lock:
        if _global_connection_pool is not None:
            _global_connection_pool.shutdown()
            _global_connection_pool = None
