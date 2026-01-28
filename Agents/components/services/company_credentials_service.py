"""
Service for managing company-specific credentials (auto-generated passwords for Workday accounts)
"""
import string
import secrets
from datetime import datetime
from typing import Optional, Dict
from loguru import logger
from cryptography.fernet import Fernet
import os


class PasswordGenerator:
    """Generate secure passwords that meet Workday requirements"""
    
    @staticmethod
    def generate_workday_password(length: int = 16) -> str:
        """
        Generate a password that meets Workday requirements:
        - Minimum 8 characters
        - An uppercase character
        - A lowercase character
        - A special character
        - An alphabetic character
        - A numeric character
        
        Args:
            length: Length of password (default 16 for extra security)
        
        Returns:
            A secure password meeting all requirements
        """
        if length < 8:
            length = 8
        
        # Character sets
        uppercase = string.ascii_uppercase
        lowercase = string.ascii_lowercase
        digits = string.digits
        special = "!@#$%^&*"  # Common special characters that work on most platforms
        
        # Ensure at least one character from each required category
        password_chars = [
            secrets.choice(uppercase),    # Uppercase
            secrets.choice(lowercase),    # Lowercase
            secrets.choice(digits),       # Numeric
            secrets.choice(special),      # Special
        ]
        
        # Fill the rest with random characters from all sets
        all_chars = uppercase + lowercase + digits + special
        password_chars.extend(secrets.choice(all_chars) for _ in range(length - 4))
        
        # Shuffle to avoid predictable patterns
        secrets.SystemRandom().shuffle(password_chars)
        
        return ''.join(password_chars)


class CompanyCredentialsService:
    """
    Service for managing company-specific credentials
    Stores auto-generated passwords for Workday and other ATS accounts
    """
    
    def __init__(self, db_session=None):
        """
        Initialize the service
        
        Args:
            db_session: Optional SQLAlchemy database session (creates its own if not provided)
        """
        self.db = db_session
        self.owns_session = db_session is None
        if self.owns_session:
            import sys
            sys.path.append(os.path.join(os.path.dirname(__file__), '../../..'))
            from database_config import SessionLocal
            self.db = SessionLocal()
        
        self.encryption_key = self._get_or_create_encryption_key()
        self.cipher_suite = Fernet(self.encryption_key)
    
    def close(self):
        """Close the database session if we own it"""
        if self.owns_session and self.db:
            self.db.close()
    
    def _get_or_create_encryption_key(self) -> bytes:
        """
        Get or create an encryption key for password storage
        In production, this should be stored securely (e.g., environment variable)
        """
        key_env = os.getenv('CREDENTIALS_ENCRYPTION_KEY')
        if key_env:
            return key_env.encode()
        
        # For development, use a fixed key (DO NOT use in production)
        # In production, generate with: Fernet.generate_key()
        logger.warning("⚠️ Using default encryption key - set CREDENTIALS_ENCRYPTION_KEY in production!")
        return b'rSQmE9vR8xJ0hKd8fLNdP4pO9bY5zW3yT6uA2sD7fG8='  # Base64 encoded key
    
    def _encrypt_password(self, password: str) -> str:
        """Encrypt a password for storage"""
        encrypted = self.cipher_suite.encrypt(password.encode())
        return encrypted.decode()
    
    def _decrypt_password(self, encrypted_password: str) -> str:
        """Decrypt a password from storage"""
        decrypted = self.cipher_suite.decrypt(encrypted_password.encode())
        return decrypted.decode()
    
    def get_credentials(self, user_id: str, company_domain: str) -> Optional[Dict[str, str]]:
        """
        Retrieve credentials for a company
        
        Args:
            user_id: UUID of the user
            company_domain: Domain of the company (e.g., 'troutmanpepper.com')
        
        Returns:
            Dict with 'email' and 'password' if found, None otherwise
        """
        import sys
        sys.path.append(os.path.join(os.path.dirname(__file__), '../../..'))
        from database_config import CompanyCredentials
        from uuid import UUID
        
        try:
            # Convert string to UUID if needed
            if isinstance(user_id, str):
                user_id = UUID(user_id)
            
            credential = self.db.query(CompanyCredentials).filter(
                CompanyCredentials.user_id == user_id,
                CompanyCredentials.company_domain == company_domain
            ).first()
            
            if credential:
                return {
                    'email': credential.email,
                    'password': self._decrypt_password(credential.password_encrypted),
                    'created_at': credential.created_at
                }
            return None
        except Exception as e:
            logger.error(f"Error retrieving credentials for {company_domain}: {e}")
            return None
    
    def save_credentials(
        self,
        user_id: str,
        company_name: str,
        company_domain: str,
        email: str,
        password: str,
        ats_type: str = 'workday'
    ) -> bool:
        """
        Save or update credentials for a company
        
        Args:
            user_id: UUID of the user
            company_name: Display name of the company
            company_domain: Domain of the company
            email: Email used for the account
            password: Plain-text password to encrypt and store
            ats_type: Type of ATS (workday, greenhouse, etc.)
        
        Returns:
            True if successful, False otherwise
        """
        import sys
        sys.path.append(os.path.join(os.path.dirname(__file__), '../../..'))
        from database_config import CompanyCredentials
        from uuid import UUID
        
        try:
            # Convert string to UUID if needed
            if isinstance(user_id, str):
                user_id = UUID(user_id)
            
            # Check if credentials already exist
            existing = self.db.query(CompanyCredentials).filter(
                CompanyCredentials.user_id == user_id,
                CompanyCredentials.company_domain == company_domain
            ).first()
            
            encrypted_password = self._encrypt_password(password)
            
            if existing:
                # Update existing credentials
                existing.email = email
                existing.password_encrypted = encrypted_password
                existing.ats_type = ats_type
                existing.updated_at = datetime.utcnow()
                logger.info(f"✅ Updated credentials for {company_name}")
            else:
                # Create new credentials
                new_credential = CompanyCredentials(
                    user_id=user_id,
                    company_name=company_name,
                    company_domain=company_domain,
                    email=email,
                    password_encrypted=encrypted_password,
                    ats_type=ats_type
                )
                self.db.add(new_credential)
                logger.info(f"✅ Saved new credentials for {company_name}")
            
            self.db.commit()
            return True
        
        except Exception as e:
            self.db.rollback()
            logger.error(f"Error saving credentials for {company_name}: {e}")
            return False
    
    def generate_and_save_credentials(
        self,
        user_id: str,
        company_name: str,
        company_domain: str,
        email: str,
        ats_type: str = 'workday'
    ) -> Optional[str]:
        """
        Generate a new password and save credentials
        
        Args:
            user_id: UUID of the user
            company_name: Display name of the company
            company_domain: Domain of the company
            email: Email to use for the account
            ats_type: Type of ATS
        
        Returns:
            The generated password if successful, None otherwise
        """
        try:
            # Generate password
            password = PasswordGenerator.generate_workday_password()
            
            # Save to database
            success = self.save_credentials(
                user_id=user_id,
                company_name=company_name,
                company_domain=company_domain,
                email=email,
                password=password,
                ats_type=ats_type
            )
            
            if success:
                logger.info(f"🔐 Generated and saved credentials for {company_name}")
                return password
            else:
                return None
        
        except Exception as e:
            logger.error(f"Error generating credentials for {company_name}: {e}")
            return None

