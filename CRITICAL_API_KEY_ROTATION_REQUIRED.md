# 🚨 CRITICAL: API KEY ROTATION REQUIRED

## ⚠️ YOUR API KEYS WERE EXPOSED IN GIT HISTORY

Your `.env` file was committed to Git in the past, which means **ALL your API keys are exposed** in the Git history.

---

## 🔴 **EXPOSED CREDENTIALS**

The following credentials were found in Git history:

```
❌ GOOGLE_API_KEY=AIzaSyCQTZsq3iIyIC7Zi7NGbmMxEJT6BlDqi-M
❌ GOOGLE_CLIENT_ID=1012230438623-rrk1fm68401n3apmu3258ikqhu0mdokr...
❌ GOOGLE_CLIENT_SECRET=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
❌ ENCRYPTION_KEY=OiectVMMwGyN4MTjmj18MW9zd47RSGk168e4GrkGqI0=
❌ THEMUSE_API_KEY=4a20f3bb2d3c601723b94ba448a4fde9647d128ae691047a3556a9f3be11bcbd
❌ THEIRSTACK_API_KEY=eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...
```

---

## 🎯 **WHAT YOU NEED TO DO**

### **Option 1: Clean History First, Then Rotate (RECOMMENDED)**

This removes the exposed keys from Git history, then you rotate them.

```powershell
# Step 1: Clean Git history
git filter-branch --force --index-filter `
    "git rm --cached --ignore-unmatch .env" `
    --prune-empty --tag-name-filter cat -- --all

# Step 2: Cleanup
git reflog expire --expire=now --all
git gc --prune=now --aggressive

# Step 3: Verify .env is gone from history
git log --all -- .env
# Should show: nothing (empty)

# Step 4: NOW rotate all keys (see below)

# Step 5: Force push (ONLY if you haven't shared repo)
git push origin --force --all
```

### **Option 2: Rotate Keys First, Then Clean (SAFER)**

This ensures new keys are never exposed.

```powershell
# Step 1: Rotate ALL keys immediately (see instructions below)
# Step 2: Update your local .env with new keys
# Step 3: Test application works
# Step 4: Then clean Git history (same commands as Option 1)
# Step 5: Push with new keys only
```

---

## 🔑 **HOW TO ROTATE EACH KEY**

### **1. Google Gemini API Key** (CRITICAL)

```
1. Go to: https://aistudio.google.com/apikey
2. Find your current key: AIzaSyCQTZsq3iIyIC7Zi7NGbmMxEJT6BlDqi-M
3. Click "Delete" or "Revoke"
4. Click "Create API Key"
5. Copy the new key
6. Update .env: GOOGLE_API_KEY=NEW_KEY_HERE
7. Also update: GEMINI_API_KEY=NEW_KEY_HERE
```

### **2. Google OAuth Credentials** (CRITICAL)

```
1. Go to: https://console.cloud.google.com/apis/credentials
2. Find OAuth 2.0 Client: 1012230438623-rrk1fm68401n3apmu3258ikqhu0mdokr
3. Click "Delete"
4. Click "Create Credentials" → "OAuth 2.0 Client ID"
5. Application type: Web application
6. Authorized redirect URIs: http://localhost:5000/api/oauth/callback
7. Copy new Client ID and Client Secret
8. Update .env:
   GOOGLE_CLIENT_ID=NEW_CLIENT_ID
   GOOGLE_CLIENT_SECRET=NEW_CLIENT_SECRET
```

### **3. Encryption Key** (CRITICAL)

```powershell
# Generate new key
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

# Copy output and update .env:
ENCRYPTION_KEY=NEW_KEY_HERE
```

**⚠️ WARNING:** This will invalidate all existing encrypted data (OAuth tokens, Mimikree passwords). Users will need to reconnect their accounts.

### **4. JWT Secret Key** (CRITICAL)

```powershell
# Generate new secret
python -c "import secrets; print(secrets.token_urlsafe(32))"

# Update .env:
JWT_SECRET_KEY=NEW_SECRET_HERE
```

**⚠️ WARNING:** This will invalidate all existing user sessions. Users will need to log in again.

### **5. TheMuse API Key** (if using)

```
1. Go to: https://www.themuse.com/developers/api/v2
2. Log in to your account
3. Revoke old key: 4a20f3bb2d3c601723b94ba448a4fde9647d128ae691047a3556a9f3be11bcbd
4. Generate new API key
5. Update .env: THEMUSE_API_KEY=NEW_KEY_HERE
```

### **6. TheirStack API Key** (if using)

```
1. Go to: https://theirstack.com/
2. Log in to your account
3. Go to API settings
4. Revoke old key
5. Generate new API key
6. Update .env: THEIRSTACK_API_KEY=NEW_KEY_HERE
```

---

## ⏰ **TIMELINE**

### **If Repository is Private:**
- **Urgency**: Medium
- **Timeline**: Do it this week
- **Risk**: Low (only you have access)

### **If Repository is Public:**
- **Urgency**: CRITICAL
- **Timeline**: Do it NOW (within 24 hours)
- **Risk**: HIGH (keys are publicly accessible)

### **If Not Pushed to GitHub Yet:**
- **Urgency**: Medium
- **Timeline**: Before first push
- **Risk**: None (keys not exposed yet)

---

## 📋 **STEP-BY-STEP EXECUTION PLAN**

### **Plan A: I Haven't Pushed to GitHub Yet (EASIEST)**

```powershell
# 1. Clean Git history locally
git filter-branch --force --index-filter "git rm --cached --ignore-unmatch .env" --prune-empty --tag-name-filter cat -- --all

# 2. Cleanup
git reflog expire --expire=now --all
git gc --prune=now --aggressive

# 3. Rotate all keys (follow instructions above)

# 4. Update .env with new keys

# 5. Test application

# 6. Push to GitHub (clean history, new keys)
git push origin main
```

### **Plan B: I Already Pushed to GitHub (MORE URGENT)**

```powershell
# 1. Rotate ALL keys IMMEDIATELY (do this first!)

# 2. Update .env with new keys

# 3. Test application works

# 4. Clean Git history
git filter-branch --force --index-filter "git rm --cached --ignore-unmatch .env" --prune-empty --tag-name-filter cat -- --all
git reflog expire --expire=now --all
git gc --prune=now --aggressive

# 5. Force push to GitHub
git push origin --force --all

# 6. Verify on GitHub that .env is not visible in any commit
```

---

## ✅ **VERIFICATION**

After rotating keys and cleaning history:

```powershell
# 1. Check Git history
git log --all -- .env
# Should show: nothing (empty)

# 2. Check GitHub
# Browse your repository commits - .env should not appear

# 3. Test application
python server/api_server.py
# Should start without errors

# 4. Test API calls
# Try resume tailoring - should work with new keys
```

---

## 💡 **WHY THIS MATTERS**

**With exposed API keys, someone could:**
- Use your Gemini API quota (cost you money if you upgrade)
- Access your Google Drive (through OAuth)
- Decrypt your encrypted data (with encryption key)
- Impersonate users (with JWT secret)
- Use your job search APIs

**Even if repository is private, it's best practice to:**
- Never commit secrets
- Rotate keys if exposed
- Keep clean Git history

---

## 🚀 **AFTER KEY ROTATION**

Once you've rotated all keys:

1. **Update your local .env** with new keys
2. **Test the application** thoroughly
3. **Users will need to:**
   - Log in again (new JWT secret)
   - Reconnect Google account (new encryption key)
   - Reconnect Mimikree account (new encryption key)
4. **This is normal** - it's a one-time inconvenience for security

---

## 📞 **NEED HELP?**

If you're unsure about any step:

1. **Don't push to GitHub yet** if you haven't already
2. **Rotate keys first** (safest approach)
3. **Then clean history**
4. **Then push**

**The most important thing: Rotate the keys!**

Even if you don't clean Git history right now, rotating keys makes the old exposed keys useless.

---

## ✅ **QUICK DECISION**

**Choose ONE:**

### **A. I haven't pushed to GitHub yet**
```powershell
# Run this script - it will clean everything
.\cleanup_sensitive_data.ps1
# Then rotate keys
# Then push
```

### **B. I already pushed to GitHub (private repo)**
```powershell
# 1. Rotate keys first (most important!)
# 2. Then run cleanup script
# 3. Force push: git push origin --force --all
```

### **C. I already pushed to GitHub (public repo)**
```powershell
# 🚨 URGENT!
# 1. Rotate ALL keys RIGHT NOW
# 2. Consider creating new repository
# 3. Or clean history and force push
```

---

**Let me know which scenario applies to you, and I'll guide you through the exact steps!** 🔒
