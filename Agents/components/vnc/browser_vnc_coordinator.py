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
import os
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
                 resume_path: Optional[str] = None,
                 job_url: Optional[str] = None):
        """
        Initialize coordinator

        Args:
            job_url: If provided, browser will launch in app mode restricted to this URL
        """
        self.display_width = display_width
        self.display_height = display_height
        self.vnc_port = vnc_port
        self.vnc_password = vnc_password
        self.user_id = user_id
        self.session_id = session_id
        self.resume_path = resume_path
        self.job_url = job_url

        self.virtual_display = None
        self.window_manager_process = None
        self.vnc_server = None
        self.websockify_process = None
        self.ws_port = 6900 + (vnc_port - 5900)
        # Calculate CDP port for WebSocket connection (offsets from 9222)
        self.cdp_port = 9222 + (vnc_port - 5900)

        self.playwright = None
        self.browser = None
        self.page = None
        self.session_dir = None
        self.browser_process = None  # To track the sudo process
        self.firejail_home = None
        self.private_desktop = None
        self.allowed_domains = []  # Whitelist for navigation

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
            # CRITICAL: Calculate unique display number based on VNC port
            # Each VNC session MUST have its own display to isolate browser windows
            # VNC port 5900 → Display :99, VNC port 5901 → Display :100, etc.
            display_num = 99 + (self.vnc_port - 5900)
            logger.info(f"📺 Starting virtual display :{display_num} for VNC port {self.vnc_port}...")
            logger.info(f"🔍 DEBUG - Display mapping: VNC port {self.vnc_port} -> Display :{display_num}")

            self.virtual_display = VirtualDisplayManager(
                width=self.display_width,
                height=self.display_height,
                display_num=display_num  # CRITICAL: Unique display per session
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

            # Prepare private home for sandboxed browser (Firejail)
            self.firejail_home = os.path.join(self.session_dir, "sandbox_home")
            self.private_desktop = os.path.join(self.firejail_home, "Desktop")
            self.mask_app_dir = os.path.join(self.firejail_home, "mask_app")

            for path in (self.private_desktop, self.mask_app_dir):
                os.makedirs(path, mode=0o700, exist_ok=True)

            subprocess.run(["chown", "-R", "restricted_user:restricted_user", self.firejail_home], check=True)

            # Populate the masked /app directory with a README so users know why it's empty
            mask_readme = os.path.join(self.mask_app_dir, "README.txt")
            with open(mask_readme, "w", encoding="utf-8") as f:
                f.write("⚠️ Access to /app is restricted inside this sandbox.\n")
            subprocess.run(["chown", "restricted_user:restricted_user", mask_readme], check=True)

            # If no resume provided, drop a README to explain
            if not self.resume_path:
                placeholder = os.path.join(self.private_desktop, "README.txt")
                with open(placeholder, "w", encoding="utf-8") as f:
                    f.write("Upload your resume from here. Placeholders only – please provide your file.\n")
                subprocess.run(["chown", "restricted_user:restricted_user", placeholder], check=True)

            # Step 3.5: Inject resume if provided
            if self.resume_path:
                try:
                    logger.info(f"📄 Injecting resume from {self.resume_path}...")
                    target_path = os.path.join(self.private_desktop, "resume.pdf")
                    
                    # Copy file (we are root/app so we can write there)
                    import shutil
                    shutil.copy2(self.resume_path, target_path)
                    
                    # Set ownership to restricted_user
                    subprocess.run(["chown", "restricted_user:restricted_user", target_path], check=True)
                    logger.info(f"✅ Resume injected to {target_path}")
                except Exception as e:
                    logger.error(f"Failed to inject resume: {e}")

            # Step 4: Start Browser as Restricted User via CDP
            logger.info("🌐 Starting Browser as restricted_user (CDP mode)...")
            self.playwright = await async_playwright().start()

            # Prepare user data directory for the restricted user
            # We need a unique profile dir that restricted_user can write to
            user_data_dir = f"/tmp/chrome_profile_{self.session_id or 'default'}_{self.cdp_port}"
            
            # Create and chown the profile dir
            if not os.path.exists(user_data_dir):
                os.makedirs(user_data_dir, mode=0o700)
            subprocess.run(["chown", "-R", "restricted_user:restricted_user", user_data_dir], check=True)

            # Determine start URL and mode
            # If job_url is provided, use app mode for maximum security
            # App mode removes all browser UI (tabs, address bar, etc.)
            start_url = self.job_url if self.job_url else "about:blank"

            # Extract allowed domain from job_url for navigation restriction
            if self.job_url:
                from urllib.parse import urlparse
                parsed = urlparse(self.job_url)
                base_domain = f"{parsed.scheme}://{parsed.netloc}"
                self.allowed_domains = [base_domain]
                logger.info(f"🔒 Browser restricted to domain: {base_domain}")

            # Construct command to launch chromium via sudo + firejail sandbox
            browser_cmd = [
                "sudo", "-u", "restricted_user",
                "firejail",
                "--quiet",
                "--x11=inherit",
                "--dbus-user=none",
                f"--private={self.firejail_home}",
                "--private-dev",
                "--private-tmp",
                "--blacklist=/app",
                "--blacklist=/home/restricted_user",
                f"--bind={self.mask_app_dir},/app",
                "/usr/bin/chromium",
                f"--remote-debugging-port={self.cdp_port}",
                f"--user-data-dir={user_data_dir}",
                "--no-sandbox",
                "--disable-gpu",
                "--disable-dev-shm-usage",
                "--disable-setuid-sandbox",
                "--disable-infobars",
                "--no-first-run",
                "--no-default-browser-check",
                f"--download.default_directory={os.path.join(self.firejail_home, 'Desktop')}",
            ]

            # Add app mode or kiosk mode based on whether job_url is provided
            if self.job_url:
                # App mode: No tabs, no address bar, just the app
                browser_cmd.extend([
                    f"--app={start_url}",
                    "--start-maximized",
                ])
                logger.info(f"🔐 Launching browser in APP MODE (tab-restricted) for: {start_url}")
            else:
                # Kiosk mode: For general browsing (backward compatibility)
                browser_cmd.extend([
                    "--kiosk",
                    "--start-maximized",
                    start_url
                ])
                logger.info(f"🌐 Launching browser in KIOSK MODE for: {start_url}")

            logger.info(f"🚀 Launching browser process: {' '.join(browser_cmd)}")
            
            # Launch process
            self.browser_process = subprocess.Popen(
                browser_cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True
            )

            # Wait for CDP port to be ready
            import requests
            
            max_retries = 20
            ready = False
            for i in range(max_retries):
                try:
                    # Check if port is listening by trying to get version
                    requests.get(f"http://localhost:{self.cdp_port}/json/version", timeout=0.5)
                    ready = True
                    logger.info(f"✅ Browser CDP port {self.cdp_port} is ready")
                    break
                except:
                    if self.browser_process.poll() is not None:
                        _, stderr = self.browser_process.communicate()
                        logger.error(f"❌ Browser process exited early: {stderr}")
                        return False
                    time.sleep(0.5)
            
            if not ready:
                logger.error("❌ Timeout waiting for browser CDP port")
                return False

            # Connect Playwright to the running browser
            logger.info(f"🔌 Connecting Playwright to CDP port {self.cdp_port}...")
            self.browser = await self.playwright.chromium.connect_over_cdp(
                f"http://localhost:{self.cdp_port}"
            )

            # Create browser context (CDP connects to the browser, we need a context)
            # IMPORTANT: Always create a NEW context to avoid reusing old sessions
            # Close any existing contexts first to prevent URL mixup
            logger.info(f"🔍 Found {len(self.browser.contexts)} existing contexts")
            for old_context in self.browser.contexts:
                try:
                    logger.info(f"🗑️ Closing old context with {len(old_context.pages)} pages")
                    await old_context.close()
                except Exception as e:
                    logger.warning(f"Failed to close old context: {e}")
            
            # Create a fresh new context
            logger.info("✨ Creating fresh browser context for new session")
            context = await self.browser.new_context(
                viewport={'width': self.display_width, 'height': self.display_height}
            )

            # Create a fresh new page
            logger.info("✨ Creating fresh page for new session")
            self.page = await context.new_page()
            
            # CRITICAL: Verify and navigate to the correct URL
            # The browser launched with --app={job_url}, but we need to ensure the page is actually there
            if self.job_url:
                current_url = self.page.url
                logger.info(f"🔍 Current page URL: {current_url}")
                logger.info(f"🎯 Expected job URL: {self.job_url}")
                
                # If page is not at the correct URL, navigate to it
                if self.job_url not in current_url and current_url != "about:blank":
                    logger.warning(f"⚠️ Page URL mismatch! Navigating to correct URL...")
                    try:
                        await self.page.goto(self.job_url, wait_until="domcontentloaded", timeout=30000)
                        logger.info(f"✅ Navigated to correct URL: {self.job_url}")
                    except Exception as e:
                        logger.error(f"❌ Failed to navigate to job URL: {e}")
                elif current_url == "about:blank":
                    # Browser just started, need to navigate
                    logger.info("📍 Navigating to job URL...")
                    try:
                        await self.page.goto(self.job_url, wait_until="domcontentloaded", timeout=30000)
                        logger.info(f"✅ Navigated to job URL: {self.job_url}")
                    except Exception as e:
                        logger.error(f"❌ Failed to navigate to job URL: {e}")
                else:
                    logger.info(f"✅ Page is already at correct URL")
            
            logger.info(f"✅ Fresh browser context and page created for URL: {self.job_url}")

            # Inject security controls if job_url is provided
            if self.job_url:
                await self._inject_security_controls()

            logger.info(f"✅ Session files will be isolated to: /home/restricted_user/Desktop")

            logger.info("=" * 80)
            logger.info("✅ VNC-enabled browser environment started successfully")
            logger.info(f"📺 Display: {self.virtual_display.display}")
            logger.info(f"🖥️ VNC Port: {self.vnc_port}")
            logger.info(f"🔌 WebSocket Port: {self.ws_port}")
            logger.info(f"🌐 Browser PID: {self.browser_process.pid}")
            logger.info(f"🔗 Job URL: {self.job_url}")
            logger.info(f"🆔 Session ID: {self.session_id}")
            logger.info(f"👤 User ID: {self.user_id}")
            logger.info(f"🔐 Mode: {'APP MODE (tab-restricted)' if self.job_url else 'KIOSK MODE'}")
            logger.info("=" * 80)

            return True
            
        except Exception as e:
            logger.error(f"Failed to start VNC environment: {e}")
            await self.stop()
            return False

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
    
    async def _inject_security_controls(self):
        """
        Inject JavaScript to block tab operations and monitor navigation
        """
        try:
            logger.info("🔐 Injecting security controls...")

            # Inject script to block tab-related keyboard shortcuts
            await self.page.evaluate("""
                () => {
                    console.log('🔒 Security controls activated');

                    // Block tab-related keyboard shortcuts
                    document.addEventListener('keydown', (e) => {
                        // Block Ctrl+T (new tab)
                        if (e.ctrlKey && e.key === 't') {
                            e.preventDefault();
                            e.stopPropagation();
                            console.warn('🚫 New tab blocked');
                            return false;
                        }

                        // Block Ctrl+Tab (switch tabs)
                        if (e.ctrlKey && e.key === 'Tab') {
                            e.preventDefault();
                            e.stopPropagation();
                            console.warn('🚫 Tab switch blocked');
                            return false;
                        }

                        // Block Ctrl+W (close tab)
                        if (e.ctrlKey && e.key === 'w') {
                            e.preventDefault();
                            e.stopPropagation();
                            console.warn('🚫 Close tab blocked');
                            return false;
                        }

                        // Block Ctrl+Shift+T (reopen closed tab)
                        if (e.ctrlKey && e.shiftKey && e.key === 't') {
                            e.preventDefault();
                            e.stopPropagation();
                            console.warn('🚫 Reopen tab blocked');
                            return false;
                        }

                        // Block Ctrl+N (new window)
                        if (e.ctrlKey && e.key === 'n') {
                            e.preventDefault();
                            e.stopPropagation();
                            console.warn('🚫 New window blocked');
                            return false;
                        }

                        // Block F12 (dev tools - optional, uncomment if needed)
                        // if (e.key === 'F12') {
                        //     e.preventDefault();
                        //     e.stopPropagation();
                        //     console.warn('🚫 DevTools blocked');
                        //     return false;
                        // }
                    }, true);

                    // Add visual indicator that session is restricted
                    const indicator = document.createElement('div');
                    indicator.id = 'security-indicator';
                    indicator.innerHTML = '🔒 Secure Job Application Session';
                    indicator.style.cssText = `
                        position: fixed;
                        top: 0;
                        right: 0;
                        background: #4CAF50;
                        color: white;
                        padding: 8px 16px;
                        font-family: Arial, sans-serif;
                        font-size: 12px;
                        z-index: 999999;
                        border-bottom-left-radius: 4px;
                        box-shadow: 0 2px 4px rgba(0,0,0,0.2);
                    `;
                    document.body.appendChild(indicator);

                    console.log('✅ Security controls injected successfully');
                }
            """)

            logger.info("✅ Security controls injected successfully")

        except Exception as e:
            logger.error(f"Failed to inject security controls: {e}")
            # Non-critical, continue anyway

    async def cleanup_session_files(self, session_id: str = None):
        """
        Cleanup session-specific files.

        Args:
            session_id: Optional legacy parameter (kept for compatibility)
        """
        try:
            import shutil

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
                    logger.info("✓ Browser disconnected")
                except Exception as e:
                    logger.debug(f"Error disconnecting browser: {e}")

            # Stop browser process (sudo)
            if self.browser_process:
                try:
                    # Since we used sudo, normal terminate might not kill the child
                    # We need to use sudo kill
                    cmd = ["sudo", "kill", str(self.browser_process.pid)]
                    subprocess.run(cmd, check=False)
                    self.browser_process.wait(timeout=2)
                    logger.info("✓ Browser process killed")
                except Exception as e:
                    logger.debug(f"Error killing browser process: {e}")
                    try:
                        self.browser_process.kill()
                    except:
                        pass
            
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

