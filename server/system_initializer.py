"""
System Initializer

Initializes all resource management, monitoring, and error recovery systems
"""

import logging
import atexit
from typing import Optional

logger = logging.getLogger(__name__)

# Global initialization flag
_initialized = False


def initialize_system():
    """
    Initialize all system components:
    - Resource Manager (thread pool, retry logic)
    - VNC Connection Pool
    - Health Monitor
    """
    global _initialized
    
    if _initialized:
        logger.warning("System already initialized")
        return
    
    try:
        logger.info("=" * 80)
        logger.info("🚀 Initializing Job Application Agent System")
        logger.info("=" * 80)
        
        # Initialize Resource Manager
        from resource_manager import get_resource_manager, shutdown_resource_manager
        resource_manager = get_resource_manager()
        logger.info("✅ Resource Manager initialized")
        logger.info(f"   Max workers: {resource_manager.max_workers}")
        logger.info(f"   Retry config: max_attempts={resource_manager.retry_handler.config.max_attempts}")
        logger.info(f"   Circuit breaker threshold: {resource_manager.circuit_breaker.config.failure_threshold}")
        
        # Initialize VNC Connection Pool
        from vnc_connection_pool import get_connection_pool, shutdown_connection_pool
        connection_pool = get_connection_pool()
        logger.info("✅ VNC Connection Pool initialized")
        logger.info(f"   Max connections: {connection_pool.max_total_connections}")
        logger.info(f"   Connection timeout: {connection_pool.connection_timeout}s")
        logger.info(f"   Idle timeout: {connection_pool.idle_timeout}s")
        
        # Initialize Health Monitor
        from health_monitor import get_health_monitor, shutdown_health_monitor
        health_monitor = get_health_monitor()
        logger.info("✅ Health Monitor initialized")
        logger.info(f"   Check interval: {health_monitor.check_interval}s")
        logger.info(f"   CPU threshold: {health_monitor.cpu_threshold}%")
        logger.info(f"   Memory threshold: {health_monitor.memory_threshold}%")
        
        # Register error recovery callbacks
        _register_recovery_callbacks(health_monitor)
        
        # Register shutdown handlers
        atexit.register(shutdown_system)
        
        _initialized = True
        
        logger.info("=" * 80)
        logger.info("✅ System initialization complete")
        logger.info("=" * 80)
        
    except Exception as e:
        logger.error(f"❌ System initialization failed: {e}")
        import traceback
        logger.error(traceback.format_exc())
        raise


def shutdown_system():
    """
    Gracefully shutdown all system components
    """
    global _initialized
    
    if not _initialized:
        return
    
    try:
        logger.info("=" * 80)
        logger.info("🛑 Shutting down Job Application Agent System")
        logger.info("=" * 80)
        
        # Get final stats before shutdown
        try:
            from resource_manager import get_resource_manager
            from vnc_connection_pool import get_connection_pool
            from health_monitor import get_health_monitor
            
            rm = get_resource_manager()
            pool = get_connection_pool()
            hm = get_health_monitor()
            
            logger.info("📊 Final Statistics:")
            logger.info(f"   Resource Manager: {rm.get_stats()}")
            logger.info(f"   Connection Pool: {pool.get_stats()}")
            logger.info(f"   Health: Total errors: {hm.total_errors}, Recoveries: {hm.total_recoveries}")
        except:
            pass
        
        # Shutdown Health Monitor
        from health_monitor import shutdown_health_monitor
        shutdown_health_monitor()
        logger.info("✅ Health Monitor shutdown")
        
        # Shutdown VNC Connection Pool
        from vnc_connection_pool import shutdown_connection_pool
        shutdown_connection_pool()
        logger.info("✅ VNC Connection Pool shutdown")
        
        # Shutdown Resource Manager
        from resource_manager import shutdown_resource_manager
        shutdown_resource_manager()
        logger.info("✅ Resource Manager shutdown")
        
        _initialized = False
        
        logger.info("=" * 80)
        logger.info("✅ System shutdown complete")
        logger.info("=" * 80)
        
    except Exception as e:
        logger.error(f"❌ Error during system shutdown: {e}")
        import traceback
        logger.error(traceback.format_exc())


def _register_recovery_callbacks(health_monitor):
    """Register error recovery callbacks"""
    
    def recover_connection_error(error):
        """Recover from connection errors"""
        logger.info(f"🔧 Attempting connection error recovery for session {error.session_id}")
        try:
            from resource_manager import get_resource_manager
            rm = get_resource_manager()
            
            # Cleanup event loops
            rm.cleanup_all_loops()
            
            logger.info("✅ Connection error recovery completed")
        except Exception as e:
            logger.error(f"Connection recovery failed: {e}")
    
    def recover_thread_exhaustion(error):
        """Recover from thread exhaustion"""
        logger.info("🔧 Attempting thread exhaustion recovery")
        try:
            from resource_manager import get_resource_manager
            rm = get_resource_manager()
            
            # Force cleanup of all event loops
            rm.cleanup_all_loops()
            
            # Log current stats
            stats = rm.get_stats()
            logger.info(f"   After cleanup: {stats}")
            
            logger.info("✅ Thread exhaustion recovery completed")
        except Exception as e:
            logger.error(f"Thread recovery failed: {e}")
    
    def recover_vnc_error(error):
        """Recover from VNC-specific errors"""
        logger.info(f"🔧 Attempting VNC error recovery for session {error.session_id}")
        try:
            from vnc_connection_pool import get_connection_pool
            pool = get_connection_pool()
            
            # Remove problematic session
            if error.session_id:
                pool.remove_session(error.session_id)
                logger.info(f"   Removed session {error.session_id} from pool")
            
            logger.info("✅ VNC error recovery completed")
        except Exception as e:
            logger.error(f"VNC recovery failed: {e}")
    
    # Register callbacks
    health_monitor.register_recovery_callback("connection_closed", recover_connection_error)
    health_monitor.register_recovery_callback("thread_exhaustion", recover_thread_exhaustion)
    health_monitor.register_recovery_callback("vnc_error", recover_vnc_error)
    
    logger.info("✅ Registered error recovery callbacks")


def get_system_status() -> dict:
    """
    Get comprehensive system status
    
    Returns:
        Dictionary with status of all components
    """
    if not _initialized:
        return {
            'initialized': False,
            'message': 'System not initialized'
        }
    
    try:
        from resource_manager import get_resource_manager
        from vnc_connection_pool import get_connection_pool
        from health_monitor import get_health_monitor
        
        rm = get_resource_manager()
        pool = get_connection_pool()
        hm = get_health_monitor()
        
        return {
            'initialized': True,
            'resource_manager': rm.get_stats(),
            'connection_pool': pool.get_stats(),
            'health': hm.get_health_report()
        }
    except Exception as e:
        logger.error(f"Error getting system status: {e}")
        return {
            'initialized': True,
            'error': str(e)
        }


# Convenience function for error reporting
def report_error(
    error_type: str,
    error_message: str,
    session_id: Optional[str] = None,
    recoverable: bool = True
):
    """
    Report an error to the health monitor
    
    Args:
        error_type: Type of error (e.g., 'connection_closed', 'thread_exhaustion')
        error_message: Detailed error message
        session_id: Optional session ID associated with error
        recoverable: Whether error is recoverable
    """
    try:
        if _initialized:
            from health_monitor import get_health_monitor
            hm = get_health_monitor()
            hm.record_error(error_type, error_message, session_id, recoverable)
    except Exception as e:
        logger.error(f"Failed to report error: {e}")
