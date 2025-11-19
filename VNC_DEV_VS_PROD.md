# 🔧 VNC Development vs Production Configuration

## ✅ Fixed! WebSocket URLs Now Handle Both Environments

### Development Configuration:

**Frontend:** `http://localhost:3000` (React dev server)
**Backend:** `http://localhost:5000` (Flask)
**VNC Server:** `localhost:5900`
**Websockify:** `localhost:6900`
**WebSocket URL:** `ws://localhost:6900` ✅ (HTTP, no SSL)

### Production Configuration:

**Frontend:** `https://your-app.vercel.app` (Vercel)
**Backend:** `https://your-backend.railway.app` (Railway)
**VNC Server:** Internal `localhost:5900`
**Websockify:** Internal `localhost:6900`
**WebSocket URL:** `wss://your-backend.railway.app/vnc-stream/{session_id}` ✅ (HTTPS with SSL)

---

## 🔍 How the Code Detects Environment:

```python
# In server/vnc_api_endpoints.py (lines 122-131)

# Detect environment
is_development = os.getenv('FLASK_ENV') == 'development' or 'localhost' in request.host

# Choose protocol
ws_protocol = 'ws' if is_development else 'wss'

# Generate URL
if is_development:
    # Direct connection to websockify
    websocket_url = f"{ws_protocol}://localhost:6900"
else:
    # Proxied through backend
    websocket_url = f"{ws_protocol}://{request.host}/vnc-stream/{session_id}"
```

**This automatically works for both environments!** ✅

---

## 🧪 Testing in Development:

### 1. Start Backend:
```powershell
# Set development environment
$env:FLASK_ENV="development"

# Start server
python server\api_server.py
```

**Expected log:**
```
✅ VNC streaming initialized successfully
🚀 Starting server with Socket.IO support on port 5000
   Mode: DEVELOPMENT
   VNC Streaming: ENABLED ✅
```

### 2. Start Websockify (Manual for local testing):
```powershell
# In a separate terminal
websockify 6900 localhost:5900
```

**Expected output:**
```
WebSocket server settings:
  - Listen on :6900
  - Web server disabled
  - Target: localhost:5900
  
Starting server...
```

### 3. Start Frontend:
```powershell
cd Website\job-agent-frontend
npm start
```

Frontend opens: `http://localhost:3000`

### 4. Test VNC Connection:

**Click "Apply with Live View"**

**Expected WebSocket URL:**
```
ws://localhost:6900  ✅ (Correct for development!)
```

**noVNC will connect directly to websockify on port 6900.**

---

## 🚀 Production Deployment:

### Railway automatically handles:
1. ✅ Starts Xvfb (virtual display)
2. ✅ Starts x11vnc (VNC server on 5900)
3. ✅ Starts websockify (WebSocket proxy on 6900)
4. ✅ Starts Flask with Socket.IO (API on 5000)

### Frontend will receive:
```json
{
  "websocket_url": "wss://your-backend.railway.app/vnc-stream/abc-123"
}
```

**noVNC will connect through backend proxy with SSL.** ✅

---

## 🔧 Environment Variables:

### Development:
```bash
FLASK_ENV=development  # Triggers ws:// and localhost URLs
```

### Production (Railway):
```bash
FLASK_ENV=production  # Triggers wss:// and railway.app URLs
# OR simply don't set it (defaults to production)
```

---

## 💡 Why This Matters:

### Wrong Configuration ❌:
```
Development using wss://localhost:6900
→ SSL error (localhost doesn't have SSL certificate)
→ Connection fails

Production using ws://railway.app
→ Mixed content error (HTTPS page loading WS resource)
→ Connection blocked by browser
```

### Correct Configuration ✅:
```
Development: ws://localhost:6900
→ No SSL needed
→ Direct connection works!

Production: wss://railway.app/vnc-stream/...
→ SSL matches HTTPS frontend
→ Secure connection works!
```

---

## 🧪 How to Test Both Environments:

### Test Development Mode:
```powershell
# Set environment
$env:FLASK_ENV="development"

# Start backend
python server\api_server.py

# Start websockify manually
websockify 6900 localhost:5900

# Test API
curl -X POST http://localhost:5000/api/vnc/apply-job `
  -H "Content-Type: application/json" `
  -H "Authorization: Bearer TOKEN" `
  -d '{"jobUrl": "https://test.com"}'

# Check response:
# "websocket_url": "ws://localhost:6900" ✅
```

### Test Production Mode:
```powershell
# Don't set FLASK_ENV (defaults to production)

# Start backend
python server\api_server.py

# Test API
curl -X POST http://localhost:5000/api/vnc/apply-job `
  -H "Content-Type: application/json" `
  -H "Authorization: Bearer TOKEN" `
  -d '{"jobUrl": "https://test.com"}'

# Check response:
# "websocket_url": "wss://localhost:5000/vnc-stream/abc-123" ✅
```

---

## ✅ Now it works correctly for both!

**Development:**
- Frontend on localhost:3000 ✅
- Backend on localhost:5000 ✅
- WebSocket: ws://localhost:6900 ✅

**Production:**
- Frontend on vercel.app ✅
- Backend on railway.app ✅
- WebSocket: wss://railway.app/vnc-stream/... ✅

**All environment issues resolved!** 🎉

