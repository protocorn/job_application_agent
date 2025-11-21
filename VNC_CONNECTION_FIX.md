# 🔧 VNC WebSocket Proxy Fix

## Problem
Users reported that after the job application agent finishes, the browser view (VNC) is not visible.
Logs showed:
```
Client → VNC forwarding ended: Connection closed: 1005
```
and
```
Proxying to websockify on localhost:6900
```

## Root Cause
The `vnc_stream_proxy` (Flask endpoint) was acting as a "middleman" between the Frontend (Client) and `websockify`.
The chain was:
`Client (WS) -> Flask (WS) -> vnc_stream_proxy (TCP) -> websockify (WS Server) -> VNC Server (TCP)`

1. **Frontend** connects to Flask via WebSocket.
2. **Flask-Sock** accepts the connection and unwraps the WebSocket frames, exposing the raw payload (VNC/RFB protocol data).
3. **vnc_stream_proxy** was forwarding this **raw data** to `websockify` (port 6900).
4. **websockify** listens on port 6900 and **expects a WebSocket Handshake**.
5. Because `vnc_stream_proxy` sent raw data (not a WS handshake), `websockify` rejected the connection or closed it immediately.

## Solution
The `vnc_stream_proxy` should act as the bridge itself, replacing the need for an external `websockify` process for this specific route.
It should connect directly to the VNC Server (TCP 5900).

The corrected chain is:
`Client (WS) -> Flask (WS) -> vnc_stream_proxy (TCP) -> VNC Server (TCP)`

## Changes
Modified `server/vnc_stream_proxy.py`:
- Changed connection target from `ws_port` (6900+) to `vnc_port` (5900+).
- Updated logging to reflect direct connection to VNC server.

This ensures that the raw VNC data unwrapped by Flask is sent to the VNC server which expects raw VNC data.

