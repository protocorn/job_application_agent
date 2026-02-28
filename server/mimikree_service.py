"""
Mimikree Service for Per-User Credential Management
Handles secure storage and management of user Mimikree credentials
"""

import os
import logging
from datetime import datetime
from typing import Dict, Any, Optional, Tuple
from cryptography.fernet import Fernet
from database_config import SessionLocal, User
from security_manager import security_manager

logger = logging.getLogger(__name__)

class MimikreeService:
    """
    Service for managing per-user Mimikree credentials
    """
    
    def __init__(self):
        # Get encryption key from security manager
        self.cipher_suite = security_manager.cipher_suite
        # Use development URL (localhost:8080) unless running in production
        if os.getenv('FLASK_ENV') == 'production':
            self.base_url = os.getenv('MIMIKREE_BASE_URL', 'https://www.mimikree.com')
        else:
            self.base_url = os.getenv('MIMIKREE_BASE_URL', 'http://localhost:8080')
    
    def connect_user_mimikree(self, user_id: int, email: str, password: str) -> Dict[str, Any]:
        """
        Connect a user's Mimikree account
        
        Args:
            user_id: User ID
            email: Mimikree email
            password: Mimikree password
            
        Returns:
            Result dictionary with success status
        """
        try:
            # Validate credentials by attempting to authenticate with Mimikree
            is_valid, error_msg = self._validate_mimikree_credentials(email, password)
            
            if not is_valid:
                return {
                    'success': False,
                    'error': f'Invalid Mimikree credentials: {error_msg}'
                }
            
            # Encrypt password
            encrypted_password = self._encrypt_password(password)
            
            # Store in database
            db = SessionLocal()
            try:
                user = db.query(User).filter(User.id == user_id).first()
                if not user:
                    return {
                        'success': False,
                        'error': 'User not found'
                    }
                
                # Update user's Mimikree credentials
                user.mimikree_email = email
                user.mimikree_password_encrypted = encrypted_password
                user.mimikree_connected_at = datetime.utcnow()
                user.mimikree_is_connected = True
                
                db.commit()
                
                # Log security event
                security_manager.log_security_event(
                    event_type=security_manager.SECURITY_EVENTS['DATA_ACCESS'],
                    user_id=user_id,
                    details={
                        'action': 'mimikree_connected',
                        'email': email
                    }
                )
                
                logger.info(f"Mimikree account connected for user {user_id}")
                
                return {
                    'success': True,
                    'message': 'Mimikree account connected successfully',
                    'email': email,
                    'connected_at': user.mimikree_connected_at.isoformat()
                }
                
            finally:
                db.close()
                
        except Exception as e:
            logger.error(f"Error connecting Mimikree for user {user_id}: {e}")
            return {
                'success': False,
                'error': f'Failed to connect Mimikree account: {str(e)}'
            }
    
    def disconnect_user_mimikree(self, user_id: int) -> Dict[str, Any]:
        """
        Disconnect a user's Mimikree account
        
        Args:
            user_id: User ID
            
        Returns:
            Result dictionary with success status
        """
        try:
            db = SessionLocal()
            try:
                user = db.query(User).filter(User.id == user_id).first()
                if not user:
                    return {
                        'success': False,
                        'error': 'User not found'
                    }
                
                # Clear Mimikree credentials
                user.mimikree_email = None
                user.mimikree_password_encrypted = None
                user.mimikree_connected_at = None
                user.mimikree_is_connected = False
                
                db.commit()
                
                # Log security event
                security_manager.log_security_event(
                    event_type=security_manager.SECURITY_EVENTS['DATA_ACCESS'],
                    user_id=user_id,
                    details={
                        'action': 'mimikree_disconnected'
                    }
                )
                
                logger.info(f"Mimikree account disconnected for user {user_id}")
                
                return {
                    'success': True,
                    'message': 'Mimikree account disconnected successfully'
                }
                
            finally:
                db.close()
                
        except Exception as e:
            logger.error(f"Error disconnecting Mimikree for user {user_id}: {e}")
            return {
                'success': False,
                'error': f'Failed to disconnect Mimikree account: {str(e)}'
            }
    
    def get_user_mimikree_status(self, user_id: int) -> Dict[str, Any]:
        """
        Get user's Mimikree connection status
        
        Args:
            user_id: User ID
            
        Returns:
            Status dictionary
        """
        try:
            db = SessionLocal()
            try:
                user = db.query(User).filter(User.id == user_id).first()
                if not user:
                    return {
                        'success': False,
                        'error': 'User not found'
                    }
                
                return {
                    'success': True,
                    'is_connected': user.mimikree_is_connected or False,
                    'email': user.mimikree_email if user.mimikree_is_connected else None,
                    'connected_at': user.mimikree_connected_at.isoformat() if user.mimikree_connected_at else None
                }
                
            finally:
                db.close()
                
        except Exception as e:
            logger.error(f"Error getting Mimikree status for user {user_id}: {e}")
            return {
                'success': False,
                'error': f'Failed to get Mimikree status: {str(e)}'
            }
    
    def get_user_mimikree_credentials(self, user_id: int) -> Optional[Tuple[str, str]]:
        """
        Get decrypted Mimikree credentials for a user
        
        Args:
            user_id: User ID
            
        Returns:
            Tuple of (email, password) or None if not connected
        """
        try:
            db = SessionLocal()
            try:
                user = db.query(User).filter(User.id == user_id).first()
                if not user or not user.mimikree_is_connected:
                    return None
                
                if not user.mimikree_email or not user.mimikree_password_encrypted:
                    return None
                
                # Decrypt password
                password = self._decrypt_password(user.mimikree_password_encrypted)
                
                return (user.mimikree_email, password)
                
            finally:
                db.close()
                
        except Exception as e:
            logger.error(f"Error getting Mimikree credentials for user {user_id}: {e}")
            return None
    
    def _validate_mimikree_credentials(self, email: str, password: str) -> Tuple[bool, str]:
        """
        Validate Mimikree credentials by attempting authentication
        
        Args:
            email: Mimikree email
            password: Mimikree password
            
        Returns:
            Tuple of (is_valid, error_message)
        """
        try:
            # Import Mimikree client
            import sys
            sys.path.append(os.path.join(os.path.dirname(__file__), '..', 'Agents'))
            from mimikree_integration import MimikreeClient
            
            # Test authentication
            client = MimikreeClient()
            client.authenticate(email, password)
            
            # If we get here, authentication was successful
            return True, ""
            
        except Exception as e:
            error_msg = str(e)
            logger.warning(f"Mimikree credential validation failed: {error_msg}")
            
            # Return user-friendly error messages
            if 'authentication' in error_msg.lower() or 'login' in error_msg.lower():
                return False, "Invalid email or password"
            elif 'connection' in error_msg.lower() or 'network' in error_msg.lower():
                return False, "Unable to connect to Mimikree. Please try again later."
            else:
                return False, "Authentication failed. Please check your credentials."
    
    def _encrypt_password(self, password: str) -> str:
        """Encrypt password for storage"""
        return self.cipher_suite.encrypt(password.encode()).decode()
    
    def _decrypt_password(self, encrypted_password: str) -> str:
        """Decrypt password from storage"""
        return self.cipher_suite.decrypt(encrypted_password.encode()).decode()
    
    def test_user_connection(self, user_id: int) -> Dict[str, Any]:
        """
        Test user's Mimikree connection
        
        Args:
            user_id: User ID
            
        Returns:
            Test result dictionary
        """
        try:
            credentials = self.get_user_mimikree_credentials(user_id)
            if not credentials:
                return {
                    'success': False,
                    'error': 'No Mimikree credentials found. Please connect your account first.'
                }
            
            email, password = credentials
            is_valid, error_msg = self._validate_mimikree_credentials(email, password)
            
            if is_valid:
                return {
                    'success': True,
                    'message': 'Mimikree connection is working properly',
                    'email': email
                }
            else:
                return {
                    'success': False,
                    'error': f'Connection test failed: {error_msg}'
                }
                
        except Exception as e:
            logger.error(f"Error testing Mimikree connection for user {user_id}: {e}")
            return {
                'success': False,
                'error': f'Connection test failed: {str(e)}'
            }

# Global service instance
mimikree_service = MimikreeService()
