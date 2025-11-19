# 🔌 VNC Backend Integration Guide

## ✅ Step 2 Complete: Agent Integration

### What's Been Built:

1. **`job_application_agent.py` (modified)**
   - Added `vnc_mode` parameter
   - Uses `BrowserVNCCoordinator` when VNC enabled
   - Keeps browser alive for user interaction
   - Returns VNC session info

2. **`server/vnc_api_endpoints.py`** (new)
   - `/api/vnc/apply-job` - Start VNC job application
   - `/api/vnc/sessions` - List user's VNC sessions
   - `/api/vnc/session/<id>` - Get/delete specific session
   - `/api/vnc/health` - VNC infrastructure health

3. **`server/vnc_websocket_proxy.py`** (new)
   - Manages websockify processes
   - Converts VNC to WebSocket
   - One proxy per VNC session

4. **`server/vnc_socketio_handler.py`** (new)
   - Flask-SocketIO handlers
   - Real-time VNC streaming
   - Client connection management

---

## 🔧 How to Integrate with api_server.py

Add these lines to `server/api_server.py`:

### 1. Add imports (at the top):

```python
# VNC streaming support (add after existing imports)
from vnc_api_endpoints import vnc_api
from vnc_socketio_handler import setup_vnc_socketio
from flask_socketio import SocketIO
```

### 2. Initialize Socket.IO (after app = Flask(__name__)):

```python
# Initialize Socket.IO for VNC streaming
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='threading')

# Setup VNC handlers
setup_vnc_socketio(socketio)

logger.info("✅ Socket.IO initialized for VNC streaming")
```

### 3. Register VNC Blueprint (before app.run()):

```python
# Register VNC API endpoints
app.register_blueprint(vnc_api)
logger.info("✅ VNC API endpoints registered")
```

### 4. Update app.run() to use socketio.run():

```python
# Change from:
# app.run(host='0.0.0.0', port=5000, debug=False)

# To:
socketio.run(app, host='0.0.0.0', port=5000, debug=False)
```

---

## 🎯 Complete Integration Example:

```python
# server/api_server.py

from flask import Flask, request, jsonify
from flask_cors import CORS
from flask_socketio import SocketIO
import logging

# ... your existing imports ...

# VNC streaming support (NEW)
from vnc_api_endpoints import vnc_api
from vnc_socketio_handler import setup_vnc_socketio

# Initialize app
app = Flask(__name__)
CORS(app)

# Initialize Socket.IO for VNC streaming (NEW)
socketio = SocketIO(
    app, 
    cors_allowed_origins="*",
    async_mode='threading',
    logger=True,
    engineio_logger=True
)

# Setup VNC handlers (NEW)
setup_vnc_socketio(socketio)

# ... your existing endpoints ...

# Register VNC API endpoints (NEW)
app.register_blueprint(vnc_api)

# ... rest of your code ...

if __name__ == "__main__":
    # Use socketio.run instead of app.run (CHANGED)
    socketio.run(
        app,
        host='0.0.0.0',
        port=int(os.getenv('PORT', 5000)),
        debug=False,
        allow_unsafe_werkzeug=True
    )
```

---

## 📡 API Endpoints Available:

### Start VNC Job Application
```bash
POST /api/vnc/apply-job
Authorization: Bearer <token>

{
  "jobUrl": "https://greenhouse.io/job/12345"
}

Response:
{
  "success": true,
  "session_id": "abc-123",
  "vnc_port": 5900,
  "websocket_url": "wss://your-backend.railway.app/vnc-stream/abc-123",
  "message": "VNC session started"
}
```

### Get User's VNC Sessions
```bash
GET /api/vnc/sessions
Authorization: Bearer <token>

Response:
{
  "sessions": [
    {
      "session_id": "abc-123",
      "job_url": "https://...",
      "vnc_port": 5900,
      "status": "active",
      "created_at": "2025-01-18T10:30:00"
    }
  ]
}
```

### Get Specific Session
```bash
GET /api/vnc/session/abc-123
Authorization: Bearer <token>

Response:
{
  "session_id": "abc-123",
  "websocket_url": "wss://...",
  "status": "active"
}
```

### Close Session
```bash
DELETE /api/vnc/session/abc-123
Authorization: Bearer <token>

Response:
{
  "success": true,
  "message": "VNC session closed"
}
```

---

## 🧪 Testing Locally:

### 1. Install dependencies:
```powershell
pip install flask-socketio websockify
```

### 2. Start backend with VNC:
```powershell
python server\api_server.py
```

### 3. Test VNC endpoint:
```powershell
curl -X POST http://localhost:5000/api/vnc/apply-job `
  -H "Content-Type: application/json" `
  -H "Authorization: Bearer YOUR_TOKEN" `
  -d '{"jobUrl": "https://test-job-url.com"}'
```

### 4. Connect VNC viewer:
```
Download: TightVNC Viewer
Connect to: localhost:5900
```

You should see the browser filling the form!

---

## 🎯 How It Works (Complete Flow):

```
1. User clicks "Apply" on your website (Vercel)
   ↓
2. Frontend → POST /api/vnc/apply-job
   ↓
3. Backend starts VNC session:
   - Starts Xvfb (virtual display)
   - Starts x11vnc (VNC server)
   - Launches Playwright browser (visible on virtual display)
   - Starts websockify (VNC → WebSocket)
   ↓
4. Backend returns: { websocket_url: "wss://..." }
   ↓
5. Frontend connects to WebSocket URL
   ↓
6. noVNC viewer shows live browser stream
   ↓
7. Agent fills form (user watches in real-time!)
   ↓
8. Agent pauses (browser stays open)
   ↓
9. User takes over via noVNC (clicks, types)
   ↓
10. User submits when ready
    ↓
11. User clicks "Done" → DELETE /api/vnc/session/{id}
    ↓
12. Backend closes VNC session, frees resources
```

---

## 📊 Resource Management:

**Per VNC session:**
- Display: 20 MB RAM
- VNC server: 30 MB RAM  
- Browser: 500 MB RAM
- Websockify: 20 MB RAM
- **Total: ~570 MB per session**

**Railway Hobby (8 GB RAM):**
- 8000 MB / 570 MB = **~14 sessions max**
- Recommended: 10 sessions max (leave buffer)

**Cost:**
- Per session (15 min): ~$0.03-0.05
- 100 jobs/month: ~$3-5 (within $5 Hobby plan!) ✅

---

## ✅ Next Steps:

**Step 3:** Create frontend noVNC viewer component (React)
**Step 4:** End-to-end testing
**Step 5:** Deploy to Railway

**Ready to continue?** 🚀

