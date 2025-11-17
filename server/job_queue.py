"""
Production Job Queue System for Job Application Agent
Handles concurrent users, priority scheduling, and resource management
"""

import asyncio
import json
import logging
import time
import uuid
from datetime import datetime, timedelta
from enum import Enum
from typing import Dict, Any, Optional, List, Callable
from dataclasses import dataclass
import redis
import threading
from concurrent.futures import ThreadPoolExecutor
import os

# Redis connection for job queue
# Support both local Redis and Upstash (with TLS)
REDIS_URL = os.getenv('REDIS_URL')

if REDIS_URL:
    # Use Redis URL with different DB for job queue
    import urllib.parse
    
    # Convert to rediss:// for TLS if using Upstash
    redis_url = REDIS_URL
    is_upstash = 'upstash.io' in redis_url
    if redis_url.startswith('redis://') and is_upstash:
        redis_url = redis_url.replace('redis://', 'rediss://', 1)
    
    parsed = urllib.parse.urlparse(redis_url)
    # Upstash free tier only supports DB 0, use DB 1 for local Redis
    db_number = 0 if is_upstash else 1
    redis_url_with_db = f"{parsed.scheme}://{parsed.netloc}/{db_number}"
    
    redis_client = redis.from_url(
        redis_url_with_db,
        decode_responses=True
    )
else:
    # Use individual connection parameters (for local Redis)
    redis_client = redis.Redis(
        host=os.getenv('REDIS_HOST', 'localhost'),
        port=int(os.getenv('REDIS_PORT', 6379)),
        db=1,  # Use different DB for job queue
        decode_responses=True
    )

class JobStatus(Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMEOUT = "timeout"

class JobPriority(Enum):
    CRITICAL = 1    # System maintenance, critical fixes
    HIGH = 2        # Premium users, urgent requests
    NORMAL = 3      # Regular users, standard requests
    LOW = 4         # Batch jobs, background tasks
    BULK = 5        # Mass operations, low priority

@dataclass
class JobRequest:
    """Job request data structure"""
    job_id: str
    user_id: str  # Changed to str to support UUID
    job_type: str
    priority: JobPriority
    payload: Dict[str, Any]
    created_at: datetime
    scheduled_at: Optional[datetime] = None
    timeout_seconds: int = 300  # 5 minutes default
    retry_count: int = 0
    max_retries: int = 3
    
    def to_dict(self) -> Dict[str, Any]:
        # JSON-serialize the payload first to ensure it's compatible
        try:
            payload_json = json.dumps(self.payload)
        except TypeError as e:
            # Provide helpful error message if payload contains non-serializable objects
            raise TypeError(f"Payload contains non-JSON-serializable data: {str(e)}. Ensure all objects (like Credentials) are converted to dicts before submitting.")

        # Build dict manually to avoid any tuple issues from asdict()
        data = {
            'job_id': self.job_id,
            'user_id': self.user_id,
            'job_type': self.job_type,
            'priority': self.priority.value,
            'payload': payload_json,
            'created_at': self.created_at.isoformat(),
            'timeout_seconds': self.timeout_seconds,
            'retry_count': self.retry_count,
            'max_retries': self.max_retries
        }

        # Add optional scheduled_at if present
        if self.scheduled_at:
            data['scheduled_at'] = self.scheduled_at.isoformat()

        return data
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'JobRequest':
        # Convert priority from string to int (Redis returns strings when decode_responses=True)
        priority_value = data['priority']
        if isinstance(priority_value, str):
            priority_value = int(priority_value)
        data['priority'] = JobPriority(priority_value)

        # Convert numeric fields from strings
        # Note: user_id is kept as string to support UUID
        if isinstance(data.get('timeout_seconds'), str):
            data['timeout_seconds'] = int(data['timeout_seconds'])
        if isinstance(data.get('retry_count'), str):
            data['retry_count'] = int(data['retry_count'])
        if isinstance(data.get('max_retries'), str):
            data['max_retries'] = int(data['max_retries'])

        data['created_at'] = datetime.fromisoformat(data['created_at'])
        if data.get('scheduled_at'):
            data['scheduled_at'] = datetime.fromisoformat(data['scheduled_at'])

        # Deserialize the payload from JSON string
        if isinstance(data.get('payload'), str):
            data['payload'] = json.loads(data['payload'])

        return cls(**data)

@dataclass
class JobResult:
    """Job execution result"""
    job_id: str
    status: JobStatus
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    execution_time: Optional[float] = None
    
    def to_dict(self) -> Dict[str, Any]:
        # Build dict manually to avoid any tuple issues from asdict()
        data = {
            'job_id': self.job_id,
            'status': self.status.value,
        }

        # Only add non-None values (Redis doesn't like None values)
        if self.result is not None:
            data['result'] = json.dumps(self.result)
        if self.error is not None:
            data['error'] = self.error
        if self.execution_time is not None:
            data['execution_time'] = str(self.execution_time)  # Convert to string for Redis
        if self.started_at:
            data['started_at'] = self.started_at.isoformat()
        if self.completed_at:
            data['completed_at'] = self.completed_at.isoformat()

        return data

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'JobResult':
        """Create JobResult from dictionary (from Redis)"""
        # Deserialize result from JSON string if present
        result = None
        if data.get('result'):
            try:
                result = json.loads(data['result'])
            except (json.JSONDecodeError, TypeError):
                result = data['result']  # Keep as-is if not JSON

        # Parse datetime fields
        started_at = None
        if data.get('started_at'):
            try:
                started_at = datetime.fromisoformat(data['started_at'])
            except (ValueError, TypeError):
                pass

        completed_at = None
        if data.get('completed_at'):
            try:
                completed_at = datetime.fromisoformat(data['completed_at'])
            except (ValueError, TypeError):
                pass

        # Parse execution_time
        execution_time = None
        if data.get('execution_time'):
            try:
                execution_time = float(data['execution_time'])
            except (ValueError, TypeError):
                pass

        return cls(
            job_id=data['job_id'],
            status=JobStatus(data['status']),  # JobStatus uses string values, no conversion needed
            result=result,
            error=data.get('error'),
            started_at=started_at,
            completed_at=completed_at,
            execution_time=execution_time
        )

class JobQueue:
    """
    Production job queue with priority scheduling and resource management
    """
    
    def __init__(self, max_workers: int = 5, max_concurrent_per_user: int = 2):
        self.max_workers = max_workers
        self.max_concurrent_per_user = max_concurrent_per_user
        self.logger = logging.getLogger(__name__)
        
        # Redis keys
        self.queue_key = "job_queue"
        self.active_jobs_key = "active_jobs"
        self.results_key = "job_results"
        self.user_jobs_key = "user_jobs"
        
        # Thread pool for job execution
        self.executor = ThreadPoolExecutor(max_workers=max_workers)
        
        # Job handlers registry
        self.job_handlers: Dict[str, Callable] = {}
        
        # Start background worker
        self.running = False
        self.worker_thread = None
    
    def register_handler(self, job_type: str, handler: Callable):
        """Register a job handler function"""
        self.job_handlers[job_type] = handler
        self.logger.info(f"Registered handler for job type: {job_type}")
    
    def submit_job(self,
                   user_id: str,  # Changed to str to support UUID
                   job_type: str,
                   payload: Dict[str, Any],
                   priority: JobPriority = JobPriority.NORMAL,
                   scheduled_at: Optional[datetime] = None,
                   timeout_seconds: int = 300) -> str:
        """
        Submit a job to the queue
        
        Returns:
            job_id: Unique job identifier
        """
        job_id = str(uuid.uuid4())
        
        # Check user's concurrent job limit
        user_active_jobs = self._get_user_active_jobs(user_id)
        if len(user_active_jobs) >= self.max_concurrent_per_user:
            raise Exception(f"User {user_id} has reached maximum concurrent jobs limit ({self.max_concurrent_per_user})")
        
        # Create job request
        job_request = JobRequest(
            job_id=job_id,
            user_id=user_id,
            job_type=job_type,
            priority=priority,
            payload=payload,
            created_at=datetime.utcnow(),
            scheduled_at=scheduled_at,
            timeout_seconds=timeout_seconds
        )
        
        # Add to queue with priority score
        priority_score = self._calculate_priority_score(job_request)
        
        try:
            # Store job data
            redis_client.hset(f"job_data:{job_id}", mapping=job_request.to_dict())
            
            # Add to priority queue
            redis_client.zadd(self.queue_key, {job_id: priority_score})
            
            # Track user jobs
            redis_client.sadd(f"{self.user_jobs_key}:{user_id}", job_id)
            
            # Set expiration (24 hours)
            redis_client.expire(f"job_data:{job_id}", 86400)
            redis_client.expire(f"{self.user_jobs_key}:{user_id}", 86400)
            
            self.logger.info(f"Job {job_id} submitted for user {user_id} with priority {priority.name}")
            
            return job_id
            
        except redis.RedisError as e:
            self.logger.error(f"Failed to submit job {job_id}: {e}")
            raise Exception(f"Failed to submit job: {e}")
    
    def get_job_status(self, job_id: str) -> Optional[JobResult]:
        """Get job status and result"""
        try:
            # Check if job is active
            if redis_client.sismember(self.active_jobs_key, job_id):
                return JobResult(job_id=job_id, status=JobStatus.RUNNING)
            
            # Check if job is in queue
            if redis_client.zscore(self.queue_key, job_id) is not None:
                return JobResult(job_id=job_id, status=JobStatus.QUEUED)
            
            # Check results
            result_data = redis_client.hgetall(f"{self.results_key}:{job_id}")
            if result_data:
                result = JobResult.from_dict(result_data)
                return result
            
            return None
            
        except Exception as e:
            self.logger.error(f"Error getting job status for {job_id}: {e}")
            return None
    
    def cancel_job(self, job_id: str, user_id: str) -> bool:
        """Cancel a job (only if owned by user)"""
        try:
            # Verify job ownership
            job_data = redis_client.hgetall(f"job_data:{job_id}")
            if not job_data or job_data.get('user_id') != str(user_id):
                return False
            
            # Remove from queue
            redis_client.zrem(self.queue_key, job_id)
            
            # If running, mark as cancelled (worker will handle)
            if redis_client.sismember(self.active_jobs_key, job_id):
                redis_client.hset(f"cancel_signal:{job_id}", "cancelled", "true")
                redis_client.expire(f"cancel_signal:{job_id}", 300)
            
            # Store cancelled result
            result = JobResult(
                job_id=job_id,
                status=JobStatus.CANCELLED,
                completed_at=datetime.utcnow()
            )
            redis_client.hset(f"{self.results_key}:{job_id}", mapping=result.to_dict())
            
            self.logger.info(f"Job {job_id} cancelled by user {user_id}")
            return True
            
        except Exception as e:
            self.logger.error(f"Error cancelling job {job_id}: {e}")
            return False
    
    def get_queue_stats(self) -> Dict[str, Any]:
        """Get queue statistics"""
        try:
            queue_size = redis_client.zcard(self.queue_key)
            active_jobs = redis_client.scard(self.active_jobs_key)
            
            # Get priority breakdown
            priority_breakdown = {}
            for priority in JobPriority:
                min_score = priority.value * 1000000
                max_score = (priority.value + 1) * 1000000 - 1
                count = redis_client.zcount(self.queue_key, min_score, max_score)
                priority_breakdown[priority.name] = count
            
            return {
                "queue_size": queue_size,
                "active_jobs": active_jobs,
                "max_workers": self.max_workers,
                "priority_breakdown": priority_breakdown,
                "worker_running": self.running
            }
            
        except Exception as e:
            self.logger.error(f"Error getting queue stats: {e}")
            return {"error": str(e)}
    
    def get_user_jobs(self, user_id: str) -> List[Dict[str, Any]]:
        """Get all jobs for a user"""
        try:
            job_ids = redis_client.smembers(f"{self.user_jobs_key}:{user_id}")
            jobs = []
            
            for job_id in job_ids:
                status = self.get_job_status(job_id)
                if status:
                    job_data = redis_client.hgetall(f"job_data:{job_id}")
                    if job_data:
                        jobs.append({
                            "job_id": job_id,
                            "job_type": job_data.get("job_type"),
                            "status": status.status.value,
                            "created_at": job_data.get("created_at"),
                            "priority": job_data.get("priority")
                        })
            
            return sorted(jobs, key=lambda x: x["created_at"], reverse=True)
            
        except Exception as e:
            self.logger.error(f"Error getting user jobs for {user_id}: {e}")
            return []
    
    def start_worker(self):
        """Start the background job worker"""
        if self.running:
            return
        
        self.running = True
        self.redis_error_count = 0  # Track consecutive Redis errors
        self.last_quota_warning = 0  # Track last quota warning time
        self.worker_thread = threading.Thread(target=self._worker_loop, daemon=True)
        self.worker_thread.start()
        self.logger.info("Job queue worker started")
    
    def stop_worker(self):
        """Stop the background job worker"""
        self.running = False
        if self.worker_thread:
            self.worker_thread.join(timeout=10)
        self.executor.shutdown(wait=True)
        self.logger.info("Job queue worker stopped")
    
    def _is_quota_exceeded_error(self, error_msg: str) -> bool:
        """Check if error is due to Redis quota being exceeded"""
        quota_keywords = [
            'max requests limit exceeded',
            'quota exceeded',
            'rate limit exceeded',
            'usage limit'
        ]
        error_lower = str(error_msg).lower()
        return any(keyword in error_lower for keyword in quota_keywords)
    
    def _worker_loop(self):
        """Main worker loop with exponential backoff for Redis errors"""
        consecutive_errors = 0
        max_backoff = 300  # Maximum 5 minutes backoff
        
        while self.running:
            try:
                # Check if we can process more jobs
                active_count = redis_client.scard(self.active_jobs_key)
                if active_count >= self.max_workers:
                    time.sleep(1)
                    continue
                
                # Get next job from queue
                job_data = redis_client.zpopmax(self.queue_key, 1)
                if not job_data:
                    time.sleep(1)
                    continue
                
                job_id, priority_score = job_data[0]
                
                # Load job details
                job_details = redis_client.hgetall(f"job_data:{job_id}")
                if not job_details:
                    continue
                
                job_request = JobRequest.from_dict(job_details)
                
                # Check if job is scheduled for future
                if job_request.scheduled_at and job_request.scheduled_at > datetime.utcnow():
                    # Put back in queue
                    redis_client.zadd(self.queue_key, {job_id: priority_score})
                    time.sleep(1)
                    continue
                
                # Mark as active
                redis_client.sadd(self.active_jobs_key, job_id)
                
                # Submit to thread pool
                future = self.executor.submit(self._execute_job, job_request)
                
                # Don't wait for completion here - let it run async
                
                # Reset error counter on successful iteration
                consecutive_errors = 0
                
            except Exception as e:
                error_msg = str(e)
                consecutive_errors += 1
                
                # Check if this is a quota exceeded error
                if self._is_quota_exceeded_error(error_msg):
                    # Log quota warning (throttled to once per hour)
                    current_time = time.time()
                    if current_time - self.last_quota_warning > 3600:
                        self.logger.error(
                            f"⚠️ REDIS QUOTA EXCEEDED ⚠️\n"
                            f"Upstash Redis has hit its monthly request limit.\n"
                            f"Job queue worker will retry with exponential backoff.\n"
                            f"Solutions:\n"
                            f"  1. Upgrade Upstash plan at https://upstash.com/\n"
                            f"  2. Wait for quota to reset (check Upstash dashboard)\n"
                            f"  3. Switch to local Redis for development\n"
                            f"Error: {error_msg}"
                        )
                        self.last_quota_warning = current_time
                    
                    # Use longer backoff for quota errors (exponential up to max)
                    backoff_time = min(max_backoff, 30 * (2 ** min(consecutive_errors - 1, 4)))
                    self.logger.warning(f"Quota exceeded. Backing off for {backoff_time} seconds...")
                    time.sleep(backoff_time)
                else:
                    # Regular error - log it
                    self.logger.error(f"Error in worker loop: {e}")
                    
                    # Exponential backoff for other errors (shorter)
                    backoff_time = min(60, 5 * (2 ** min(consecutive_errors - 1, 3)))
                    time.sleep(backoff_time)
    
    def _execute_job(self, job_request: JobRequest):
        """Execute a single job"""
        job_id = job_request.job_id
        start_time = time.time()
        
        try:
            self.logger.info(f"Starting job {job_id} (type: {job_request.job_type})")
            
            # Check if job handler exists
            if job_request.job_type not in self.job_handlers:
                raise Exception(f"No handler registered for job type: {job_request.job_type}")
            
            handler = self.job_handlers[job_request.job_type]
            
            # Execute with timeout
            result_data = None
            try:
                # Check for cancellation signal
                if redis_client.exists(f"cancel_signal:{job_id}"):
                    raise Exception("Job was cancelled")
                
                # Execute handler
                result_data = handler(job_request.payload)
                
                # Create success result
                result = JobResult(
                    job_id=job_id,
                    status=JobStatus.COMPLETED,
                    result=result_data,
                    started_at=datetime.utcfromtimestamp(start_time),
                    completed_at=datetime.utcnow(),
                    execution_time=time.time() - start_time
                )
                
            except Exception as e:
                # Create failure result
                result = JobResult(
                    job_id=job_id,
                    status=JobStatus.FAILED,
                    error=str(e),
                    started_at=datetime.utcfromtimestamp(start_time),
                    completed_at=datetime.utcnow(),
                    execution_time=time.time() - start_time
                )
                
                self.logger.error(f"Job {job_id} failed: {e}")
            
            # Store result
            try:
                result_dict = result.to_dict()
                redis_client.hset(f"{self.results_key}:{job_id}", mapping=result_dict)
                redis_client.expire(f"{self.results_key}:{job_id}", 86400)  # 24 hours
                self.logger.info(f"Job {job_id} completed with status {result.status.value}")
            except Exception as store_error:
                self.logger.error(f"Failed to store job result for {job_id}: {store_error}")
                self.logger.error(f"Result dict: {result_dict if 'result_dict' in locals() else 'N/A'}")

        except Exception as e:
            self.logger.error(f"Critical error executing job {job_id}: {e}")
            
        finally:
            # Always remove from active jobs
            redis_client.srem(self.active_jobs_key, job_id)
            
            # Clean up cancellation signal
            redis_client.delete(f"cancel_signal:{job_id}")
    
    def _calculate_priority_score(self, job_request: JobRequest) -> float:
        """Calculate priority score for job ordering"""
        # Base score from priority (lower = higher priority)
        base_score = job_request.priority.value * 1000000
        
        # Add timestamp for FIFO within same priority
        timestamp_score = int(job_request.created_at.timestamp())
        
        # Combine scores
        return base_score + timestamp_score
    
    def _get_user_active_jobs(self, user_id: str) -> List[str]:
        """Get list of active jobs for a user"""
        try:
            user_jobs = redis_client.smembers(f"{self.user_jobs_key}:{user_id}")
            active_jobs = []
            
            for job_id in user_jobs:
                if redis_client.sismember(self.active_jobs_key, job_id):
                    active_jobs.append(job_id)
                elif redis_client.zscore(self.queue_key, job_id) is not None:
                    active_jobs.append(job_id)
            
            return active_jobs
            
        except Exception as e:
            self.logger.error(f"Error getting user active jobs: {e}")
            return []

# Global job queue instance
job_queue = JobQueue(
    max_workers=int(os.getenv('JOB_QUEUE_MAX_WORKERS', 5)),
    max_concurrent_per_user=int(os.getenv('JOB_QUEUE_MAX_PER_USER', 2))
)

# Job handler decorators
def job_handler(job_type: str):
    """Decorator to register job handlers"""
    def decorator(func):
        job_queue.register_handler(job_type, func)
        return func
    return decorator

# Utility functions
def submit_resume_tailoring_job(user_id: str, payload: Dict[str, Any]) -> str:
    """Submit a resume tailoring job"""
    return job_queue.submit_job(
        user_id=user_id,
        job_type="resume_tailoring",
        payload=payload,
        priority=JobPriority.NORMAL,
        timeout_seconds=600  # 10 minutes for resume tailoring
    )

def submit_job_application_job(user_id: str, payload: Dict[str, Any]) -> str:
    """Submit a job application job"""
    return job_queue.submit_job(
        user_id=user_id,
        job_type="job_application",
        payload=payload,
        priority=JobPriority.NORMAL,
        timeout_seconds=1800  # 30 minutes for job application
    )

def submit_job_search_job(user_id: str, payload: Dict[str, Any]) -> str:
    """Submit a job search job"""
    return job_queue.submit_job(
        user_id=user_id,
        job_type="job_search",
        payload=payload,
        priority=JobPriority.LOW,
        timeout_seconds=300  # 5 minutes for job search
    )
