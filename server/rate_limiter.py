"""
Production-Grade Rate Limiter for Job Application Agent
Handles API quotas, user limits, and concurrent request management
"""

import time
import redis
import json
import logging
from typing import Dict, Any, Optional, Tuple
from datetime import datetime, timedelta
from functools import wraps
from flask import request, jsonify, g
from dataclasses import dataclass
import os

# Redis connection for distributed rate limiting
# Support both local Redis and Upstash (with TLS)
REDIS_URL = os.getenv('REDIS_URL')

if REDIS_URL:
    # Use Redis URL (for Upstash with TLS)
    # Convert to rediss:// for TLS if using Upstash
    redis_url = REDIS_URL
    if redis_url.startswith('redis://') and 'upstash.io' in redis_url:
        redis_url = redis_url.replace('redis://', 'rediss://', 1)
    
    redis_client = redis.from_url(
        redis_url,
        decode_responses=True
    )
else:
    # Use individual connection parameters (for local Redis)
    redis_client = redis.Redis(
        host=os.getenv('REDIS_HOST', 'localhost'),
        port=int(os.getenv('REDIS_PORT', 6379)),
        db=0,
        decode_responses=True
    )

@dataclass
class RateLimit:
    """Rate limit configuration"""
    requests: int
    window: int  # seconds
    burst: int = None  # burst allowance

class ProductionRateLimiter:
    """
    Production-grade rate limiter with Redis backend
    Supports per-user, per-endpoint, and global rate limiting
    """

    # Admin users (unlimited rate limits)
    ADMIN_EMAILS = [
        'chordiasahil24@gmail.com',
        "chordiasahil2412@gmail.com"
    ]
    
    # Track Redis availability
    redis_available = True
    last_redis_error_time = 0
    redis_error_backoff = 60  # seconds to wait before retrying Redis after quota error

    # API Quota Limits (per day unless specified)
    LIMITS = {
        # Gemini API limits (conservative estimates)
        'gemini_requests_per_minute': RateLimit(8, 60),  # 8 req/min (buffer for 10 req/min limit)
        'gemini_requests_per_day': RateLimit(1000, 86400),  # 1000 req/day
        
        # User-specific limits
        'resume_tailoring_per_user_per_day': RateLimit(5, 86400),
        'job_applications_per_user_per_day': RateLimit(20, 86400),
        'job_search_per_user_per_day': RateLimit(5, 86400),
        
        # Global system limits
        'concurrent_tailoring_sessions': RateLimit(3, 1),  # Max 3 concurrent sessions
        'concurrent_job_applications': RateLimit(5, 1),   # Max 5 concurrent applications
        
        # API endpoint limits
        'api_requests_per_user_per_minute': RateLimit(30, 60),
        'api_requests_per_ip_per_minute': RateLimit(100, 60),
    }
    
    def __init__(self):
        self.logger = logging.getLogger(__name__)

    def is_admin_user(self, user_id: str) -> bool:
        """
        Check if a user is an admin (unlimited rate limits)

        Args:
            user_id: User ID (UUID string) to check

        Returns:
            True if user is admin, False otherwise
        """
        try:
            # Import here to avoid circular imports
            from database_config import get_db, get_user_by_id
            import uuid

            # Get database session
            db = next(get_db())
            try:
                # Convert string user_id to UUID
                user_uuid = uuid.UUID(str(user_id))

                # Look up user by ID
                user = get_user_by_id(db, user_uuid)

                if user and user.email:
                    return user.email in self.ADMIN_EMAILS

                return False
            finally:
                db.close()

        except Exception as e:
            self.logger.error(f"Error checking admin status for user {user_id}: {e}")
            return False

    def _get_key(self, limit_type: str, identifier: str) -> str:
        """Generate Redis key for rate limit tracking"""
        return f"rate_limit:{limit_type}:{identifier}"
    
    def _get_window_key(self, limit_type: str, identifier: str, window_start: int) -> str:
        """Generate Redis key for sliding window"""
        return f"rate_limit_window:{limit_type}:{identifier}:{window_start}"
    
    def check_limit(self, limit_type: str, identifier: str) -> Tuple[bool, Dict[str, Any]]:
        """
        Check if request is within rate limit

        Args:
            limit_type: Type of rate limit to check
            identifier: User ID (as string) or IP address

        Returns:
            (allowed: bool, info: dict)
        """
        if limit_type not in self.LIMITS:
            return True, {"error": "Unknown limit type"}

        # If Redis quota exceeded recently, fail open (allow all requests)
        if not ProductionRateLimiter.redis_available:
            current_time = time.time()
            if current_time - ProductionRateLimiter.last_redis_error_time < ProductionRateLimiter.redis_error_backoff:
                # Still in backoff period
                return True, {"error": "Redis quota exceeded", "allowed": True, "graceful_degradation": True}
            else:
                # Try to reconnect
                ProductionRateLimiter.redis_available = True

        # Check if identifier is a user ID (UUID string) and if that user is admin
        try:
            # Try to parse as UUID - if successful, check if admin
            import uuid
            try:
                user_uuid = uuid.UUID(str(identifier))
                # It's a valid UUID, check if admin
                if self.is_admin_user(str(identifier)):
                    self.logger.info(f"Admin user {identifier} bypassing rate limit for {limit_type}")
                    return True, {
                        "allowed": True,
                        "admin": True,
                        "limit": "unlimited",
                        "remaining": "unlimited"
                    }
            except ValueError:
                # Not a UUID, probably an IP address - proceed with normal rate limiting
                pass
        except Exception as e:
            # If anything goes wrong, proceed with normal rate limiting
            self.logger.debug(f"Admin check skipped for identifier {identifier}: {e}")
            pass

        limit = self.LIMITS[limit_type]
        key = self._get_key(limit_type, identifier)

        try:
            # Use sliding window algorithm
            now = int(time.time())
            window_start = now - limit.window
            
            # Clean old entries
            redis_client.zremrangebyscore(key, 0, window_start)
            
            # Count current requests in window
            current_count = redis_client.zcard(key)
            
            if current_count >= limit.requests:
                # Get oldest request time for reset info
                oldest = redis_client.zrange(key, 0, 0, withscores=True)
                reset_time = int(oldest[0][1]) + limit.window if oldest else now + limit.window
                
                return False, {
                    "allowed": False,
                    "limit": limit.requests,
                    "remaining": 0,
                    "reset_time": reset_time,
                    "retry_after": reset_time - now
                }
            
            # Add current request
            redis_client.zadd(key, {str(now): now})
            redis_client.expire(key, limit.window)
            
            remaining = limit.requests - current_count - 1
            
            return True, {
                "allowed": True,
                "limit": limit.requests,
                "remaining": remaining,
                "reset_time": now + limit.window
            }
            
        except redis.RedisError as e:
            error_msg = str(e).lower()
            
            # Check if this is a quota exceeded error
            if 'max requests limit exceeded' in error_msg or 'quota exceeded' in error_msg:
                current_time = time.time()
                
                # Throttle error logging for quota errors
                if current_time - ProductionRateLimiter.last_redis_error_time > 3600:
                    self.logger.error(
                        f"⚠️ REDIS QUOTA EXCEEDED IN RATE LIMITER ⚠️\n"
                        f"Rate limiting is temporarily disabled to preserve remaining quota.\n"
                        f"Please check Upstash dashboard and consider upgrading.\n"
                        f"Error: {e}"
                    )
                    ProductionRateLimiter.last_redis_error_time = current_time
                
                ProductionRateLimiter.redis_available = False
            else:
                self.logger.error(f"Redis error in rate limiter: {e}")
            
            # Fail open - allow request if Redis is down
            return True, {"error": "Rate limiter unavailable", "allowed": True}
    
    def increment_usage(self, limit_type: str, identifier: str, amount: int = 1):
        """Increment usage counter"""
        # Skip if Redis quota exceeded
        if not ProductionRateLimiter.redis_available:
            return
        
        key = self._get_key(limit_type, identifier)
        now = int(time.time())
        
        try:
            for _ in range(amount):
                redis_client.zadd(key, {f"{now}_{_}": now})
            
            if limit_type in self.LIMITS:
                redis_client.expire(key, self.LIMITS[limit_type].window)
                
        except redis.RedisError as e:
            error_msg = str(e).lower()
            if 'max requests limit exceeded' in error_msg or 'quota exceeded' in error_msg:
                ProductionRateLimiter.redis_available = False
                ProductionRateLimiter.last_redis_error_time = time.time()
            else:
                self.logger.error(f"Redis error incrementing usage: {e}")
    
    def get_usage_stats(self, limit_type: str, identifier: str) -> Dict[str, Any]:
        """Get current usage statistics"""
        if limit_type not in self.LIMITS:
            return {"error": "Unknown limit type"}
        
        # Return default stats if Redis quota exceeded
        if not ProductionRateLimiter.redis_available:
            limit = self.LIMITS[limit_type]
            return {
                "limit": limit.requests,
                "used": 0,
                "remaining": limit.requests,
                "window_seconds": limit.window,
                "reset_time": int(time.time()) + limit.window,
                "error": "Redis quota exceeded - showing default values"
            }
        
        limit = self.LIMITS[limit_type]
        key = self._get_key(limit_type, identifier)
        
        try:
            now = int(time.time())
            window_start = now - limit.window
            
            # Clean old entries
            redis_client.zremrangebyscore(key, 0, window_start)
            
            # Get current count
            current_count = redis_client.zcard(key)
            
            return {
                "limit": limit.requests,
                "used": current_count,
                "remaining": max(0, limit.requests - current_count),
                "window_seconds": limit.window,
                "reset_time": now + limit.window
            }
            
        except redis.RedisError as e:
            error_msg = str(e).lower()
            if 'max requests limit exceeded' in error_msg or 'quota exceeded' in error_msg:
                ProductionRateLimiter.redis_available = False
                ProductionRateLimiter.last_redis_error_time = time.time()
            else:
                self.logger.error(f"Redis error getting usage stats: {e}")
            return {"error": "Stats unavailable"}

# Global rate limiter instance
rate_limiter = ProductionRateLimiter()

def rate_limit(limit_type: str, get_identifier=None):
    """
    Decorator for rate limiting endpoints
    
    Args:
        limit_type: Type of rate limit to apply
        get_identifier: Function to get identifier (defaults to user_id or IP)
    """
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            # Get identifier
            if get_identifier:
                identifier = get_identifier()
            elif hasattr(request, 'current_user'):
                identifier = str(request.current_user['id'])
            else:
                identifier = request.remote_addr
            
            # Check rate limit
            allowed, info = rate_limiter.check_limit(limit_type, identifier)
            
            if not allowed:
                response = jsonify({
                    "error": "Rate limit exceeded",
                    "limit": info.get("limit"),
                    "retry_after": info.get("retry_after"),
                    "reset_time": info.get("reset_time")
                })
                response.status_code = 429
                response.headers['X-RateLimit-Limit'] = str(info.get("limit", ""))
                response.headers['X-RateLimit-Remaining'] = str(info.get("remaining", 0))
                response.headers['X-RateLimit-Reset'] = str(info.get("reset_time", ""))
                response.headers['Retry-After'] = str(info.get("retry_after", 60))
                return response
            
            # Add rate limit headers to response
            response = f(*args, **kwargs)
            if hasattr(response, 'headers'):
                response.headers['X-RateLimit-Limit'] = str(info.get("limit", ""))
                response.headers['X-RateLimit-Remaining'] = str(info.get("remaining", 0))
                response.headers['X-RateLimit-Reset'] = str(info.get("reset_time", ""))
            
            return response
        return decorated_function
    return decorator

class GeminiQuotaManager:
    """
    Manages Gemini API quota across all users
    Implements fair queuing and priority scheduling
    """
    
    def __init__(self):
        self.logger = logging.getLogger(__name__)
        self.quota_key = "gemini_quota_manager"
    
    def can_make_request(self) -> Tuple[bool, Dict[str, Any]]:
        """Check if we can make a Gemini API request"""
        # Check global minute limit
        allowed, info = rate_limiter.check_limit('gemini_requests_per_minute', 'global')
        if not allowed:
            return False, {"reason": "minute_limit_exceeded", **info}
        
        # Check global daily limit
        allowed, info = rate_limiter.check_limit('gemini_requests_per_day', 'global')
        if not allowed:
            return False, {"reason": "daily_limit_exceeded", **info}
        
        return True, {"allowed": True}
    
    def reserve_quota(self, user_id: str, priority: int = 5) -> str:
        """
        Reserve quota for a user request

        Args:
            user_id: User ID (UUID string)
            priority: Priority level (lower = higher priority)

        Returns:
            reservation_id or raises exception
        """
        reservation_id = f"{user_id}_{int(time.time())}_{priority}"
        
        # Add to priority queue
        redis_client.zadd(f"{self.quota_key}:queue", {reservation_id: priority})
        
        return reservation_id
    
    def release_quota(self, reservation_id: str):
        """Release reserved quota"""
        redis_client.zrem(f"{self.quota_key}:queue", reservation_id)
    
    def get_queue_position(self, reservation_id: str) -> int:
        """Get position in queue (0 = next)"""
        rank = redis_client.zrevrank(f"{self.quota_key}:queue", reservation_id)
        return rank if rank is not None else -1

# Global quota manager
gemini_quota_manager = GeminiQuotaManager()

def with_gemini_quota(priority: int = 5):
    """
    Decorator to manage Gemini API quota
    
    Args:
        priority: Request priority (1=highest, 10=lowest)
    """
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            user_id = getattr(request, 'current_user', {}).get('id', 0)
            
            # Check if we can make request
            can_request, info = gemini_quota_manager.can_make_request()
            if not can_request:
                return jsonify({
                    "error": "API quota exceeded",
                    "reason": info.get("reason"),
                    "retry_after": info.get("retry_after", 60)
                }), 429
            
            # Reserve quota
            reservation_id = gemini_quota_manager.reserve_quota(user_id, priority)
            
            try:
                # Execute function
                result = f(*args, **kwargs)
                
                # Increment usage counters
                rate_limiter.increment_usage('gemini_requests_per_minute', 'global')
                rate_limiter.increment_usage('gemini_requests_per_day', 'global')
                
                return result
                
            finally:
                # Always release quota
                gemini_quota_manager.release_quota(reservation_id)
        
        return decorated_function
    return decorator

def get_rate_limit_status() -> Dict[str, Any]:
    """Get current rate limit status for monitoring"""
    status = {}
    
    # Global Gemini limits
    status['gemini_minute'] = rate_limiter.get_usage_stats('gemini_requests_per_minute', 'global')
    status['gemini_daily'] = rate_limiter.get_usage_stats('gemini_requests_per_day', 'global')
    
    # Queue status
    queue_size = redis_client.zcard(f"{gemini_quota_manager.quota_key}:queue")
    status['queue_size'] = queue_size
    
    return status
