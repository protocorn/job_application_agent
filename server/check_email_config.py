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
    print("🔍 EMAIL CONFIGURATION CHECKER (Resend API)")
    print("=" * 60 + "\n")
    
    # Check required environment variables
    required_vars = {
        'RESEND_API_KEY': os.getenv('RESEND_API_KEY'),
        'FROM_EMAIL': os.getenv('FROM_EMAIL'),
        'FRONTEND_URL': os.getenv('FRONTEND_URL')
    }
    
    all_configured = True
    
    print("📋 Environment Variables:")
    print("-" * 60)
    for var_name, var_value in required_vars.items():
        if var_value:
            # Mask API key for security
            if 'API_KEY' in var_name or 'PASSWORD' in var_name:
                display_value = var_value[:7] + '***' + f' (length: {len(var_value)})'
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
        print("\nExample .env configuration for Resend:")
        print("""
RESEND_API_KEY=re_xxxxxxxxxxxxxxxxxxxxxxxxxx
FROM_EMAIL=onboarding@resend.dev
FRONTEND_URL=http://localhost:3000
        """)
        print("\n📝 To get your Resend API key:")
        print("   1. Go to https://resend.com/")
        print("   2. Sign up for free account")
        print("   3. Go to API Keys section")
        print("   4. Create new API key")
        print("   5. Copy and use as RESEND_API_KEY")
        print("\n" + "=" * 60)
        return False
    
    print("\n✅ All configuration variables are set!")
    
    # Test email sending
    print("\n" + "=" * 60)
    print("📧 TESTING EMAIL SEND (Resend API)")
    print("=" * 60 + "\n")
    
    try:
        from email_service import email_service
        
        if not email_service.is_configured:
            print("❌ Email service reports it's NOT configured")
            print("   This usually means RESEND_API_KEY is missing")
            return False
        
        print("✅ Email service is configured with Resend API")
        
        # For Resend, ask user for test email
        test_email = input("\n📧 Enter email address to send test to (or press Enter to skip): ").strip()
        
        if not test_email:
            print("\n⏭️  Skipping test email send")
            print("✅ Configuration looks good!")
            print("\n" + "=" * 60)
            return True
        
        print(f"\n📤 Attempting to send test email to: {test_email}\n")
        
        test_token = "TEST_TOKEN_12345"
        result = email_service.send_verification_email(
            to_email=test_email,
            verification_token=test_token,
            first_name="Test User"
        )
        
        if result:
            print("\n✅ TEST EMAIL SENT SUCCESSFULLY!")
            print(f"\n📬 Check your inbox at: {test_email}")
            print("   (Don't forget to check spam/junk folder)")
            print("\n💡 Tip: Check Resend dashboard for email status:")
            print("   https://resend.com/emails")
            print("\n" + "=" * 60)
            return True
        else:
            print("\n❌ Failed to send test email")
            print("\nPossible issues:")
            print("   • RESEND_API_KEY is incorrect")
            print("   • FROM_EMAIL is not verified in Resend")
            print("   • Network connectivity issues")
            print("\nCheck the server logs above for detailed error messages")
            print("\n💡 Check Resend dashboard: https://resend.com/")
            print("\n" + "=" * 60)
            return False
            
    except Exception as e:
        print(f"❌ Error during email test: {e}")
        print("\n" + "=" * 60)
        return False

if __name__ == "__main__":
    success = check_email_configuration()
    sys.exit(0 if success else 1)

