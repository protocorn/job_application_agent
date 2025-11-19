# 🚀 VNC Streaming Setup Guide

## 📋 What We're Building

A cloud-based browser automation system where:
1. Agent runs browser on Railway (visible on virtual display)
2. VNC streams browser to your website (Vercel)
3. User sees and controls browser from website
4. Agent fills form, user reviews and submits

**Result: 100% accurate form preservation + user interaction from website!**

---

## 🏗️ Architecture Overview

```
┌─────────────────────┐
│  Frontend (Vercel)  │
│  ┌───────────────┐  │
│  │ noVNC Viewer  │←──── WebSocket ────┐
│  │ (User sees    │  │                  │
│  │  live browser)│  │                  │
│  └───────────────┘  │                  │
└─────────────────────┘                  │
                                         │
┌─────────────────────────────────────────┴──┐
│  Backend (Railway)                         │
│  ┌─────────────────────────────────────┐  │
│  │  Virtual Display (Xvfb) :99         │  │
│  │  ┌───────────────────────────────┐  │  │
│  │  │  Chrome Browser (Visible!)    │  │  │
│  │  │  ┌─────────────────────────┐  │  │  │
│  │  │  │ Job Application Form    │  │  │  │
│  │  │  │ [Name: ______]          │  │  │  │
│  │  │  │ [Email: _____]          │  │  │  │
│  │  │  │ Agent fills these! →    │  │  │  │
│  │  │  └─────────────────────────┘  │  │  │
│  │  └───────────────────────────────┘  │  │
│  └──────────┬──────────────────────────┘  │
│             ↓                              │
│  ┌─────────────────────┐                  │
│  │  VNC Server (x11vnc)│                  │
│  │  Port: 5900         │                  │
│  └──────────┬──────────┘                  │
│             ↓                              │
│  ┌─────────────────────┐                  │
│  │  Websockify Proxy   │                  │
│  │  Converts VNC to WS │──────────────────┘
│  └─────────────────────┘
└───────────────────────────────────────────┘
```

---

## ✅ Step 1: Infrastructure Setup (COMPLETED)

**Files created:**
- ✅ `Agents/components/vnc/virtual_display_manager.py` - Manages Xvfb
- ✅ `Agents/components/vnc/vnc_server.py` - Manages x11vnc
- ✅ `Agents/components/vnc/browser_vnc_coordinator.py` - Coordinates everything
- ✅ `Dockerfile.vnc` - Railway deployment configuration
- ✅ `requirements_vnc.txt` - VNC dependencies
- ✅ `railway.json` - Railway build configuration

**What these do:**
- Start virtual display (Xvfb) on Railway
- Launch visible browser on virtual display
- Start VNC server to stream display
- Coordinate all components

---

## 🔧 Step 2: Test Locally (Before Railway)

### Install dependencies:

```powershell
# Install Python dependencies
pip install -r requirements_vnc.txt

# Install system dependencies (if on Linux/WSL)
# For Windows: Install WSL first, then:
sudo apt-get update
sudo apt-get install -y xvfb x11vnc chromium-browser

# Install Playwright browsers
playwright install chromium
```

### Test virtual display:

```python
# test_vnc_setup.py
import asyncio
from Agents.components.vnc import BrowserVNCCoordinator

async def test_vnc():
    coordinator = BrowserVNCCoordinator(vnc_port=5900)
    
    if await coordinator.start():
        print("✅ VNC environment started!")
        print(f"VNC URL: {coordinator.get_vnc_url()}")
        print(f"Status: {coordinator.get_status()}")
        
        # Test navigation
        page = coordinator.get_page()
        await page.goto("https://example.com")
        print(f"✅ Navigated to: {page.url}")
        
        # Keep running for 30 seconds
        print("\n🔗 Connect with VNC viewer to: localhost:5900")
        print("   Press Ctrl+C to stop...")
        await asyncio.sleep(30)
        
        await coordinator.stop()
    else:
        print("❌ Failed to start VNC environment")

asyncio.run(test_vnc())
```

```powershell
python test_vnc_setup.py
```

**Connect with VNC viewer:**
- Download: TightVNC, RealVNC, or TigerVNC
- Connect to: `localhost:5900`
- You should see the browser!

---

## 📦 Step 3: Railway Deployment Configuration

### Update `server/api_server.py` to support VNC:

```python
# Add at the top
from Agents.components.vnc import BrowserVNCCoordinator
import threading

# Global VNC coordinators (one per active session)
VNC_SESSIONS = {}

# Add health check endpoint
@app.route("/health", methods=['GET'])
def health_check():
    return jsonify({"status": "healthy", "vnc_sessions": len(VNC_SESSIONS)}), 200
```

### Deploy to Railway:

```bash
# Railway will use Dockerfile.vnc automatically
railway up
```

---

## 🎯 Next Steps

Now that infrastructure is set up, we need to:

1. **Integrate with job_application_agent.py** (NEXT)
   - Modify agent to use VNC coordinator
   - Keep browser alive instead of closing
   - Return VNC session info

2. **Add WebSocket endpoint** (After integration)
   - Convert VNC to WebSocket using websockify
   - Allow frontend to connect

3. **Create frontend viewer** (After backend ready)
   - Embed noVNC in React component
   - Show live browser to user

---

## ⏰ Estimated Timeline

- ✅ Step 1: Infrastructure setup (DONE)
- 🔄 Step 2: Agent integration (2-3 hours)
- ⏳ Step 3: WebSocket endpoint (1-2 hours)
- ⏳ Step 4: Frontend viewer (2-3 hours)
- ⏳ Step 5: Testing (2-3 hours)

**Total: ~8-12 hours of focused work**

---

## 💰 Cost Estimate (Railway Hobby)

**Running 1 VNC session:**
- Virtual display (Xvfb): ~20MB RAM, 0.1 vCPU
- VNC server (x11vnc): ~30MB RAM, 0.2 vCPU  
- Browser (Chrome): ~500MB RAM, 1 vCPU
- **Total per session: ~550MB RAM, 1.3 vCPU**

**Hobby plan limits:**
- 8 GB RAM → ~14 concurrent sessions MAX
- 8 vCPU → ~6 concurrent sessions MAX
- **Realistically: 5-6 concurrent job applications**

**Cost per job (15 min avg):**
- ~$0.03-0.05 per application
- 100 jobs/month = $3-5 (within $5 Hobby plan!) ✅

---

## 🎉 Initial Setup Complete!

**What's ready:**
- ✅ Virtual display manager
- ✅ VNC server wrapper  
- ✅ Browser coordinator
- ✅ Docker configuration
- ✅ Railway deployment config

**Next: Integrate with your job application agent!**

Ready to continue? Let's modify `job_application_agent.py` to use VNC! 🚀

