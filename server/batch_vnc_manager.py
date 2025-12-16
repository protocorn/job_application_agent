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
from typing import Dict, List, Any, Optional
from datetime import datetime

logger = logging.getLogger(__name__)


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


class BatchVNCSession:
    """Represents a batch of job applications"""
    
    def __init__(self, batch_id: str, user_id: str, job_urls: List[str]):
        self.batch_id = batch_id
        self.user_id = user_id
        self.jobs: List[BatchVNCJob] = []
        self.created_at = datetime.now()
        self.status = 'processing'  # processing, completed, failed
        
        # Create jobs
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
            'jobs': [job.to_dict() for job in self.jobs]
        }


class BatchVNCManager:
    """Manages multiple batch VNC sessions"""
    
    def __init__(self):
        self.batches: Dict[str, BatchVNCSession] = {}
        self._lock = threading.RLock()  # Reentrant lock for thread safety
        
    def create_batch(self, user_id: str, job_urls: List[str]) -> str:
        """Create a new batch session"""
        batch_id = str(uuid.uuid4())
        batch = BatchVNCSession(batch_id, user_id, job_urls)
        
        with self._lock:
            self.batches[batch_id] = batch
        
        logger.info(f"📦 Created batch {batch_id} with {len(job_urls)} jobs for user {user_id}")
        return batch_id
    
    def get_batch(self, batch_id: str) -> Optional[BatchVNCSession]:
        """Get batch by ID"""
        with self._lock:
            return self.batches.get(batch_id)
    
    def get_user_batches(self, user_id: str) -> List[BatchVNCSession]:
        """Get all batches for a user"""
        with self._lock:
            return [batch for batch in self.batches.values() if batch.user_id == user_id]
    
    def update_job_status(self, batch_id: str, job_id: str, status: str, **kwargs):
        """Update status of a specific job in a batch"""
        with self._lock:
            batch = self.batches.get(batch_id)
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

