# Email Verification Setup Guide

## Overview
Email verification has been successfully implemented for the Job Application Agent. Users must now verify their email address before they can log in.

## What's Been Implemented

### Backend Changes
1. **Database Schema** ([database_config.py:54-57](database_config.py#L54-L57))
   - Added `email_verified` (Boolean, default: False)
   - Added `verification_token` (String, unique)
   - Added `verification_token_expires` (DateTime)

2. **Email Service** ([server/email_service.py](server/email_service.py))
   - HTML email template with verification link
   - SMTP support (Gmail, etc.)
   - Easy to extend to SendGrid/Resend

3. **Auth Service Updates** ([server/auth.py](server/auth.py))
   - `register_user`: Generates verification token and sends email
   - `authenticate_user`: Checks email verification before login
   - `verify_email`: New method to verify email tokens

4. **API Endpoints** ([server/api_server.py:1623-1641](server/api_server.py#L1623-L1641))
   - `GET /api/auth/verify-email?token=<token>`: Verifies email

### Frontend Changes
1. **Verification Page** ([Website/job-agent-frontend/src/VerifyEmail.js](Website/job-agent-frontend/src/VerifyEmail.js))
   - Handles email verification from link
   - Shows success/error states
   - Auto-redirects after verification

2. **Signup Flow** ([Website/job-agent-frontend/src/Signup.js](Website/job-agent-frontend/src/Signup.js))
   - Shows success message after registration
   - Redirects to login page (not auto-login)
   - Displays verification instructions

3. **Routing** ([Website/job-agent-frontend/src/App.js:27](Website/job-agent-frontend/src/App.js#L27))
   - Added `/verify-email` route

## Email Service Configuration

You need to configure SMTP settings in your `.env` file:

```bash
# Email Configuration (Required for email verification)
SMTP_SERVER=smtp.gmail.com
SMTP_PORT=587
SMTP_USERNAME=your-email@gmail.com
SMTP_PASSWORD=your-app-password  # For Gmail, use App Password, not regular password
FROM_EMAIL=your-email@gmail.com
FRONTEND_URL=http://localhost:3000  # Update for production
```

### Gmail Setup (Recommended for Testing)

1. **Enable 2-Factor Authentication** on your Google account
2. **Generate App Password**:
   - Go to Google Account Settings
   - Security → 2-Step Verification → App passwords
   - Select "Mail" and generate a password
   - Use this password as `SMTP_PASSWORD`

3. **Update .env**:
```bash
SMTP_SERVER=smtp.gmail.com
SMTP_PORT=587
SMTP_USERNAME=youremail@gmail.com
SMTP_PASSWORD=xxxx-xxxx-xxxx-xxxx  # 16-character app password
FROM_EMAIL=youremail@gmail.com
FRONTEND_URL=http://localhost:3000
```

### Alternative Email Services

#### SendGrid
```bash
SMTP_SERVER=smtp.sendgrid.net
SMTP_PORT=587
SMTP_USERNAME=apikey
SMTP_PASSWORD=your-sendgrid-api-key
```

#### Mailgun
```bash
SMTP_SERVER=smtp.mailgun.org
SMTP_PORT=587
SMTP_USERNAME=your-mailgun-username
SMTP_PASSWORD=your-mailgun-password
```

#### AWS SES
```bash
SMTP_SERVER=email-smtp.us-east-1.amazonaws.com
SMTP_PORT=587
SMTP_USERNAME=your-aws-smtp-username
SMTP_PASSWORD=your-aws-smtp-password
```

## User Flow

### Registration
1. User signs up with email, password, name
2. Account is created with `email_verified=False`
3. Verification email is sent with a unique token (valid for 24 hours)
4. User sees success message and is redirected to login page
5. User cannot log in until email is verified

### Email Verification
1. User clicks verification link in email
2. Link format: `http://localhost:3000/verify-email?token=<unique-token>`
3. Frontend calls `/api/auth/verify-email?token=<token>`
4. Backend verifies token and marks `email_verified=True`
5. User is auto-logged in and redirected to dashboard

### Login
1. User attempts to log in
2. System checks if `email_verified=True`
3. If not verified: Shows error "Please verify your email address..."
4. If verified: Login succeeds and JWT token is issued

## Testing

### Migration
Run the migration to add verification fields to existing database:
```bash
python server/migrate_add_email_verification.py
```

### Test the Flow
1. Start the backend server
2. Start the frontend
3. Sign up with a real email address
4. Check your email inbox
5. Click the verification link
6. Try logging in

## Production Considerations

1. **Update FRONTEND_URL**: Change to your production domain
```bash
FRONTEND_URL=https://yourapp.com
```

2. **Use Professional Email Service**: Gmail is fine for testing but use SendGrid/Mailgun/AWS SES for production

3. **Email Template Customization**: Edit [server/email_service.py:50-104](server/email_service.py#L50-L104) to customize the email design

4. **Token Expiration**: Currently 24 hours. Adjust in [server/auth.py:73](server/auth.py#L73):
```python
verification_expires = datetime.utcnow() + timedelta(hours=24)  # Change hours as needed
```

5. **Resend Verification Email**: You may want to add a "Resend verification email" feature for users who don't receive it

## Existing Users

For existing users in the database (created before verification was added), you have two options:

### Option 1: Auto-verify existing users
```python
from database_config import SessionLocal, User

db = SessionLocal()
db.query(User).update({User.email_verified: True})
db.commit()
db.close()
```

### Option 2: Force re-verification
Let existing users request a new verification email through a "Forgot Password" or "Resend Verification" feature.

## Troubleshooting

### Email Not Sending
- Check SMTP credentials in `.env`
- Verify SMTP server and port
- Check server logs for error messages
- For Gmail: Ensure App Password is used, not regular password

### Verification Link Not Working
- Check `FRONTEND_URL` in `.env` matches your frontend URL
- Verify token hasn't expired (24 hours)
- Check browser console for errors

### User Can't Login
- Verify `email_verified=True` in database
- Check error message - might be email not verified

## Files Modified/Created

### Backend
- `database_config.py` - Added verification fields to User model
- `server/auth.py` - Added verification logic
- `server/email_service.py` - NEW: Email sending service
- `server/api_server.py` - Added verification endpoint
- `server/migrate_add_email_verification.py` - NEW: Database migration

### Frontend
- `Website/job-agent-frontend/src/VerifyEmail.js` - NEW: Verification page
- `Website/job-agent-frontend/src/Signup.js` - Updated signup flow
- `Website/job-agent-frontend/src/App.js` - Added verification route
- `Website/job-agent-frontend/src/Auth.css` - Added verification styles

## Support

If you encounter any issues, check:
1. Server logs for backend errors
2. Browser console for frontend errors
3. Email service logs
4. Database to verify user records
