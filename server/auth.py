import bcrypt
import jwt
import os
from datetime import datetime, timedelta
from sqlalchemy.orm import Session
from database_config import User, SessionLocal, get_db
from typing import Optional, Dict, Any
import logging

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
    def create_jwt_token(user_id: int, email: str) -> str:
        """Create a JWT token for user authentication"""
        payload = {
            'user_id': user_id,
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
        """Register a new user"""
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

            # Create new user
            new_user = User(
                email=email,
                password_hash=hashed_password,
                first_name=first_name,
                last_name=last_name
            )

            db.add(new_user)
            db.commit()
            db.refresh(new_user)

            # Create JWT token
            token = AuthService.create_jwt_token(new_user.id, new_user.email)

            return {
                'success': True,
                'message': 'User registered successfully',
                'user': {
                    'id': new_user.id,
                    'email': new_user.email,
                    'first_name': new_user.first_name,
                    'last_name': new_user.last_name,
                    'created_at': new_user.created_at.isoformat()
                },
                'token': token
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
                    'id': user.id,
                    'email': user.email,
                    'first_name': user.first_name,
                    'last_name': user.last_name,
                    'created_at': user.created_at.isoformat()
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
    def get_user_from_token(token: str) -> Optional[Dict[str, Any]]:
        """Get user information from JWT token"""
        payload = AuthService.verify_jwt_token(token)
        if not payload:
            return None

        db = SessionLocal()
        try:
            user = db.query(User).filter(User.id == payload['user_id']).first()
            if not user or not user.is_active:
                return None

            return {
                'id': user.id,
                'email': user.email,
                'first_name': user.first_name,
                'last_name': user.last_name,
                'created_at': user.created_at.isoformat()
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