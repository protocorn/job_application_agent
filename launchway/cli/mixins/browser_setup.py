"""Browser profile setup mixin — one-time persistent login."""

import logging
from launchway.cli.utils import Colors

logger = logging.getLogger(__name__)


class BrowserSetupMixin:

    async def browser_profile_setup_menu(self):
        self.clear_screen()
        self.print_header("🌐 BROWSER PROFILE SETUP")

        if not self._ensure_agents_bootstrapped():
            self.pause()
            return

        from Agents.persistent_browser_manager import PersistentBrowserManager

        print("\n" + "=" * 60)
        print("  WHY SETUP A PERSISTENT BROWSER PROFILE?")
        print("=" * 60)
        print("\n✓ Stay logged into job boards (LinkedIn, Indeed, etc.)")
        print("✓ Prevent bot detection and verification codes")
        print("✓ Build trust with job sites over time")
        print("✓ Use same profile for manual AND automated applications")
        print("✓ Resume applications exactly where you left off")
        print("\n" + "=" * 60)
        print("  ONE-TIME SETUP STEPS:")
        print("=" * 60)
        print("\n1. A browser window will open")
        print("2. Login to LinkedIn, Indeed, Glassdoor, etc.")
        print("3. Accept cookie consents")
        print("4. Complete any security verifications")
        print("5. Close browser when done")
        print("\nThis creates a persistent profile used for all future sessions.")
        print("=" * 60 + "\n")

        if not self.get_input_yn("Setup persistent profile now? (y/n): ", default=None):
            self.print_info("Setup cancelled.")
            self.pause()
            return

        try:
            print(f"\n{Colors.OKBLUE}Initializing browser profile...{Colors.ENDC}")
            manager      = PersistentBrowserManager()
            profile_info = manager.get_profile_info(str(self.current_user['id']))

            if profile_info['exists']:
                print(f"\n{Colors.WARNING}⚠️  Profile already exists:{Colors.ENDC}")
                print(f"  Size:  {profile_info['size_mb']} MB")
                print(f"  Files: {profile_info['files_count']}")
                print(f"  Path:  {profile_info['profile_path']}")

                choice = self.get_input(
                    "\n1. Continue with existing profile\n"
                    "2. Reset and setup new\n"
                    "3. Cancel\n\nChoice (1-3): "
                ).strip()

                if choice == '2':
                    if self.get_input_yn("Delete existing profile? (y/n): ", default=None):
                        manager.delete_profile(str(self.current_user['id']))
                        self.print_success("Existing profile deleted.")
                    else:
                        self.print_info("Setup cancelled.")
                        self.pause()
                        return
                elif choice == '3':
                    self.print_info("Setup cancelled.")
                    self.pause()
                    return

            print(f"\n{Colors.OKBLUE}🚀 Launching browser for setup...{Colors.ENDC}")
            print(f"  User ID:      {self.current_user['id']}")
            print(f"  Profile path: {manager.get_profile_path(str(self.current_user['id']))}\n")

            context = await manager.initialize_profile_for_user(
                user_id=str(self.current_user['id']),
                manual_setup=True,
            )

            try:
                await context.close()
                if hasattr(context, '_playwright'):
                    await context._playwright.stop()
                PersistentBrowserManager.close_browser_for_user(str(self.current_user['id']))
            except Exception as e:
                logger.warning(f"Error closing browser: {e}")

            profile_info = manager.get_profile_info(str(self.current_user['id']))
            print(f"\n{Colors.OKGREEN}✓ Browser profile setup complete!{Colors.ENDC}\n")
            print(f"  Size:     {profile_info['size_mb']} MB")
            print(f"  Files:    {profile_info['files_count']}")
            print(f"  Location: {profile_info['profile_path']}")
            print(f"\nYou're now ready to use automated job applications!")

        except Exception as e:
            self.print_error(f"Profile setup failed: {str(e)}")
            logger.error(f"Browser profile setup error: {e}", exc_info=True)

        self.pause()
