# Beta Access System Documentation

## Overview

The Beta Access System allows you to control who can use your Job Application Agent platform during the beta testing phase. Users must request beta access, and administrators can approve or reject these requests through a dedicated admin dashboard.

## Features

- ✅ **Beta Request Form**: Users can request access with a reason
- ✅ **Automatic Email Notifications**: Users receive approval emails automatically
- ✅ **Admin Dashboard**: Review and manage beta access requests
- ✅ **Access Control**: Non-approved users are automatically redirected
- ✅ **Pending Status Tracking**: Users can see their request status

## Setup Instructions

### 1. Configure Admin Emails

Add your admin email(s) to your `.env` file:

```env
ADMIN_EMAILS=your.email@example.com,another.admin@example.com
```

Multiple admin emails can be separated by commas.

### 2. Run Database Migration

The migration has already been run if you followed the setup, but if you need to run it again:

```bash
python migrate_add_beta_access.py
```

This adds the following fields to the `users` table:
- `beta_access_requested` (boolean)
- `beta_access_approved` (boolean)
- `beta_request_date` (timestamp)
- `beta_approved_date` (timestamp)
- `beta_request_reason` (text)

### 3. Configure Email Service (Optional but Recommended)

Set up email notifications for beta approvals:

```env
RESEND_API_KEY=your_resend_api_key_here
FROM_EMAIL=noreply@yourdomain.com
FRONTEND_URL=https://your-frontend-url.com
```

Get your Resend API key from: https://resend.com/api-keys

## How It Works

### User Flow

1. **Sign Up & Verify Email**
   - User creates an account
   - User verifies their email address

2. **Request Beta Access**
   - Upon login, user is redirected to `/beta-request`
   - User fills out a form explaining why they want access
   - User submits the request

3. **Pending Status**
   - User sees a "Pending" screen with their request details
   - User can return to dashboard but cannot access main features

4. **Approval**
   - Admin approves the request
   - User receives an approval email
   - User can now access all platform features

### Admin Flow

1. **Access Admin Dashboard**
   - Navigate to `/admin/beta` (or add link to your navigation)
   - Only emails in `ADMIN_EMAILS` can access this page

2. **Review Requests**
   - View all pending beta access requests
   - See user information and their reason for requesting access
   - View statistics (pending count, approved count)

3. **Take Action**
   - **Approve**: User receives email and gains full access
   - **Reject**: Request is removed, user can reapply

## API Endpoints

### User Endpoints

**Request Beta Access**
```
POST /api/beta/request
Authorization: Bearer <token>
Body: { "reason": "Why I want beta access..." }
```

**Check Beta Status**
```
GET /api/beta/status
Authorization: Bearer <token>
```

### Admin Endpoints

**Get All Requests**
```
GET /api/admin/beta/requests
Authorization: Bearer <admin_token>
```

**Approve User**
```
POST /api/admin/beta/approve/<user_id>
Authorization: Bearer <admin_token>
```

**Reject User**
```
POST /api/admin/beta/reject/<user_id>
Authorization: Bearer <admin_token>
```

## Frontend Routes

- `/beta-request` - User beta access request form
- `/admin/beta` - Admin dashboard for managing requests

## Disabling Beta Access (Going Public)

When you're ready to allow all users without approval:

### Option 1: Auto-Approve All New Users

Modify `server/auth.py` in the `register_user` function:

```python
# After creating new_user, add:
new_user.beta_access_approved = True
new_user.beta_approved_date = datetime.utcnow()
```

### Option 2: Approve All Existing Users

Run this SQL query on your database:

```sql
UPDATE public.users
SET beta_access_approved = true,
    beta_approved_date = NOW()
WHERE beta_access_approved = false;
```

### Option 3: Remove Beta Check from Frontend

In `Website/job-agent-frontend/src/ProtectedRoute.js`:

```javascript
// Change this line:
const ProtectedRoute = ({ featureName, children, requireBetaAccess = true }) => {
// To:
const ProtectedRoute = ({ featureName, children, requireBetaAccess = false }) => {
```

## Customization

### Email Templates

Edit the approval email template in `server/email_service.py`:
- Function: `send_beta_approval_email`
- Customize the HTML and text content

### Form Fields

Modify `Website/job-agent-frontend/src/BetaRequest.js`:
- Add more form fields
- Change minimum character requirements
- Adjust UI/styling

### Admin Dashboard

Customize `Website/job-agent-frontend/src/AdminBeta.js`:
- Add search/filter functionality
- Add bulk approval options
- Add user analytics

## Troubleshooting

### Users can't request beta access

1. Check if they verified their email
2. Ensure the backend endpoint is accessible
3. Check browser console for errors

### Admin dashboard shows "Unauthorized"

1. Verify your email is in `ADMIN_EMAILS` in `.env`
2. Restart the backend server after updating `.env`
3. Check that the email matches exactly (case-sensitive)

### Approval emails not sending

1. Verify `RESEND_API_KEY` is set in `.env`
2. Check `FROM_EMAIL` is a verified domain in Resend
3. Check backend logs for email errors

### Migration errors

1. Ensure database connection is working
2. Check if fields already exist: `\d public.users` in psql
3. Run migration again (it uses `IF NOT EXISTS`)

## Database Schema

```sql
-- Beta access fields added to users table
ALTER TABLE public.users ADD COLUMN beta_access_requested BOOLEAN DEFAULT FALSE;
ALTER TABLE public.users ADD COLUMN beta_access_approved BOOLEAN DEFAULT FALSE;
ALTER TABLE public.users ADD COLUMN beta_request_date TIMESTAMP;
ALTER TABLE public.users ADD COLUMN beta_approved_date TIMESTAMP;
ALTER TABLE public.users ADD COLUMN beta_request_reason TEXT;
```

## Security Considerations

1. **Admin Access**: Only emails in `ADMIN_EMAILS` can access admin dashboard
2. **Rate Limiting**: Consider adding rate limiting to prevent spam requests
3. **Input Validation**: User input (reason) is validated on both frontend and backend
4. **Authentication**: All endpoints require valid JWT tokens

## Future Enhancements

Consider adding:
- Email notifications when requests are submitted
- Request expiration after X days
- User request history tracking
- Analytics dashboard for beta program
- Automatic approval based on criteria
- Waitlist positions and estimated approval times

## Support

For issues or questions:
1. Check the troubleshooting section above
2. Review backend logs in `server/logs/`
3. Check browser console for frontend errors
4. Verify environment variables are set correctly
