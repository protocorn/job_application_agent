# Google API Setup Guide

## Required Google APIs

Your Job Application Agent requires the following Google APIs to be enabled in your Google Cloud Project:

### 1. Google Drive API
- **Purpose**: Copy and manage resume documents
- **Required for**: Resume tailoring, document copying

### 2. Google Docs API ⚠️ **REQUIRED - Currently Disabled**
- **Purpose**: Read and edit Google Docs content
- **Required for**: Resume tailoring, content modification
- **Enable at**: https://console.developers.google.com/apis/api/docs.googleapis.com/overview?project=861770539324

## How to Enable APIs

### Step 1: Go to Google Cloud Console
1. Visit [Google Cloud Console](https://console.cloud.google.com/)
2. Select your project (Project ID: `861770539324`)

### Step 2: Enable Required APIs

#### Option A: Enable via Direct Links
1. **Google Drive API**: https://console.developers.google.com/apis/api/drive.googleapis.com/overview?project=861770539324
2. **Google Docs API**: https://console.developers.google.com/apis/api/docs.googleapis.com/overview?project=861770539324

#### Option B: Enable via API Library
1. Go to **APIs & Services** → **Library**
2. Search for "Google Docs API"
3. Click on it and press **Enable**
4. Repeat for "Google Drive API" if not already enabled

### Step 3: Verify APIs are Enabled
1. Go to **APIs & Services** → **Dashboard**
2. You should see both APIs listed as enabled
3. Wait 2-3 minutes for changes to propagate

## Current Error

```
Google Docs API has not been used in project 861770539324 before or it is disabled.
Enable it by visiting https://console.developers.google.com/apis/api/docs.googleapis.com/overview?project=861770539324
```

## After Enabling

1. Wait 2-3 minutes for the API to be fully activated
2. Retry your resume tailoring request
3. The error should be resolved

## Troubleshooting

### Error persists after enabling
- **Wait longer**: API enablement can take up to 5 minutes
- **Clear credentials**: Reconnect your Google account in the app
- **Check quotas**: Ensure you haven't exceeded API quotas

### Permission errors
- Ensure the OAuth consent screen is configured
- Verify the OAuth client has the correct scopes:
  - `https://www.googleapis.com/auth/drive.file`
  - `https://www.googleapis.com/auth/documents`

### Billing errors
- Google Cloud Project needs billing enabled for some APIs
- Check [Billing Settings](https://console.cloud.google.com/billing)

## API Quotas

### Google Docs API
- **Free quota**: 60 requests per minute per user
- **Daily limit**: Check your project's quota page

### Google Drive API
- **Free quota**: 1,000 requests per 100 seconds
- **Daily limit**: Usually very high for file operations

## Need Help?

If you continue to experience issues:
1. Check the [Google Workspace API Status](https://www.google.com/appsstatus/)
2. Review [Google Docs API Documentation](https://developers.google.com/docs/api)
3. Check server logs for detailed error messages
