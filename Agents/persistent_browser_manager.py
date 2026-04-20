"""
Persistent Browser Profile Manager
Creates and manages user-specific browser profiles that persist across sessions
"""

import os
import json
import logging
import platform
import subprocess
from pathlib import Path
from typing import Dict, Any, Optional
from datetime import datetime
from playwright.async_api import Browser, BrowserContext, async_playwright

logger = logging.getLogger(__name__)

# Global registry to track active browser contexts per user
_active_contexts: Dict[str, BrowserContext] = {}
# Track which event loop the contexts were created in
_active_contexts_loop_id: int = -1


def _clear_stale_contexts_if_new_loop() -> None:
    """If asyncio.run() was called again a new event loop is running.
    All previously stored contexts are bound to the old (closed) loop and
    must be discarded before we try to use them.
    """
    global _active_contexts, _active_contexts_loop_id
    try:
        import asyncio
        current = id(asyncio.get_running_loop())
    except RuntimeError:
        return  # not in an async context - nothing to do
    if current != _active_contexts_loop_id:
        if _active_contexts:
            logger.info(f"🔄 New event loop - discarding {len(_active_contexts)} stale browser context(s)")
        _active_contexts = {}
        _active_contexts_loop_id = current


async def close_all_active_browsers() -> int:
    """
    Properly close every active persistent browser context and remove it
    from the registry.  For persistent contexts, closing the context also
    terminates the underlying Chrome process — this is the correct way to
    prevent stale Chrome processes from blocking the next launch.

    Call this at the end of any batch operation so the next run always
    starts with a clean slate.

    Returns the number of contexts that were successfully closed.
    """
    closed = 0
    for user_id, ctx in list(_active_contexts.items()):
        try:
            await ctx.close()
            logger.info(f"🔒 Closed browser context for user {user_id}")
            closed += 1
        except Exception as e:
            logger.debug(f"Could not close context for {user_id}: {e}")
        finally:
            _active_contexts.pop(user_id, None)
    if closed:
        logger.info(f"✅ Closed {closed} browser context(s) after batch")
    return closed


class PersistentBrowserManager:
    """
    Manages persistent browser profiles for users
    
    Benefits:
    - Cookies and sessions persist across runs
    - Job boards recognize the "device"
    - No bot detection or verification codes
    - Manual and automated work in same profile
    - Can resume applications anytime
    - Reuses same browser for multiple jobs (opens new tabs)
    """
    
    def __init__(self, base_dir: str = "./browser_profiles"):
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)
        logger.info(f"Persistent browser manager initialized: {self.base_dir}")
    
    def get_profile_path(self, user_id: str) -> Path:
        """Get path to user's browser profile directory"""
        profile_path = self.base_dir / f"user_{user_id}"
        profile_path.mkdir(parents=True, exist_ok=True)
        return profile_path
    
    async def launch_persistent_browser(
        self,
        user_id: str,
        headless: bool = False,
        proxy_config: Optional[Dict[str, Any]] = None,
        playwright_instance=None  # Accept existing playwright instance
    ) -> BrowserContext:
        """
        Launch browser with persistent profile, or reuse existing one
        
        Args:
            user_id: User ID for profile isolation
            headless: Run headless or visible
            proxy_config: Proxy configuration dict
        
        Returns:
            BrowserContext that persists across sessions
        """
        # Discard all contexts from a previous asyncio.run() event loop before
        # checking the cache - they are tied to the old (closed) loop.
        _clear_stale_contexts_if_new_loop()

        # Check if browser is already open for this user
        if user_id in _active_contexts:
            context = _active_contexts[user_id]
            try:
                # Verify cached context is truly alive (RPC round-trip).
                # A stale context may still expose .pages but fail on new_page().
                await context.cookies()
                pages = context.pages
                logger.info(f"♻️  Reusing existing browser for user {user_id} ({len(pages)} tabs open)")
                print(f"[INFO] ♻️  Reusing existing browser ({len(pages)} tabs)")
                return context
            except Exception as e:
                logger.warning(f"Existing browser context invalid: {e}. Creating new one.")
                print(f"[WARN] ⚠️  Previous browser was closed. Opening fresh browser with your profile...")
                # Remove invalid context and try to close it
                try:
                    await context.close()
                except:
                    pass  # Already closed or invalid
                _active_contexts.pop(user_id, None)
        
        profile_path = self.get_profile_path(user_id)
        
        logger.info(f"🌟 Launching NEW persistent browser for user {user_id}")
        logger.info(f"Profile path: {profile_path}")
        logger.info(f"Headless: {headless}")
        
        # Build launch arguments with anti-detection
        args = [
            '--disable-blink-features=AutomationControlled',  # Hide automation
            '--disable-dev-shm-usage',  # For stability
            '--no-sandbox',  # Required in some environments
            '--disable-setuid-sandbox',
            '--disable-web-security',  # For some job sites
            '--disable-features=IsolateOrigins,site-per-process',
            '--start-maximized',  # Let Chrome size the window correctly for the OS/display
            '--force-device-scale-factor=1',  # Prevent DPI scaling issues
        ]
        
        # User agent (use real Chrome user agent)
        user_agent = (
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
            'AppleWebKit/537.36 (KHTML, like Gecko) '
            'Chrome/120.0.0.0 Safari/537.36'
        )
        
        # Build context options
        context_options = {
            # no_viewport + start-maximized gives native browser sizing and proper page scrolling
            'no_viewport': True,
            'user_agent': user_agent,
            'locale': 'en-US',
            'timezone_id': 'America/New_York',
            'permissions': ['geolocation', 'notifications'],
            'color_scheme': 'light',
            'has_touch': False,
            'is_mobile': False,
        }
        
        # Add proxy if provided
        if proxy_config:
            context_options['proxy'] = proxy_config
            logger.info(f"Using proxy: {proxy_config.get('server', 'unknown')}")
        
        # Get or create playwright instance
        if playwright_instance:
            playwright = playwright_instance
            created_playwright = False
        else:
            playwright = await async_playwright().start()
            created_playwright = True
        
        import asyncio

        # Minimal flag set that works reliably across environments
        safer_args = [
            '--disable-blink-features=AutomationControlled',
            '--disable-dev-shm-usage',
            '--no-sandbox',
            '--disable-setuid-sandbox',
            '--start-maximized',
            '--force-device-scale-factor=1',
        ]

        # ── Pre-launch cleanup ────────────────────────────────────────────────
        pre_killed = self._kill_stale_playwright_chrome(profile_path)
        if pre_killed > 0:
            logger.info("🧹 Pre-launch: terminated %s stale Playwright Chrome process(es)", pre_killed)
            await asyncio.sleep(1.5)
        self._cleanup_stale_profile_locks(profile_path)

        # ── Attempt 1 ────────────────────────────────────────────────────────
        try:
            context = await playwright.chromium.launch_persistent_context(
                user_data_dir=str(profile_path),
                headless=headless,
                args=args,
                **context_options
            )
        except Exception as first_error:
            logger.warning(
                "Persistent browser launch failed (attempt 1) for profile %s: %s",
                profile_path,
                first_error,
            )

            killed1 = self._kill_stale_playwright_chrome(profile_path)
            if killed1 > 0:
                logger.info("🧹 Killed %s orphaned Playwright Chrome process(es) after attempt 1", killed1)
            self._cleanup_stale_profile_locks(profile_path)
            await asyncio.sleep(2.0)

            # ── Attempt 2 (safer flags) ───────────────────────────────────────
            try:
                logger.info("🔁 Retrying persistent browser launch with safer Chromium flags (attempt 2)")
                context = await playwright.chromium.launch_persistent_context(
                    user_data_dir=str(profile_path),
                    headless=headless,
                    args=safer_args,
                    **context_options
                )
            except Exception as second_error:
                logger.warning(
                    "Persistent browser launch failed (attempt 2) for profile %s: %s",
                    profile_path,
                    second_error,
                )
                killed2 = self._kill_stale_playwright_chrome(profile_path)
                if killed2 > 0:
                    logger.info("🧹 Killed %s orphaned Playwright Chrome process(es) after attempt 2", killed2)
                self._cleanup_stale_profile_locks(profile_path)
                await asyncio.sleep(3.0)
                logger.info("🔁 Final retry after extended stale-process cleanup (attempt 3)")
                context = await playwright.chromium.launch_persistent_context(
                    user_data_dir=str(profile_path),
                    headless=headless,
                    args=safer_args,
                    **context_options
                )
        
        # Store playwright instance for cleanup (only if we created it)
        if created_playwright:
            context._playwright = playwright
        else:
            context._playwright = None  # Don't close shared instance
        
        # Add anti-detection JavaScript
        await self._inject_anti_detection(context)
        
        # Save profile metadata
        self._save_profile_metadata(user_id, {
            'last_used': datetime.now().isoformat(),
            'headless': headless,
            'proxy': proxy_config is not None
        })
        
        # Store context in global registry for reuse
        _active_contexts[user_id] = context
        logger.info(f"✓ Persistent browser launched for user {user_id} (stored for reuse)")
        
        return context

    def _cleanup_stale_profile_locks(self, profile_path: Path) -> None:
        """
        Clean up stale Chromium lock artifacts AND session-recovery files.

        Session recovery files are the most common cause of Chrome crashing
        with STATUS_BREAKPOINT (exit code 0x80000003) immediately on startup.
        When Playwright force-kills Chrome at the end of a session, files like
        Last Session / Last Tabs are left in a partial, inconsistent state.
        The next Chrome launch tries to restore that session, hits an internal
        DCHECK assertion, and dies before establishing the CDP pipe — which
        Playwright reports as "Target page, context or browser has been closed".

        Deleting those files (not cookies / local-storage / login data) is safe:
        the only side-effect is that Chrome opens a blank tab instead of
        restoring the previous tab list.  Persistent login sessions are kept.
        """
        removed = 0
        default_dir = profile_path / "Default"

        # ── 1. Singleton lock files (both profile root and Default/) ───────────
        lock_names = (
            "SingletonLock", "SingletonCookie", "SingletonSocket",
            "lockfile", ".parentlock",
        )
        for d in [profile_path, default_dir]:
            for name in lock_names:
                p = d / name
                try:
                    if p.exists():
                        p.unlink()
                        removed += 1
                        logger.debug(f"Removed lock: {p}")
                except Exception as e:
                    logger.debug(f"Could not remove {p}: {e}")

        # ── 2. Session-recovery files (the main crash trigger) ─────────────────
        # Chrome reads these on startup to restore the previous session.
        # If Chrome was force-killed they are incomplete → DCHECK crash.
        session_files = ("Last Session", "Last Tabs", "Current Session", "Current Tabs")
        for fname in session_files:
            p = default_dir / fname
            try:
                if p.exists():
                    p.unlink()
                    removed += 1
                    logger.debug(f"Removed session file: {p}")
            except Exception as e:
                logger.debug(f"Could not remove {p}: {e}")

        # ── 3. SQLite WAL journal files left by a crashed session ──────────────
        # An open -journal file causes SQLite to run recovery on next open.
        # If the journal is corrupt Chrome can crash during early DB initialisation.
        try:
            if default_dir.exists():
                for jf in default_dir.glob("*-journal"):
                    try:
                        jf.unlink()
                        removed += 1
                        logger.debug(f"Removed journal: {jf}")
                    except Exception:
                        pass
        except Exception as e:
            logger.debug(f"Journal scan error: {e}")

        if removed > 0:
            logger.info(f"🧹 Removed {removed} stale profile artifact(s) (locks + session files)")

    def _kill_stale_playwright_chrome(self, profile_path: Path) -> int:
        """
        Kill ALL chrome.exe processes that belong to Playwright's own managed
        Chromium installation (located under ms-playwright in AppData).

        Uses cmd.exe native tools (tasklist + wmic + taskkill) instead of
        PowerShell because they are faster, simpler, and not blocked by
        execution policy restrictions.  Two complementary methods are run:

        1. WMIC query on ExecutablePath — finds every chrome.exe whose binary
           lives under ms-playwright, including orphaned sandbox/GPU helpers
           that carry no --user-data-dir flag.  Uses /T to kill child trees.

        2. WMIC query on CommandLine — catches any chrome not matched by path
           (edge case) that has the specific --user-data-dir in its cmdline.
        """
        if platform.system().lower() != "windows":
            return 0

        killed = 0
        profile_str = str(profile_path)

        # ── Method 1: kill by ExecutablePath (catches all, including helpers) ──
        # Use /format:value which outputs one "Field=Value" per line — no CSV
        # quoting issues even when paths contain commas.
        try:
            pids_result = subprocess.run(
                [
                    "wmic", "process",
                    "where", "name='chrome.exe'",
                    "get", "ProcessId,ExecutablePath",
                    "/format:value",
                ],
                capture_output=True, text=True, timeout=15,
            )
            current_pid = None
            current_path = None
            for raw_line in pids_result.stdout.splitlines():
                line = raw_line.strip()
                if line.startswith("ExecutablePath="):
                    current_path = line[len("ExecutablePath="):]
                elif line.startswith("ProcessId="):
                    current_pid = line[len("ProcessId="):]
                if current_pid and current_path is not None:
                    if "ms-playwright" in current_path.lower() and current_pid.isdigit():
                        try:
                            subprocess.run(
                                ["taskkill", "/PID", current_pid, "/T", "/F"],
                                capture_output=True, timeout=8,
                            )
                            killed += 1
                            logger.debug(f"Killed Playwright chrome PID {current_pid}")
                        except Exception:
                            pass
                    # Reset for next process block
                    current_pid = None
                    current_path = None
        except Exception as e:
            logger.debug(f"WMIC path-based chrome kill failed: {e}")

        # ── Method 2: kill by --user-data-dir in CommandLine (fallback) ──
        # Catches the browser process that still holds the profile path.
        # Note: orphaned sandbox helper processes (GPU, renderer) have NO
        # --user-data-dir in their CommandLine — only the main browser does.
        try:
            cmdline_result = subprocess.run(
                [
                    "wmic", "process",
                    "where", "name='chrome.exe'",
                    "get", "ProcessId,CommandLine",
                    "/format:value",
                ],
                capture_output=True, text=True, timeout=15,
            )
            current_pid = None
            current_cmdline = None
            for raw_line in cmdline_result.stdout.splitlines():
                line = raw_line.strip()
                if line.startswith("CommandLine="):
                    current_cmdline = line[len("CommandLine="):]
                elif line.startswith("ProcessId="):
                    current_pid = line[len("ProcessId="):]
                if current_pid and current_cmdline is not None:
                    if profile_str in current_cmdline and current_pid.isdigit():
                        try:
                            subprocess.run(
                                ["taskkill", "/PID", current_pid, "/T", "/F"],
                                capture_output=True, timeout=8,
                            )
                            killed += 1
                            logger.debug(f"Killed profile-locked chrome PID {current_pid}")
                        except Exception:
                            pass
                    current_pid = None
                    current_cmdline = None
        except Exception as e:
            logger.debug(f"WMIC cmdline-based chrome kill failed: {e}")

        # IMPORTANT: do not kill all chrome.exe processes.
        # Users may have active work in personal/work Chrome profiles.
        # We only terminate processes that can be confidently identified as
        # Playwright-managed or profile-specific.
        return killed
    
    async def _inject_anti_detection(self, context: BrowserContext):
        """Inject JavaScript to hide automation detection"""
        
        anti_detection_script = """
        // Remove webdriver flag
        Object.defineProperty(navigator, 'webdriver', {
            get: () => undefined
        });
        
        // Add realistic Chrome object
        window.chrome = {
            runtime: {},
            loadTimes: function() {},
            csi: function() {},
            app: {}
        };
        
        // Mock plugins
        Object.defineProperty(navigator, 'plugins', {
            get: () => [1, 2, 3, 4, 5]
        });
        
        // Mock languages
        Object.defineProperty(navigator, 'languages', {
            get: () => ['en-US', 'en']
        });
        
        // Hide automation in permissions
        const originalQuery = window.navigator.permissions.query;
        window.navigator.permissions.query = (parameters) => (
            parameters.name === 'notifications' ?
                Promise.resolve({ state: Notification.permission }) :
                originalQuery(parameters)
        );
        """
        
        # Add init script to all pages
        await context.add_init_script(anti_detection_script)
        
        logger.debug("Anti-detection scripts injected")
    
    def _save_profile_metadata(self, user_id: str, metadata: Dict[str, Any]):
        """Save profile metadata for tracking"""
        profile_path = self.get_profile_path(user_id)
        metadata_file = profile_path / "profile_metadata.json"
        
        try:
            with open(metadata_file, 'w') as f:
                json.dump(metadata, f, indent=2)
        except Exception as e:
            logger.warning(f"Failed to save profile metadata: {e}")
    
    def get_profile_info(self, user_id: str) -> Dict[str, Any]:
        """Get information about a user's profile"""
        profile_path = self.get_profile_path(user_id)
        
        info = {
            "user_id": user_id,
            "profile_path": str(profile_path),
            "exists": profile_path.exists(),
            "size_mb": 0,
            "files_count": 0
        }
        
        if profile_path.exists():
            # Calculate profile size
            total_size = sum(
                f.stat().st_size for f in profile_path.rglob('*') if f.is_file()
            )
            info["size_mb"] = round(total_size / (1024 * 1024), 2)
            info["files_count"] = len(list(profile_path.rglob('*')))
            
            # Load metadata if available
            metadata_file = profile_path / "profile_metadata.json"
            if metadata_file.exists():
                try:
                    with open(metadata_file, 'r') as f:
                        metadata = json.load(f)
                        info.update(metadata)
                except Exception as e:
                    logger.warning(f"Failed to load profile metadata: {e}")
        
        return info
    
    def list_profiles(self) -> list:
        """List all user profiles"""
        profiles = []
        
        if self.base_dir.exists():
            for profile_dir in self.base_dir.iterdir():
                if profile_dir.is_dir() and profile_dir.name.startswith('user_'):
                    user_id = profile_dir.name.replace('user_', '')
                    profiles.append(self.get_profile_info(user_id))
        
        return profiles
    
    @staticmethod
    def close_browser_for_user(user_id: str):
        """
        Close and clean up browser context for a user
        
        Args:
            user_id: User ID whose browser to close
        """
        if user_id in _active_contexts:
            del _active_contexts[user_id]
            logger.info(f"🧹 Removed browser context for user {user_id} from registry")
    
    @staticmethod
    def get_active_contexts() -> Dict[str, Any]:
        """Get information about all active browser contexts"""
        return {
            user_id: {
                'num_pages': len(context.pages),
                'pages': [page.url for page in context.pages]
            }
            for user_id, context in _active_contexts.items()
        }
    
    def delete_profile(self, user_id: str) -> bool:
        """Delete a user's browser profile"""
        profile_path = self.get_profile_path(user_id)
        
        # Also remove from active contexts if present
        self.close_browser_for_user(user_id)
        
        try:
            if profile_path.exists():
                import shutil
                shutil.rmtree(profile_path)
                logger.info(f"Deleted profile for user {user_id}")
                return True
            else:
                logger.warning(f"Profile for user {user_id} does not exist")
                return False
        except Exception as e:
            logger.error(f"Failed to delete profile for user {user_id}: {e}")
            return False
    
    async def initialize_profile_for_user(
        self,
        user_id: str,
        manual_setup: bool = True
    ) -> BrowserContext:
        """
        Initialize a new profile for a user
        
        If manual_setup=True, launches browser for user to:
        - Login to job boards manually
        - Accept cookies
        - Complete any verifications
        - Build trust with job sites
        
        Args:
            user_id: User ID
            manual_setup: If True, keeps browser open for manual login
        
        Returns:
            BrowserContext
        """
        logger.info(f"Initializing profile for user {user_id}")
        
        # Launch visible browser for setup
        context = await self.launch_persistent_browser(
            user_id=user_id,
            headless=False  # Always visible for setup
        )
        
        if manual_setup:
            page = context.pages[0] if context.pages else await context.new_page()
            
            # Navigate to common job boards for login
            print("\n" + "="*60)
            print("🎯 BROWSER PROFILE SETUP")
            print("="*60)
            print("\nA browser window has opened for you.")
            print("\nRecommended setup steps:")
            print("  1. Login to LinkedIn (if you use LinkedIn)")
            print("  2. Login to Indeed (if you use Indeed)")
            print("  3. Login to Glassdoor (if you use Glassdoor)")
            print("  4. Accept any cookie consents")
            print("  5. Complete any security verifications")
            print("\nThis one-time setup will:")
            print("  ✓ Keep you logged in for future sessions")
            print("  ✓ Prevent bot detection")
            print("  ✓ Avoid verification codes")
            print("  ✓ Build trust with job sites")
            print("\nWhen done, close the browser or press Ctrl+C here.")
            print("="*60 + "\n")
            
            # Navigate to LinkedIn login as starting point
            try:
                await page.goto('https://www.linkedin.com/login')
            except Exception as e:
                logger.warning(f"Failed to navigate to LinkedIn: {e}")
            
            # Wait for user to complete setup
            try:
                import asyncio
                await asyncio.sleep(99999)  # Wait indefinitely
            except (KeyboardInterrupt, asyncio.CancelledError, Exception):
                print("\n✓ Profile setup completed!")
        
        return context


# Convenience function
async def get_persistent_browser(
    user_id: str,
    headless: bool = False,
    proxy_config: Optional[Dict[str, Any]] = None
) -> BrowserContext:
    """
    Quick function to get a persistent browser context
    
    Example:
        context = await get_persistent_browser("user123", headless=False)
        page = await context.new_page()
        await page.goto("https://www.linkedin.com/jobs/")
    """
    manager = PersistentBrowserManager()
    return await manager.launch_persistent_browser(user_id, headless, proxy_config)


if __name__ == "__main__":
    # Test the persistent browser manager
    import asyncio
    import logging
    
    logging.basicConfig(level=logging.INFO)
    
    async def test():
        print("Testing Persistent Browser Manager...\n")
        
        manager = PersistentBrowserManager()
        
        # Test 1: Get profile info
        info = manager.get_profile_info("test_user")
        print(f"Profile info: {info}")
        
        # Test 2: Launch persistent browser
        print("\nLaunching persistent browser...")
        context = await manager.launch_persistent_browser(
            user_id="test_user",
            headless=False
        )
        
        print("✓ Browser launched with persistent profile")
        
        # Create a page and navigate
        page = context.pages[0] if context.pages else await context.new_page()
        await page.goto("https://www.example.com")
        
        print("✓ Navigated to example.com")
        print("\nClose the browser to continue...")
        
        # Wait a bit
        await asyncio.sleep(5)
        
        # Close
        await context.close()
        await context._playwright.stop()
        
        print("✓ Browser closed")
        
        # Test 3: Check profile again
        info = manager.get_profile_info("test_user")
        print(f"\nProfile after use: {info}")
        print(f"Profile size: {info['size_mb']} MB")
    
    asyncio.run(test())
