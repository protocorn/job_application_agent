# 🚀 Deployment Checklist - Resume Tailoring Beta
## Job Application Agent - Production Deployment Guide

---

## ✅ PRE-DEPLOYMENT CHECKLIST

### 🔒 Security Review
- [x] Run security check script: `.\security_check_before_commit.ps1`
- [ ] Verify no `.env` files are committed
- [ ] Verify no `token.json` files are committed
- [ ] Verify no hardcoded API keys in code
- [ ] Verify all sensitive data is in environment variables

### 🗄️ Database Setup (Supabase)
- [ ] Create Supabase account at https://supabase.com
- [ ] Create new project: `job-agent-beta`
- [ ] Copy PostgreSQL connection string
- [ ] Run database migrations:
  ```powershell
  # Set environment variables
  $env:DB_HOST="your-project.supabase.co"
  $env:DB_PORT="5432"
  $env:DB_NAME="postgres"
  $env:DB_USER="postgres"
  $env:DB_PASSWORD="your-supabase-password"

  # Run migrations
  python migrate_database.py
  python migrate_add_projects.py
  python migrate_add_mimikree_credentials.py
  python migrate_add_google_oauth.py
  ```
- [ ] Verify tables created successfully in Supabase dashboard

### 📦 Redis Setup (Upstash)
- [ ] Create Upstash account at https://upstash.com
- [ ] Create Redis database: `job-agent-redis`
- [ ] Copy Redis connection URL
- [ ] Test connection locally (optional)

### 🔑 API Keys Setup

#### Google Gemini API
- [ ] Get API key from https://aistudio.google.com/apikey
- [ ] Copy API key securely

#### Google OAuth
- [ ] Go to https://console.cloud.google.com
- [ ] Create project: "Job Agent Beta"
- [ ] Enable Google Docs API and Google Drive API
- [ ] Create OAuth 2.0 credentials
- [ ] Add redirect URIs:
  - `http://localhost:5000/api/oauth/callback` (for testing)
  - `https://your-backend.railway.app/api/oauth/callback` (add after backend deployment)
- [ ] Copy Client ID and Client Secret

#### Generate Security Keys
```powershell
# Generate Fernet encryption key
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

# Generate JWT secret key
python -c "import secrets; print(secrets.token_urlsafe(32))"
```
- [ ] Copy and save these keys securely

---

## 🚂 BACKEND DEPLOYMENT (Railway)

### 1️⃣ Prepare Git Repository
```powershell
# Check git status
git status

# Run security check
.\security_check_before_commit.ps1

# Commit changes
git add .
git commit -m "feat: Production deployment setup for resume tailoring beta"

# Push to GitHub
git push origin main
```

### 2️⃣ Create Railway Project
- [ ] Go to https://railway.app
- [ ] Sign up/login with GitHub
- [ ] Click "New Project"
- [ ] Select "Deploy from GitHub repo"
- [ ] Choose your repository
- [ ] Railway auto-detects Python project

### 3️⃣ Configure Environment Variables in Railway

Go to **Variables** tab and add:

#### Core Settings
```
FLASK_ENV=production
```

#### Database (Supabase)
```
DB_HOST=your-project.supabase.co
DB_PORT=5432
DB_NAME=postgres
DB_USER=postgres
DB_PASSWORD=your-supabase-password
```

#### Redis (Upstash)
```
REDIS_URL=redis://default:your-password@your-host.upstash.io:6379
```

Or separately:
```
REDIS_HOST=your-host.upstash.io
REDIS_PORT=6379
REDIS_PASSWORD=your-password
```

#### Google APIs
```
GOOGLE_API_KEY=your-gemini-api-key
GOOGLE_CLIENT_ID=your-oauth-client-id.apps.googleusercontent.com
GOOGLE_CLIENT_SECRET=your-oauth-client-secret
GOOGLE_REDIRECT_URI=https://your-backend.railway.app/api/oauth/callback
```

#### Security
```
JWT_SECRET_KEY=your-generated-jwt-secret-32-chars
ENCRYPTION_KEY=your-generated-fernet-key
```

#### Job Queue Settings
```
JOB_QUEUE_MAX_WORKERS=3
JOB_QUEUE_MAX_PER_USER=1
```

#### CORS Origins (will update after Vercel deployment)
```
CORS_ORIGINS=http://localhost:3000,https://your-frontend.vercel.app
```

### 4️⃣ Deploy Backend
- [ ] Railway will automatically deploy on push
- [ ] Wait for build to complete (check Deployments tab)
- [ ] Copy your Railway backend URL: `https://your-app.railway.app`

### 5️⃣ Test Backend Deployment
```powershell
# Test health endpoint
curl https://your-backend.railway.app/api/health

# Expected response:
# {"status": "ok"}
```

- [ ] Backend health check passes
- [ ] Check Railway logs for errors
- [ ] Verify database connection in logs
- [ ] Verify Redis connection in logs

### 6️⃣ Update Google OAuth Redirect URI
- [ ] Go to Google Cloud Console → Credentials
- [ ] Edit OAuth 2.0 Client
- [ ] Add authorized redirect URI: `https://your-backend.railway.app/api/oauth/callback`
- [ ] Save changes

---

## 🌐 FRONTEND DEPLOYMENT (Vercel)

### 1️⃣ Create API Configuration File

Create `Website/job-agent-frontend/.env.production`:
```
REACT_APP_API_URL=https://your-backend.railway.app
```

### 2️⃣ Update Package.json (if needed)

Verify `Website/job-agent-frontend/package.json` has build script:
```json
{
  "scripts": {
    "build": "react-scripts build"
  }
}
```

### 3️⃣ Test Build Locally
```powershell
cd Website\job-agent-frontend
npm install
npm run build
```
- [ ] Build completes without errors

### 4️⃣ Deploy to Vercel
- [ ] Go to https://vercel.com
- [ ] Sign up/login with GitHub
- [ ] Click "New Project"
- [ ] Import your GitHub repository
- [ ] Configure project:
  - **Framework Preset**: Create React App
  - **Root Directory**: `Website/job-agent-frontend`
  - **Build Command**: `npm run build`
  - **Output Directory**: `build`

### 5️⃣ Configure Environment Variables in Vercel

Go to **Settings → Environment Variables**:
```
REACT_APP_API_URL=https://your-backend.railway.app
```

### 6️⃣ Deploy Frontend
- [ ] Click "Deploy"
- [ ] Wait for build to complete
- [ ] Copy your Vercel URL: `https://your-app.vercel.app`

### 7️⃣ Update CORS in Railway
- [ ] Go back to Railway → Variables
- [ ] Update `CORS_ORIGINS`:
  ```
  CORS_ORIGINS=http://localhost:3000,https://your-app.vercel.app
  ```
- [ ] Railway will redeploy automatically

---

## 🧪 POST-DEPLOYMENT TESTING

### Test Checklist

#### ✅ Backend Tests
- [ ] Health endpoint: `https://your-backend.railway.app/api/health`
- [ ] CORS working (check browser console for errors)
- [ ] Database connection (check Railway logs)
- [ ] Redis connection (check Railway logs)

#### ✅ Frontend Tests
- [ ] Frontend loads: `https://your-app.vercel.app`
- [ ] No console errors in browser
- [ ] Can access login page
- [ ] Can access signup page

#### ✅ Full User Flow
- [ ] Sign up new user
- [ ] Login with credentials
- [ ] Navigate to Profile page
- [ ] Connect Google account (OAuth flow)
- [ ] Upload or paste resume
- [ ] Navigate to Resume Tailoring page
- [ ] Paste job description
- [ ] Submit tailoring job
- [ ] Wait for job completion
- [ ] View tailored resume
- [ ] Logout

### 🐛 Troubleshooting

#### Frontend can't reach backend
```powershell
# Check CORS configuration
# Verify CORS_ORIGINS in Railway includes your Vercel URL
# Check browser console for CORS errors
```

#### Database connection failed
```powershell
# Verify Supabase credentials in Railway
# Check Supabase database is active
# Verify IP allowlist is disabled in Supabase (or includes Railway IPs)
```

#### Redis connection failed
```powershell
# Verify Upstash credentials in Railway
# Check Upstash database is active
# Test REDIS_URL format
```

#### OAuth not working
```powershell
# Verify GOOGLE_REDIRECT_URI matches Railway backend URL
# Check Google Cloud Console authorized redirect URIs
# Ensure both redirect URIs are added (localhost + Railway URL)
```

---

## 📊 MONITORING & MAINTENANCE

### Daily Checks
- [ ] Check Railway logs for errors
- [ ] Monitor Upstash Redis usage (10K commands/day limit)
- [ ] Monitor Gemini API usage (1500 requests/day limit)

### Weekly Checks
- [ ] Review user feedback
- [ ] Check Supabase storage usage (500MB limit)
- [ ] Review Railway execution hours (500 hours/month limit)

### Monthly Checks
- [ ] Review security logs
- [ ] Update dependencies if needed
- [ ] Rotate API keys (best practice)

---

## 🔐 SECURITY POST-DEPLOYMENT

### Immediate Tasks
- [ ] Verify `.env` is not in git: `git ls-files | grep .env` (should be empty)
- [ ] Verify no secrets in GitHub repository
- [ ] Enable 2FA on all accounts (Railway, Vercel, Supabase, Upstash)
- [ ] Document all credentials securely (use password manager)

### Regular Tasks
- [ ] Monitor Railway logs for suspicious activity
- [ ] Review Supabase database access logs
- [ ] Keep API keys secure and rotated
- [ ] Monitor for any security alerts from platforms

---

## 📈 BETA TESTING LIMITS

### Free Tier Limits
- **Railway**: 500 hours/month (~16.6 days of continuous operation)
- **Supabase**: 500MB storage, unlimited API requests
- **Upstash Redis**: 10,000 commands/day
- **Gemini API**: 15 requests/minute, 1,500 requests/day
- **Vercel**: Unlimited deployments, 100GB bandwidth/month

### Beta User Limits (Enforced by Code)
- **Resume Tailoring**: 5 sessions/user/day
- **Concurrent Jobs**: 1 per user
- **Total Beta Users**: 10-15 users

---

## 🎯 SUCCESS CRITERIA

### Deployment Complete When:
- [x] Security script passes
- [ ] Backend deployed to Railway
- [ ] Frontend deployed to Vercel
- [ ] All environment variables configured
- [ ] Database migrations completed
- [ ] Redis connected
- [ ] Full user flow tested successfully
- [ ] No console errors
- [ ] OAuth flow working
- [ ] Resume tailoring working end-to-end

---

## 🆘 EMERGENCY CONTACTS & ROLLBACK

### If Something Goes Wrong:
1. **Railway Issues**: Check Railway status page
2. **Vercel Issues**: Check Vercel status page
3. **Supabase Issues**: Check Supabase status page
4. **Upstash Issues**: Check Upstash status page

### Rollback Procedure:
```powershell
# In Railway:
# Go to Deployments → Click on previous successful deployment → Redeploy

# In Vercel:
# Go to Deployments → Click on previous deployment → Promote to Production
```

---

## 🎉 DEPLOYMENT COMPLETE!

Once all checkboxes are marked, your Resume Tailoring Beta is live!

**Your URLs:**
- **Frontend**: https://your-app.vercel.app
- **Backend**: https://your-backend.railway.app
- **Database**: Supabase Dashboard
- **Redis**: Upstash Dashboard

**Next Steps:**
1. Share frontend URL with beta testers
2. Monitor usage and errors
3. Collect feedback
4. Iterate and improve

---

**Good luck with your beta launch! 🚀**
