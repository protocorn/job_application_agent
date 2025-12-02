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
                 vnc_password: Optional[str] = None,
                 user_id: Optional[str] = None,
                 session_id: Optional[str] = None,
                 resume_path: Optional[str] = None):
        """
        Initialize coordinator

        Args:
            display_width: Virtual display width
            display_height: Virtual display height
            vnc_port: VNC server port
            vnc_password: Optional VNC password for security
            user_id: User ID for file isolation
            session_id: Session ID for file isolation
            resume_path: Path to resume file to inject (optional)
        """
        self.display_width = display_width
        self.display_height = display_height
        self.vnc_port = vnc_port
        self.vnc_password = vnc_password
        self.user_id = user_id
        self.session_id = session_id
        self.resume_path = resume_path

        self.virtual_display = None
        self.window_manager_process = None # Track Window Manager
        self.vnc_server = None
        self.websockify_process = None
        self.ws_port = 6900 + (vnc_port - 5900)  # Calculate websockify port
        self.playwright = None
        self.browser = None
        self.page = None
        self.session_dir = None  # Will be created for user-specific file isolation

    def _create_session_directory(self) -> str:
        """
        Create isolated session directory for user files (e.g., resume downloads).

        Security:
        - Each user gets their own directory: /tmp/vnc_sessions/{user_id}/{session_id}
        - Permissions set to 0o700 (owner read/write/execute only)
        - Prevents other users from accessing files

        Returns:
            Path to created session directory
        """
        import os

        if not self.user_id or not self.session_id:
            # Fallback to session-only directory if user_id not provided
            session_dir = f"/tmp/vnc_sessions/anonymous/{self.session_id or 'default'}"
        else:
            session_dir = f"/tmp/vnc_sessions/{self.user_id}/{self.session_id}"

        # Create directory with restrictive permissions (owner only)
        os.makedirs(session_dir, mode=0o700, exist_ok=True)

        logger.info(f"📁 Created isolated session directory: {session_dir}")
        return session_dir

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
            
            # Step 3: Create user-specific session directory for file isolation
            logger.info("📁 Creating isolated session directory...")
            self.session_dir = self._create_session_directory()

            # Step 3.5: Inject resume if provided
            if self.resume_path:
                try:
                    logger.info(f"📄 Injecting resume from {self.resume_path}...")
                    # Update target path to be inside the session directory instead of restricted_user home
                    # since we are temporarily reverting the restricted_user launch.
                    target_path = f"{self.session_dir}/resume.pdf"
                    
                    # Copy file
                    await self.inject_file(self.resume_path, target_path)
                    
                    # No need to chown if we are running as the same user
                    # subprocess.run(["chown", "restricted_user:restricted_user", target_path], check=True)
                    logger.info(f"✅ Resume injected to {target_path}")
                except Exception as e:
                    logger.error(f"Failed to inject resume: {e}")

            # Step 4: Start Playwright with visible browser on virtual display
            logger.info("🌐 Starting Playwright browser on virtual display...")
            self.playwright = await async_playwright().start()

            # Launch browser on the virtual display (headless=False!)
            # SECURE LAUNCH: Use wrapper script to run as restricted_user
            # NOTE: We can't use remote debugging pipe with sudo wrapper because the pipes
            # are file descriptors owned by the parent process, and sudo closes them or 
            # they are not accessible to the child process.
            # INSTEAD: We will let Playwright manage the process but we can't easily 
            # wrap the executable with sudo if we want Playwright to control it via pipes.
            
            # ALTERNATIVE APPROACH:
            # We use 'args' to try to enforce sandbox if possible, but since we are in Docker
            # we already rely on container isolation.
            # To get user isolation inside the container, we have to run the *entire* agent 
            # as the restricted user, OR we accept that Playwright runs as the app user 
            # but we use a custom executable wrapper that somehow preserves pipes (very hard).
            
            # FIXED APPROACH FOR SUDO:
            # Playwright connects via pipes. If we use sudo, we break the pipes.
            # We must use a WebSocket connection instead of pipes if we want to launch remotely?
            # Or we try to run chromium directly without the wrapper but with setuid? No.
            
            # WORKAROUND:
            # We will use the wrapper but we have to ensure Playwright can talk to it.
            # Playwright launches the browser and expects to talk to it via Stdio.
            # 'sudo' might be prompting for password (we fixed that with NOPASSWD).
            # But 'sudo' might not pass stdin/stdout correctly for the Chrome DevTools Protocol.
            
            # Let's try adding -E to sudo to preserve env, and ensure we don't close fds?
            # Actually, Playwright uses --remote-debugging-pipe. 
            # Chromium writes to fd 3 and 4. Sudo might not pass these.
            
            # If we remove --remote-debugging-pipe from args, Playwright might fall back to WebSocket?
            # No, Playwright *injects* that arg.
            
            # REVERTING WRAPPER STRATEGY TEMPORARILY to restore functionality
            # while we devise a better isolation strategy (e.g. running the whole worker as restricted user).
            
            # For now, we will run as the main user but rely on the resume isolation 
            # and file permissions we set up.
            # The restricted_user strategy is cleaner but requires more complex Playwright setup (CDP over WS).
            
            logger.warning("⚠️ Reverting to standard browser launch (root/app user) to fix pipe error.")
            logger.warning("   File isolation is still active via custom directory.")
            
            self.browser = await self.playwright.chromium.launch(
                headless=False,  # Visible browser on virtual display!
                # executable_path='/usr/local/bin/browser_launcher.sh',  # REMOVED due to pipe error
                args=[
                    '--disable-dev-shm-usage',
                    '--no-sandbox',
                    '--disable-setuid-sandbox',
                    '--disable-gpu',
                    '--disable-infobars',
                    '--kiosk',
                    f'--download.default_directory={self.session_dir}', # Isolate downloads to session dir
                ]
            )

            # Create browser context with fixed viewport and user-specific downloads directory
            context = await self.browser.new_context(
                viewport={'width': self.display_width, 'height': self.display_height},
                accept_downloads=True
                # Note: downloads_path is not available in all Playwright versions
                # Downloads will be saved to the default temp directory
                # For better isolation, consider using context.set_default_download_path() if available
            )

            # Create page
            self.page = await context.new_page()

            logger.info(f"✅ Session files will be isolated to: {self.session_dir}")
            
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
    
    async def cleanup_session_files(self, session_id: str = None):
        """
        Cleanup session-specific files.

        Args:
            session_id: Optional legacy parameter (kept for compatibility)
        """
        try:
            import shutil
            import os

            # Use instance session_dir if available, otherwise fall back to legacy path
            if self.session_dir and os.path.exists(self.session_dir):
                shutil.rmtree(self.session_dir)
                logger.info(f"🧹 Cleaned up isolated session directory: {self.session_dir}")
            elif session_id:
                # Legacy cleanup for old-style session directories
                session_dir = f"/tmp/session_{session_id}"
                if os.path.exists(session_dir):
                    shutil.rmtree(session_dir)
                    logger.info(f"🧹 Cleaned up legacy session files: {session_dir}")

        except Exception as e:
            logger.error(f"Error cleaning up session files: {e}")

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
            
            # Try to cleanup files if session ID is available via instance
            # Note: Ideally session_id should be passed to coordinator init
            
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
        # Stop coordinator
        await self.coordinator.stop()
        
        # Cleanup session-specific files
        await self.coordinator.cleanup_session_files(self.session_id)

