# 🔧 Reverting Optimization & Fixing Disconnects

## Problem
1.  **Glitches:** Maximizing the browser (`--start-maximized`) caused visual glitches, likely due to the lack of a window manager to handle window sizing correctly, causing the browser to fight with Xvfb bounds.
2.  **Disconnects:** The `-clip` flag in `x11vnc` and the browser maximization changes likely caused a geometry mismatch or "race condition" where the client tried to resize/render a framebuffer that changed size or format, leading to the `Tried changing state of a disconnected RFB object` error.

## Solution

### 1. Reverted Browser Size
Modified `Agents/components/vnc/browser_vnc_coordinator.py`:
- Removed `--start-maximized`.
- Restored explicit viewport size: `viewport={'width': 1920, 'height': 1080}`.
- This provides a stable, known frame size that works well with the VNC server.

### 2. Stabilized VNC Server
Modified `Agents/components/vnc/vnc_server.py`:
- Removed `-clip 1920x1080+0+0`.
- While clipping is good for isolation, if the browser isn't perfectly positioned at (0,0) (which it might not be without a window manager), clipping can cut off part of the window or cause rendering errors. Removing it restores stability.
- Kept `-capslock` and 16-bit color (in `virtual_display_manager.py`) as those are safe.

## Action Required
1.  **Restart Backend:** To apply the code changes.
2.  **Start New Session:** The browser should look "normal" again (fixed size) and the connection should be stable.

