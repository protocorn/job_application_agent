"""
Email service for sending verification emails
Supports SMTP (Gmail, etc.) and can be extended to use SendGrid, Resend, etc.
"""
import os
import logging
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class EmailService:
    def __init__(self):
        # Email configuration from environment variables
        self.smtp_server = os.getenv('SMTP_SERVER', 'smtp.gmail.com')
        self.smtp_port = int(os.getenv('SMTP_PORT', '587'))
        self.smtp_username = os.getenv('SMTP_USERNAME')
        self.smtp_password = os.getenv('SMTP_PASSWORD')
        self.from_email = os.getenv('FROM_EMAIL', self.smtp_username)
        self.frontend_url = os.getenv('FRONTEND_URL', 'http://localhost:3000')

        # Check if email is configured
        self.is_configured = bool(self.smtp_username and self.smtp_password)

        if not self.is_configured:
            logger.warning("Email service not configured. Set SMTP_USERNAME and SMTP_PASSWORD in .env")

    def send_verification_email(self, to_email: str, verification_token: str, first_name: str) -> bool:
        """
        Send email verification link to user

        Args:
            to_email: Recipient email address
            verification_token: Unique verification token
            first_name: User's first name for personalization

        Returns:
            bool: True if email sent successfully, False otherwise
        """
        if not self.is_configured:
            logger.error("Cannot send email: SMTP not configured")
            return False

        try:
            # Create verification link
            verification_link = f"{self.frontend_url}/verify-email?token={verification_token}"

            # Create email message
            message = MIMEMultipart('alternative')
            message['Subject'] = 'Verify Your Email - Job Application Agent'
            message['From'] = self.from_email
            message['To'] = to_email

            # Email body (HTML version)
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
                            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                            color: white;
                            padding: 30px;
                            text-align: center;
                            border-radius: 10px 10px 0 0;
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
                            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                            color: white;
                            text-decoration: none;
                            border-radius: 8px;
                            font-weight: bold;
                            box-shadow: 0 4px 15px rgba(102, 126, 234, 0.3);
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
                            <p style="word-break: break-all; color: #667eea;">{verification_link}</p>
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
            text_body = f"""
            Hi {first_name},

            Thank you for signing up for Job Application Agent!

            To complete your registration, please verify your email address by clicking the link below:

            {verification_link}

            This link will expire in 24 hours.

            If you didn't create an account, you can safely ignore this email.

            Best regards,
            The Job Application Agent Team
            """

            # Attach both versions
            part1 = MIMEText(text_body, 'plain')
            part2 = MIMEText(html_body, 'html')
            message.attach(part1)
            message.attach(part2)

            # Send email
            with smtplib.SMTP(self.smtp_server, self.smtp_port) as server:
                server.starttls()
                server.login(self.smtp_username, self.smtp_password)
                server.send_message(message)

            logger.info(f"Verification email sent successfully to {to_email}")
            return True

        except Exception as e:
            logger.error(f"Failed to send verification email to {to_email}: {e}")
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

# Global instance
email_service = EmailService()
