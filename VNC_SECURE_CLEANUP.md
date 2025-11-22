# 🔧 Secure VNC File Management

## Problem
Users could potentially see the entire server file system or, worse, other users' temp files if we blindly injected files into shared directories like `/tmp` or `/root/Desktop`.

## Solution

### 1. Session-Specific Injection
We now inject files into a **unique directory** for every single session:
`/tmp/session_{session_id}/resume.pdf`

This means User A's resume is in `/tmp/session_A/` and User B's is in `/tmp/session_B/`.
Even if User A opens the file picker, they would have to guess User B's UUID to find their files.

### 2. Automatic Cleanup
I added a `cleanup_session_files` method to the `BrowserVNCCoordinator`.
The `BrowserVNCSession` context manager now calls this **automatically** when the session ends (when the `with` block exits).

```python
async def __aexit__(self, exc_type, exc_val, exc_tb):
    await self.coordinator.stop()
    await self.coordinator.cleanup_session_files(self.session_id)
```

This ensures that as soon as the job application is done or the user disconnects/closes the session, their temp files are wiped from the server.

## Security Note
While the user can still *see* system files (like `/usr/bin`), they are running as a non-privileged user (ideally) inside the container, so they cannot damage the system or read sensitive server configs (assuming standard Docker security practices). The main concern was **User-to-User data leakage**, which this fix addresses.

