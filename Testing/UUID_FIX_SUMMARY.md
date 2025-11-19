# UUID Support - Fix Summary

## Problem
The database uses UUIDs for user IDs, but the code was expecting integer IDs.

## Changes Made

### 1. Updated `agent_profile_service.py`
- Changed `get_profile_by_user_id(user_id: int)` to `get_profile_by_user_id(user_id: Union[str, UUID])`
- Added UUID conversion: converts string UUIDs to UUID objects automatically
- Now properly handles both string and UUID object types

### 2. Updated `job_application_agent_test.py`
- Changed `--user-id` argument from `type=int` to `type=str` (line 2561)
- Updated environment variable handling to pass UUID string directly (no int conversion)

### 3. Updated `run_agent_with_tracking.py`
- Changed `--user-id` argument from `type=int` to `type=str` (line 246)
- Now accepts UUID strings like "033b8626-a468-48fc-9601-fdaec6f0fee9"

### 4. Updated `list_users.py`
- Shows proper UUID format in example commands
- Wraps UUID in quotes in example: `--user-id "033b8626-a468-48fc-9601-fdaec6f0fee9"`

## How to Use

### Find Your User UUID
```bash
python Testing/list_users.py
```

This shows all users with their UUIDs, names, and emails.

### Run Agent with Specific User
```bash
python Testing/run_agent_with_tracking.py \
  --links "https://job-url-here" \
  --user-id "033b8626-a468-48fc-9601-fdaec6f0fee9" \
  --headful \
  --keep-open \
  --slowmo 20
```

### Run Agent Directly (Without Testing Framework)
```bash
python Agents/job_application_agent_test.py \
  --links "https://job-url-here" \
  --user-id "033b8626-a468-48fc-9601-fdaec6f0fee9" \
  --headful \
  --keep-open \
  --slowmo 20
```

## Database Schema

**Users Table:**
- `id` → UUID (primary key)
- `email`, `first_name`, `last_name`, etc.

**UserProfile Table:**
- `id` → Integer (sequential, auto-increment)
- `user_id` → UUID (foreign key to users.id)
- All profile fields (resume_url, education, work_experience, etc.)

The service queries by `user_id` (UUID), not by the profile's sequential ID.

## Testing

After these changes, you can now:
1. ✅ Pass UUID strings as `--user-id` parameter
2. ✅ Agent loads the correct user profile from database
3. ✅ No more type conversion errors
4. ✅ Works with both the agent and testing framework
