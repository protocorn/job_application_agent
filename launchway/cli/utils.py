"""Terminal colors and shared print/input helpers."""

import os
import getpass
import time


def format_credits(remaining, limit, reset_time=None) -> str:
    """
    Format a single-line credits status string,
    e.g. '4/5 remaining today (resets in 14h 32m)'.
    Works for both numeric and 'unlimited' values.
    """
    if remaining == "unlimited" or limit == "unlimited":
        return "unlimited"
    try:
        remaining = int(remaining)
        limit = int(limit)
    except (TypeError, ValueError):
        return "unknown"

    resets = ""
    if reset_time:
        diff = int(reset_time - time.time())
        if diff > 0:
            h = diff // 3600
            m = (diff % 3600) // 60
            resets = f" (resets in {h}h {m}m)" if h > 0 else f" (resets in {m}m)"

    return f"{remaining}/{limit} remaining today{resets}"


class Colors:
    """ANSI color codes for terminal output."""
    HEADER    = '\033[95m'
    OKBLUE    = '\033[94m'
    OKCYAN    = '\033[96m'
    OKGREEN   = '\033[92m'
    WARNING   = '\033[93m'
    FAIL      = '\033[91m'
    ENDC      = '\033[0m'
    BOLD      = '\033[1m'
    UNDERLINE = '\033[4m'
    YELLOW    = '\033[93m'
    RESET     = '\033[0m'


class PrintMixin:
    """Shared terminal UI helpers - mixed into CLIJobAgent."""

    def clear_screen(self):
        os.system('cls' if os.name == 'nt' else 'clear')

    def print_header(self, text: str):
        print(f"\n{Colors.HEADER}{Colors.BOLD}{'=' * 60}{Colors.ENDC}")
        print(f"{Colors.HEADER}{Colors.BOLD}{text.center(60)}{Colors.ENDC}")
        print(f"{Colors.HEADER}{Colors.BOLD}{'=' * 60}{Colors.ENDC}\n")

    def print_success(self, text: str, *args, **kwargs):
        print(f"{Colors.OKGREEN}[OK] {text}{Colors.ENDC}", *args, **kwargs)

    def print_error(self, text: str, *args, **kwargs):
        print(f"{Colors.FAIL}[ERROR] {text}{Colors.ENDC}", *args, **kwargs)

    def print_info(self, text: str, *args, **kwargs):
        print(f"{Colors.OKCYAN}[INFO] {text}{Colors.ENDC}", *args, **kwargs)

    def print_warning(self, text: str, *args, **kwargs):
        print(f"{Colors.WARNING}[WARN] {text}{Colors.ENDC}", *args, **kwargs)

    def get_input(self, prompt: str, password: bool = False) -> str:
        try:
            if password:
                return getpass.getpass(f"{Colors.OKBLUE}{prompt}{Colors.ENDC}")
            return input(f"{Colors.OKBLUE}{prompt}{Colors.ENDC}")
        except EOFError as e:
            # Treat stream-closed input the same as Ctrl+C for clean shutdown.
            raise KeyboardInterrupt() from e

    def get_input_yn(self, prompt: str, default: str = None) -> bool:
        """
        Prompt for y/n; re-prompt until input is 'y', 'n', or empty (empty uses default).
        default: 'y', 'n', or None (no default - empty not allowed).
        Returns True for yes, False for no.
        """
        while True:
            raw = self.get_input(prompt).strip().lower()
            if raw in ('y', 'yes'):
                return True
            if raw in ('n', 'no'):
                return False
            if raw == '' and default is not None:
                return default == 'y'
            self.print_warning("Please enter y or n" + (f" (or press Enter for {default})" if default else "") + ".")

    def pause(self):
        try:
            input(f"\n{Colors.OKCYAN}Press Enter to continue...{Colors.ENDC}")
        except EOFError:
            pass
