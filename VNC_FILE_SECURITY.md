# 🔧 VNC File System Security

## Problem
The user can see and potentially browse the server's file system (e.g., `/app`, `requirements.txt`) via the browser's "Upload File" dialog.
Since the browser runs on the server, it has access to the server's files.

## Solution Strategies

### 1. Kiosk Mode (Applied)
I updated the Playwright launch arguments to include `--kiosk`.
This forces the browser into full-screen "Kiosk" mode.
*   **Benefit:** Hides the address bar, window controls, and menus. Makes it much harder for a user to "break out" of the application flow or open arbitrary file managers via the browser UI.
*   **Limitation:** When a file picker opens, it is a *system* dialog, not a browser dialog, so Kiosk mode doesn't strictly block it.

### 2. User Isolation (Infrastructure Level - REQUIRED)
**Code changes cannot fully fix this.** The only way to truly hide server files is to run the browser in a **restricted environment**.

**Recommendation for Railway/Docker:**
1.  **Create a restricted user:** Don't run the app as `root`.
2.  **Chroot / Jail:** Ideally, the browser process should run in a `chroot` jail where it can *only* see `/tmp/session_123`.
3.  **Separate Containers:** The most secure architecture is to spin up a **new, ephemeral Docker container** for each VNC session. That way, "root" in the container is isolated from the main server.

### 3. Mitigation (Current Setup)
Since we are running a single monolithic server:
*   **Do not store sensitive secrets in plain text files** (use Environment Variables).
*   **Session Isolation:** We inject resumes into `/tmp/session_{id}`. Even if a user navigates up, they just see system libs, not other users' resumes (because we put them in unique folders).
*   **Non-Root User:** Ensure your `Dockerfile` switches to a non-root user (`USER appuser`). This prevents the browser from reading `/root` or modifying system files.

## Action
I applied `--kiosk` mode to make the UI stricter.
**Strongly Recommend:** Verify your Dockerfile uses a non-root user to limit what files are readable.

