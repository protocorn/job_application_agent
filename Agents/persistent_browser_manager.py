"""
Persistent Browser Profile Manager
Creates and manages user-specific browser profiles that persist across sessions
"""

import os
import json
import logging
from pathlib import Path
from typing import Dict, Any, Optional
from datetime import datetime
from playwright.async_api import Browser, BrowserContext, async_playwright

logger = logging.getLogger(__name__)

# Global registry to track active browser contexts per user
_active_contexts: Dict[str, BrowserContext] = {}


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
        
        # Launch persistent context (this is the magic!)
        context = await playwright.chromium.launch_persistent_context(
            user_data_dir=str(profile_path),
            headless=headless,
            args=args,
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
            except (KeyboardInterrupt, Exception):
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
