"""
Email service for sending verification emails
Uses Resend API for reliable email delivery
"""
import os
import logging
import requests
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class EmailService:
    def __init__(self):
        # Resend API configuration
        self.resend_api_key = os.getenv('RESEND_API_KEY')
        self.from_email = os.getenv('FROM_EMAIL', 'onboarding@resend.dev')
        self.frontend_url = os.getenv('FRONTEND_URL', 'http://localhost:3000')
        self.resend_api_url = 'https://api.resend.com/emails'

        # Check if email is configured
        self.is_configured = bool(self.resend_api_key)

        if not self.is_configured:
            logger.warning("⚠️  Email service not configured. Set RESEND_API_KEY in .env")
        else:
            logger.info("✅ Email service configured with Resend API")

    def send_verification_email(self, to_email: str, verification_token: str, first_name: str) -> bool:
        """
        Send email verification link to user using Resend API

        Args:
            to_email: Recipient email address
            verification_token: Unique verification token
            first_name: User's first name for personalization

        Returns:
            bool: True if email sent successfully, False otherwise
        """
        if not self.is_configured:
            logger.error("❌ Cannot send email: RESEND_API_KEY not configured")
            logger.error("   Set RESEND_API_KEY in your .env file or Railway environment variables")
            return False

        try:
            # Create verification link
            verification_link = f"{self.frontend_url}/verify-email?token={verification_token}"

            # Email HTML body
            html_body = f"""
            <html>
                <head>
                    <style>
                        body {{
                            font-family: Arial, sans-serif;
                            line-height: 1.6;
                            color: #333;
                        }}
                        .container {{
                            max-width: 600px;
                            margin: 0 auto;
                            padding: 20px;
                            background-color: #f9f9f9;
                        }}
                        .header {{
                            background: linear-gradient(135deg, #FF8C42 0%, #FF6B35 100%);
                            color: white;
                            padding: 30px;
                            text-align: center;
                            border-radius: 10px 10px 0 0;
                            text-shadow: 0 2px 4px rgba(0, 0, 0, 0.2);
                        }}
                        .content {{
                            background-color: white;
                            padding: 30px;
                            border-radius: 0 0 10px 10px;
                        }}
                        .button {{
                            display: inline-block;
                            padding: 15px 30px;
                            margin: 20px 0;
                            background: linear-gradient(135deg, #FF8C42 0%, #FF6B35 100%);
                            color: white;
                            text-decoration: none;
                            border-radius: 8px;
                            font-weight: bold;
                            box-shadow: 0 4px 15px rgba(255, 140, 66, 0.4);
                            transition: all 0.3s ease;
                        }}
                        .button:hover {{
                            box-shadow: 0 6px 20px rgba(255, 140, 66, 0.6);
                            transform: translateY(-2px);
                        }}
                        .footer {{
                            margin-top: 20px;
                            text-align: center;
                            color: #666;
                            font-size: 12px;
                        }}
                    </style>
                </head>
                <body>
                    <div class="container">
                        <div class="header">
                            <h1>Welcome to Job Application Agent!</h1>
                        </div>
                        <div class="content">
                            <h2>Hi {first_name},</h2>
                            <p>Thank you for signing up! We're excited to have you on board.</p>
                            <p>To complete your registration and start applying to jobs automatically, please verify your email address by clicking the button below:</p>
                            <center>
                                <a href="{verification_link}" class="button">Verify Email Address</a>
                            </center>
                            <p>Or copy and paste this link into your browser:</p>
                            <p style="word-break: break-all; color: #FF8C42; font-weight: 500;">{verification_link}</p>
                            <p><strong>This link will expire in 24 hours.</strong></p>
                            <p>If you didn't create an account, you can safely ignore this email.</p>
                            <p>Best regards,<br>The Job Application Agent Team</p>
                        </div>
                        <div class="footer">
                            <p>This is an automated email. Please do not reply to this message.</p>
                        </div>
                    </div>
                </body>
            </html>
            """

            # Plain text version (fallback)
            text_body = f"""Hi {first_name},

Thank you for signing up for Job Application Agent!

To complete your registration, please verify your email address by clicking the link below:

{verification_link}

This link will expire in 24 hours.

If you didn't create an account, you can safely ignore this email.

Best regards,
The Job Application Agent Team
            """

            # Prepare Resend API request
            payload = {
                "from": self.from_email,
                "to": [to_email],
                "subject": "Verify Your Email - Job Application Agent",
                "html": html_body,
                "text": text_body
            }

            headers = {
                "Authorization": f"Bearer {self.resend_api_key}",
                "Content-Type": "application/json"
            }

            # Send email via Resend API
            response = requests.post(
                self.resend_api_url,
                json=payload,
                headers=headers,
                timeout=10
            )

            # Check response
            if response.status_code == 200:
                logger.info(f"✅ Verification email sent successfully to {to_email}")
                return True
            else:
                logger.error(f"❌ Resend API error: {response.status_code} - {response.text}")
                return False

        except requests.exceptions.RequestException as e:
            logger.error(f"❌ Network error sending email to {to_email}: {e}")
            return False
        except Exception as e:
            logger.error(f"❌ Failed to send verification email to {to_email}: {e}")
            return False

    def send_password_reset_email(self, to_email: str, reset_token: str, first_name: str) -> bool:
        """
        Send password reset link to user (for future implementation)

        Args:
            to_email: Recipient email address
            reset_token: Unique reset token
            first_name: User's first name

        Returns:
            bool: True if email sent successfully
        """
        # TODO: Implement password reset email
        pass

    def send_beta_approval_email(self, to_email: str, first_name: str) -> bool:
        """
        Send beta access approval notification to user

        Args:
            to_email: Recipient email address
            first_name: User's first name for personalization

        Returns:
            bool: True if email sent successfully, False otherwise
        """
        if not self.is_configured:
            logger.error("Cannot send email: RESEND_API_KEY not configured")
            return False

        try:
            # Email HTML body
            html_body = f"""
            <html>
                <head>
                    <style>
                        body {{
                            font-family: Arial, sans-serif;
                            line-height: 1.6;
                            color: #333;
                        }}
                        .container {{
                            max-width: 600px;
                            margin: 0 auto;
                            padding: 20px;
                            background-color: #f9f9f9;
                        }}
                        .header {{
                            background: linear-gradient(135deg, #4CAF50 0%, #45a049 100%);
                            color: white;
                            padding: 30px;
                            text-align: center;
                            border-radius: 10px 10px 0 0;
                            text-shadow: 0 2px 4px rgba(0, 0, 0, 0.2);
                        }}
                        .content {{
                            background-color: white;
                            padding: 30px;
                            border-radius: 0 0 10px 10px;
                        }}
                        .button {{
                            display: inline-block;
                            padding: 15px 30px;
                            margin: 20px 0;
                            background: linear-gradient(135deg, #FF8C42 0%, #FF6B35 100%);
                            color: white;
                            text-decoration: none;
                            border-radius: 8px;
                            font-weight: bold;
                            box-shadow: 0 4px 15px rgba(255, 140, 66, 0.4);
                        }}
                        .badge {{
                            display: inline-block;
                            padding: 8px 16px;
                            background: linear-gradient(135deg, #4CAF50 0%, #45a049 100%);
                            color: white;
                            border-radius: 20px;
                            font-weight: bold;
                            margin: 10px 0;
                        }}
                        .footer {{
                            margin-top: 20px;
                            text-align: center;
                            color: #666;
                            font-size: 12px;
                        }}
                    </style>
                </head>
                <body>
                    <div class="container">
                        <div class="header">
                            <h1>🎉 Welcome to Beta!</h1>
                        </div>
                        <div class="content">
                            <h2>Hi {first_name},</h2>
                            <p>Great news! Your beta access request has been approved!</p>
                            <center>
                                <div class="badge">BETA ACCESS APPROVED</div>
                            </center>
                            <p>You now have full access to Job Application Agent. Here's what you can do:</p>
                            <ul>
                                <li>Automatically apply to jobs on multiple platforms</li>
                                <li>Tailor your resume for each position</li>
                                <li>Track all your applications in one place</li>
                                <li>Save time with AI-powered automation</li>
                            </ul>
                            <p>Ready to get started? Log in to your dashboard and begin your job search journey!</p>
                            <center>
                                <a href="{self.frontend_url}/login" class="button">Go to Dashboard</a>
                            </center>
                            <p>We'd love to hear your feedback as you explore the platform. Feel free to reach out with any questions or suggestions!</p>
                            <p>Best regards,<br>The Job Application Agent Team</p>
                        </div>
                        <div class="footer">
                            <p>This is an automated email. Please do not reply to this message.</p>
                        </div>
                    </div>
                </body>
            </html>
            """

            # Plain text version (fallback)
            text_body = f"""Hi {first_name},

Great news! Your beta access request has been approved!

You now have full access to Job Application Agent. Log in to your dashboard and start applying to jobs automatically.

Visit: {self.frontend_url}/login

We'd love to hear your feedback as you explore the platform!

Best regards,
The Job Application Agent Team
            """

            # Prepare Resend API request
            payload = {
                "from": self.from_email,
                "to": [to_email],
                "subject": "🎉 Your Beta Access is Approved!",
                "html": html_body,
                "text": text_body
            }

            headers = {
                "Authorization": f"Bearer {self.resend_api_key}",
                "Content-Type": "application/json"
            }

            # Send email via Resend API
            response = requests.post(
                self.resend_api_url,
                json=payload,
                headers=headers,
                timeout=10
            )

            # Check response
            if response.status_code == 200:
                logger.info(f"Beta approval email sent successfully to {to_email}")
                return True
            else:
                logger.error(f"Resend API error: {response.status_code} - {response.text}")
                return False

        except Exception as e:
            logger.error(f"Failed to send beta approval email to {to_email}: {e}")
            return False

# Global instance
email_service = EmailService()
