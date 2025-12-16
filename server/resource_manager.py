"""
Resource Manager for Thread Pool, Connection Retry, and Cleanup

Manages system resources to prevent thread exhaustion, connection failures, and leaks.
"""

import logging
import asyncio
import threading
import time
import functools
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from typing import Optional, Callable, Any, Dict
from contextlib import contextmanager
from dataclasses import dataclass
from enum import Enum

logger = logging.getLogger(__name__)


class CircuitState(Enum):
    """Circuit breaker states"""
    CLOSED = "closed"      # Normal operation
    OPEN = "open"          # Failing, reject requests
    HALF_OPEN = "half_open"  # Testing if service recovered


@dataclass
class RetryConfig:
    """Configuration for retry logic"""
    max_attempts: int = 3
    initial_delay: float = 1.0
    max_delay: float = 60.0
    exponential_base: float = 2.0
    timeout: Optional[float] = 30.0


@dataclass
class CircuitBreakerConfig:
    """Configuration for circuit breaker"""
    failure_threshold: int = 5
    success_threshold: int = 2
    timeout: float = 60.0


class CircuitBreaker:
    """Circuit breaker pattern to prevent cascading failures"""
    
    def __init__(self, config: CircuitBreakerConfig):
        self.config = config
        self.state = CircuitState.CLOSED
        self.failure_count = 0
        self.success_count = 0
        self.last_failure_time = 0
        self._lock = threading.Lock()
    
    def call(self, func: Callable, *args, **kwargs) -> Any:
        """Execute function with circuit breaker protection"""
        with self._lock:
            if self.state == CircuitState.OPEN:
                # Check if timeout has passed
                if time.time() - self.last_failure_time >= self.config.timeout:
                    logger.info("Circuit breaker: Moving to HALF_OPEN state")
                    self.state = CircuitState.HALF_OPEN
                else:
                    raise Exception("Circuit breaker is OPEN - service unavailable")
        
        try:
            result = func(*args, **kwargs)
            self._on_success()
            return result
        except Exception as e:
            self._on_failure()
            raise
    
    def _on_success(self):
        """Handle successful execution"""
        with self._lock:
            if self.state == CircuitState.HALF_OPEN:
                self.success_count += 1
                if self.success_count >= self.config.success_threshold:
                    logger.info("Circuit breaker: Moving to CLOSED state")
                    self.state = CircuitState.CLOSED
                    self.failure_count = 0
                    self.success_count = 0
            elif self.state == CircuitState.CLOSED:
                self.failure_count = 0
    
    def _on_failure(self):
        """Handle failed execution"""
        with self._lock:
            self.failure_count += 1
            self.last_failure_time = time.time()
            
            if self.state == CircuitState.HALF_OPEN:
                logger.warning("Circuit breaker: Moving to OPEN state (half-open failure)")
                self.state = CircuitState.OPEN
                self.success_count = 0
            elif self.failure_count >= self.config.failure_threshold:
                logger.warning(f"Circuit breaker: Moving to OPEN state ({self.failure_count} failures)")
                self.state = CircuitState.OPEN


class RetryHandler:
    """Handles retry logic with exponential backoff"""
    
    def __init__(self, config: RetryConfig):
        self.config = config
    
    def execute(self, func: Callable, *args, **kwargs) -> Any:
        """Execute function with retry logic"""
        last_exception = None
        
        for attempt in range(self.config.max_attempts):
            try:
                return func(*args, **kwargs)
            except Exception as e:
                last_exception = e
                
                if attempt < self.config.max_attempts - 1:
                    delay = min(
                        self.config.initial_delay * (self.config.exponential_base ** attempt),
                        self.config.max_delay
                    )
                    logger.warning(
                        f"Attempt {attempt + 1}/{self.config.max_attempts} failed: {e}. "
                        f"Retrying in {delay:.2f}s..."
                    )
                    time.sleep(delay)
                else:
                    logger.error(f"All {self.config.max_attempts} attempts failed: {e}")
        
        raise last_exception
    
    async def execute_async(self, func: Callable, *args, **kwargs) -> Any:
        """Execute async function with retry logic"""
        last_exception = None
        
        for attempt in range(self.config.max_attempts):
            try:
                return await func(*args, **kwargs)
            except Exception as e:
                last_exception = e
                
                if attempt < self.config.max_attempts - 1:
                    delay = min(
                        self.config.initial_delay * (self.config.exponential_base ** attempt),
                        self.config.max_delay
                    )
                    logger.warning(
                        f"Attempt {attempt + 1}/{self.config.max_attempts} failed: {e}. "
                        f"Retrying in {delay:.2f}s..."
                    )
                    await asyncio.sleep(delay)
                else:
                    logger.error(f"All {self.config.max_attempts} attempts failed: {e}")
        
        raise last_exception


class ResourceManager:
    """
    Manages system resources including:
    - Thread pool for limited concurrent operations
    - Event loop management
    - Connection retry logic
    - Resource cleanup
    """
    
    def __init__(
        self,
        max_workers: int = 10,
        retry_config: Optional[RetryConfig] = None,
        circuit_breaker_config: Optional[CircuitBreakerConfig] = None
    ):
        self.max_workers = max_workers
        self.executor = ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix="ResourceManager"
        )
        self.retry_handler = RetryHandler(retry_config or RetryConfig())
        self.circuit_breaker = CircuitBreaker(
            circuit_breaker_config or CircuitBreakerConfig()
        )
        self.active_loops: Dict[int, asyncio.AbstractEventLoop] = {}
        self._cleanup_lock = threading.Lock()
        
        # Track active resources
        self.active_threads = 0
        self.completed_threads = 0
        
        logger.info(f"✅ ResourceManager initialized with {max_workers} max workers")
    
    def submit_task(self, func: Callable, *args, **kwargs) -> Any:
        """
        Submit a task to the thread pool with automatic retry and circuit breaker
        
        Args:
            func: Function to execute
            *args, **kwargs: Arguments to pass to function
            
        Returns:
            Future object representing the execution
        """
        def wrapped_func():
            try:
                self.active_threads += 1
                logger.debug(f"Active threads: {self.active_threads}/{self.max_workers}")
                
                # Execute with circuit breaker and retry
                return self.circuit_breaker.call(
                    self.retry_handler.execute,
                    func, *args, **kwargs
                )
            finally:
                self.active_threads -= 1
                self.completed_threads += 1
        
        return self.executor.submit(wrapped_func)
    
    @contextmanager
    def managed_event_loop(self):
        """
        Context manager for event loop lifecycle
        
        Usage:
            with resource_manager.managed_event_loop() as loop:
                loop.run_until_complete(my_async_func())
        """
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        thread_id = threading.get_ident()
        
        try:
            with self._cleanup_lock:
                self.active_loops[thread_id] = loop
            
            logger.debug(f"Created event loop for thread {thread_id}")
            yield loop
            
        finally:
            try:
                # Cancel all pending tasks
                pending = asyncio.all_tasks(loop)
                for task in pending:
                    task.cancel()
                
                # Wait for tasks to complete cancellation
                if pending:
                    loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
                
                # Close the loop
                loop.close()
                logger.debug(f"Closed event loop for thread {thread_id}")
                
            except Exception as e:
                logger.error(f"Error cleaning up event loop: {e}")
            finally:
                with self._cleanup_lock:
                    self.active_loops.pop(thread_id, None)
    
    def cleanup_all_loops(self):
        """Emergency cleanup of all active event loops"""
        with self._cleanup_lock:
            for thread_id, loop in list(self.active_loops.items()):
                try:
                    if not loop.is_closed():
                        loop.call_soon_threadsafe(loop.stop)
                        loop.close()
                    logger.info(f"Cleaned up event loop for thread {thread_id}")
                except Exception as e:
                    logger.error(f"Error cleaning up loop for thread {thread_id}: {e}")
            
            self.active_loops.clear()
    
    def shutdown(self, wait: bool = True, timeout: Optional[float] = 30.0):
        """
        Shutdown the resource manager and cleanup all resources
        
        Args:
            wait: Whether to wait for pending tasks
            timeout: Maximum time to wait (seconds)
        """
        logger.info("🛑 Shutting down ResourceManager...")
        
        try:
            # Cleanup event loops first
            self.cleanup_all_loops()
            
            # Shutdown thread pool
            self.executor.shutdown(wait=wait, timeout=timeout)
            
            logger.info(f"✅ ResourceManager shutdown complete")
            logger.info(f"   Total threads executed: {self.completed_threads}")
            
        except Exception as e:
            logger.error(f"Error during ResourceManager shutdown: {e}")
    
    def get_stats(self) -> Dict[str, Any]:
        """Get current resource usage statistics"""
        return {
            'max_workers': self.max_workers,
            'active_threads': self.active_threads,
            'completed_threads': self.completed_threads,
            'active_event_loops': len(self.active_loops),
            'circuit_breaker_state': self.circuit_breaker.state.value,
            'circuit_breaker_failures': self.circuit_breaker.failure_count
        }


# Decorators for easy use
def with_retry(config: Optional[RetryConfig] = None):
    """Decorator to add retry logic to a function"""
    retry_handler = RetryHandler(config or RetryConfig())
    
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            return retry_handler.execute(func, *args, **kwargs)
        
        @functools.wraps(func)
        async def async_wrapper(*args, **kwargs):
            return await retry_handler.execute_async(func, *args, **kwargs)
        
        return async_wrapper if asyncio.iscoroutinefunction(func) else wrapper
    
    return decorator


# Global resource manager instance
_global_resource_manager: Optional[ResourceManager] = None
_manager_lock = threading.Lock()


def get_resource_manager() -> ResourceManager:
    """Get or create the global resource manager instance"""
    global _global_resource_manager
    
    with _manager_lock:
        if _global_resource_manager is None:
            _global_resource_manager = ResourceManager(
                max_workers=10,  # Limit concurrent browser sessions
                retry_config=RetryConfig(
                    max_attempts=3,
                    initial_delay=2.0,
                    max_delay=30.0
                ),
                circuit_breaker_config=CircuitBreakerConfig(
                    failure_threshold=5,
                    timeout=120.0
                )
            )
        
        return _global_resource_manager


def shutdown_resource_manager():
    """Shutdown the global resource manager"""
    global _global_resource_manager
    
    with _manager_lock:
        if _global_resource_manager is not None:
            _global_resource_manager.shutdown()
            _global_resource_manager = None
