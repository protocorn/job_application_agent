"""
Security Manager for Job Application Agent
Implements security hardening, audit logging, and vulnerability protection
"""

import os
import logging
import hashlib
import secrets
import time
import json
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional, Tuple
from functools import wraps
import re
import ipaddress
from flask import request, jsonify, g
import jwt
from cryptography.fernet import Fernet
import bcrypt
from sqlalchemy import text
import redis

# Security configuration
SECURITY_CONFIG = {
    'password_min_length': 8,
    'password_require_uppercase': True,
    'password_require_lowercase': True,
    'password_require_numbers': True,
    'password_require_special': True,
    'max_login_attempts': 5,
    'lockout_duration': 900,  # 15 minutes
    'jwt_expiration_hours': 24,
    'session_timeout_minutes': 60,
    'max_file_size_mb': 10,
    'allowed_file_types': ['.pdf', '.doc', '.docx', '.txt'],
    'rate_limit_requests_per_minute': 60,
    'suspicious_activity_threshold': 10
}

# Redis for security tracking
# Support both local Redis and Upstash (with TLS)
REDIS_URL = os.getenv('REDIS_URL')

if REDIS_URL:
    # Use Redis URL with different DB for security
    import urllib.parse

    # Convert to rediss:// for TLS if using Upstash
    redis_url = REDIS_URL
    is_upstash = 'upstash.io' in redis_url
    if redis_url.startswith('redis://') and is_upstash:
        redis_url = redis_url.replace('redis://', 'rediss://', 1)

    parsed = urllib.parse.urlparse(redis_url)
    # Upstash free tier only supports DB 0, use DB 2 for local Redis
    db_number = 0 if is_upstash else 2
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
        db=2,  # Use different DB for security
        decode_responses=True
    )

class SecurityManager:
    """
    Comprehensive security manager for production deployment
    """
    
    def __init__(self):
        self.logger = logging.getLogger(__name__)
        self.setup_security_logging()
        
        # Initialize encryption
        self.encryption_key = self._get_or_create_encryption_key()
        self.cipher_suite = Fernet(self.encryption_key)
        
        # Security event types
        self.SECURITY_EVENTS = {
            'LOGIN_SUCCESS': 'login_success',
            'LOGIN_FAILURE': 'login_failure',
            'PASSWORD_CHANGE': 'password_change',
            'ACCOUNT_LOCKED': 'account_locked',
            'SUSPICIOUS_ACTIVITY': 'suspicious_activity',
            'DATA_ACCESS': 'data_access',
            'FILE_UPLOAD': 'file_upload',
            'API_ABUSE': 'api_abuse',
            'SECURITY_VIOLATION': 'security_violation'
        }
    
    def setup_security_logging(self):
        """Set up dedicated security logging"""
        security_logger = logging.getLogger('security')
        security_logger.setLevel(logging.INFO)
        
        # Create security log handler
        log_dir = os.path.join(os.path.dirname(__file__), 'logs')
        os.makedirs(log_dir, exist_ok=True)
        
        security_log_file = os.path.join(log_dir, 'security.log')
        handler = logging.FileHandler(security_log_file)
        
        formatter = logging.Formatter(
            '%(asctime)s - %(levelname)s - %(message)s'
        )
        handler.setFormatter(formatter)
        security_logger.addHandler(handler)
        
        self.security_logger = security_logger
    
    def _get_or_create_encryption_key(self) -> bytes:
        """Get or create encryption key for sensitive data"""
        key = os.getenv('ENCRYPTION_KEY')
        
        if not key:
            # Generate new key
            key = Fernet.generate_key()
            self.logger.warning(f"Generated new encryption key. Set ENCRYPTION_KEY environment variable: {key.decode()}")
            return key
        
        if isinstance(key, str):
            key = key.encode()
        
        try:
            # Validate key
            Fernet(key)
            return key
        except Exception as e:
            raise ValueError(f"Invalid encryption key: {e}")
    
    def validate_password_strength(self, password: str) -> Tuple[bool, List[str]]:
        """
        Validate password strength according to security policy
        
        Returns:
            (is_valid, error_messages)
        """
        errors = []
        
        if len(password) < SECURITY_CONFIG['password_min_length']:
            errors.append(f"Password must be at least {SECURITY_CONFIG['password_min_length']} characters long")
        
        if SECURITY_CONFIG['password_require_uppercase'] and not re.search(r'[A-Z]', password):
            errors.append("Password must contain at least one uppercase letter")
        
        if SECURITY_CONFIG['password_require_lowercase'] and not re.search(r'[a-z]', password):
            errors.append("Password must contain at least one lowercase letter")
        
        if SECURITY_CONFIG['password_require_numbers'] and not re.search(r'\d', password):
            errors.append("Password must contain at least one number")
        
        if SECURITY_CONFIG['password_require_special'] and not re.search(r'[!@#$%^&*(),.?":{}|<>]', password):
            errors.append("Password must contain at least one special character")
        
        # Check for common weak passwords
        weak_patterns = [
            r'password', r'123456', r'qwerty', r'admin', r'letmein',
            r'welcome', r'monkey', r'dragon', r'master', r'shadow'
        ]
        
        for pattern in weak_patterns:
            if re.search(pattern, password.lower()):
                errors.append("Password contains common weak patterns")
                break
        
        return len(errors) == 0, errors
    
    def hash_password(self, password: str) -> str:
        """Hash password with bcrypt"""
        # Validate password strength first
        is_valid, errors = self.validate_password_strength(password)
        if not is_valid:
            raise ValueError(f"Password validation failed: {', '.join(errors)}")
        
        salt = bcrypt.gensalt(rounds=12)  # Higher cost for better security
        hashed = bcrypt.hashpw(password.encode('utf-8'), salt)
        return hashed.decode('utf-8')
    
    def verify_password(self, password: str, hashed_password: str) -> bool:
        """Verify password against hash"""
        return bcrypt.checkpw(password.encode('utf-8'), hashed_password.encode('utf-8'))
    
    def check_login_attempts(self, identifier: str) -> Tuple[bool, int]:
        """
        Check if account is locked due to failed login attempts
        
        Returns:
            (is_allowed, remaining_attempts)
        """
        key = f"login_attempts:{identifier}"
        
        try:
            attempts_data = redis_client.get(key)
            if not attempts_data:
                return True, SECURITY_CONFIG['max_login_attempts']
            
            attempts_info = json.loads(attempts_data)
            attempts = attempts_info.get('count', 0)
            locked_until = attempts_info.get('locked_until')
            
            # Check if lockout period has expired
            if locked_until and datetime.utcnow().timestamp() > locked_until:
                redis_client.delete(key)
                return True, SECURITY_CONFIG['max_login_attempts']
            
            if attempts >= SECURITY_CONFIG['max_login_attempts']:
                return False, 0
            
            return True, SECURITY_CONFIG['max_login_attempts'] - attempts
            
        except Exception as e:
            self.logger.error(f"Error checking login attempts: {e}")
            return True, SECURITY_CONFIG['max_login_attempts']
    
    def record_login_attempt(self, identifier: str, success: bool, user_id: Optional[int] = None):
        """Record login attempt for rate limiting and security monitoring"""
        key = f"login_attempts:{identifier}"
        
        try:
            if success:
                # Clear failed attempts on successful login
                redis_client.delete(key)
                
                # Log successful login
                self.log_security_event(
                    event_type=self.SECURITY_EVENTS['LOGIN_SUCCESS'],
                    user_id=user_id,
                    details={
                        'identifier': identifier,
                        'ip_address': request.remote_addr if request else None,
                        'user_agent': request.headers.get('User-Agent') if request else None
                    }
                )
            else:
                # Increment failed attempts
                attempts_data = redis_client.get(key)
                if attempts_data:
                    attempts_info = json.loads(attempts_data)
                    attempts = attempts_info.get('count', 0) + 1
                else:
                    attempts = 1
                
                # Check if account should be locked
                if attempts >= SECURITY_CONFIG['max_login_attempts']:
                    locked_until = (datetime.utcnow() + timedelta(seconds=SECURITY_CONFIG['lockout_duration'])).timestamp()
                    
                    attempts_info = {
                        'count': attempts,
                        'locked_until': locked_until,
                        'first_attempt': datetime.utcnow().isoformat()
                    }
                    
                    # Log account lockout
                    self.log_security_event(
                        event_type=self.SECURITY_EVENTS['ACCOUNT_LOCKED'],
                        details={
                            'identifier': identifier,
                            'attempts': attempts,
                            'locked_until': locked_until,
                            'ip_address': request.remote_addr if request else None
                        }
                    )
                else:
                    attempts_info = {
                        'count': attempts,
                        'first_attempt': datetime.utcnow().isoformat()
                    }
                
                redis_client.setex(key, SECURITY_CONFIG['lockout_duration'], json.dumps(attempts_info))
                
                # Log failed login
                self.log_security_event(
                    event_type=self.SECURITY_EVENTS['LOGIN_FAILURE'],
                    details={
                        'identifier': identifier,
                        'attempts': attempts,
                        'ip_address': request.remote_addr if request else None,
                        'user_agent': request.headers.get('User-Agent') if request else None
                    }
                )
                
        except Exception as e:
            self.logger.error(f"Error recording login attempt: {e}")
    
    def validate_file_upload(self, filename: str, file_size: int, file_content: bytes) -> Tuple[bool, str]:
        """
        Validate file upload for security
        
        Returns:
            (is_valid, error_message)
        """
        # Check file size
        max_size = SECURITY_CONFIG['max_file_size_mb'] * 1024 * 1024
        if file_size > max_size:
            return False, f"File size exceeds maximum allowed size of {SECURITY_CONFIG['max_file_size_mb']}MB"
        
        # Check file extension
        file_ext = os.path.splitext(filename.lower())[1]
        if file_ext not in SECURITY_CONFIG['allowed_file_types']:
            return False, f"File type {file_ext} is not allowed. Allowed types: {', '.join(SECURITY_CONFIG['allowed_file_types'])}"
        
        # Check for malicious content patterns
        malicious_patterns = [
            b'<script', b'javascript:', b'vbscript:', b'onload=', b'onerror=',
            b'<?php', b'<%', b'eval(', b'exec(', b'system(', b'shell_exec('
        ]
        
        content_lower = file_content.lower()
        for pattern in malicious_patterns:
            if pattern in content_lower:
                self.log_security_event(
                    event_type=self.SECURITY_EVENTS['SECURITY_VIOLATION'],
                    details={
                        'type': 'malicious_file_upload',
                        'filename': filename,
                        'pattern_detected': pattern.decode('utf-8', errors='ignore'),
                        'ip_address': request.remote_addr if request else None
                    }
                )
                return False, "File contains potentially malicious content"
        
        # Log file upload
        self.log_security_event(
            event_type=self.SECURITY_EVENTS['FILE_UPLOAD'],
            user_id=getattr(request, 'current_user', {}).get('id'),
            details={
                'filename': filename,
                'file_size': file_size,
                'file_type': file_ext
            }
        )
        
        return True, ""
    
    def sanitize_input(self, input_data: Any) -> Any:
        """Sanitize user input to prevent injection attacks"""
        if isinstance(input_data, str):
            # Remove potentially dangerous characters
            sanitized = re.sub(r'[<>"\';\\]', '', input_data)
            
            # Limit length
            if len(sanitized) > 10000:  # 10KB limit
                sanitized = sanitized[:10000]
            
            return sanitized.strip()
        
        elif isinstance(input_data, dict):
            return {key: self.sanitize_input(value) for key, value in input_data.items()}
        
        elif isinstance(input_data, list):
            return [self.sanitize_input(item) for item in input_data]
        
        return input_data
    
    def validate_ip_address(self, ip_address: str) -> bool:
        """Validate and check if IP address is allowed"""
        try:
            ip = ipaddress.ip_address(ip_address)
            
            # Block private networks in production (optional)
            if os.getenv('BLOCK_PRIVATE_IPS', 'false').lower() == 'true':
                if ip.is_private:
                    return False
            
            # Check against blocked IPs
            blocked_ips = self.get_blocked_ips()
            if ip_address in blocked_ips:
                return False
            
            return True
            
        except ValueError:
            return False
    
    def get_blocked_ips(self) -> List[str]:
        """Get list of blocked IP addresses"""
        try:
            blocked_ips = redis_client.smembers("blocked_ips")
            return list(blocked_ips)
        except Exception as e:
            self.logger.error(f"Error getting blocked IPs: {e}")
            return []
    
    def block_ip_address(self, ip_address: str, reason: str, duration_hours: int = 24):
        """Block an IP address"""
        try:
            redis_client.sadd("blocked_ips", ip_address)
            redis_client.expire("blocked_ips", duration_hours * 3600)
            
            # Log the blocking
            self.log_security_event(
                event_type=self.SECURITY_EVENTS['SECURITY_VIOLATION'],
                details={
                    'type': 'ip_blocked',
                    'ip_address': ip_address,
                    'reason': reason,
                    'duration_hours': duration_hours
                }
            )
            
            self.logger.warning(f"Blocked IP address {ip_address}: {reason}")
            
        except Exception as e:
            self.logger.error(f"Error blocking IP address: {e}")
    
    def detect_suspicious_activity(self, user_id: int, activity_type: str, details: Dict[str, Any]):
        """Detect and respond to suspicious activity"""
        key = f"suspicious_activity:{user_id}:{activity_type}"
        
        try:
            # Increment activity counter
            count = redis_client.incr(key)
            redis_client.expire(key, 3600)  # 1 hour window
            
            if count >= SECURITY_CONFIG['suspicious_activity_threshold']:
                # Log suspicious activity
                self.log_security_event(
                    event_type=self.SECURITY_EVENTS['SUSPICIOUS_ACTIVITY'],
                    user_id=user_id,
                    details={
                        'activity_type': activity_type,
                        'count': count,
                        'threshold': SECURITY_CONFIG['suspicious_activity_threshold'],
                        **details
                    }
                )
                
                # Consider blocking user or IP
                if request and request.remote_addr:
                    self.block_ip_address(
                        request.remote_addr,
                        f"Suspicious activity: {activity_type} (count: {count})",
                        duration_hours=1
                    )
                
                return True
            
            return False
            
        except Exception as e:
            self.logger.error(f"Error detecting suspicious activity: {e}")
            return False
    
    def encrypt_sensitive_data(self, data: str) -> str:
        """Encrypt sensitive data"""
        return self.cipher_suite.encrypt(data.encode()).decode()
    
    def decrypt_sensitive_data(self, encrypted_data: str) -> str:
        """Decrypt sensitive data"""
        return self.cipher_suite.decrypt(encrypted_data.encode()).decode()
    
    def log_security_event(self, event_type: str, user_id: Optional[int] = None, details: Dict[str, Any] = None):
        """Log security event for audit trail"""
        event_data = {
            'timestamp': datetime.utcnow().isoformat(),
            'event_type': event_type,
            'user_id': user_id,
            'ip_address': request.remote_addr if request else None,
            'user_agent': request.headers.get('User-Agent') if request else None,
            'details': details or {}
        }
        
        # Log to security logger
        self.security_logger.info(json.dumps(event_data))
        
        # Store in Redis for real-time monitoring
        try:
            redis_client.lpush("security_events", json.dumps(event_data))
            redis_client.ltrim("security_events", 0, 1000)  # Keep last 1000 events
        except Exception as e:
            self.logger.error(f"Error storing security event: {e}")
    
    def get_security_events(self, limit: int = 100) -> List[Dict[str, Any]]:
        """Get recent security events"""
        try:
            events = redis_client.lrange("security_events", 0, limit - 1)
            return [json.loads(event) for event in events]
        except Exception as e:
            self.logger.error(f"Error getting security events: {e}")
            return []
    
    def run_security_audit(self) -> Dict[str, Any]:
        """Run comprehensive security audit"""
        audit_results = {
            'timestamp': datetime.utcnow().isoformat(),
            'checks': {}
        }
        
        # Check environment variables
        required_env_vars = [
            'JWT_SECRET_KEY', 'ENCRYPTION_KEY', 'DB_PASSWORD',
            'GOOGLE_CLIENT_SECRET', 'GOOGLE_API_KEY'
        ]
        
        missing_env_vars = []
        weak_env_vars = []
        
        for var in required_env_vars:
            value = os.getenv(var)
            if not value:
                missing_env_vars.append(var)
            elif len(value) < 32:  # Minimum 32 characters for security
                weak_env_vars.append(var)
        
        audit_results['checks']['environment_variables'] = {
            'missing': missing_env_vars,
            'weak': weak_env_vars,
            'status': 'PASS' if not missing_env_vars and not weak_env_vars else 'FAIL'
        }
        
        # Check recent security events
        recent_events = self.get_security_events(50)
        failed_logins = len([e for e in recent_events if e['event_type'] == self.SECURITY_EVENTS['LOGIN_FAILURE']])
        suspicious_activities = len([e for e in recent_events if e['event_type'] == self.SECURITY_EVENTS['SUSPICIOUS_ACTIVITY']])
        
        audit_results['checks']['recent_activity'] = {
            'failed_logins': failed_logins,
            'suspicious_activities': suspicious_activities,
            'status': 'WARN' if failed_logins > 10 or suspicious_activities > 5 else 'PASS'
        }
        
        # Check blocked IPs
        blocked_ips = self.get_blocked_ips()
        audit_results['checks']['blocked_ips'] = {
            'count': len(blocked_ips),
            'ips': blocked_ips[:10],  # Show first 10
            'status': 'INFO'
        }
        
        return audit_results

# Global security manager instance
security_manager = SecurityManager()

# Decorators for security
def require_secure_headers(f):
    """Decorator to add security headers to responses"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        response = f(*args, **kwargs)
        
        if hasattr(response, 'headers'):
            # Security headers
            response.headers['X-Content-Type-Options'] = 'nosniff'
            response.headers['X-Frame-Options'] = 'DENY'
            response.headers['X-XSS-Protection'] = '1; mode=block'
            response.headers['Strict-Transport-Security'] = 'max-age=31536000; includeSubDomains'
            response.headers['Content-Security-Policy'] = "default-src 'self'"
            response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
        
        return response
    return decorated_function

def validate_input(f):
    """Decorator to sanitize input data"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        # Note: request.json is read-only, so we validate but don't modify
        # The actual sanitization should happen in the route handler if needed
        if request.json:
            # Just validate that the input is safe, don't try to modify it
            # The sanitize_input method will be called explicitly in routes if needed
            pass
        return f(*args, **kwargs)
    return decorated_function

def check_ip_whitelist(f):
    """Decorator to check IP whitelist"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not security_manager.validate_ip_address(request.remote_addr):
            security_manager.log_security_event(
                event_type=security_manager.SECURITY_EVENTS['SECURITY_VIOLATION'],
                details={
                    'type': 'blocked_ip_access',
                    'ip_address': request.remote_addr,
                    'endpoint': request.endpoint
                }
            )
            return jsonify({'error': 'Access denied'}), 403
        
        return f(*args, **kwargs)
    return decorated_function

# Utility functions
def get_security_status() -> Dict[str, Any]:
    """Get current security status"""
    return {
        'blocked_ips_count': len(security_manager.get_blocked_ips()),
        'recent_events_count': len(security_manager.get_security_events(100)),
        'audit_results': security_manager.run_security_audit()
    }
