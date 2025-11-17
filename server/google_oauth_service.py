import os
import logging
from datetime import datetime, timedelta
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from cryptography.fernet import Fernet
from database_config import SessionLocal, User
from typing import Optional, Dict, Any, Union
import uuid

# OAuth Configuration
SCOPES = [
    'https://www.googleapis.com/auth/documents',
    'https://www.googleapis.com/auth/drive',  # Full Drive access needed for resume tailoring
    'https://www.googleapis.com/auth/userinfo.email',
    'openid'
]

# Get OAuth credentials from environment
CLIENT_ID = os.getenv('GOOGLE_CLIENT_ID')
CLIENT_SECRET = os.getenv('GOOGLE_CLIENT_SECRET')
REDIRECT_URI = os.getenv('GOOGLE_REDIRECT_URI', 'http://localhost:5000/api/oauth/callback')

# Encryption key for storing tokens (store this securely in production)
ENCRYPTION_KEY = os.getenv('ENCRYPTION_KEY', Fernet.generate_key())
cipher_suite = Fernet(ENCRYPTION_KEY if isinstance(ENCRYPTION_KEY, bytes) else ENCRYPTION_KEY.encode())


class GoogleOAuthService:
    """Service for managing Google OAuth authentication for users"""

    @staticmethod
    def _convert_user_id(user_id: Union[str, uuid.UUID]) -> uuid.UUID:
        """Convert user_id string to UUID"""
        if isinstance(user_id, uuid.UUID):
            return user_id
        try:
            return uuid.UUID(str(user_id))
        except (ValueError, AttributeError, TypeError) as e:
            raise ValueError(f"Invalid user ID format: {user_id}")

    @staticmethod
    def get_authorization_url(user_id: Union[str, uuid.UUID]) -> str:
        """
        Generate Google OAuth authorization URL for a user

        Args:
            user_id: User ID (UUID or string) to associate with this OAuth flow

        Returns:
            Authorization URL to redirect user to
        """
        flow = Flow.from_client_config(
            {
                "web": {
                    "client_id": CLIENT_ID,
                    "client_secret": CLIENT_SECRET,
                    "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                    "token_uri": "https://oauth2.googleapis.com/token",
                    "redirect_uris": [REDIRECT_URI]
                }
            },
            scopes=SCOPES,
            redirect_uri=REDIRECT_URI
        )

        # Store user_id in state parameter for callback
        authorization_url, state = flow.authorization_url(
            access_type='offline',
            include_granted_scopes='true',
            state=str(user_id),
            prompt='consent'  # Force consent to get refresh token
        )

        return authorization_url

    @staticmethod
    def handle_oauth_callback(code: str, user_id: Union[str, uuid.UUID]) -> Dict[str, Any]:
        """
        Handle OAuth callback and store tokens for user

        Args:
            code: Authorization code from Google
            user_id: User ID (UUID or string) to store tokens for

        Returns:
            Dictionary with success status and message
        """
        try:
            # Convert user_id to UUID
            user_uuid = GoogleOAuthService._convert_user_id(user_id)
            # Exchange authorization code for tokens directly - no scope validation
            import requests
            token_response = requests.post(
                'https://oauth2.googleapis.com/token',
                data={
                    'code': code,
                    'client_id': CLIENT_ID,
                    'client_secret': CLIENT_SECRET,
                    'redirect_uri': REDIRECT_URI,
                    'grant_type': 'authorization_code'
                }
            )

            if token_response.status_code != 200:
                raise Exception(f"Token exchange failed: {token_response.text}")

            token_data = token_response.json()

            # Create credentials from the token response
            credentials = Credentials(
                token=token_data['access_token'],
                refresh_token=token_data.get('refresh_token'),
                token_uri="https://oauth2.googleapis.com/token",
                client_id=CLIENT_ID,
                client_secret=CLIENT_SECRET,
                scopes=token_data.get('scope', '').split(),
                expiry=datetime.now() + timedelta(seconds=token_data.get('expires_in', 3600))
            )

            # Get user email from token info (no API call needed)
            try:
                import requests
                token_info_url = f"https://oauth2.googleapis.com/tokeninfo?access_token={credentials.token}"
                response = requests.get(token_info_url)
                token_info = response.json()
                google_email = token_info.get('email', 'Unknown')
            except Exception as e:
                logging.warning(f"Could not fetch email from token info: {e}")
                google_email = 'Connected Account'

            # Encrypt and store tokens
            db = SessionLocal()
            try:
                user = db.query(User).filter(User.id == user_uuid).first()
                if not user:
                    return {'success': False, 'error': 'User not found'}

                # Encrypt tokens before storing
                encrypted_refresh = GoogleOAuthService._encrypt_token(credentials.refresh_token) if credentials.refresh_token else None
                encrypted_access = GoogleOAuthService._encrypt_token(credentials.token)

                user.google_refresh_token = encrypted_refresh
                user.google_access_token = encrypted_access
                user.google_token_expiry = credentials.expiry
                user.google_account_email = google_email

                db.commit()

                logging.info(f"Successfully stored Google OAuth tokens for user {user_uuid}")
                return {
                    'success': True,
                    'message': 'Google account connected successfully',
                    'google_email': google_email
                }

            except Exception as e:
                db.rollback()
                logging.error(f"Error storing OAuth tokens: {e}")
                return {'success': False, 'error': 'Failed to store tokens'}
            finally:
                db.close()

        except Exception as e:
            logging.error(f"Error in OAuth callback: {e}")
            return {'success': False, 'error': str(e)}

    @staticmethod
    def get_credentials(user_id: Union[str, uuid.UUID]) -> Optional[Credentials]:
        """
        Get valid Google credentials for a user
        Refreshes token if expired

        Args:
            user_id: User ID (UUID or string)

        Returns:
            Google Credentials object or None
        """
        db = SessionLocal()
        try:
            # Convert user_id to UUID
            user_uuid = GoogleOAuthService._convert_user_id(user_id)

            user = db.query(User).filter(User.id == user_uuid).first()
            if not user or not user.google_refresh_token:
                logging.warning(f"No Google tokens found for user {user_uuid}")
                return None

            # Decrypt tokens
            refresh_token = GoogleOAuthService._decrypt_token(user.google_refresh_token)
            access_token = GoogleOAuthService._decrypt_token(user.google_access_token)

            credentials = Credentials(
                token=access_token,
                refresh_token=refresh_token,
                token_uri="https://oauth2.googleapis.com/token",
                client_id=CLIENT_ID,
                client_secret=CLIENT_SECRET,
                scopes=SCOPES
            )

            # Refresh if expired
            if credentials.expired and credentials.refresh_token:
                try:
                    credentials.refresh(Request())

                    # Update stored tokens
                    user.google_access_token = GoogleOAuthService._encrypt_token(credentials.token)
                    user.google_token_expiry = credentials.expiry
                    db.commit()

                    logging.info(f"Refreshed Google token for user {user_uuid}")
                except Exception as refresh_error:
                    # If refresh fails (invalid_grant), clear the tokens
                    if 'invalid_grant' in str(refresh_error).lower():
                        logging.warning(f"Invalid grant error for user {user_uuid}, clearing tokens")
                        user.google_access_token = None
                        user.google_refresh_token = None
                        user.google_token_expiry = None
                        db.commit()
                        return None
                    raise

            return credentials

        except Exception as e:
            logging.error(f"Error getting credentials for user {user_uuid}: {e}")
            # If it's an invalid_grant error, clear tokens
            if 'invalid_grant' in str(e).lower():
                try:
                    user = db.query(User).filter(User.id == user_uuid).first()
                    if user:
                        user.google_access_token = None
                        user.google_refresh_token = None
                        user.google_token_expiry = None
                        db.commit()
                        logging.info(f"Cleared invalid tokens for user {user_uuid}")
                except Exception as clear_error:
                    logging.error(f"Error clearing tokens: {clear_error}")
            return None
        finally:
            db.close()

    @staticmethod
    def disconnect_google_account(user_id: Union[str, uuid.UUID]) -> Dict[str, Any]:
        """
        Disconnect Google account for a user

        Args:
            user_id: User ID (UUID or string)

        Returns:
            Dictionary with success status
        """
        db = SessionLocal()
        try:
            # Convert user_id to UUID
            user_uuid = GoogleOAuthService._convert_user_id(user_id)

            user = db.query(User).filter(User.id == user_uuid).first()
            if not user:
                return {'success': False, 'error': 'User not found'}

            user.google_refresh_token = None
            user.google_access_token = None
            user.google_token_expiry = None
            user.google_account_email = None

            db.commit()

            logging.info(f"Disconnected Google account for user {user_uuid}")
            return {'success': True, 'message': 'Google account disconnected'}

        except Exception as e:
            db.rollback()
            logging.error(f"Error disconnecting Google account: {e}")
            return {'success': False, 'error': str(e)}
        finally:
            db.close()

    @staticmethod
    def is_connected(user_id: Union[str, uuid.UUID]) -> bool:
        """
        Check if user has connected Google account

        Args:
            user_id: User ID (UUID or string)

        Returns:
            True if connected, False otherwise
        """
        db = SessionLocal()
        try:
            # Convert user_id to UUID
            user_uuid = GoogleOAuthService._convert_user_id(user_id)

            user = db.query(User).filter(User.id == user_uuid).first()
            return user and user.google_refresh_token is not None
        finally:
            db.close()

    @staticmethod
    def get_google_email(user_id: Union[str, uuid.UUID]) -> Optional[str]:
        """
        Get connected Google account email

        Args:
            user_id: User ID (UUID or string)

        Returns:
            Google email or None
        """
        db = SessionLocal()
        try:
            # Convert user_id to UUID
            user_uuid = GoogleOAuthService._convert_user_id(user_id)

            user = db.query(User).filter(User.id == user_uuid).first()
            return user.google_account_email if user else None
        finally:
            db.close()

    @staticmethod
    def _encrypt_token(token: str) -> str:
        """Encrypt a token for storage"""
        if not token:
            return None
        return cipher_suite.encrypt(token.encode()).decode()

    @staticmethod
    def _decrypt_token(encrypted_token: str) -> str:
        """Decrypt a stored token"""
        if not encrypted_token:
            return None
        return cipher_suite.decrypt(encrypted_token.encode()).decode()
