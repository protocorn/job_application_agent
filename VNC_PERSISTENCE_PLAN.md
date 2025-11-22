# 🔧 Session Persistence & Recovery

## Problem
When the Railway server restarts (deployment, crash, or maintenance), all in-memory VNC sessions are lost.
Users lose their progress and have to restart the application process manually.

## Solution
We cannot keep the *process* alive (RAM is wiped), but we can save the **Session Metadata** to the database and **Auto-Resume** it on server startup.

### 1. Database Schema
Add a `vnc_sessions` table to `database_config.py`:
```python
class VNCSession(Base):
    __tablename__ = "vnc_sessions"
    id = Column(String, primary_key=True) # session_id
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"))
    job_url = Column(String)
    status = Column(String) # active, completed, failed
    created_at = Column(DateTime)
    last_active_at = Column(DateTime)
```

### 2. Session Manager Update
Modify `VNCSessionManager` to:
*   **Save:** When creating a session, write row to DB.
*   **Update:** Heartbeat or status update writes to DB.
*   **Load:** On `__init__`, read "active" sessions from DB.

### 3. Recovery Logic
On server startup (`initialize_production_infrastructure`):
1.  Query DB for `status='active'`.
2.  For each active session:
    *   Check if it's actually running (it won't be after restart).
    *   **Restart it:** Spin up a new VNC/Browser for that `job_url`.
    *   **Inject Resume:** Re-inject the resume file (need to persist path).
3.  The user might see a "Disconnect" then "Reconnect", but the session comes back!

## Implementation Steps
1.  Update `database_config.py` with `VNCSession` model.
2.  Update `VNCSessionManager` to use `SessionLocal` for DB ops.
3.  Add recovery loop in `vnc_api_endpoints.py` or `api_server.py`.

