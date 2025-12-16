"""
Health Monitor and Error Recovery System

Monitors system health and provides error recovery mechanisms
"""

import logging
import threading
import time
import psutil
import os
from typing import Dict, List, Optional, Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum

logger = logging.getLogger(__name__)


class HealthStatus(Enum):
    """Health status levels"""
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"
    CRITICAL = "critical"


@dataclass
class HealthMetrics:
    """System health metrics"""
    timestamp: datetime
    cpu_percent: float
    memory_percent: float
    memory_available_mb: float
    active_threads: int
    active_connections: int
    error_rate: float
    status: HealthStatus
    
    def to_dict(self) -> Dict:
        return {
            'timestamp': self.timestamp.isoformat(),
            'cpu_percent': self.cpu_percent,
            'memory_percent': self.memory_percent,
            'memory_available_mb': self.memory_available_mb,
            'active_threads': self.active_threads,
            'active_connections': self.active_connections,
            'error_rate': self.error_rate,
            'status': self.status.value
        }


@dataclass
class ErrorRecord:
    """Record of an error occurrence"""
    timestamp: datetime
    error_type: str
    error_message: str
    session_id: Optional[str] = None
    recoverable: bool = True


class HealthMonitor:
    """
    Monitors system health and provides error recovery
    """
    
    def __init__(
        self,
        check_interval: int = 30,  # Check every 30 seconds
        error_window: int = 300,  # Track errors over 5 minutes
        max_error_rate: float = 0.1,  # 10% error rate threshold
        cpu_threshold: float = 80.0,  # 80% CPU threshold
        memory_threshold: float = 85.0,  # 85% memory threshold
    ):
        self.check_interval = check_interval
        self.error_window = error_window
        self.max_error_rate = max_error_rate
        self.cpu_threshold = cpu_threshold
        self.memory_threshold = memory_threshold
        
        # Health tracking
        self.current_status = HealthStatus.HEALTHY
        self.metrics_history: List[HealthMetrics] = []
        self.error_history: List[ErrorRecord] = []
        self._lock = threading.RLock()
        
        # Statistics
        self.total_errors = 0
        self.total_recoveries = 0
        self.consecutive_unhealthy_checks = 0
        
        # Monitoring thread
        self._monitor_thread = None
        self._shutdown = threading.Event()
        
        # Recovery callbacks
        self.recovery_callbacks: Dict[str, Callable] = {}
        
        logger.info("✅ Health Monitor initialized")
    
    def start(self):
        """Start the health monitoring thread"""
        if self._monitor_thread is not None:
            logger.warning("Health monitor already running")
            return
        
        self._shutdown.clear()
        self._monitor_thread = threading.Thread(
            target=self._monitor_loop,
            daemon=True,
            name="HealthMonitor"
        )
        self._monitor_thread.start()
        logger.info("🏥 Health monitor started")
    
    def stop(self):
        """Stop the health monitoring thread"""
        if self._monitor_thread is None:
            return
        
        logger.info("🛑 Stopping health monitor...")
        self._shutdown.set()
        
        if self._monitor_thread.is_alive():
            self._monitor_thread.join(timeout=10)
        
        self._monitor_thread = None
        logger.info("✅ Health monitor stopped")
    
    def record_error(
        self,
        error_type: str,
        error_message: str,
        session_id: Optional[str] = None,
        recoverable: bool = True
    ):
        """Record an error occurrence"""
        with self._lock:
            error = ErrorRecord(
                timestamp=datetime.now(),
                error_type=error_type,
                error_message=error_message,
                session_id=session_id,
                recoverable=recoverable
            )
            self.error_history.append(error)
            self.total_errors += 1
            
            # Trigger recovery if needed
            if recoverable:
                self._attempt_recovery(error)
            
            logger.warning(
                f"⚠️ Recorded error: {error_type} - {error_message} "
                f"(session: {session_id}, recoverable: {recoverable})"
            )
    
    def register_recovery_callback(self, error_type: str, callback: Callable):
        """Register a callback for error recovery"""
        with self._lock:
            self.recovery_callbacks[error_type] = callback
            logger.info(f"✅ Registered recovery callback for {error_type}")
    
    def get_current_metrics(self) -> HealthMetrics:
        """Get current system health metrics"""
        try:
            # CPU and memory
            cpu_percent = psutil.cpu_percent(interval=0.1)
            memory = psutil.virtual_memory()
            memory_percent = memory.percent
            memory_available_mb = memory.available / (1024 * 1024)
            
            # Thread count
            active_threads = threading.active_count()
            
            # Connection count (placeholder - should be injected from connection pool)
            active_connections = 0
            
            # Calculate error rate
            error_rate = self._calculate_error_rate()
            
            # Determine status
            status = self._determine_health_status(
                cpu_percent, memory_percent, error_rate
            )
            
            metrics = HealthMetrics(
                timestamp=datetime.now(),
                cpu_percent=cpu_percent,
                memory_percent=memory_percent,
                memory_available_mb=memory_available_mb,
                active_threads=active_threads,
                active_connections=active_connections,
                error_rate=error_rate,
                status=status
            )
            
            return metrics
            
        except Exception as e:
            logger.error(f"Error getting health metrics: {e}")
            return HealthMetrics(
                timestamp=datetime.now(),
                cpu_percent=0,
                memory_percent=0,
                memory_available_mb=0,
                active_threads=0,
                active_connections=0,
                error_rate=0,
                status=HealthStatus.UNHEALTHY
            )
    
    def get_health_report(self) -> Dict:
        """Get comprehensive health report"""
        with self._lock:
            current_metrics = self.get_current_metrics()
            
            # Recent errors
            recent_errors = [
                {
                    'timestamp': e.timestamp.isoformat(),
                    'type': e.error_type,
                    'message': e.error_message,
                    'session_id': e.session_id,
                    'recoverable': e.recoverable
                }
                for e in self.error_history[-10:]  # Last 10 errors
            ]
            
            # Historical metrics
            recent_metrics = [m.to_dict() for m in self.metrics_history[-20:]]
            
            return {
                'current_status': self.current_status.value,
                'current_metrics': current_metrics.to_dict(),
                'total_errors': self.total_errors,
                'total_recoveries': self.total_recoveries,
                'consecutive_unhealthy_checks': self.consecutive_unhealthy_checks,
                'recent_errors': recent_errors,
                'recent_metrics': recent_metrics,
                'thresholds': {
                    'cpu_percent': self.cpu_threshold,
                    'memory_percent': self.memory_threshold,
                    'error_rate': self.max_error_rate
                }
            }
    
    def _monitor_loop(self):
        """Background monitoring loop"""
        logger.info("🏥 Health monitoring loop started")
        
        while not self._shutdown.is_set():
            try:
                # Get current metrics
                metrics = self.get_current_metrics()
                
                with self._lock:
                    # Store metrics
                    self.metrics_history.append(metrics)
                    
                    # Trim history (keep last hour)
                    max_history = 3600 // self.check_interval
                    if len(self.metrics_history) > max_history:
                        self.metrics_history = self.metrics_history[-max_history:]
                    
                    # Update current status
                    self.current_status = metrics.status
                    
                    # Track consecutive unhealthy checks
                    if metrics.status in [HealthStatus.UNHEALTHY, HealthStatus.CRITICAL]:
                        self.consecutive_unhealthy_checks += 1
                    else:
                        self.consecutive_unhealthy_checks = 0
                    
                    # Log status changes
                    if metrics.status != HealthStatus.HEALTHY:
                        logger.warning(
                            f"⚠️ System health: {metrics.status.value} "
                            f"(CPU: {metrics.cpu_percent:.1f}%, "
                            f"Memory: {metrics.memory_percent:.1f}%, "
                            f"Errors: {metrics.error_rate:.2%})"
                        )
                    
                    # Trigger emergency recovery if critically unhealthy
                    if self.consecutive_unhealthy_checks >= 3:
                        logger.error("🚨 System critically unhealthy - triggering emergency recovery")
                        self._emergency_recovery()
                
                # Sleep until next check
                self._shutdown.wait(self.check_interval)
                
            except Exception as e:
                logger.error(f"Error in health monitoring loop: {e}")
                time.sleep(self.check_interval)
    
    def _calculate_error_rate(self) -> float:
        """Calculate current error rate"""
        with self._lock:
            if not self.error_history:
                return 0.0
            
            # Count errors in window
            cutoff = datetime.now() - timedelta(seconds=self.error_window)
            recent_errors = sum(1 for e in self.error_history if e.timestamp > cutoff)
            
            # Calculate rate (errors per second)
            return recent_errors / self.error_window
    
    def _determine_health_status(
        self,
        cpu_percent: float,
        memory_percent: float,
        error_rate: float
    ) -> HealthStatus:
        """Determine overall health status"""
        # Critical thresholds
        if (cpu_percent > 95 or 
            memory_percent > 95 or 
            error_rate > self.max_error_rate * 3):
            return HealthStatus.CRITICAL
        
        # Unhealthy thresholds
        if (cpu_percent > self.cpu_threshold or 
            memory_percent > self.memory_threshold or 
            error_rate > self.max_error_rate):
            return HealthStatus.UNHEALTHY
        
        # Degraded thresholds
        if (cpu_percent > self.cpu_threshold * 0.8 or 
            memory_percent > self.memory_threshold * 0.8 or 
            error_rate > self.max_error_rate * 0.5):
            return HealthStatus.DEGRADED
        
        return HealthStatus.HEALTHY
    
    def _attempt_recovery(self, error: ErrorRecord):
        """Attempt to recover from an error"""
        try:
            callback = self.recovery_callbacks.get(error.error_type)
            if callback:
                logger.info(f"🔧 Attempting recovery for {error.error_type}")
                callback(error)
                self.total_recoveries += 1
                logger.info(f"✅ Recovery successful for {error.error_type}")
            else:
                logger.debug(f"No recovery callback registered for {error.error_type}")
        except Exception as e:
            logger.error(f"Recovery failed for {error.error_type}: {e}")
    
    def _emergency_recovery(self):
        """Emergency recovery procedures"""
        logger.warning("🚨 Initiating emergency recovery procedures...")
        
        try:
            # Import resource manager
            from resource_manager import get_resource_manager
            from vnc_connection_pool import get_connection_pool
            
            # Get stats
            rm = get_resource_manager()
            pool = get_connection_pool()
            
            rm_stats = rm.get_stats()
            pool_stats = pool.get_stats()
            
            logger.info(f"📊 Current stats before recovery:")
            logger.info(f"   Resource Manager: {rm_stats}")
            logger.info(f"   Connection Pool: {pool_stats}")
            
            # Cleanup idle event loops
            rm.cleanup_all_loops()
            
            # Reset circuit breaker if stuck open
            if rm.circuit_breaker.state.value == "open":
                logger.info("🔧 Resetting circuit breaker")
                rm.circuit_breaker.state = rm.circuit_breaker.state.CLOSED
                rm.circuit_breaker.failure_count = 0
            
            logger.info("✅ Emergency recovery completed")
            
        except Exception as e:
            logger.error(f"Emergency recovery failed: {e}")


# Global health monitor instance
_global_health_monitor: Optional[HealthMonitor] = None
_monitor_lock = threading.Lock()


def get_health_monitor() -> HealthMonitor:
    """Get or create the global health monitor"""
    global _global_health_monitor
    
    with _monitor_lock:
        if _global_health_monitor is None:
            _global_health_monitor = HealthMonitor(
                check_interval=30,
                error_window=300,
                max_error_rate=0.1,
                cpu_threshold=80.0,
                memory_threshold=85.0
            )
            _global_health_monitor.start()
        
        return _global_health_monitor


def shutdown_health_monitor():
    """Shutdown the global health monitor"""
    global _global_health_monitor
    
    with _monitor_lock:
        if _global_health_monitor is not None:
            _global_health_monitor.stop()
            _global_health_monitor = None
