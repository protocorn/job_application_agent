import bcrypt
import jwt
import os
import secrets
import uuid
from datetime import datetime, timedelta
from sqlalchemy.orm import Session
from database_config import User, SessionLocal, get_db
from typing import Optional, Dict, Any, Union
import logging
from email_service import email_service

# JWT Configuration
JWT_SECRET_KEY = os.getenv('JWT_SECRET_KEY', 'your-secret-key-change-this-in-production')
JWT_ALGORITHM = 'HS256'
JWT_EXPIRATION_HOURS = 24

class AuthService:

    @staticmethod
    def hash_password(password: str) -> str:
        """Hash a password using bcrypt"""
        salt = bcrypt.gensalt()
        hashed = bcrypt.hashpw(password.encode('utf-8'), salt)
        return hashed.decode('utf-8')

    @staticmethod
    def verify_password(password: str, hashed_password: str) -> bool:
        """Verify a password against its hash"""
        return bcrypt.checkpw(password.encode('utf-8'), hashed_password.encode('utf-8'))

    @staticmethod
    def create_jwt_token(user_id: Union[int, uuid.UUID], email: str) -> str:
        """Create a JWT token for user authentication"""
        # Convert UUID to string for JWT payload
        user_id_str = str(user_id) if isinstance(user_id, uuid.UUID) else user_id
        payload = {
            'user_id': user_id_str,
            'email': email,
            'exp': datetime.utcnow() + timedelta(hours=JWT_EXPIRATION_HOURS),
            'iat': datetime.utcnow()
        }
        return jwt.encode(payload, JWT_SECRET_KEY, algorithm=JWT_ALGORITHM)

    @staticmethod
    def verify_jwt_token(token: str) -> Optional[Dict[str, Any]]:
        """Verify and decode a JWT token"""
        try:
            payload = jwt.decode(token, JWT_SECRET_KEY, algorithms=[JWT_ALGORITHM])
            return payload
        except jwt.ExpiredSignatureError:
            logging.warning("JWT token has expired")
            return None
        except jwt.InvalidTokenError:
            logging.warning("Invalid JWT token")
            return None

    @staticmethod
    def register_user(email: str, password: str, first_name: str, last_name: str) -> Dict[str, Any]:
        """Register a new user and send verification email"""
        db = SessionLocal()
        try:
            # Check if user already exists
            existing_user = db.query(User).filter(User.email == email).first()
            if existing_user:
                return {
                    'success': False,
                    'error': 'User with this email already exists'
                }

            # Hash password
            hashed_password = AuthService.hash_password(password)

            # Generate verification token
            verification_token = secrets.token_urlsafe(32)
            verification_expires = datetime.utcnow() + timedelta(hours=24)

            # Create new user
            new_user = User(
                email=email,
                password_hash=hashed_password,
                first_name=first_name,
                last_name=last_name,
                email_verified=False,
                verification_token=verification_token,
                verification_token_expires=verification_expires
            )

            db.add(new_user)
            db.commit()
            db.refresh(new_user)

            # Send verification email
            email_sent = email_service.send_verification_email(
                to_email=email,
                verification_token=verification_token,
                first_name=first_name
            )

            if not email_sent:
                logging.error(f"Failed to send verification email to {email}")
                # Still return success but inform user about email issue
                return {
                    'success': True,
                    'message': 'User registered successfully, but we could not send the verification email. Please try resending from the login page.',
                    'email_sent': False,
                    'user': {
                        'id': str(new_user.id),
                        'email': new_user.email,
                        'first_name': new_user.first_name,
                        'last_name': new_user.last_name,
                        'email_verified': new_user.email_verified,
                        'created_at': new_user.created_at.isoformat()
                    }
                }

            # Do NOT create JWT token yet - user must verify email first
            return {
                'success': True,
                'message': 'User registered successfully. Please check your email to verify your account.',
                'email_sent': True,
                'user': {
                    'id': str(new_user.id),
                    'email': new_user.email,
                    'first_name': new_user.first_name,
                    'last_name': new_user.last_name,
                    'email_verified': new_user.email_verified,
                    'created_at': new_user.created_at.isoformat()
                }
            }

        except Exception as e:
            db.rollback()
            logging.error(f"Error registering user: {e}")
            return {
                'success': False,
                'error': 'Failed to register user'
            }
        finally:
            db.close()

    @staticmethod
    def authenticate_user(email: str, password: str) -> Dict[str, Any]:
        """Authenticate a user and return JWT token"""
        db = SessionLocal()
        try:
            # Find user by email
            user = db.query(User).filter(User.email == email).first()
            if not user:
                return {
                    'success': False,
                    'error': 'Invalid email or password'
                }

            # Verify password
            if not AuthService.verify_password(password, user.password_hash):
                return {
                    'success': False,
                    'error': 'Invalid email or password'
                }

            # Check if email is verified
            if not user.email_verified:
                return {
                    'success': False,
                    'error': 'Please verify your email address before logging in. Check your inbox for the verification link.',
                    'email_not_verified': True
                }

            # Check if user is active
            if not user.is_active:
                return {
                    'success': False,
                    'error': 'User account is deactivated'
                }

            # Create JWT token
            token = AuthService.create_jwt_token(user.id, user.email)

            return {
                'success': True,
                'message': 'Authentication successful',
                'user': {
                    'id': str(user.id),
                    'email': user.email,
                    'first_name': user.first_name,
                    'last_name': user.last_name,
                    'created_at': user.created_at.isoformat(),
                    'beta_access_requested': user.beta_access_requested or False,
                    'beta_access_approved': user.beta_access_approved or False
                },
                'token': token
            }

        except Exception as e:
            logging.error(f"Error authenticating user: {e}")
            return {
                'success': False,
                'error': 'Authentication failed'
            }
        finally:
            db.close()

    @staticmethod
    def verify_email(verification_token: str) -> Dict[str, Any]:
        """Verify user email with verification token"""
        db = SessionLocal()
        try:
            # Find user by verification token
            user = db.query(User).filter(User.verification_token == verification_token).first()

            if not user:
                return {
                    'success': False,
                    'error': 'Invalid verification token'
                }

            # Check if token has expired
            if user.verification_token_expires and user.verification_token_expires < datetime.utcnow():
                return {
                    'success': False,
                    'error': 'Verification token has expired. Please request a new verification email.'
                }

            # Check if already verified
            if user.email_verified:
                return {
                    'success': True,
                    'message': 'Email already verified. You can log in now.',
                    'already_verified': True
                }

            # Verify the email
            user.email_verified = True
            user.verification_token = None
            user.verification_token_expires = None
            db.commit()

            # Create JWT token for automatic login
            token = AuthService.create_jwt_token(user.id, user.email)

            return {
                'success': True,
                'message': 'Email verified successfully! You can now log in.',
                'user': {
                    'id': str(user.id),
                    'email': user.email,
                    'first_name': user.first_name,
                    'last_name': user.last_name,
                    'email_verified': user.email_verified
                },
                'token': token
            }

        except Exception as e:
            db.rollback()
            logging.error(f"Error verifying email: {e}")
            return {
                'success': False,
                'error': 'Email verification failed'
            }
        finally:
            db.close()

    @staticmethod
    def resend_verification_email(email: str) -> Dict[str, Any]:
        """Resend verification email to user"""
        db = SessionLocal()
        try:
            # Find user by email
            user = db.query(User).filter(User.email == email.strip().lower()).first()

            if not user:
                return {
                    'success': False,
                    'error': 'No account found with this email address'
                }

            # Check if already verified
            if user.email_verified:
                return {
                    'success': False,
                    'error': 'Email is already verified. You can log in now.'
                }

            # Generate new verification token
            verification_token = secrets.token_urlsafe(32)
            verification_expires = datetime.utcnow() + timedelta(hours=24)

            # Update user with new token
            user.verification_token = verification_token
            user.verification_token_expires = verification_expires
            db.commit()

            # Send verification email
            email_sent = email_service.send_verification_email(
                to_email=user.email,
                verification_token=verification_token,
                first_name=user.first_name
            )

            if not email_sent:
                logging.error(f"Failed to resend verification email to {email}")
                return {
                    'success': False,
                    'error': 'Failed to send verification email. Please try again later or contact support.'
                }

            return {
                'success': True,
                'message': 'Verification email sent successfully. Please check your inbox.'
            }

        except Exception as e:
            db.rollback()
            logging.error(f"Error resending verification email: {e}")
            return {
                'success': False,
                'error': 'Failed to resend verification email'
            }
        finally:
            db.close()

    @staticmethod
    def get_user_from_token(token: str) -> Optional[Dict[str, Any]]:
        """Get user information from JWT token"""
        payload = AuthService.verify_jwt_token(token)
        if not payload:
            return None

        db = SessionLocal()
        try:
            # Convert user_id from string to UUID
            user_id_str = payload['user_id']
            try:
                user_id = uuid.UUID(user_id_str) if isinstance(user_id_str, str) else user_id_str
            except (ValueError, AttributeError):
                logging.error(f"Invalid UUID in token: {user_id_str}")
                return None

            user = db.query(User).filter(User.id == user_id).first()
            if not user or not user.is_active:
                return None

            return {
                'id': str(user.id),
                'email': user.email,
                'first_name': user.first_name,
                'last_name': user.last_name,
                'created_at': user.created_at.isoformat(),
                'beta_access_requested': user.beta_access_requested or False,
                'beta_access_approved': user.beta_access_approved or False
            }
        except Exception as e:
            logging.error(f"Error getting user from token: {e}")
            return None
        finally:
            db.close()

def require_auth(f):
    """Decorator to require authentication for API endpoints"""
    from functools import wraps
    from flask import request, jsonify

    @wraps(f)
    def decorated_function(*args, **kwargs):
        token = request.headers.get('Authorization')
        if not token:
            return jsonify({'error': 'No authorization token provided'}), 401

        if token.startswith('Bearer '):
            token = token[7:]

        user = AuthService.get_user_from_token(token)
        if not user:
            return jsonify({'error': 'Invalid or expired token'}), 401

        # Add user to request context
        request.current_user = user
        return f(*args, **kwargs)

    return decorated_function