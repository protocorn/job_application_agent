"""
Email service for sending verification emails
Uses Resend API for reliable email delivery
"""
import os
import logging
import requests
from flask import has_request_context, request
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
        self.backend_url = (os.getenv('BACKEND_URL') or '').strip().rstrip('/')
        if not self.backend_url:
            # Railway commonly exposes one of these variables in production.
            railway_domain = (os.getenv('RAILWAY_PUBLIC_DOMAIN') or os.getenv('RAILWAY_STATIC_URL') or '').strip()
            if railway_domain:
                if railway_domain.startswith('http'):
                    self.backend_url = railway_domain.rstrip('/')
                else:
                    self.backend_url = f"https://{railway_domain}".rstrip('/')
        self.resend_api_url = 'https://api.resend.com/emails'

        # Check if email is configured
        self.is_configured = bool(self.resend_api_key)

        if not self.is_configured:
            logger.warning("⚠️  Email service not configured. Set RESEND_API_KEY in .env")
        else:
            logger.info("✅ Email service configured with Resend API")

    def _verification_link(self, token: str) -> str:
        """
        Prefer backend verification links so email verification is device-agnostic.
        Fallback to frontend route for local/dev setups without a public backend URL.
        """
        backend_base_url = self._resolve_backend_base_url()
        if backend_base_url:
            return f"{backend_base_url}/api/auth/verify-email?token={token}&redirect=1"
        return f"{self.frontend_url}/verify-email?token={token}"

    def _email_change_verification_link(self, token: str) -> str:
        """Build the email-change verification URL with the same backend-first strategy."""
        backend_base_url = self._resolve_backend_base_url()
        if backend_base_url:
            return f"{backend_base_url}/api/auth/verify-email-change?token={token}&redirect=1"
        return f"{self.frontend_url}/verify-email-change?token={token}"

    def _resolve_backend_base_url(self) -> str:
        """
        Resolve the backend origin used in email links.
        Priority:
          1) BACKEND_URL / Railway env vars
          2) current request host (when called during a request)
        """
        if self.backend_url:
            return self.backend_url
        if has_request_context():
            return (request.host_url or '').rstrip('/')
        return ''

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
            verification_link = self._verification_link(verification_token)

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
                            <h1>Welcome to Launchway!</h1>
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
                            <p>Best regards,<br>The Launchway Team</p>
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

Thank you for signing up for Launchway!

To complete your registration, please verify your email address by clicking the link below:

{verification_link}

This link will expire in 24 hours.

If you didn't create an account, you can safely ignore this email.

Best regards,
The Launchway Team
            """

            # Prepare Resend API request
            payload = {
                "from": self.from_email,
                "to": [to_email],
                "subject": "Verify Your Email - Launchway",
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

    def send_email_change_verification(self, to_email: str, token: str, first_name: str, old_email: str) -> bool:
        """
        Send a verification link to a user's NEW email to confirm an email address change.

        Args:
            to_email: The new (pending) email address to verify
            token: Unique email-change token
            first_name: User's first name for personalisation
            old_email: The current email address (shown in the email for context)

        Returns:
            bool: True if sent successfully, False otherwise
        """
        if not self.is_configured:
            logger.error("Cannot send email: RESEND_API_KEY not configured")
            return False

        try:
            verification_link = self._email_change_verification_link(token)

            html_body = f"""
            <html>
                <head>
                    <style>
                        body {{ font-family: Arial, sans-serif; line-height: 1.6; color: #333; }}
                        .container {{ max-width: 600px; margin: 0 auto; padding: 20px; background-color: #f9f9f9; }}
                        .header {{ background: linear-gradient(135deg, #FF8C42 0%, #FF6B35 100%); color: white; padding: 30px; text-align: center; border-radius: 10px 10px 0 0; }}
                        .content {{ background-color: white; padding: 30px; border-radius: 0 0 10px 10px; }}
                        .button {{ display: inline-block; padding: 15px 30px; margin: 20px 0; background: linear-gradient(135deg, #FF8C42 0%, #FF6B35 100%); color: white; text-decoration: none; border-radius: 8px; font-weight: bold; }}
                        .alert {{ background: #fff3cd; border: 1px solid #ffc107; border-radius: 6px; padding: 12px 16px; margin: 16px 0; color: #856404; }}
                        .footer {{ margin-top: 20px; text-align: center; color: #666; font-size: 12px; }}
                    </style>
                </head>
                <body>
                    <div class="container">
                        <div class="header"><h1>Confirm Email Change - Launchway</h1></div>
                        <div class="content">
                            <h2>Hi {first_name},</h2>
                            <p>You requested to change your Launchway account email address.</p>
                            <table style="width:100%; border-collapse:collapse; margin:16px 0;">
                                <tr><td style="padding:6px; color:#666; width:120px;">Current email:</td><td style="padding:6px; font-weight:bold;">{old_email}</td></tr>
                                <tr><td style="padding:6px; color:#666;">New email:</td><td style="padding:6px; font-weight:bold;">{to_email}</td></tr>
                            </table>
                            <p>Click the button below to confirm this change. <strong>Your current email stays active until you verify the new one.</strong></p>
                            <center>
                                <a href="{verification_link}" class="button">Confirm New Email Address</a>
                            </center>
                            <p>Or copy and paste this link into your browser:</p>
                            <p style="word-break: break-all; color: #FF8C42; font-weight: 500;">{verification_link}</p>
                            <p><strong>This link expires in 24 hours.</strong></p>
                            <div class="alert">⚠️ If you did not request this change, please ignore this email - your account email will not be modified.</div>
                            <p>Best regards,<br>The Launchway Team</p>
                        </div>
                        <div class="footer"><p>This is an automated email. Please do not reply.</p></div>
                    </div>
                </body>
            </html>
            """

            text_body = f"""Hi {first_name},

You requested to change your Launchway account email.

Current email: {old_email}
New email:     {to_email}

Click the link below to confirm:
{verification_link}

This link expires in 24 hours.
If you did not request this change, ignore this email.

The Launchway Team"""

            payload = {
                "from": self.from_email,
                "to": [to_email],
                "subject": "Confirm Your New Email Address - Launchway",
                "html": html_body,
                "text": text_body
            }
            headers = {
                "Authorization": f"Bearer {self.resend_api_key}",
                "Content-Type": "application/json"
            }

            response = requests.post(self.resend_api_url, json=payload, headers=headers, timeout=10)
            if response.status_code == 200:
                logger.info(f"Email-change verification sent to {to_email}")
                return True
            else:
                logger.error(f"Resend API error: {response.status_code} - {response.text}")
                return False

        except Exception as e:
            logger.error(f"Failed to send email-change verification to {to_email}: {e}")
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
                            <p>You now have access to all Launchway core features:</p>
                            <ul>
                                <li><strong>✅ Tailor Resume</strong> - tailor your resume for each job posting</li>
                                <li><strong>✅ Job Search</strong> - discover and filter relevant roles</li>
                                <li><strong>✅ Auto Apply</strong> - get guided automation for applications</li>
                                <li><strong>✅ Profile + Settings</strong> - manage resume, preferences, and integrations</li>
                                <li><strong>✅ History + Credits</strong> - track usage and application activity</li>
                            </ul>
                            <p><strong>How to get started:</strong></p>
                            <ol>
                                <li>Log in to your account</li>
                                <li>Download and install the CLI</li>
                                <li>Complete your profile and connect your resume</li>
                                <li>Run your first resume tailoring on a real job post</li>
                                <li>Start job search and assisted apply from the CLI</li>
                            </ol>
                            <p>Ready to begin? Log in and run your first end-to-end flow.</p>
                            <center>
                                <a href="{self.frontend_url}/login" class="button">Get Started</a>
                            </center>
                            <p>I'd love to hear your feedback as you explore the platform. Feel free to reach out with any questions or suggestions!</p>
                            <p>Best regards,<br>Sahil, Founder, Launchway</p>
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

You now have access to all Launchway core features:

✅ Tailor Resume - tailor your resume for each job posting
✅ Job Search - discover and filter relevant roles
✅ Auto Apply - get guided automation for applications
✅ Profile + Settings - manage resume, preferences, and integrations
✅ History + Credits - track usage and application activity

How to get started:
1) Log in to your account
2) Download and install the CLI
3) Complete your profile and connect your resume
4) Run your first resume tailoring on a real job post
5) Start job search and assisted apply from the CLI

Ready to begin? Log in and run your first end-to-end flow.

Visit: {self.frontend_url}/login

I'd love to hear your feedback as you explore the platform!

Best regards,
Sahil, Founder, Launchway
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

    def send_beta_rejection_email(self, to_email: str, first_name: str, rejection_reason: str) -> bool:
        """
        Send beta access rejection notification to user with reason

        Args:
            to_email: Recipient email address
            first_name: User's first name for personalization
            rejection_reason: Admin-provided reason for rejection

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
                            background: linear-gradient(135deg, #757575 0%, #616161 100%);
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
                        .reason-box {{
                            background-color: #f5f5f5;
                            border-left: 4px solid #757575;
                            padding: 15px;
                            margin: 20px 0;
                            border-radius: 4px;
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
                            <h1>Beta Access Update</h1>
                        </div>
                        <div class="content">
                            <h2>Hi {first_name},</h2>
                            <p>Thank you for your interest in Launchway's Resume Tailoring beta!</p>
                            <p>After reviewing your request, beta access cannot be approved at this time.</p>
                            <div class="reason-box">
                                <strong>Reason:</strong><br>
                                {rejection_reason}
                            </div>
                            <p>I appreciate your understanding and encourage you to apply again in the future as I expand my beta program.</p>
                            <p>If you have any questions or would like to discuss this further, please feel free to reach out.</p>
                            <center>
                                <a href="{self.frontend_url}/beta-request" class="button">Request Access Again</a>
                            </center>
                            <p>Best regards,<br>Sahil, Founder, Launchway</p>
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

Thank you for your interest in Launchway's Resume Tailoring beta!

After reviewing your request, beta access cannot be approved at this time.

Reason:
{rejection_reason}

I appreciate your understanding and encourage you to apply again in the future as I expand my beta program.

If you have any questions or would like to discuss this further, please feel free to reach out.

You can request access again at: {self.frontend_url}/beta-request

Best regards,
Sahil, Founder, Launchway
            """

            # Prepare Resend API request
            payload = {
                "from": self.from_email,
                "to": [to_email],
                "subject": "Beta Access Request Update - Launchway",
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
                logger.info(f"Beta rejection email sent successfully to {to_email}")
                return True
            else:
                logger.error(f"Resend API error: {response.status_code} - {response.text}")
                return False

        except Exception as e:
            logger.error(f"Failed to send beta rejection email to {to_email}: {e}")
            return False

    def send_bug_report_approved_email(
        self,
        to_email: str,
        first_name: str,
        report_title: str,
        severity: str,
        reward_resume_bonus: int,
        reward_job_apply_bonus: int
    ) -> bool:
        """Notify user that their bug report was approved and rewards granted."""
        if not self.is_configured:
            logger.error("Cannot send email: RESEND_API_KEY not configured")
            return False

        try:
            safe_title = (report_title or "your report").strip()
            severity_label = (severity or "medium").strip().capitalize()
            html_body = f"""
            <html>
                <body style="font-family: Arial, sans-serif; line-height: 1.6; color: #333;">
                    <div style="max-width: 640px; margin: 0 auto; padding: 20px; background: #f9f9f9;">
                        <div style="background: #2e7d32; color: #fff; padding: 20px; border-radius: 8px 8px 0 0;">
                            <h2 style="margin: 0;">Thank you for your bug report!</h2>
                        </div>
                        <div style="background: #fff; padding: 24px; border-radius: 0 0 8px 8px;">
                            <p>Hi {first_name},</p>
                            <p>We reviewed your bug report and approved it for rewards.</p>
                            <p><strong>Report:</strong> {safe_title}<br />
                            <strong>Severity:</strong> {severity_label}</p>
                            <div style="background: #f1f8e9; border-left: 4px solid #2e7d32; padding: 12px; margin: 16px 0;">
                                <strong>Reward added to your account:</strong><br />
                                +{reward_resume_bonus} max Resume Tailoring credits<br />
                                +{reward_job_apply_bonus} max Auto Apply credits
                            </div>
                            <p>Your increased limits are now active permanently.</p>
                            <p>Thanks again for helping us improve Launchway beta.</p>
                            <p>The Launchway Team</p>
                        </div>
                    </div>
                </body>
            </html>
            """
            text_body = f"""Hi {first_name},

Your bug report was approved.

Report: {safe_title}
Severity: {severity_label}

Reward added to your account:
+{reward_resume_bonus} max Resume Tailoring credits
+{reward_job_apply_bonus} max Auto Apply credits

Your increased limits are now active permanently.

Thank you for helping us improve Launchway beta.
The Launchway Team
"""

            response = requests.post(
                self.resend_api_url,
                json={
                    "from": self.from_email,
                    "to": [to_email],
                    "subject": "Your bug bounty reward has been granted - Launchway",
                    "html": html_body,
                    "text": text_body,
                },
                headers={
                    "Authorization": f"Bearer {self.resend_api_key}",
                    "Content-Type": "application/json",
                },
                timeout=10,
            )
            if response.status_code == 200:
                logger.info(f"Bug report approval email sent to {to_email}")
                return True
            logger.error(f"Resend API error: {response.status_code} - {response.text}")
            return False
        except Exception as e:
            logger.error(f"Failed to send bug report approval email to {to_email}: {e}")
            return False

    def send_bug_report_rejected_email(
        self,
        to_email: str,
        first_name: str,
        report_title: str,
        rejection_reason: str
    ) -> bool:
        """Notify user that their bug report was rejected."""
        if not self.is_configured:
            logger.error("Cannot send email: RESEND_API_KEY not configured")
            return False

        try:
            safe_title = (report_title or "your report").strip()
            safe_reason = (rejection_reason or "Insufficient reproduction details.").strip()
            html_body = f"""
            <html>
                <body style="font-family: Arial, sans-serif; line-height: 1.6; color: #333;">
                    <div style="max-width: 640px; margin: 0 auto; padding: 20px; background: #f9f9f9;">
                        <div style="background: #616161; color: #fff; padding: 20px; border-radius: 8px 8px 0 0;">
                            <h2 style="margin: 0;">Bug report review update</h2>
                        </div>
                        <div style="background: #fff; padding: 24px; border-radius: 0 0 8px 8px;">
                            <p>Hi {first_name},</p>
                            <p>Thanks for submitting a bug report. We reviewed it but could not approve it for rewards at this time.</p>
                            <p><strong>Report:</strong> {safe_title}</p>
                            <div style="background: #f5f5f5; border-left: 4px solid #616161; padding: 12px; margin: 16px 0;">
                                <strong>Reason:</strong><br />{safe_reason}
                            </div>
                            <p>You can submit a revised report with clearer reproduction steps and environment details.</p>
                            <p>The Launchway Team</p>
                        </div>
                    </div>
                </body>
            </html>
            """
            text_body = f"""Hi {first_name},

Thanks for submitting a bug report. We reviewed it but could not approve it for rewards at this time.

Report: {safe_title}
Reason: {safe_reason}

You can submit a revised report with clearer reproduction steps and environment details.

The Launchway Team
"""

            response = requests.post(
                self.resend_api_url,
                json={
                    "from": self.from_email,
                    "to": [to_email],
                    "subject": "Bug report review update - Launchway",
                    "html": html_body,
                    "text": text_body,
                },
                headers={
                    "Authorization": f"Bearer {self.resend_api_key}",
                    "Content-Type": "application/json",
                },
                timeout=10,
            )
            if response.status_code == 200:
                logger.info(f"Bug report rejection email sent to {to_email}")
                return True
            logger.error(f"Resend API error: {response.status_code} - {response.text}")
            return False
        except Exception as e:
            logger.error(f"Failed to send bug report rejection email to {to_email}: {e}")
            return False

# Global instance
email_service = EmailService()
