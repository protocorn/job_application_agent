# ⚠️ Email Service Not Working on Railway

## The Problem

You're seeing this error:
```
ERROR | Failed to send verification email: [Errno 101] Network is unreachable
```

**Root Cause:** Railway.app **blocks outbound SMTP connections** (ports 25, 465, 587) for security reasons.

---

## ✅ Solutions (Choose One)

### **Option 1: Use SendGrid (Recommended - FREE)**

SendGrid offers 100 emails/day for free and works with Railway.

#### Setup Steps:

1. **Sign up for SendGrid:**
   - Go to https://sendgrid.com/
   - Create free account (no credit card required)
   - Verify your email

2. **Create API Key:**
   - Go to Settings → API Keys
   - Click "Create API Key"
   - Name it "Job Application Agent"
   - Select "Full Access"
   - **Copy the API key** (you won't see it again!)

3. **Update your `.env` file:**
```env
# SendGrid Configuration
SMTP_SERVER=smtp.sendgrid.net
SMTP_PORT=587
SMTP_USERNAME=apikey
SMTP_PASSWORD=YOUR_SENDGRID_API_KEY_HERE
FROM_EMAIL=your-verified-email@domain.com
FRONTEND_URL=https://your-app.com
```

4. **Verify Sender Email:**
   - In SendGrid dashboard, go to Settings → Sender Authentication
   - Click "Verify a Single Sender"
   - Use your real email address
   - Check your email and click verification link

5. **Deploy to Railway:**
```powershell
# Add env variables to Railway
railway variables set SMTP_SERVER=smtp.sendgrid.net
railway variables set SMTP_PORT=587
railway variables set SMTP_USERNAME=apikey
railway variables set SMTP_PASSWORD=YOUR_API_KEY
railway variables set FROM_EMAIL=your-email@domain.com
railway variables set FRONTEND_URL=https://your-app.railway.app
```

---

### **Option 2: Use Mailgun (Alternative)**

Free tier: 5,000 emails/month

#### Setup:
```env
SMTP_SERVER=smtp.mailgun.org
SMTP_PORT=587
SMTP_USERNAME=your-mailgun-username
SMTP_PASSWORD=your-mailgun-password
FROM_EMAIL=your-email@domain.com
FRONTEND_URL=https://your-app.com
```

---

### **Option 3: Use AWS SES**

Cheapest for high volume ($0.10 per 1,000 emails)

#### Setup:
```env
SMTP_SERVER=email-smtp.us-east-1.amazonaws.com
SMTP_PORT=587
SMTP_USERNAME=your-aws-smtp-username
SMTP_PASSWORD=your-aws-smtp-password
FROM_EMAIL=your-verified-email@domain.com
FRONTEND_URL=https://your-app.com
```

---

### **Option 4: Use Resend (Modern Alternative)**

#### Setup:
```env
SMTP_SERVER=smtp.resend.com
SMTP_PORT=465
SMTP_USERNAME=resend
SMTP_PASSWORD=your-resend-api-key
FROM_EMAIL=onboarding@resend.dev
FRONTEND_URL=https://your-app.com
```

---

## 🧪 Testing After Configuration

### **1. Update Railway Environment Variables:**
```powershell
railway variables set SMTP_SERVER=smtp.sendgrid.net
railway variables set SMTP_USERNAME=apikey
railway variables set SMTP_PASSWORD=your_api_key_here
```

### **2. Restart Railway Service:**
```powershell
railway restart
```

### **3. Check Logs:**
```powershell
railway logs
```

Look for:
```
✅ Verification email sent successfully to user@example.com
```

Instead of:
```
❌ Failed to send verification email: Network is unreachable
```

---

## 🔍 Troubleshooting

### **Still Not Working?**

1. **Check Railway Variables:**
```powershell
railway variables
```
Make sure all SMTP variables are set correctly.

2. **Check SendGrid Dashboard:**
   - Go to Activity → Recent Activity
   - See if emails are being sent but blocked

3. **Check Spam Folder:**
   - SendGrid emails often go to spam initially
   - Mark as "Not Spam" to whitelist

4. **Verify Sender:**
   - In SendGrid, make sure sender email is verified
   - Green checkmark should appear

5. **Check Railway Logs:**
```powershell
railway logs --follow
```

### **Common Errors:**

| Error | Solution |
|-------|----------|
| "Network is unreachable" | You're still using Gmail SMTP - switch to SendGrid |
| "Authentication failed" | Wrong API key or username |
| "Sender not verified" | Verify sender email in SendGrid dashboard |
| "Daily sending limit reached" | Free tier limit hit (100/day for SendGrid) |

---

## 📝 Testing Locally vs Railway

### **Local Testing (Gmail works):**
```env
# .env (local)
SMTP_SERVER=smtp.gmail.com
SMTP_PORT=587
SMTP_USERNAME=your-email@gmail.com
SMTP_PASSWORD=your-app-password
FROM_EMAIL=your-email@gmail.com
FRONTEND_URL=http://localhost:3000
```

### **Railway Production (Use SendGrid):**
```powershell
# Railway environment variables
railway variables set SMTP_SERVER=smtp.sendgrid.net
railway variables set SMTP_USERNAME=apikey
railway variables set SMTP_PASSWORD=SG.xxxxx
railway variables set FROM_EMAIL=verified@yourdomain.com
railway variables set FRONTEND_URL=https://yourapp.railway.app
```

---

## 🚀 Quick SendGrid Setup (5 Minutes)

```bash
# 1. Sign up: https://sendgrid.com/
# 2. Create API key
# 3. Set Railway variables:
railway variables set SMTP_SERVER=smtp.sendgrid.net
railway variables set SMTP_PORT=587
railway variables set SMTP_USERNAME=apikey
railway variables set SMTP_PASSWORD=YOUR_API_KEY
railway variables set FROM_EMAIL=your@email.com
railway variables set FRONTEND_URL=https://yourapp.railway.app

# 4. Restart
railway restart

# 5. Test
railway logs --follow
```

---

## ✨ After Setup

Once configured, emails will be sent successfully:

```
✅ Email service is configured
📤 Attempting to send verification email
✅ Verification email sent successfully!
```

Users will receive beautiful verification emails with your purple gradient buttons! 📧

---

## 💡 Pro Tips

1. **For Development:** Use Gmail (free, easy)
2. **For Production on Railway:** Use SendGrid (Railway won't block it)
3. **For High Volume:** Use AWS SES (cheapest)
4. **For Modern API:** Use Resend (clean API, good docs)

---

## 📧 Need Help?

If you're still having issues:
1. Share the error from `railway logs`
2. Confirm which SMTP service you're using
3. Check if Railway environment variables are set correctly

