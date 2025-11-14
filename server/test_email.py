"""
Quick test script to verify email sending works
"""
import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

from email_service import email_service
import logging

logging.basicConfig(level=logging.INFO)

def test_email_service():
    print("\n=== Email Service Configuration ===")
    print(f"SMTP Server: {email_service.smtp_server}")
    print(f"SMTP Port: {email_service.smtp_port}")
    print(f"From Email: {email_service.from_email}")
    print(f"Frontend URL: {email_service.frontend_url}")
    print(f"Is Configured: {email_service.is_configured}")
    
    if not email_service.is_configured:
        print("\n❌ Email service is NOT configured!")
        print("Please set SMTP_USERNAME and SMTP_PASSWORD in .env file")
        return False
    
    print("\n✅ Email service is configured")
    
    # Test sending verification email
    print("\n=== Testing Email Send ===")
    test_email = email_service.from_email  # Send to yourself for testing
    test_token = "TEST_TOKEN_123"
    test_name = "Test User"
    
    print(f"Attempting to send test email to: {test_email}")
    
    try:
        result = email_service.send_verification_email(
            to_email=test_email,
            verification_token=test_token,
            first_name=test_name
        )
        
        if result:
            print("\n✅ Test email sent successfully!")
            print(f"Check your inbox at: {test_email}")
            print("(Check spam folder if you don't see it)")
            return True
        else:
            print("\n❌ Failed to send test email")
            print("Check the logs above for error details")
            return False
            
    except Exception as e:
        print(f"\n❌ Error sending email: {e}")
        return False

if __name__ == "__main__":
    success = test_email_service()
    sys.exit(0 if success else 1)

