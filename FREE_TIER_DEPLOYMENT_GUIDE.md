# 🆓 Free Tier Deployment Guide
## Deploy Job Application Agent Beta with $0 Monthly Cost

---

## 🎯 **WHAT YOU'LL GET**
- **Backend API**: Railway.app (FREE)
- **Database**: Supabase PostgreSQL (FREE)
- **Redis**: Upstash (FREE)
- **Frontend**: Vercel (FREE)
- **APIs**: All free tiers (Gemini, Google Docs)
- **Total Cost**: **$0/month** ✅

---

## 📋 **PREREQUISITES**

- GitHub account
- Google account (for APIs)
- Credit card for Vercel/Railway verification (won't be charged)

---

## 🚀 **STEP-BY-STEP DEPLOYMENT**

### **STEP 1: Set Up Supabase (PostgreSQL Database)**

#### **1.1 Create Account**
```
1. Go to https://supabase.com
2. Sign up with GitHub
3. Click "New Project"
   - Name: job-agent-beta
   - Database Password: (generate secure password)
   - Region: Choose closest to you
   - Plan: Free tier ✅
```

#### **1.2 Get Database Connection String**
```
1. Go to Project Settings → Database
2. Copy "Connection string" (URI mode)
3. It looks like:
   postgresql://postgres:[password]@[host].supabase.co:5432/postgres
```

#### **1.3 Create Tables**
```powershell
# Run migrations from your local machine
$env:DB_HOST="your-project.supabase.co"
$env:DB_PORT="5432"
$env:DB_NAME="postgres"
$env:DB_USER="postgres"
$env:DB_PASSWORD="your-supabase-password"

python migrate_database.py
python migrate_add_projects.py
python migrate_add_mimikree_credentials.py
```

---

### **STEP 2: Set Up Upstash (Redis)**

#### **2.1 Create Account**
```
1. Go to https://upstash.com
2. Sign up with GitHub
3. Click "Create Database"
   - Name: job-agent-redis
   - Type: Redis
   - Region: Choose closest to you
   - Plan: Free ✅
```

#### **2.2 Get Redis Connection**
```
1. Click on your database
2. Copy "Redis URL" from dashboard
3. Format: redis://default:[password]@[host]:6379
```

---

### **STEP 3: Set Up Google APIs**

#### **3.1 Enable Gemini API**
```
1. Go to https://aistudio.google.com
2. Sign in with Google account
3. Click "Get API Key"
4. Create API key (FREE tier)
5. Copy the key
```

#### **3.2 Set Up Google Cloud Project**
```
1. Go to https://console.cloud.google.com
2. Create new project: "Job Agent"
3. Enable APIs:
   - Google Docs API
   - Google Drive API
   - Enable both (FREE tier)
```

#### **3.3 Create OAuth Credentials**
```
1. Go to APIs & Services → Credentials
2. Create OAuth 2.0 Client ID
   - Application type: Web application
   - Name: Job Agent OAuth
   - Authorized redirect URIs: 
     http://localhost:5000/api/oauth/callback
     https://yourdomain.com/api/oauth/callback (add later)
3. Copy Client ID and Client Secret
```

---

### **STEP 4: Deploy Backend to Railway.app**

#### **4.1 Prepare Repository**
```powershell
# Initialize git if not already done
git init
git add .
git commit -m "Ready for Railway deployment"

# Push to GitHub
git remote add origin https://github.com/yourusername/job-agent.git
git branch -M main
git push -u origin main
```

#### **4.2 Create Railway Project**
```
1. Go to https://railway.app
2. Sign up with GitHub
3. Click "New Project"
4. Select "Deploy from GitHub repo"
5. Choose your job-agent repository
6. Railway will auto-detect Python
```

#### **4.3 Configure Environment Variables**
```
In Railway dashboard, go to Variables tab and add:

FLASK_ENV=production
DB_HOST=your-project.supabase.co
DB_PORT=5432
DB_NAME=postgres
DB_USER=postgres
DB_PASSWORD=your-supabase-password

REDIS_HOST=your-upstash-host.upstash.io
REDIS_PORT=6379

GOOGLE_API_KEY=your-gemini-api-key
GOOGLE_CLIENT_ID=your-oauth-client-id
GOOGLE_CLIENT_SECRET=your-oauth-client-secret
GOOGLE_REDIRECT_URI=https://your-app.railway.app/api/oauth/callback

JWT_SECRET_KEY=your-32-character-secret-here
ENCRYPTION_KEY=your-fernet-key-here

JOB_QUEUE_MAX_WORKERS=3
JOB_QUEUE_MAX_PER_USER=1
```

#### **4.4 Create Start Command**
```
Create a file: Procfile (no extension)

web: python server/api_server.py
```

#### **4.5 Deploy**
```
Railway will automatically deploy on push!
Your backend URL: https://job-agent-backend.railway.app
```

---

### **STEP 5: Deploy Frontend to Vercel**

#### **5.1 Update API URLs**
```javascript
// In your React app, update API base URL
const API_BASE_URL = process.env.REACT_APP_API_URL || 'https://your-backend.railway.app';

// Update all API calls to use this base URL
axios.get(`${API_BASE_URL}/api/profile`, ...)
```

#### **5.2 Deploy to Vercel**
```
1. Go to https://vercel.com
2. Sign up with GitHub
3. Click "New Project"
4. Import your GitHub repository
5. Set Framework: Create React App
6. Root Directory: Website/job-agent-frontend
7. Click "Deploy"

Environment Variables in Vercel:
REACT_APP_API_URL=https://your-backend.railway.app
```

#### **5.3 Custom Domain (Optional - $3-5/year)**
```
1. Buy domain from Namecheap: yourdomain.com
2. In Vercel: Settings → Domains → Add
3. Follow Vercel's DNS setup instructions
4. Update Google OAuth redirect URI
```

---

### **STEP 6: Configure CORS**

```python
# Update server/api_server.py
from flask_cors import CORS

# Replace this:
CORS(app)

# With this:
CORS(app, origins=[
    'http://localhost:3000',
    'https://your-frontend.vercel.app',
    'https://yourdomain.com'  # if using custom domain
])
```

---

## ✅ **VERIFICATION CHECKLIST**

After deployment, test:

- [ ] **Backend Health**: `https://your-backend.railway.app/api/health`
  - Should return: `{"status": "ok"}`

- [ ] **Database Connection**: Check Railway logs for successful DB connection

- [ ] **Redis Connection**: Check Railway logs for Redis ping success

- [ ] **Frontend Loading**: Visit your Vercel URL

- [ ] **User Registration**: Try signing up

- [ ] **Google OAuth**: Try connecting Google account

- [ ] **Mimikree Connection**: Try connecting Mimikree account

- [ ] **Resume Tailoring**: End-to-end test with sample resume

---

## 🐛 **COMMON ISSUES & FIXES**

### **Issue 1: Railway runs out of free hours**
```
Problem: Free tier = 500 hours/month (~16 days)
Solution: 
- App sleeps after 15 min inactivity (automatic)
- Wakes up on request (adds 2-3 sec delay)
- Upgrade to Hobby ($5/month) for always-on
```

### **Issue 2: "Database connection failed"**
```
Fix:
1. Check Supabase database is active
2. Verify connection string in Railway env vars
3. Check IP allowlist in Supabase (should be disabled for Railway)
```

### **Issue 3: "Redis connection failed"**
```
Fix:
1. Check Upstash database is active
2. Verify Redis URL in Railway env vars
3. Ensure Redis commands haven't exceeded 10K/day limit
```

### **Issue 4: "Gemini API quota exceeded"**
```
Fix:
1. Check usage in Google AI Studio dashboard
2. Free tier: 15 req/min, 1500 req/day
3. If exceeded, wait 24 hours for reset
4. Implement better rate limiting in code
```

---

## 📊 **MONITORING YOUR FREE TIER USAGE**

### **Railway.app**
```
Dashboard → Usage
- Check execution hours (500 limit)
- Monitor memory usage
- View deployment logs
```

### **Supabase**
```
Dashboard → Database
- Check storage (500MB limit)
- Monitor active connections
- View query performance
```

### **Upstash**
```
Dashboard → Your Database
- Check daily commands (10K limit)
- Monitor storage (256MB limit)
- View command metrics
```

### **Gemini API**
```
https://aistudio.google.com/apikey
- View API key usage
- Check rate limits (15/min, 1500/day)
```

---

## 🎯 **WHAT TO DO IF YOU HIT FREE TIER LIMITS**

### **Gemini API (1,500/day limit exceeded)**
```
Options:
1. Implement request batching (combine multiple calls)
2. Add caching for common operations
3. Limit users to 5 sessions/day instead of 20
4. Wait 24 hours for reset (unlikely with 10 users)
```

### **Railway Hours (500/month exceeded)**
```
Options:
1. Upgrade to Hobby plan: $5/month ✅ AFFORDABLE
2. Optimize code to reduce execution time
3. Use sleep mode aggressively
```

### **Database Storage (500MB exceeded)**
```
Options:
1. Clean up old data (action history, logs)
2. Upgrade Supabase to Pro: $25/month
3. Use Railway PostgreSQL instead (1GB free)
```

---

## 💡 **COST-SAVING TIPS**

1. **Use Sleep Mode**: Let Railway sleep - 2-3 sec wake time is acceptable for beta
2. **Aggressive Cleanup**: Delete old logs, expired sessions weekly
3. **Optimize Queries**: Use database indexes (already implemented)
4. **Cache Heavily**: Use Redis for frequently accessed data
5. **Batch Operations**: Combine multiple Gemini calls where possible

---

## 🎓 **STUDENT BUDGET REALITY CHECK**

```
Your Monthly Income: $1,500 - 2,000
Safe Project Budget: $50 - 100 (2.5-5% of income)

This Deployment: $0 - 5 (0.25% of income!)

Conclusion: Extremely affordable! ✅
```

---

## 🏁 **FINAL DEPLOYMENT COMMAND**

```powershell
# One-command deployment checklist
Write-Host "=== FREE TIER DEPLOYMENT CHECKLIST ===" -ForegroundColor Green
Write-Host "1. ✅ Supabase database created"
Write-Host "2. ✅ Upstash Redis created"
Write-Host "3. ✅ Google APIs configured"
Write-Host "4. ✅ Railway.app connected to GitHub"
Write-Host "5. ✅ Environment variables configured"
Write-Host "6. ✅ Vercel frontend deployed"
Write-Host ""
Write-Host "Total Cost: $0/month" -ForegroundColor Green
Write-Host "Ready for Beta Testing! 🚀" -ForegroundColor Cyan
```

---

**You can literally deploy this entire system for FREE and test with 10-15 users without spending a penny!** 🎉
