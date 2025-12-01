#!/usr/bin/env python3
"""
Orphaned Process Cleanup Script

This script cleans up orphaned processes that may remain after VNC sessions crash or fail to clean up properly.
It should be run periodically via cron or systemd timer.

Processes cleaned:
- Xvfb (virtual displays)
- x11vnc (VNC servers)
- websockify (WebSocket proxies)
- Chrome/Chromium browser processes from Playwright

Usage:
  python cleanup_orphaned_processes.py [--dry-run] [--max-age HOURS]

Options:
  --dry-run     Show what would be cleaned without actually killing processes
  --max-age     Only kill processes older than this many hours (default: 24)
  --force       Kill all matching processes regardless of age
"""

import os
import sys
import argparse
import subprocess
import time
from datetime import datetime, timedelta
from loguru import logger

# Configure logging
logger.remove()  # Remove default handler
logger.add(sys.stdout, format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level:8}</level> | {message}")
logger.add("logs/process_cleanup_{time}.log", rotation="1 week", retention="4 weeks")


def get_process_age_hours(pid: int) -> float:
    """
    Get process age in hours.

    Args:
        pid: Process ID

    Returns:
        Age in hours, or 0 if cannot determine
    """
    try:
        if os.name == 'nt':  # Windows
            # Use WMIC to get process creation time
            result = subprocess.run(
                ['wmic', 'process', 'where', f'ProcessId={pid}', 'get', 'CreationDate'],
                capture_output=True,
                text=True,
                timeout=5
            )
            if result.returncode == 0:
                lines = result.stdout.strip().split('\n')
                if len(lines) >= 2:
                    creation_str = lines[1].strip()
                    # Parse WMI datetime format: 20250101120000.123456+000
                    creation_time = datetime.strptime(creation_str[:14], '%Y%m%d%H%M%S')
                    age = datetime.now() - creation_time
                    return age.total_seconds() / 3600
        else:  # Linux/Unix
            # Use ps to get process start time
            result = subprocess.run(
                ['ps', '-p', str(pid), '-o', 'etimes='],
                capture_output=True,
                text=True,
                timeout=5
            )
            if result.returncode == 0:
                elapsed_seconds = int(result.stdout.strip())
                return elapsed_seconds / 3600
    except Exception as e:
        logger.debug(f"Could not determine age for PID {pid}: {e}")
        return 0

    return 0


def find_orphaned_processes() -> dict:
    """
    Find orphaned VNC and browser processes.

    Returns:
        Dictionary mapping process names to list of PIDs
    """
    orphaned = {
        'xvfb': [],
        'x11vnc': [],
        'websockify': [],
        'chrome': []
    }

    try:
        if os.name == 'nt':  # Windows
            # On Windows, use tasklist
            result = subprocess.run(
                ['tasklist', '/FO', 'CSV', '/NH'],
                capture_output=True,
                text=True,
                timeout=10
            )

            if result.returncode == 0:
                for line in result.stdout.strip().split('\n'):
                    parts = line.strip('"').split('","')
                    if len(parts) >= 2:
                        name = parts[0].lower()
                        try:
                            pid = int(parts[1])
                        except ValueError:
                            continue

                        if 'chrome' in name or 'chromium' in name:
                            orphaned['chrome'].append(pid)
        else:  # Linux/Unix
            # Use ps to find processes
            result = subprocess.run(
                ['ps', 'aux'],
                capture_output=True,
                text=True,
                timeout=10
            )

            if result.returncode == 0:
                for line in result.stdout.split('\n')[1:]:  # Skip header
                    if not line.strip():
                        continue

                    parts = line.split()
                    if len(parts) < 11:
                        continue

                    pid = int(parts[1])
                    command = ' '.join(parts[10:])

                    # Check for VNC processes
                    if 'Xvfb' in command:
                        orphaned['xvfb'].append(pid)
                    elif 'x11vnc' in command:
                        orphaned['x11vnc'].append(pid)
                    elif 'websockify' in command or 'websockify.py' in command:
                        orphaned['websockify'].append(pid)
                    elif ('chrome' in command.lower() or 'chromium' in command.lower()) and 'playwright' in command:
                        orphaned['chrome'].append(pid)

    except Exception as e:
        logger.error(f"Error finding processes: {e}")

    return orphaned


def kill_process(pid: int, force: bool = False) -> bool:
    """
    Kill a process.

    Args:
        pid: Process ID
        force: Use SIGKILL instead of SIGTERM

    Returns:
        True if process was killed successfully
    """
    try:
        if os.name == 'nt':  # Windows
            subprocess.run(
                ['taskkill', '/F' if force else '', '/PID', str(pid)],
                capture_output=True,
                timeout=5
            )
        else:  # Linux/Unix
            import signal
            os.kill(pid, signal.SIGKILL if force else signal.SIGTERM)

        logger.info(f"Killed process {pid} ({'SIGKILL' if force else 'SIGTERM'})")
        return True

    except Exception as e:
        logger.debug(f"Could not kill process {pid}: {e}")
        return False


def cleanup_processes(orphaned: dict, max_age_hours: float = 24, dry_run: bool = False, force_all: bool = False):
    """
    Clean up orphaned processes.

    Args:
        orphaned: Dictionary of process names to PIDs
        max_age_hours: Only kill processes older than this many hours
        dry_run: If True, only show what would be cleaned
        force_all: If True, kill all matching processes regardless of age
    """
    total_found = sum(len(pids) for pids in orphaned.values())
    total_killed = 0
    total_skipped_age = 0

    logger.info(f"Found {total_found} orphaned processes")

    for process_name, pids in orphaned.items():
        if not pids:
            continue

        logger.info(f"\n{process_name}: {len(pids)} process(es)")

        for pid in pids:
            age_hours = get_process_age_hours(pid)

            if not force_all and age_hours < max_age_hours:
                logger.info(f"  PID {pid}: SKIP (age: {age_hours:.1f}h < {max_age_hours}h)")
                total_skipped_age += 1
                continue

            if dry_run:
                logger.info(f"  PID {pid}: WOULD KILL (age: {age_hours:.1f}h)")
                total_killed += 1
            else:
                if kill_process(pid):
                    logger.info(f"  PID {pid}: KILLED (age: {age_hours:.1f}h)")
                    total_killed += 1
                else:
                    logger.warning(f"  PID {pid}: FAILED TO KILL")

    logger.info(f"\nSummary:")
    logger.info(f"  Total found: {total_found}")
    logger.info(f"  {'Would kill' if dry_run else 'Killed'}: {total_killed}")
    logger.info(f"  Skipped (too young): {total_skipped_age}")


def cleanup_temp_directories(max_age_hours: float = 24, dry_run: bool = False):
    """
    Clean up old temporary session directories.

    Args:
        max_age_hours: Only clean directories older than this many hours
        dry_run: If True, only show what would be cleaned
    """
    logger.info("\nCleaning up temporary session directories...")

    vnc_sessions_dir = "/tmp/vnc_sessions"

    if not os.path.exists(vnc_sessions_dir):
        logger.info("  No temporary session directories found")
        return

    total_cleaned = 0
    total_skipped = 0
    cutoff_time = time.time() - (max_age_hours * 3600)

    try:
        for user_dir in os.listdir(vnc_sessions_dir):
            user_path = os.path.join(vnc_sessions_dir, user_dir)
            if not os.path.isdir(user_path):
                continue

            for session_dir in os.listdir(user_path):
                session_path = os.path.join(user_path, session_dir)
                if not os.path.isdir(session_path):
                    continue

                # Check directory age
                try:
                    mtime = os.path.getmtime(session_path)
                    age_hours = (time.time() - mtime) / 3600

                    if mtime < cutoff_time:
                        if dry_run:
                            logger.info(f"  WOULD REMOVE: {session_path} (age: {age_hours:.1f}h)")
                            total_cleaned += 1
                        else:
                            import shutil
                            shutil.rmtree(session_path)
                            logger.info(f"  REMOVED: {session_path} (age: {age_hours:.1f}h)")
                            total_cleaned += 1
                    else:
                        logger.debug(f"  SKIP: {session_path} (age: {age_hours:.1f}h)")
                        total_skipped += 1

                except Exception as e:
                    logger.warning(f"  ERROR: Could not clean {session_path}: {e}")

    except Exception as e:
        logger.error(f"Error cleaning temp directories: {e}")

    logger.info(f"  {'Would clean' if dry_run else 'Cleaned'}: {total_cleaned} directories")
    logger.info(f"  Skipped (too young): {total_skipped}")


def main():
    parser = argparse.ArgumentParser(
        description='Clean up orphaned VNC and browser processes'
    )
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Show what would be cleaned without actually cleaning'
    )
    parser.add_argument(
        '--max-age',
        type=float,
        default=24,
        help='Only clean processes/directories older than this many hours (default: 24)'
    )
    parser.add_argument(
        '--force',
        action='store_true',
        help='Clean all matching processes regardless of age'
    )

    args = parser.parse_args()

    logger.info("=" * 70)
    logger.info("ORPHANED PROCESS CLEANUP")
    logger.info("=" * 70)
    logger.info(f"Mode: {'DRY RUN' if args.dry_run else 'LIVE'}")
    logger.info(f"Max age: {args.max_age} hours" if not args.force else "Force: ALL processes")
    logger.info("")

    # Find and clean orphaned processes
    orphaned = find_orphaned_processes()
    cleanup_processes(orphaned, args.max_age, args.dry_run, args.force)

    # Clean temporary directories (Linux only)
    if os.name != 'nt':
        cleanup_temp_directories(args.max_age, args.dry_run)

    logger.info("\n" + "=" * 70)
    logger.info("CLEANUP COMPLETE")
    logger.info("=" * 70)


if __name__ == "__main__":
    main()
