"""
Email Configuration Checker
This script helps diagnose email sending issues
"""
import os
import sys
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

def check_email_configuration():
    """Check if email is properly configured"""
    print("\n" + "=" * 60)
    print("🔍 EMAIL CONFIGURATION CHECKER")
    print("=" * 60 + "\n")
    
    # Check required environment variables
    required_vars = {
        'SMTP_SERVER': os.getenv('SMTP_SERVER'),
        'SMTP_PORT': os.getenv('SMTP_PORT'),
        'SMTP_USERNAME': os.getenv('SMTP_USERNAME'),
        'SMTP_PASSWORD': os.getenv('SMTP_PASSWORD'),
        'FROM_EMAIL': os.getenv('FROM_EMAIL'),
        'FRONTEND_URL': os.getenv('FRONTEND_URL')
    }
    
    all_configured = True
    
    print("📋 Environment Variables:")
    print("-" * 60)
    for var_name, var_value in required_vars.items():
        if var_value:
            # Mask password for security
            if 'PASSWORD' in var_name:
                display_value = '*' * 16 + f' (length: {len(var_value)})'
            else:
                display_value = var_value
            print(f"✅ {var_name}: {display_value}")
        else:
            print(f"❌ {var_name}: NOT SET")
            all_configured = False
    
    print("\n" + "-" * 60)
    
    if not all_configured:
        print("\n⚠️  CONFIGURATION INCOMPLETE")
        print("\nPlease set the missing environment variables in your .env file:")
        print("\nExample .env configuration for Gmail:")
        print("""
SMTP_SERVER=smtp.gmail.com
SMTP_PORT=587
SMTP_USERNAME=your-email@gmail.com
SMTP_PASSWORD=your-app-password
FROM_EMAIL=your-email@gmail.com
FRONTEND_URL=http://localhost:3000
        """)
        print("\n📝 For Gmail:")
        print("   1. Enable 2-Factor Authentication")
        print("   2. Go to Google Account → Security → App passwords")
        print("   3. Generate an app password for 'Mail'")
        print("   4. Use that 16-character password as SMTP_PASSWORD")
        print("\n" + "=" * 60)
        return False
    
    print("\n✅ All configuration variables are set!")
    
    # Test email sending
    print("\n" + "=" * 60)
    print("📧 TESTING EMAIL SEND")
    print("=" * 60 + "\n")
    
    try:
        from email_service import email_service
        
        if not email_service.is_configured:
            print("❌ Email service reports it's NOT configured")
            print("   This usually means SMTP_USERNAME or SMTP_PASSWORD is missing")
            return False
        
        print("✅ Email service is configured")
        print(f"\n📤 Attempting to send test email to: {email_service.from_email}")
        print("   (Sending to yourself for testing)\n")
        
        test_token = "TEST_TOKEN_12345"
        result = email_service.send_verification_email(
            to_email=email_service.from_email,
            verification_token=test_token,
            first_name="Test User"
        )
        
        if result:
            print("✅ TEST EMAIL SENT SUCCESSFULLY!")
            print(f"\n📬 Check your inbox at: {email_service.from_email}")
            print("   (Don't forget to check spam/junk folder)")
            print("\n" + "=" * 60)
            return True
        else:
            print("❌ Failed to send test email")
            print("\nPossible issues:")
            print("   • SMTP credentials are incorrect")
            print("   • Gmail App Password not generated correctly")
            print("   • SMTP server/port incorrect")
            print("   • Network/firewall blocking SMTP")
            print("\nCheck the server logs above for detailed error messages")
            print("\n" + "=" * 60)
            return False
            
    except Exception as e:
        print(f"❌ Error during email test: {e}")
        print("\n" + "=" * 60)
        return False

if __name__ == "__main__":
    success = check_email_configuration()
    sys.exit(0 if success else 1)

