"""
Browser VNC Coordinator

Coordinates virtual display, VNC server, and Playwright browser
for cloud-based browser automation with user interaction capability.
"""

import logging
import asyncio
import subprocess
import time
import sys
from typing import Optional
from playwright.async_api import Browser, Page, async_playwright

from .virtual_display_manager import VirtualDisplayManager
from .vnc_server import VNCServer

logger = logging.getLogger(__name__)


class BrowserVNCCoordinator:
    """Coordinates VNC streaming for browser automation"""
    
    def __init__(self, 
                 display_width: int = 1920,
                 display_height: int = 1080,
                 vnc_port: int = 5900,
                 vnc_password: Optional[str] = None):
        """
        Initialize coordinator
        
        Args:
            display_width: Virtual display width
            display_height: Virtual display height
            vnc_port: VNC server port
            vnc_password: Optional VNC password for security
        """
        self.display_width = display_width
        self.display_height = display_height
        self.vnc_port = vnc_port
        self.vnc_password = vnc_password
        
        self.virtual_display = None
        self.window_manager_process = None # Track Window Manager
        self.vnc_server = None
        self.websockify_process = None
        self.ws_port = 6900 + (vnc_port - 5900)  # Calculate websockify port
        self.playwright = None
        self.browser = None
        self.page = None
        
    async def start(self) -> bool:
        """
        Start all components (display, VNC, browser)
        
        Returns:
            True if all components started successfully
        """
        try:
            logger.info("🚀 Starting VNC-enabled browser environment")
            
            # Step 1: Start virtual display
            logger.info("📺 Starting virtual display...")
            self.virtual_display = VirtualDisplayManager(
                width=self.display_width,
                height=self.display_height
            )
            
            if not self.virtual_display.start():
                logger.error("Failed to start virtual display")
                return False
            
            # Step 1.5: Start Window Manager (Optional)
            # Fluxbox helps with popups and file pickers, but we can try without it
            # if simplicity is preferred. 
            
            # SKIPPING fluxbox for now based on request.
            # If popups fail to render later, we can re-enable it.
            # logger.info("🪟 Starting Window Manager (fluxbox)...")
            # ... (code commented out)
            
            self.window_manager_process = None

            # Step 2: Start VNC server
            logger.info("🖥️ Starting VNC server...")
            self.vnc_server = VNCServer(
                display=self.virtual_display.display,
                port=self.vnc_port,
                password=self.vnc_password
            )
            
            if not self.vnc_server.start():
                logger.error("Failed to start VNC server")
                self.virtual_display.stop()
                return False
            
            # Step 2.5: Start websockify proxy (WebSocket → VNC)
            logger.info(f"🔌 Starting websockify proxy WS:{self.ws_port} → VNC:{self.vnc_port}...")
            try:
                # Start websockify as a subprocess using python -m to ensure it uses the same environment
                cmd = [
                    sys.executable, '-m', 'websockify',
                    '--verbose',
                    str(self.ws_port),
                    f'localhost:{self.vnc_port}'
                ]
                
                self.websockify_process = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True
                )
                
                # Give it a moment to start and bind to port
                time.sleep(1.5)
                
                # Check if it's still running
                if self.websockify_process.poll() is not None:
                    # Process exited - capture output
                    _, stderr = self.websockify_process.communicate()
                    returncode = self.websockify_process.returncode
                    
                    logger.error(f"❌ Websockify failed to start (exit code: {returncode})")
                    logger.error(f"   Error output: {stderr}")
                    
                    logger.warning("Continuing without websockify (Flask-Sock will handle WebSocket directly)")
                    self.websockify_process = None
                else:
                    logger.info(f"✅ Websockify proxy started on port {self.ws_port} (PID: {self.websockify_process.pid})")
                    
                    # Read startup line to confirm it's listening (optional, non-blocking check)
                    # We won't block here, just assume it's good if it didn't exit
                    
            except FileNotFoundError:
                logger.warning("websockify command not found - continuing without it")
                logger.info("   Flask-Sock will attempt direct WebSocket → VNC proxying")
                self.websockify_process = None
            except Exception as e:
                logger.warning(f"Could not start websockify: {e}")
                self.websockify_process = None
            
            # Step 3: Start Playwright with visible browser on virtual display
            logger.info("🌐 Starting Playwright browser on virtual display...")
            self.playwright = await async_playwright().start()
            
            # Launch browser on the virtual display (headless=False!)
            self.browser = await self.playwright.chromium.launch(
                headless=False,  # Visible browser on virtual display!
                args=[
                    '--disable-dev-shm-usage',  # Overcome limited resource problems
                    '--no-sandbox',  # Required for Docker/cloud
                    '--disable-setuid-sandbox',
                    '--disable-gpu',  # Not needed for virtual display
                    '--start-maximized', # Start maximized for better VNC experience
                    '--window-position=0,0',
                ]
            )
            
            # Create browser context with maximized viewport
            # Use no_viewport=True to respect --start-maximized
            context = await self.browser.new_context(
                viewport=None, # Let window manager handle size via start-maximized
                no_viewport=True
            )
            
            # Create page
            self.page = await context.new_page()
            
            logger.info("✅ VNC-enabled browser environment started successfully")
            logger.info(f"📺 Display: {self.virtual_display.display}")
            logger.info(f"🖥️ VNC Port: {self.vnc_port}")
            logger.info(f"🔌 WebSocket Port: {self.ws_port}")
            logger.info(f"🌐 Browser: Ready")
            
            return True
            
        except Exception as e:
            logger.error(f"Failed to start VNC environment: {e}")
            await self.stop()
            return False
    
    async def stop(self):
        """Stop all components"""
        try:
            logger.info("🛑 Stopping VNC-enabled browser environment")
            
            # Stop browser
            if self.browser:
                try:
                    await self.browser.close()
                    logger.info("✓ Browser closed")
                except Exception as e:
                    logger.debug(f"Error closing browser: {e}")
            
            # Stop Playwright
            if self.playwright:
                try:
                    await self.playwright.stop()
                    logger.info("✓ Playwright stopped")
                except Exception as e:
                    logger.debug(f"Error stopping Playwright: {e}")
            
            # Stop websockify
            if self.websockify_process:
                try:
                    self.websockify_process.terminate()
                    self.websockify_process.wait(timeout=5)
                    logger.info("✓ Websockify stopped")
                except Exception as e:
                    logger.debug(f"Error stopping websockify: {e}")
                    try:
                        self.websockify_process.kill()
                    except:
                        pass
            
            # Stop Window Manager
            if self.window_manager_process:
                try:
                    import os
                    import signal
                    os.killpg(os.getpgid(self.window_manager_process.pid), signal.SIGTERM)
                    logger.info("✓ Window Manager stopped")
                except Exception as e:
                    logger.debug(f"Error stopping Window Manager: {e}")

            # Stop VNC server
            if self.vnc_server:
                self.vnc_server.stop()
            
            # Stop virtual display
            if self.virtual_display:
                self.virtual_display.stop()
            
            logger.info("✅ VNC environment stopped")
            
        except Exception as e:
            logger.error(f"Error stopping VNC environment: {e}")
    
    def get_page(self) -> Optional[Page]:
        """Get the Playwright page for automation"""
        return self.page
    
    def get_browser(self) -> Optional[Browser]:
        """Get the Playwright browser"""
        return self.browser
    
    def get_vnc_url(self) -> str:
        """Get VNC connection URL"""
        if self.vnc_server:
            return f"vnc://localhost:{self.vnc_port}"
        return None
    
    async def inject_file(self, file_path: str, target_path: str) -> bool:
        """
        Inject a local file into the remote VNC environment
        
        Args:
            file_path: Local path to the file (on the server's disk)
            target_path: Target path inside the VNC environment (e.g., /tmp/resume.pdf)
        
        Returns:
            True if successful
        """
        try:
            import shutil
            
            # Since we are running on the same machine (in Docker/Server),
            # we just copy the file to the location expected by the browser.
            # The "VNC environment" shares the same filesystem as the backend code.
            
            shutil.copy2(file_path, target_path)
            logger.info(f"✅ Injected file to {target_path}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to inject file: {e}")
            return False

    def get_status(self) -> dict:
        """Get status of all VNC components"""
        return {
            "virtual_display": {
                "running": self.virtual_display.is_running if self.virtual_display else False,
                "display": self.virtual_display.display if self.virtual_display else None,
                "resolution": f"{self.display_width}x{self.display_height}"
            },
            "vnc_server": {
                "running": self.vnc_server.is_running if self.vnc_server else False,
                "port": self.vnc_port,
                "url": self.get_vnc_url()
            },
            "websockify": {
                "running": self.websockify_process is not None and self.websockify_process.poll() is None,
                "ws_port": self.ws_port
            },
            "browser": {
                "running": self.browser is not None,
                "page_url": self.page.url if self.page else None
            }
        }


class BrowserVNCSession:
    """Manages a single browser VNC session for a job application"""
    
    def __init__(self, session_id: str, job_url: str, vnc_port: int = 5900):
        self.session_id = session_id
        self.job_url = job_url
        self.vnc_port = vnc_port
        self.coordinator = BrowserVNCCoordinator(vnc_port=vnc_port)
        
    async def __aenter__(self):
        """Async context manager enter"""
        success = await self.coordinator.start()
        if not success:
            raise RuntimeError("Failed to start VNC session")
        return self.coordinator
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit"""
        await self.coordinator.stop()

