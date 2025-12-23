"""
Batch VNC Manager

Manages batch job applications with VNC sessions.
Processes jobs sequentially and keeps VNC sessions alive for user review.
"""

import logging
import asyncio
import time
import uuid
import threading
import json
import os
from typing import Dict, List, Any, Optional
from datetime import datetime

logger = logging.getLogger(__name__)

# Try to import Redis for persistence
try:
    import redis
    REDIS_AVAILABLE = True
except ImportError:
    REDIS_AVAILABLE = False
    logger.warning("Redis not available - batch data will not persist across restarts")


class BatchVNCJob:
    """Represents a single job in a batch"""
    
    def __init__(self, job_id: str, job_url: str, batch_id: str):
        self.job_id = job_id
        self.job_url = job_url
        self.batch_id = batch_id
        self.status = 'queued'  # queued, filling, ready_for_review, completed, failed
        self.progress = 0
        self.vnc_session_id = None
        self.vnc_port = None
        self.vnc_url = None
        self.error = None
        self.started_at = None
        self.completed_at = None
        self.submitted_by_user_at = None
        
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization"""
        return {
            'job_id': self.job_id,
            'job_url': self.job_url,
            'batch_id': self.batch_id,
            'status': self.status,
            'progress': self.progress,
            'vnc_session_id': self.vnc_session_id,
            'vnc_port': self.vnc_port,
            'vnc_url': self.vnc_url,
            'error': self.error,
            'started_at': self.started_at.isoformat() if self.started_at else None,
            'completed_at': self.completed_at.isoformat() if self.completed_at else None,
            'submitted_by_user_at': self.submitted_by_user_at.isoformat() if self.submitted_by_user_at else None
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'BatchVNCJob':
        """Create from dictionary (for Redis deserialization)"""
        job = cls(data['job_id'], data['job_url'], data['batch_id'])
        job.status = data.get('status', 'queued')
        job.progress = data.get('progress', 0)
        job.vnc_session_id = data.get('vnc_session_id')
        job.vnc_port = data.get('vnc_port')
        job.vnc_url = data.get('vnc_url')
        job.error = data.get('error')
        
        # Parse datetime strings
        if data.get('started_at'):
            job.started_at = datetime.fromisoformat(data['started_at'])
        if data.get('completed_at'):
            job.completed_at = datetime.fromisoformat(data['completed_at'])
        if data.get('submitted_by_user_at'):
            job.submitted_by_user_at = datetime.fromisoformat(data['submitted_by_user_at'])
        
        return job


class BatchVNCSession:
    """Represents a batch of job applications"""
    
    def __init__(self, batch_id: str, user_id: str, job_urls: List[str] = None):
        self.batch_id = batch_id
        self.user_id = user_id
        self.jobs: List[BatchVNCJob] = []
        self.created_at = datetime.now()
        self.status = 'processing'  # processing, completed, failed
        self.tailor_preferences = {}  # Store tailor preferences
        
        # Create jobs if URLs provided
        if job_urls:
            for idx, job_url in enumerate(job_urls):
                job_id = f"{batch_id}_job_{idx}"
                job = BatchVNCJob(job_id, job_url, batch_id)
                self.jobs.append(job)
    
    def get_job(self, job_id: str) -> Optional[BatchVNCJob]:
        """Get specific job by ID"""
        for job in self.jobs:
            if job.job_id == job_id:
                return job
        return None
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization"""
        completed_jobs = sum(1 for job in self.jobs if job.status == 'completed')
        ready_jobs = sum(1 for job in self.jobs if job.status == 'ready_for_review')
        filling_jobs = sum(1 for job in self.jobs if job.status == 'filling')
        failed_jobs = sum(1 for job in self.jobs if job.status == 'failed')
        
        return {
            'batch_id': self.batch_id,
            'user_id': self.user_id,
            'created_at': self.created_at.isoformat(),
            'status': self.status,
            'total_jobs': len(self.jobs),
            'completed_jobs': completed_jobs,
            'ready_for_review': ready_jobs,
            'filling_jobs': filling_jobs,
            'failed_jobs': failed_jobs,
            'jobs': [job.to_dict() for job in self.jobs],
            'tailor_preferences': self.tailor_preferences
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'BatchVNCSession':
        """Create from dictionary (for Redis deserialization)"""
        batch = cls(data['batch_id'], data['user_id'], job_urls=None)
        batch.created_at = datetime.fromisoformat(data['created_at'])
        batch.status = data.get('status', 'processing')
        batch.tailor_preferences = data.get('tailor_preferences', {})
        
        # Restore jobs
        for job_data in data.get('jobs', []):
            job = BatchVNCJob.from_dict(job_data)
            batch.jobs.append(job)
        
        return batch


class BatchVNCManager:
    """Manages multiple batch VNC sessions with Redis persistence"""
    
    def __init__(self):
        self.batches: Dict[str, BatchVNCSession] = {}
        self._lock = threading.RLock()  # Reentrant lock for thread safety
        
        # Setup Redis for persistence with timeout
        self.redis_client = None
        if REDIS_AVAILABLE:
            try:
                redis_url = os.getenv('REDIS_URL')
                if redis_url:
                    # Add socket timeout to prevent hanging (2 seconds)
                    self.redis_client = redis.from_url(
                        redis_url, 
                        decode_responses=True,
                        socket_timeout=2.0,  # Timeout for socket operations
                        socket_connect_timeout=2.0  # Timeout for connection
                    )
                    self.redis_client.ping()
                    logger.info("✅ Redis connected for batch persistence (2s timeout)")
                    self._load_batches_from_redis()
                else:
                    logger.warning("⚠️ REDIS_URL not configured - batches will not persist")
            except Exception as e:
                logger.error(f"❌ Failed to connect to Redis: {e}")
                self.redis_client = None
    
    def _save_batch_to_redis(self, batch: BatchVNCSession):
        """Save batch to Redis for persistence (non-blocking, failures won't stop agent)"""
        if not self.redis_client:
            return
        
        try:
            key = f"batch:{batch.batch_id}"
            data = json.dumps(batch.to_dict())
            # Set expiry to 24 hours with timeout protection
            self.redis_client.setex(key, 86400, data)
            logger.debug(f"💾 Saved batch {batch.batch_id} to Redis")
        except redis.exceptions.TimeoutError as e:
            logger.warning(f"⏱️ Redis timeout saving batch {batch.batch_id} - continuing anyway")
        except redis.exceptions.ConnectionError as e:
            logger.warning(f"📡 Redis connection error saving batch {batch.batch_id} - continuing anyway")
        except Exception as e:
            logger.warning(f"⚠️ Failed to save batch to Redis: {e} - continuing anyway")
    
    def _load_batch_from_redis(self, batch_id: str) -> Optional[BatchVNCSession]:
        """Load batch from Redis with timeout protection"""
        if not self.redis_client:
            return None
        
        try:
            key = f"batch:{batch_id}"
            data = self.redis_client.get(key)
            if data:
                batch_dict = json.loads(data)
                batch = BatchVNCSession.from_dict(batch_dict)
                logger.debug(f"📥 Loaded batch {batch_id} from Redis")
                return batch
        except redis.exceptions.TimeoutError as e:
            logger.warning(f"⏱️ Redis timeout loading batch {batch_id}")
        except redis.exceptions.ConnectionError as e:
            logger.warning(f"📡 Redis connection error loading batch {batch_id}")
        except Exception as e:
            logger.warning(f"⚠️ Failed to load batch from Redis: {e}")
        
        return None
    
    def _load_batches_from_redis(self):
        """Load all batches from Redis on startup"""
        if not self.redis_client:
            return
        
        try:
            keys = self.redis_client.keys("batch:*")
            loaded_count = 0
            for key in keys:
                batch_id = key.split(":", 1)[1]
                batch = self._load_batch_from_redis(batch_id)
                if batch:
                    self.batches[batch_id] = batch
                    loaded_count += 1
            
            if loaded_count > 0:
                logger.info(f"📦 Restored {loaded_count} batches from Redis")
        except Exception as e:
            logger.error(f"Failed to load batches from Redis: {e}")
        
    def create_batch(self, user_id: str, job_urls: List[str]) -> str:
        """Create a new batch session"""
        batch_id = str(uuid.uuid4())
        batch = BatchVNCSession(batch_id, user_id, job_urls)
        
        with self._lock:
            self.batches[batch_id] = batch
            self._save_batch_to_redis(batch)
        
        logger.info(f"📦 Created batch {batch_id} with {len(job_urls)} jobs for user {user_id}")
        return batch_id
    
    def get_batch(self, batch_id: str) -> Optional[BatchVNCSession]:
        """Get batch by ID (checks memory first, then Redis)"""
        with self._lock:
            # Try memory first
            batch = self.batches.get(batch_id)
            if batch:
                return batch
            
            # Try Redis if not in memory
            batch = self._load_batch_from_redis(batch_id)
            if batch:
                # Cache in memory
                self.batches[batch_id] = batch
                logger.info(f"📦 Restored batch {batch_id} from Redis to memory")
            
            return batch
    
    def get_user_batches(self, user_id: str) -> List[BatchVNCSession]:
        """Get all batches for a user"""
        with self._lock:
            return [batch for batch in self.batches.values() if batch.user_id == user_id]
    
    def update_job_status(self, batch_id: str, job_id: str, status: str, **kwargs):
        """Update status of a specific job in a batch"""
        with self._lock:
            # Get batch (will load from Redis if needed)
            batch = self.get_batch(batch_id)
            if not batch:
                logger.warning(f"⚠️ Batch {batch_id} not found when updating job {job_id}")
                return False
            
            job = batch.get_job(job_id)
            if not job:
                logger.warning(f"⚠️ Job {job_id} not found in batch {batch_id}")
                return False
            
            job.status = status
            
            # Update optional fields
            if 'progress' in kwargs:
                job.progress = kwargs['progress']
            if 'vnc_session_id' in kwargs:
                job.vnc_session_id = kwargs['vnc_session_id']
            if 'vnc_port' in kwargs:
                job.vnc_port = kwargs['vnc_port']
            if 'vnc_url' in kwargs:
                job.vnc_url = kwargs['vnc_url']
            if 'error' in kwargs:
                job.error = kwargs['error']
            
            # Update timestamps
            if status == 'filling' and not job.started_at:
                job.started_at = datetime.now()
            elif status in ['ready_for_review', 'failed']:
                job.completed_at = datetime.now()
            elif status == 'completed':
                job.submitted_by_user_at = datetime.now()
            
            # Persist to Redis
            self._save_batch_to_redis(batch)
            
            logger.info(f"✅ Updated job {job_id} status to {status} (progress: {job.progress}%)")
            return True
    
    def mark_job_submitted(self, batch_id: str, job_id: str) -> bool:
        """Mark job as submitted by user"""
        return self.update_job_status(batch_id, job_id, 'completed')
    
    def is_batch_complete(self, batch_id: str) -> bool:
        """Check if all jobs in batch are completed or failed"""
        with self._lock:
            batch = self.batches.get(batch_id)
            if not batch:
                return False
            
            for job in batch.jobs:
                if job.status not in ['completed', 'failed']:
                    return False
            
            return True


# Global batch manager instance
batch_vnc_manager = BatchVNCManager()

