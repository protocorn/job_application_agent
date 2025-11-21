# 🔧 VNC WebSocket Subprotocol Fix

## Problem
The VNC connection was closing immediately with code `1005` (Client Closed) or `1006` (Abnormal Closure).
The backend logs showed successful TCP connection to the VNC server, but the frontend disconnected immediately.

## Root Cause
`noVNC` (the frontend client) requests the `binary` WebSocket subprotocol by default.
Our Python backend (`Flask-Sock`) does not explicitly negotiate/accept this subprotocol in its handshake response.
When `noVNC` doesn't see the expected subprotocol in the response headers, it treats it as a protocol violation or mismatch and closes the connection.

## Solution
Modified `Website/job-agent-frontend/src/VNCViewer.js` to disable subprotocol requests:
```javascript
wsProtocols: [] 
```
This forces `noVNC` to establish a standard WebSocket connection without requiring specific subprotocol confirmation from the server. Since both sides support binary frames natively, this works fine.

## Action Required
1. **Rebuild Frontend:** The frontend change requires a rebuild if you are in production, or a hot reload if in dev.
2. **Restart Batch:** The "404 Batch Not Found" error you saw was likely due to the backend restarting (clearing in-memory batch data). Please start a new batch application to test.

