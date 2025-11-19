"""
VNC WebSocket Proxy

Converts VNC protocol to WebSocket for browser consumption.
Allows noVNC frontend to connect to VNC servers.
"""

import logging
import asyncio
from flask import request
from flask_socketio import SocketIO, emit, join_room, leave_room
import subprocess
import os
import signal

logger = logging.getLogger(__name__)


class VNCWebSocketProxy:
    """Manages websockify processes for VNC to WebSocket conversion"""
    
    def __init__(self, base_websocket_port: int = 6900):
        """
        Initialize VNC WebSocket proxy manager
        
        Args:
            base_websocket_port: Starting port for WebSocket proxies
        """
        self.base_websocket_port = base_websocket_port
        self.proxies = {}  # session_id -> proxy process
        
    def start_proxy(self, session_id: str, vnc_port: int) -> Optional[int]:
        """
        Start websockify proxy for a VNC session
        
        Args:
            session_id: Session identifier
            vnc_port: VNC server port to proxy
            
        Returns:
            WebSocket port number or None if failed
        """
        try:
            # Allocate WebSocket port (offset from VNC port)
            ws_port = self.base_websocket_port + (vnc_port - 5900)
            
            logger.info(f"Starting websockify: WS port {ws_port} → VNC port {vnc_port}")
            
            # Start websockify process
            # websockify listens on ws_port and forwards to VNC port
            process = subprocess.Popen([
                'websockify',
                '--web', '/usr/share/novnc',  # noVNC web files
                str(ws_port),  # WebSocket port
                f'localhost:{vnc_port}'  # VNC target
            ], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            
            # Wait a bit for proxy to start
            import time
            time.sleep(1)
            
            # Verify it started
            if process.poll() is None:
                self.proxies[session_id] = {
                    'process': process,
                    'vnc_port': vnc_port,
                    'ws_port': ws_port
                }
                logger.info(f"✅ Websockify started for session {session_id}")
                return ws_port
            else:
                logger.error(f"Websockify failed to start for session {session_id}")
                return None
                
        except FileNotFoundError:
            logger.error("websockify not found. Install with: pip install websockify")
            return None
        except Exception as e:
            logger.error(f"Failed to start websockify: {e}")
            return None
    
    def stop_proxy(self, session_id: str):
        """Stop websockify proxy for a session"""
        try:
            if session_id in self.proxies:
                proxy_info = self.proxies[session_id]
                process = proxy_info['process']
                
                logger.info(f"Stopping websockify for session {session_id}")
                
                # Terminate process
                process.terminate()
                
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                
                del self.proxies[session_id]
                logger.info(f"✅ Websockify stopped for session {session_id}")
                
        except Exception as e:
            logger.error(f"Error stopping websockify: {e}")
    
    def get_websocket_url(self, session_id: str) -> Optional[str]:
        """Get WebSocket URL for a session"""
        if session_id in self.proxies:
            ws_port = self.proxies[session_id]['ws_port']
            # Return URL that frontend can connect to
            return f"ws://localhost:{ws_port}"
        return None


# Global proxy manager
vnc_websocket_proxy = VNCWebSocketProxy()

