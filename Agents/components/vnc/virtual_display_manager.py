"""
Virtual Display Manager for Cloud Browser Automation

Manages virtual displays (Xvfb) for running browsers in headless cloud environments
with visible browser windows that can be streamed to users.
"""

import os
import logging
import subprocess
from typing import Optional
from contextlib import contextmanager

logger = logging.getLogger(__name__)


class VirtualDisplayManager:
    """Manages virtual X display for cloud browser automation"""
    
    def __init__(self, width: int = 1920, height: int = 1080, display_num: int = 99):
        """
        Initialize virtual display manager
        
        Args:
            width: Display width in pixels
            height: Display height in pixels
            display_num: X display number (e.g., :99)
        """
        self.width = width
        self.height = height
        self.display_num = display_num
        self.display = f":{display_num}"
        self.xvfb_process = None
        self.is_running = False
        
    def start(self) -> bool:
        """
        Start the virtual display
        
        Returns:
            True if started successfully
        """
        try:
            # Check if Xvfb is installed
            if not self._is_xvfb_installed():
                logger.error("Xvfb is not installed. Install with: apt-get install xvfb")
                return False
            
            # Check if display is already running
            if self._is_display_running():
                # If we are reusing a display, we must be careful about session isolation
                # But typically each session gets its own Xvfb via unique display_num
                logger.warning(f"Display {self.display} is already running")
                return True
            
            logger.info(f"Starting virtual display {self.display} ({self.width}x{self.height})")
            
            # Start Xvfb with auth file specifically for this display
            # This isolates the X server access to this process/user
            auth_file = os.path.expanduser(f'~/.Xauthority-{self.display_num}')
            
            self.xvfb_process = subprocess.Popen([
                'Xvfb',
                self.display,
                '-screen', '0', f'{self.width}x{self.height}x16', # 16-bit color for better performance
                '-auth', auth_file, # Secure X authority
                '-ac',  # Disable access control (controlled via auth)
                '+extension', 'GLX',  # Enable OpenGL
                '+render',  # Enable render extension
                '-noreset'  # Don't reset after last client exits
            ], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            
            # Wait a bit for Xvfb to start
            import time
            time.sleep(1)
            
            # Verify it started
            if self.xvfb_process.poll() is None:
                self.is_running = True
                os.environ['DISPLAY'] = self.display
                logger.info(f"✅ Virtual display {self.display} started successfully")
                return True
            else:
                logger.error("Xvfb failed to start")
                return False
                
        except FileNotFoundError:
            logger.error("Xvfb not found. Install with: apt-get install xvfb")
            return False
        except Exception as e:
            logger.error(f"Failed to start virtual display: {e}")
            return False
    
    def stop(self):
        """Stop the virtual display"""
        try:
            if self.xvfb_process:
                logger.info(f"Stopping virtual display {self.display}")
                self.xvfb_process.terminate()
                self.xvfb_process.wait(timeout=5)
                self.is_running = False
                logger.info("✅ Virtual display stopped")
        except Exception as e:
            logger.error(f"Error stopping virtual display: {e}")
            if self.xvfb_process:
                self.xvfb_process.kill()
    
    def _is_xvfb_installed(self) -> bool:
        """Check if Xvfb is installed"""
        try:
            result = subprocess.run(['which', 'Xvfb'], capture_output=True)
            return result.returncode == 0
        except Exception:
            return False
    
    def _is_display_running(self) -> bool:
        """Check if display is already running"""
        try:
            result = subprocess.run(
                ['xdpyinfo', '-display', self.display],
                capture_output=True,
                timeout=2
            )
            return result.returncode == 0
        except Exception:
            return False
    
    @contextmanager
    def context(self):
        """Context manager for virtual display"""
        self.start()
        try:
            yield self
        finally:
            self.stop()
    
    def get_display_env(self) -> dict:
        """Get environment variables for this display"""
        return {
            'DISPLAY': self.display,
            'XAUTHORITY': os.path.expanduser('~/.Xauthority')
        }

