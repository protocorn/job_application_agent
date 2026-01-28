"""
Test script for company credentials functionality
Tests password generation, encryption, and database operations
"""
import sys
import os
from dotenv import load_dotenv

# Add current directory to path
sys.path.append(os.path.dirname(__file__))
sys.path.append(os.path.join(os.path.dirname(__file__), 'Agents'))

load_dotenv()

from components.services.company_credentials_service import PasswordGenerator, CompanyCredentialsService
from loguru import logger


def test_password_generation():
    """Test that generated passwords meet Workday requirements"""
    logger.info("🧪 Testing password generation...")
    
    # Generate multiple passwords
    for i in range(5):
        password = PasswordGenerator.generate_workday_password()
        logger.info(f"  Generated password {i+1}: {password}")
        
        # Verify requirements
        assert len(password) >= 8, "Password must be at least 8 characters"
        assert any(c.isupper() for c in password), "Password must have uppercase"
        assert any(c.islower() for c in password), "Password must have lowercase"
        assert any(c.isdigit() for c in password), "Password must have digit"
        assert any(c in "!@#$%^&*" for c in password), "Password must have special char"
        
    logger.info("✅ Password generation tests passed!")


def test_encryption_decryption():
    """Test password encryption and decryption"""
    logger.info("🧪 Testing encryption/decryption...")
    
    service = CompanyCredentialsService()
    
    # Test password
    original_password = "TestP@ssw0rd123"
    
    # Encrypt
    encrypted = service._encrypt_password(original_password)
    logger.info(f"  Original: {original_password}")
    logger.info(f"  Encrypted: {encrypted[:50]}..." if len(encrypted) > 50 else f"  Encrypted: {encrypted}")
    
    # Decrypt
    decrypted = service._decrypt_password(encrypted)
    logger.info(f"  Decrypted: {decrypted}")
    
    # Verify
    assert decrypted == original_password, "Decrypted password doesn't match original"
    
    service.close()
    logger.info("✅ Encryption/decryption tests passed!")


def test_database_operations():
    """Test saving and retrieving credentials from database"""
    logger.info("🧪 Testing database operations...")
    
    try:
        # Get a test user from the database
        from database_config import SessionLocal, User
        db = SessionLocal()
        test_user = db.query(User).first()
        
        if not test_user:
            logger.warning("⚠️ No users in database, skipping database tests")
            db.close()
            return
        
        user_id = str(test_user.id)
        logger.info(f"  Using test user: {test_user.email}")
        db.close()
        
        # Create service
        service = CompanyCredentialsService()
        
        # Test data
        test_company = "Test Company"
        test_domain = "testcompany.test.com"
        test_email = "test@example.com"
        
        # Generate and save credentials
        logger.info(f"  Saving credentials for {test_company}...")
        password = service.generate_and_save_credentials(
            user_id=user_id,
            company_name=test_company,
            company_domain=test_domain,
            email=test_email,
            ats_type='workday'
        )
        
        assert password is not None, "Failed to generate password"
        logger.info(f"  Generated and saved password: {password}")
        
        # Retrieve credentials
        logger.info(f"  Retrieving credentials for {test_domain}...")
        credentials = service.get_credentials(user_id, test_domain)
        
        assert credentials is not None, "Failed to retrieve credentials"
        assert credentials['email'] == test_email, "Email doesn't match"
        assert credentials['password'] == password, "Password doesn't match"
        logger.info(f"  Retrieved: email={credentials['email']}, password={credentials['password']}")
        
        # Test updating (should update, not create new)
        logger.info(f"  Updating credentials for {test_domain}...")
        new_password = PasswordGenerator.generate_workday_password()
        success = service.save_credentials(
            user_id=user_id,
            company_name=test_company,
            company_domain=test_domain,
            email=test_email,
            password=new_password,
            ats_type='workday'
        )
        
        assert success, "Failed to update credentials"
        
        # Verify update
        credentials = service.get_credentials(user_id, test_domain)
        assert credentials['password'] == new_password, "Password not updated"
        logger.info(f"  Updated password: {credentials['password']}")
        
        # Cleanup - delete test credentials
        logger.info(f"  Cleaning up test data...")
        from database_config import CompanyCredentials
        db = SessionLocal()
        db.query(CompanyCredentials).filter(
            CompanyCredentials.company_domain == test_domain
        ).delete()
        db.commit()
        db.close()
        
        service.close()
        logger.info("✅ Database operations tests passed!")
        
    except Exception as e:
        logger.error(f"❌ Database test failed: {e}")
        import traceback
        logger.error(traceback.format_exc())
        raise


def main():
    """Run all tests"""
    logger.info("=" * 60)
    logger.info("Starting Company Credentials Tests")
    logger.info("=" * 60)
    
    try:
        # Test 1: Password generation
        test_password_generation()
        print()
        
        # Test 2: Encryption/Decryption
        test_encryption_decryption()
        print()
        
        # Test 3: Database operations
        test_database_operations()
        print()
        
        logger.info("=" * 60)
        logger.info("✅ All tests passed successfully!")
        logger.info("=" * 60)
        
    except AssertionError as e:
        logger.error(f"❌ Test failed: {e}")
        sys.exit(1)
    except Exception as e:
        logger.error(f"❌ Unexpected error: {e}")
        import traceback
        logger.error(traceback.format_exc())
        sys.exit(1)


if __name__ == "__main__":
    main()

