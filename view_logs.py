#!/usr/bin/env python3
"""
Utility script to view job application agent logs.
Usage: python view_logs.py [options]
"""

import argparse
import os
import sys
from pathlib import Path
from datetime import datetime

# Add current directory to path for logging_config import
sys.path.append(os.path.dirname(__file__))
from logging_config import get_current_log_file, cleanup_old_logs


def view_latest_log(tail_lines=None, follow=False):
    """View the latest log file."""
    log_file = get_current_log_file()
    
    if not log_file:
        print("No log files found in the logs directory.")
        return
    
    print(f"Viewing log file: {log_file}")
    print("=" * 80)
    
    try:
        if follow:
            # Follow mode (like tail -f)
            import time
            with open(log_file, 'r', encoding='utf-8') as f:
                # Go to end of file
                f.seek(0, 2)
                while True:
                    line = f.readline()
                    if line:
                        print(line.rstrip())
                    else:
                        time.sleep(0.1)
        else:
            # Regular view mode
            with open(log_file, 'r', encoding='utf-8') as f:
                lines = f.readlines()
                
                if tail_lines:
                    lines = lines[-tail_lines:]
                
                for line in lines:
                    print(line.rstrip())
                    
    except FileNotFoundError:
        print(f"Log file not found: {log_file}")
    except KeyboardInterrupt:
        if follow:
            print("\nStopped following log file.")
        return
    except Exception as e:
        print(f"Error reading log file: {e}")


def list_log_files():
    """List all available log files."""
    logs_dir = Path("logs")
    
    if not logs_dir.exists():
        print("Logs directory does not exist.")
        return
    
    log_files = list(logs_dir.glob("*.log"))
    
    if not log_files:
        print("No log files found.")
        return
    
    print("Available log files:")
    print("-" * 50)
    
    for log_file in sorted(log_files, key=lambda f: f.stat().st_mtime, reverse=True):
        stat = log_file.stat()
        size = stat.st_size
        modified = datetime.fromtimestamp(stat.st_mtime)
        
        # Format file size
        if size < 1024:
            size_str = f"{size} B"
        elif size < 1024 * 1024:
            size_str = f"{size / 1024:.1f} KB"
        else:
            size_str = f"{size / (1024 * 1024):.1f} MB"
        
        print(f"{log_file.name:<40} {size_str:>10} {modified.strftime('%Y-%m-%d %H:%M:%S')}")


def search_logs(pattern, case_sensitive=False):
    """Search for a pattern in all log files."""
    logs_dir = Path("logs")
    
    if not logs_dir.exists():
        print("Logs directory does not exist.")
        return
    
    log_files = list(logs_dir.glob("*.log"))
    
    if not log_files:
        print("No log files found.")
        return
    
    import re
    
    flags = 0 if case_sensitive else re.IGNORECASE
    regex = re.compile(pattern, flags)
    
    matches_found = False
    
    for log_file in sorted(log_files, key=lambda f: f.stat().st_mtime, reverse=True):
        try:
            with open(log_file, 'r', encoding='utf-8') as f:
                for line_num, line in enumerate(f, 1):
                    if regex.search(line):
                        if not matches_found:
                            print(f"\nMatches in {log_file.name}:")
                            print("-" * 50)
                            matches_found = True
                        print(f"Line {line_num}: {line.rstrip()}")
        except Exception as e:
            print(f"Error reading {log_file}: {e}")
    
    if not matches_found:
        print(f"No matches found for pattern: {pattern}")


def main():
    parser = argparse.ArgumentParser(description="View job application agent logs")
    
    subparsers = parser.add_subparsers(dest='command', help='Available commands')
    
    # View command
    view_parser = subparsers.add_parser('view', help='View the latest log file')
    view_parser.add_argument('--tail', '-t', type=int, help='Show only the last N lines')
    view_parser.add_argument('--follow', '-f', action='store_true', help='Follow log file (like tail -f)')
    
    # List command
    list_parser = subparsers.add_parser('list', help='List all available log files')
    
    # Search command
    search_parser = subparsers.add_parser('search', help='Search for pattern in log files')
    search_parser.add_argument('pattern', help='Pattern to search for (regex supported)')
    search_parser.add_argument('--case-sensitive', '-c', action='store_true', help='Case sensitive search')
    
    # Cleanup command
    cleanup_parser = subparsers.add_parser('cleanup', help='Clean up old log files')
    cleanup_parser.add_argument('--days', type=int, default=30, help='Keep logs newer than N days (default: 30)')
    
    args = parser.parse_args()
    
    if args.command == 'view' or args.command is None:
        view_latest_log(tail_lines=args.tail if hasattr(args, 'tail') else None, 
                       follow=args.follow if hasattr(args, 'follow') else False)
    elif args.command == 'list':
        list_log_files()
    elif args.command == 'search':
        search_logs(args.pattern, args.case_sensitive)
    elif args.command == 'cleanup':
        cleanup_old_logs(args.days)
        print(f"Cleaned up log files older than {args.days} days.")
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
