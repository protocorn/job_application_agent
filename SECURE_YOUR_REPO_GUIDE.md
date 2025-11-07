# 🔒 Secure Your Repository Before Publishing
## Step-by-Step Guide to Remove Sensitive Data

---

## ⚠️ **CRITICAL: YOUR CURRENT SITUATION**

Your repository currently has:
- ❌ `.env` file tracked in Git (contains ALL your API keys)
- ❌ `token.json` files tracked
- ❌ User data directories (Resumes/, Cache/, sessions/)
- ❌ Hardcoded password in `database_config.py` (now fixed)

**These MUST be cleaned up before publishing to GitHub!**

---

## 🚀 **QUICK FIX (5 MINUTES)**

### **Run These Commands:**

```powershell
# 1. Run the cleanup script
.\cleanup_sensitive_data.ps1

# 2. Run security check
.\security_check_before_commit.ps1

# 3. If check passes, commit
git add .
git commit -m "security: Remove sensitive data and add comprehensive .gitignore"

# 4. Push to GitHub
git push origin main
```

---

## 📋 **DETAILED STEP-BY-STEP GUIDE**

### **Step 1: Check Current Git Status**

```powershell
# See what's currently tracked
git ls-files | Select-String -Pattern "\.env|token\.json|Resumes|Cache"
```

**If you see any of these, they need to be removed!**

---

### **Step 2: Remove Sensitive Files from Tracking**

```powershell
# Remove .env
git rm --cached .env

# Remove token files
git rm --cached Agents/token.json
git rm --cached server/token.json

# Remove user data directories
git rm -r --cached Resumes/
git rm -r --cached Cache/
git rm -r --cached server/sessions/
git rm -r --cached logs/

# Commit the removal
git commit -m "security: Remove sensitive files from Git tracking"
```

---

### **Step 3: Check Git History**

```powershell
# Check if .env was ever committed
git log --all --full-history -- .env
```

**If you see commits, your API keys are exposed in Git history!**

---

### **Step 4: Clean Git History (IF NEEDED)**

**⚠️ ONLY do this if Step 3 showed commits!**

```powershell
# Method 1: Using git filter-branch
git filter-branch --force --index-filter `
    "git rm --cached --ignore-unmatch .env" `
    --prune-empty --tag-name-filter cat -- --all

# Clean up
git reflog expire --expire=now --all
git gc --prune=now --aggressive

# Method 2: Using BFG Repo-Cleaner (faster, recommended)
# Download from: https://rtyley.github.io/bfg-repo-cleaner/
# java -jar bfg.jar --delete-files .env
# git reflog expire --expire=now --all
# git gc --prune=now --aggressive
```

---

### **Step 5: Rotate ALL API Keys**

**🚨 CRITICAL: If .env was in Git history, ALL keys are compromised!**

#### **5.1 Google Gemini API Key**
```
1. Go to: https://aistudio.google.com/apikey
2. Click on your current key
3. Delete it
4. Create new API key
5. Copy new key to .env: GOOGLE_API_KEY=new_key_here
```

#### **5.2 Google OAuth Credentials**
```
1. Go to: https://console.cloud.google.com/apis/credentials
2. Find your OAuth 2.0 Client ID
3. Delete it
4. Create new OAuth 2.0 Client ID
5. Copy new Client ID and Secret to .env
```

#### **5.3 Encryption Key**
```powershell
# Generate new encryption key
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

# Copy output to .env: ENCRYPTION_KEY=new_key_here
```

#### **5.4 JWT Secret**
```powershell
# Generate new JWT secret
python -c "import secrets; print(secrets.token_urlsafe(32))"

# Add to .env: JWT_SECRET_KEY=new_secret_here
```

#### **5.5 Other API Keys**
- TheMuse API: Get new key from https://www.themuse.com/developers
- TheirStack API: Get new key from https://theirstack.com/

---

### **Step 6: Verify Cleanup**

```powershell
# Run security check
.\security_check_before_commit.ps1
```

**Should show:** ✅ SECURITY CHECK PASSED!

---

### **Step 7: Test Application**

```powershell
# Test that app still works with new keys
python server/api_server.py
```

**Verify:**
- Server starts without errors
- Database connects
- Redis connects
- Can log in
- Can tailor resume

---

### **Step 8: Final Commit & Push**

```powershell
# Add all changes
git add .

# Commit
git commit -m "security: Secure repository for public release

- Remove sensitive files from tracking
- Add comprehensive .gitignore
- Remove hardcoded credentials
- Add security review and documentation"

# Push to GitHub
git push origin main

# If you cleaned history, force push (ONLY if you're sole contributor)
# git push origin --force --all
```

---

## 🔍 **VERIFICATION CHECKLIST**

After cleanup, verify:

- [ ] Run `.\security_check_before_commit.ps1` - should PASS
- [ ] Run `git status` - should NOT show .env, token.json, logs, etc.
- [ ] Check GitHub repository - no sensitive files visible
- [ ] Application still works with new API keys
- [ ] All tests pass
- [ ] Documentation is up to date

---

## 📊 **WHAT STAYS IN GIT (SAFE TO COMMIT)**

### **✅ Safe to Commit:**
```
✅ Source code (*.py, *.js, *.jsx, *.css)
✅ Configuration examples (env.example, env_production_example.txt)
✅ Documentation (*.md files)
✅ Requirements (requirements*.txt)
✅ Database migrations (migrate_*.py)
✅ .gitignore
✅ README files
✅ Package files (package.json, package-lock.json)
```

### **❌ NEVER Commit:**
```
❌ .env (contains API keys and passwords)
❌ token.json (OAuth tokens)
❌ credentials.json (service account keys)
❌ *.log (may contain sensitive data)
❌ Resumes/ (user personal data)
❌ Cache/ (may contain user data)
❌ backups/ (contains all user data)
❌ server/sessions/ (contains form data)
❌ *.pem, *.key (SSL certificates)
```

---

## 🎯 **DEPENDENCIES ON SENSITIVE FILES**

You asked about dependencies - here's what needs special handling:

### **1. .env File**
**Dependency:** Application won't start without it

**Solution:**
```
✅ Keep .env locally (in .gitignore)
✅ Provide env.example for others
✅ Document all required variables
✅ Use Railway/Vercel environment variables in production
```

### **2. token.json (OAuth)**
**Dependency:** Google OAuth won't work without it initially

**Solution:**
```
✅ Generated automatically on first OAuth flow
✅ Users connect their own Google accounts
✅ No need to commit - each deployment generates its own
```

### **3. Resumes/ Directory**
**Dependency:** Tailored resumes are saved here

**Solution:**
```
✅ Create directory automatically if doesn't exist
✅ Each user's resumes in their Google Drive (not local)
✅ Local copies are just cache - can be deleted
```

### **4. Cache/ Directory**
**Dependency:** Mimikree responses cached here

**Solution:**
```
✅ Create directory automatically if doesn't exist
✅ Cache regenerates on first use
✅ Not critical - just improves performance
```

### **5. Database**
**Dependency:** Application needs database

**Solution:**
```
✅ Migrations create schema automatically
✅ Each deployment has own database
✅ No need to commit database files
```

---

## 🔄 **IF YOU'VE ALREADY PUSHED TO GITHUB**

### **Scenario 1: Private Repository**
```
✅ Less urgent but still important
✅ Clean up at your convenience
✅ Rotate keys before making public
```

### **Scenario 2: Public Repository**
```
🚨 URGENT - API keys are publicly exposed!
🚨 Rotate ALL keys IMMEDIATELY
🚨 Clean Git history ASAP
🚨 Consider creating new repository
```

### **Scenario 3: Not Pushed Yet**
```
✅ Perfect! Clean up now before first push
✅ Follow steps above
✅ Push clean repository
```

---

## 💡 **BEST PRACTICES GOING FORWARD**

### **1. Never Commit Secrets**
```powershell
# Before each commit, run:
.\security_check_before_commit.ps1
```

### **2. Use Environment Variables**
```python
# ✅ GOOD
api_key = os.getenv('GOOGLE_API_KEY')

# ❌ BAD
api_key = "AIzaSyCQTZsq3iIyIC7Zi7NGbmMxEJT6BlDqi-M"
```

### **3. Use .env.example**
```
✅ Commit: env.example (with placeholder values)
❌ Never commit: .env (with real values)
```

### **4. Regular Security Checks**
```powershell
# Weekly or before major commits
.\security_check_before_commit.ps1
```

### **5. Key Rotation Schedule**
```
Every 90 days:
├── Rotate API keys
├── Rotate JWT secret
├── Rotate encryption key
└── Update .env
```

---

## 🎯 **QUICK DECISION TREE**

### **Has .env been committed to Git?**

```
Check: git log --all -- .env

YES → Clean history + Rotate ALL keys (CRITICAL)
NO → Just ensure it's in .gitignore (EASY)
```

### **Is your repo public on GitHub?**

```
YES + .env committed → URGENT! Rotate keys NOW!
YES + .env not committed → You're safe, just don't commit it
NO (private) → Clean up at your convenience
NO (not pushed yet) → Perfect! Clean up before first push
```

---

## ✅ **FINAL CHECKLIST**

Before publishing your repository:

- [ ] Ran `.\cleanup_sensitive_data.ps1`
- [ ] Ran `.\security_check_before_commit.ps1` - PASSED
- [ ] Rotated API keys (if .env was in history)
- [ ] Updated .env with new keys
- [ ] Tested application works
- [ ] Verified .gitignore is working
- [ ] Checked GitHub - no sensitive files visible
- [ ] Created env.example for others
- [ ] Documented all required environment variables

---

## 🚀 **YOU'RE READY!**

Once all checks pass, your repository is secure and ready to publish!

**Remember:**
- Keep .env local and secret
- Never commit sensitive data
- Run security checks before commits
- Rotate keys regularly

**Your application is well-secured for beta launch!** 🎉
