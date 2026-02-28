"""Terminal colors and shared print/input helpers."""

import os
import getpass


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


class PrintMixin:
    """Shared terminal UI helpers — mixed into CLIJobAgent."""

    def clear_screen(self):
        os.system('cls' if os.name == 'nt' else 'clear')

    def print_header(self, text: str):
        print(f"\n{Colors.HEADER}{Colors.BOLD}{'=' * 60}{Colors.ENDC}")
        print(f"{Colors.HEADER}{Colors.BOLD}{text.center(60)}{Colors.ENDC}")
        print(f"{Colors.HEADER}{Colors.BOLD}{'=' * 60}{Colors.ENDC}\n")

    def print_success(self, text: str):
        print(f"{Colors.OKGREEN}[OK] {text}{Colors.ENDC}")

    def print_error(self, text: str):
        print(f"{Colors.FAIL}[ERROR] {text}{Colors.ENDC}")

    def print_info(self, text: str):
        print(f"{Colors.OKCYAN}[INFO] {text}{Colors.ENDC}")

    def print_warning(self, text: str):
        print(f"{Colors.WARNING}[WARN] {text}{Colors.ENDC}")

    def get_input(self, prompt: str, password: bool = False) -> str:
        if password:
            return getpass.getpass(f"{Colors.OKBLUE}{prompt}{Colors.ENDC}")
        return input(f"{Colors.OKBLUE}{prompt}{Colors.ENDC}")

    def pause(self):
        input(f"\n{Colors.OKCYAN}Press Enter to continue...{Colors.ENDC}")
