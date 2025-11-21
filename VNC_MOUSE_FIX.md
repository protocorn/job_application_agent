# 🔧 VNC Mouse & Cursor Fix

## Problem
1. **Right-click not working:** Users cannot open context menus.
2. **Cursor misalignment:** Text selection is imprecise; the VNC cursor doesn't match the remote cursor position.

## Root Cause
1. **Right-Click:** Some default `x11vnc` configurations map mouse buttons differently or require specific flags to pass all mouse events correctly.
2. **Cursor Misalignment:** `x11vnc` often tries to hide the X11 cursor and let the client render a local cursor. This can cause sync issues where the "visible" cursor is offset from the "actual" click point.

## Solution
Updated `Agents/components/vnc/vnc_server.py` with optimized `x11vnc` flags:

```python
'-cursor', 'arrow',  # Force server-side cursor rendering to ensure alignment
'-ncache', '10'      # Client-side caching to improve responsiveness
```

Note: `x11vnc` passes right-clicks by default, but sometimes the lack of cursor sync makes it *feel* like clicks aren't registering because you're clicking the wrong spot. Fixing the cursor alignment usually resolves the interaction feel.

## Action Required
- **Restart the Backend/Agent:** The `x11vnc` server needs to restart with the new flags.
- **Reconnect VNC:** Close and reopen the VNC viewer.

