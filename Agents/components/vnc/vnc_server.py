"""
VNC Server Manager for Browser Streaming

Manages VNC server (x11vnc) for streaming virtual display to users
"""

import os
import logging
import subprocess
import signal
from typing import Optional
import asyncio

logger = logging.getLogger(__name__)


class VNCServer:
    """Manages x11vnc server for display streaming"""
    
    def __init__(self, display: str = ":99", port: int = 5900, password: Optional[str] = None):
        """
        Initialize VNC server
        
        Args:
            display: X display to stream (e.g., ":99")
            port: VNC port (default 5900)
            password: Optional password for VNC connection
        """
        self.display = display
        self.port = port
        self.password = password
        self.vnc_process = None
        self.is_running = False
        
    def start(self) -> bool:
        """
        Start VNC server
        
        Returns:
            True if started successfully
        """
        try:
            # Check if x11vnc is installed
            if not self._is_x11vnc_installed():
                logger.error("x11vnc is not installed. Install with: apt-get install x11vnc")
                return False
            
            logger.info(f"Starting VNC server on port {self.port} for display {self.display}")
            
            # Build x11vnc command
            cmd = [
                'x11vnc',
                '-display', self.display,
                '-rfbport', str(self.port),
                '-shared',  # Allow multiple clients
                '-forever',  # Keep running after client disconnects
                '-noxdamage',  # Better performance
                '-noxfixes',  # Better performance
                '-noxrecord',  # Better performance
                '-quiet',  # Less verbose
                '-cursor', 'arrow', # Fix cursor mismatch
            ]
            
            # Add password if provided
            if self.password:
                # Create password file
                passwd_file = f'/tmp/vnc_passwd_{self.port}'
                with open(passwd_file, 'w') as f:
                    f.write(self.password)
                cmd.extend(['-rfbauth', passwd_file])
            else:
                cmd.append('-nopw')  # No password
            
            # Start x11vnc process
            self.vnc_process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                preexec_fn=os.setsid  # Create new process group for clean shutdown
            )
            
            # Wait a bit for VNC to start
            import time
            time.sleep(2)
            
            # Verify it started
            if self.vnc_process.poll() is None:
                self.is_running = True
                logger.info(f"✅ VNC server started on port {self.port}")
                return True
            else:
                stderr = self.vnc_process.stderr.read().decode() if self.vnc_process.stderr else ""
                logger.error(f"x11vnc failed to start: {stderr}")
                return False
                
        except FileNotFoundError:
            logger.error("x11vnc not found. Install with: apt-get install x11vnc")
            return False
        except Exception as e:
            logger.error(f"Failed to start VNC server: {e}")
            return False
    
    def stop(self):
        """Stop VNC server"""
        try:
            if self.vnc_process:
                logger.info(f"Stopping VNC server on port {self.port}")
                
                # Send SIGTERM to process group
                os.killpg(os.getpgid(self.vnc_process.pid), signal.SIGTERM)
                
                # Wait for graceful shutdown
                try:
                    self.vnc_process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    # Force kill if still running
                    os.killpg(os.getpgid(self.vnc_process.pid), signal.SIGKILL)
                
                self.is_running = False
                logger.info("✅ VNC server stopped")
                
                # Cleanup password file if exists
                if self.password:
                    passwd_file = f'/tmp/vnc_passwd_{self.port}'
                    if os.path.exists(passwd_file):
                        os.remove(passwd_file)
                        
        except Exception as e:
            logger.error(f"Error stopping VNC server: {e}")
    
    def _is_x11vnc_installed(self) -> bool:
        """Check if x11vnc is installed"""
        try:
            result = subprocess.run(['which', 'x11vnc'], capture_output=True)
            return result.returncode == 0
        except Exception:
            return False
    
    def get_connection_url(self) -> str:
        """Get VNC connection URL"""
        return f"vnc://localhost:{self.port}"
    
    def __enter__(self):
        """Context manager enter"""
        self.start()
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit"""
        self.stop()

