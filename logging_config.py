"""
Centralized logging configuration for Job Application Agent
This module sets up file logging for all components of the job application system.
Handles both standard Python logging and loguru logging.
"""

import logging
import os
import sys
from datetime import datetime
from pathlib import Path

try:
    from loguru import logger as loguru_logger
    LOGURU_AVAILABLE = True
except ImportError:
    LOGURU_AVAILABLE = False


def setup_file_logging(log_level=logging.DEBUG, console_logging=True):
    """
    Set up file logging for the job application agent.
    
    Args:
        log_level: Logging level (default: DEBUG)
        console_logging: Whether to also log to console (default: True)
    """
    # Create logs directory if it doesn't exist
    logs_dir = Path("logs")
    logs_dir.mkdir(exist_ok=True)
    
    # Create log filename with timestamp
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_filename = logs_dir / f"job_application_agent_{timestamp}.log"
    
    # Configure root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)
    
    # Clear existing handlers to avoid duplicates
    root_logger.handlers.clear()
    
    # Create file handler
    file_handler = logging.FileHandler(log_filename, mode='a', encoding='utf-8')
    file_handler.setLevel(log_level)
    
    # Create formatter
    formatter = logging.Formatter(
        '%(asctime)s | %(levelname)s | %(name)s | %(funcName)s:%(lineno)d | %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    file_handler.setFormatter(formatter)
    
    # Add file handler to root logger
    root_logger.addHandler(file_handler)
    
    # Optionally add console handler
    if console_logging:
        console_handler = logging.StreamHandler()
        console_handler.setLevel(log_level)
        console_handler.setFormatter(formatter)
        root_logger.addHandler(console_handler)
    
    # Configure loguru logging if available (used by many components)
    if LOGURU_AVAILABLE:
        # Remove default loguru handler
        loguru_logger.remove()
        
        # Add file handler for loguru
        loguru_logger.add(
            log_filename,
            format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {level:<8} | {name}:{function}:{line} - {message}",
            level="DEBUG",
            rotation=None,
            retention=None,
            encoding='utf-8'
        )
        
        # Add console handler for loguru if requested
        if console_logging:
            loguru_logger.add(
                sys.stderr,
                format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {level:<8} | {name}:{function}:{line} - {message}",
                level="DEBUG"
            )
    
    # Log the setup
    logging.info(f"File logging configured. Logs will be saved to: {log_filename}")
    if LOGURU_AVAILABLE:
        loguru_logger.info(f"Loguru logging configured. Logs will be saved to: {log_filename}")
    
    return str(log_filename)


def setup_daily_log_rotation():
    """
    Set up daily log rotation with automatic cleanup of old logs.
    Keeps logs for 30 days by default.
    """
    import logging.handlers
    
    logs_dir = Path("logs")
    logs_dir.mkdir(exist_ok=True)
    
    # Create rotating file handler
    log_filename = logs_dir / "job_application_agent.log"
    
    # Set up rotating file handler (rotates daily, keeps 30 days)
    rotating_handler = logging.handlers.TimedRotatingFileHandler(
        filename=log_filename,
        when='midnight',
        interval=1,
        backupCount=30,
        encoding='utf-8'
    )
    
    # Create formatter
    formatter = logging.Formatter(
        '%(asctime)s | %(levelname)s | %(name)s | %(funcName)s:%(lineno)d | %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    rotating_handler.setFormatter(formatter)
    
    # Configure root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.DEBUG)
    root_logger.handlers.clear()
    root_logger.addHandler(rotating_handler)
    
    # Add console handler for immediate feedback
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.DEBUG)
    console_handler.setFormatter(formatter)
    root_logger.addHandler(console_handler)
    
    # Configure loguru logging if available
    if LOGURU_AVAILABLE:
        # Remove default loguru handler
        loguru_logger.remove()
        
        # Add rotating file handler for loguru
        loguru_logger.add(
            log_filename,
            format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {level:<8} | {name}:{function}:{line} - {message}",
            level="DEBUG",
            rotation="00:00",  # Rotate at midnight
            retention="30 days",  # Keep 30 days
            encoding='utf-8'
        )
        
        # Add console handler for loguru
        loguru_logger.add(
            sys.stderr,
            format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {level:<8} | {name}:{function}:{line} - {message}",
            level="DEBUG"
        )
    
    logging.info(f"Daily log rotation configured. Logs saved to: {log_filename}")
    if LOGURU_AVAILABLE:
        loguru_logger.info(f"Loguru daily log rotation configured. Logs saved to: {log_filename}")
    
    return str(log_filename)


def get_current_log_file():
    """
    Get the path to the current log file.
    """
    logs_dir = Path("logs")
    if not logs_dir.exists():
        return None
    
    # Find the most recent log file
    log_files = list(logs_dir.glob("job_application_agent_*.log"))
    if not log_files:
        # Check for rotating log file
        rotating_log = logs_dir / "job_application_agent.log"
        if rotating_log.exists():
            return str(rotating_log)
        return None
    
    # Return the most recent log file
    most_recent = max(log_files, key=lambda f: f.stat().st_mtime)
    return str(most_recent)


def cleanup_old_logs(days_to_keep=30):
    """
    Clean up log files older than specified days.
    """
    import time
    
    logs_dir = Path("logs")
    if not logs_dir.exists():
        return
    
    cutoff_time = time.time() - (days_to_keep * 24 * 60 * 60)
    
    for log_file in logs_dir.glob("job_application_agent_*.log"):
        if log_file.stat().st_mtime < cutoff_time:
            try:
                log_file.unlink()
                print(f"Deleted old log file: {log_file}")
            except Exception as e:
                print(f"Failed to delete {log_file}: {e}")
