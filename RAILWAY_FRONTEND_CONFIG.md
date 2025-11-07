# Frontend Configuration for Railway Backend

## Summary
The frontend has been configured to use the Railway production backend URL in production mode, while using localhost in development mode.

## Railway Backend URL
```
https://jobapplicationagent-production.up.railway.app
```

## Changes Made

### 1. Created API Configuration ([config.js](Website/job-agent-frontend/src/config.js))
- Automatically detects environment (development vs production)
- Exports `API_URL`, `getApiEndpoint()`, and `apiClient`
- `apiClient` is a pre-configured axios instance with:
  - Automatic baseURL setting based on environment
  - Automatic auth token injection via interceptor
  - Default JSON content-type headers

### 2. Updated All Frontend Files
All API calls have been updated to use the new configuration:

**Files using `apiClient` (axios-based):**
- `JobApplyPage.js` - Job application functionality
- `JobLogs.js` - Job logs and status
- `JobSearchPage.js` - Job search functionality
- `TailorResumePage.js` - Resume tailoring
- `Profile.js` - User profile management
- `components/MimikreeConnection.js` - Mimikree integration
- `components/ProjectSelector.js` - Project selection
- `components/ProjectManager.js` - Project management

**Files using `getApiEndpoint()` (fetch-based):**
- `Dashboard.js` - Dashboard and sessions
- `Login.js` - User login
- `Signup.js` - User registration
- `BatchApplyPage.js` - Batch job application
- `BatchJobsPage.js` - Batch job tracking

### 3. Environment Files Created

#### `.env.development`
```env
REACT_APP_API_URL=http://localhost:5000
```
Used automatically when running `npm start`

#### `.env.production`
```env
REACT_APP_API_URL=https://jobapplicationagent-production.up.railway.app
```
Used automatically when running `npm run build`

#### `.env.example`
Template file for documentation

## How It Works

### Development Mode (`npm start`)
- Uses `NODE_ENV=development`
- API calls go to: `http://localhost:5000`
- Falls back to localhost if `REACT_APP_API_URL` is not set

### Production Mode (`npm run build`)
- Uses `NODE_ENV=production`
- API calls go to: `https://jobapplicationagent-production.up.railway.app`
- Uses Railway backend URL by default
- Can be overridden with `REACT_APP_API_URL` environment variable

### Custom Configuration
You can override the default URL by setting the `REACT_APP_API_URL` environment variable:
```bash
# For a different backend during development
REACT_APP_API_URL=https://staging-backend.example.com npm start

# For a different backend in production build
REACT_APP_API_URL=https://custom-backend.com npm run build
```

## Deployment Instructions

### Railway Frontend Deployment
When deploying the frontend to Railway (or any hosting platform):

1. **Set environment variable:**
   ```
   REACT_APP_API_URL=https://jobapplicationagent-production.up.railway.app
   ```

2. **Build command:**
   ```bash
   npm run build
   ```

3. **The built files in `/build` directory will be configured to use the Railway backend**

### Vercel/Netlify Deployment
If deploying frontend to Vercel or Netlify:

1. Add environment variable in the platform's dashboard:
   - Variable name: `REACT_APP_API_URL`
   - Variable value: `https://jobapplicationagent-production.up.railway.app`

2. The build process will automatically use this value

## Testing

### Test Development Configuration
```bash
cd Website/job-agent-frontend
npm start
# Should connect to http://localhost:5000
```

### Test Production Build
```bash
cd Website/job-agent-frontend
npm run build
# Check that built files reference Railway URL
```

### Verify Configuration
Open browser console and check:
```javascript
// In development
console.log(process.env.NODE_ENV); // "development"
console.log(process.env.REACT_APP_API_URL); // "http://localhost:5000"

// In production build
console.log(process.env.NODE_ENV); // "production"
console.log(process.env.REACT_APP_API_URL); // "https://jobapplicationagent-production.up.railway.app"
```

## Benefits

1. **Automatic Environment Detection** - No manual configuration needed
2. **Type Safety** - Centralized configuration reduces errors
3. **DRY Principle** - Single source of truth for API URL
4. **Token Management** - Automatic auth token injection
5. **Easy Testing** - Can override URL for testing different backends
6. **Production Ready** - Configured for Railway deployment out of the box

## Notes

- The `package.json` proxy setting (`"proxy": "http://localhost:5000"`) is still in place for development server proxying
- `.gitignore` already properly excludes `.env.local` and `.env.*.local` files
- Environment files `.env.development` and `.env.production` are committed to the repository for team consistency
- For sensitive values, use `.env.local` which is gitignored

## Troubleshooting

### Frontend Can't Connect to Backend
1. Verify Railway backend is running: `https://jobapplicationagent-production.up.railway.app/health`
2. Check browser console for CORS errors
3. Verify environment variable is set correctly
4. Check that Railway backend allows CORS from frontend domain

### API Calls Going to Wrong URL
1. Check `process.env.NODE_ENV` in browser console
2. Verify `.env.production` file exists and has correct URL
3. Rebuild the application: `npm run build`
4. Clear browser cache and rebuild

### Authentication Issues
1. Verify token is stored in localStorage: `localStorage.getItem('auth_token')`
2. Check that apiClient interceptor is adding Authorization header
3. Verify backend accepts the token format
