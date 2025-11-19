"""
Browser VNC Coordinator

Coordinates virtual display, VNC server, and Playwright browser
for cloud-based browser automation with user interaction capability.
"""

import logging
import asyncio
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
        self.vnc_server = None
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
                ]
            )
            
            # Create browser context
            context = await self.browser.new_context(
                viewport={'width': self.display_width, 'height': self.display_height}
            )
            
            # Create page
            self.page = await context.new_page()
            
            logger.info("✅ VNC-enabled browser environment started successfully")
            logger.info(f"📺 Display: {self.virtual_display.display}")
            logger.info(f"🖥️ VNC Port: {self.vnc_port}")
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
    
    def get_status(self) -> dict:
        """Get status of all components"""
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

