# UUID Migration Guide - User ID Security Enhancement

## Overview

This migration converts user IDs from sequential integers (1, 2, 3...) to UUIDs (Universally Unique Identifiers) to prevent user enumeration attacks and enhance security.

## What Happens to Existing Users?

### Existing Users Are Preserved ✅

**All existing user data will be preserved.** The migration script automatically:

1. **Generates new UUIDs** for all existing users in the database
2. **Updates all relationships** (profiles, job applications, action history, etc.) to reference the new UUIDs
3. **Maintains data integrity** by updating foreign keys in a coordinated way
4. **No data loss** - all user information, profiles, and applications remain intact

### Migration Process for Existing Users

The migration script ([migrate_uuid_user_id.py](migrate_uuid_user_id.py)) performs these steps:

1. **Adds UUID column** to users table with auto-generated UUIDs for each existing user
2. **Adds UUID columns** to all related tables (user_profiles, job_applications, action_history, beta_feedback)
3. **Copies UUID values** from users table to all related tables using SQL JOINs
4. **Drops old integer ID columns** after UUIDs are in place
5. **Renames UUID columns** to become the new primary/foreign keys
6. **Re-establishes foreign key constraints** using UUIDs

### Example Migration Flow

**Before Migration:**
```
users table:
  id: 1 (integer)
  email: user@example.com

user_profiles table:
  id: 1
  user_id: 1 (integer FK)
```

**During Migration:**
```
users table:
  id: 1 (integer)
  uuid_id: "a7f8d9e2-4c3b-4a1f-9e7d-8c5b4a2f1e3d" (new)
  email: user@example.com

user_profiles table:
  id: 1
  user_id: 1 (integer FK)
  user_uuid: "a7f8d9e2-4c3b-4a1f-9e7d-8c5b4a2f1e3d" (new)
```

**After Migration:**
```
users table:
  id: "a7f8d9e2-4c3b-4a1f-9e7d-8c5b4a2f1e3d" (UUID)
  email: user@example.com

user_profiles table:
  id: 1
  user_id: "a7f8d9e2-4c3b-4a1f-9e7d-8c5b4a2f1e3d" (UUID FK)
```

## Impact on Users

### Active Sessions

**⚠️ IMPORTANT:** After migration, existing JWT tokens will become invalid because:
- Tokens contain the old integer user ID
- The system now expects UUID user IDs in tokens

**Impact:** All users will need to log in again after the migration.

### User Experience

1. **Email addresses remain the same** - users log in with the same credentials
2. **All data is preserved** - profiles, applications, history all intact
3. **Must re-authenticate** - existing sessions will be invalidated
4. **New UUIDs in responses** - API responses will now return UUID strings instead of integers

### API Response Changes

**Before Migration:**
```json
{
  "user": {
    "id": 123,
    "email": "user@example.com"
  }
}
```

**After Migration:**
```json
{
  "user": {
    "id": "a7f8d9e2-4c3b-4a1f-9e7d-8c5b4a2f1e3d",
    "email": "user@example.com"
  }
}
```

## Running the Migration

### Prerequisites

1. **Backup your database** (critical!)
   ```bash
   pg_dump -h <host> -U <user> <database> > backup_before_uuid_migration.sql
   ```

2. **Verify backup integrity**
   ```bash
   # Check backup file size
   ls -lh backup_before_uuid_migration.sql
   ```

3. **Test in staging environment first** (highly recommended)

### Migration Steps

1. **Stop the application servers** to prevent new data during migration
   ```bash
   # Stop API server
   # Stop any background workers
   ```

2. **Run the migration script**
   ```bash
   python migrate_uuid_user_id.py
   ```

3. **Verify migration success**
   ```sql
   -- Check users table has UUID IDs
   SELECT id, email FROM public.users LIMIT 5;

   -- Verify foreign key relationships
   SELECT u.id, up.user_id
   FROM public.users u
   JOIN public.user_profiles up ON u.id = up.user_id
   LIMIT 5;
   ```

4. **Deploy updated application code** with UUID support

5. **Restart application servers**

### Rollback Plan

If issues occur:

1. **Stop the application**
2. **Restore from backup**
   ```bash
   psql -h <host> -U <user> <database> < backup_before_uuid_migration.sql
   ```
3. **Revert code changes** to use integer user IDs
4. **Restart with old code**

## Testing the Migration

### Manual Testing

1. **Create a test user** before migration
2. **Note their profile data** and job applications
3. **Run migration** in test environment
4. **Verify:**
   - User can log in with same credentials
   - Profile data is intact
   - Job applications are present
   - All relationships work correctly

### Automated Testing

```python
# Run after migration
python -m pytest tests/ -v
```

## Code Changes Summary

### Files Modified

1. **[database_config.py](database_config.py)**
   - User.id: `Integer` → `UUID(as_uuid=True)`
   - All foreign keys updated to `UUID(as_uuid=True)`

2. **[server/auth.py](server/auth.py)**
   - JWT tokens now store UUID as string
   - User ID conversion from string to UUID
   - All user responses return UUID as string

3. **[server/job_queue.py](server/job_queue.py)**
   - JobRequest.user_id: `int` → `str`
   - All job submission functions accept string UUIDs

4. **[server/profile_service.py](server/profile_service.py)**
   - All methods accept user_id as string
   - Automatic UUID conversion via `_convert_user_id()` helper

### API Contract Changes

All endpoints that previously returned/accepted integer user IDs now use UUID strings:

- `POST /api/auth/signup` - returns UUID in response
- `POST /api/auth/login` - returns UUID in response
- JWT tokens contain UUID string in payload
- All authenticated endpoints receive UUID from token

## Security Benefits

### Before (Sequential IDs)

❌ **User Enumeration:** Attackers can guess valid user IDs (1, 2, 3, 4...)
❌ **Predictable:** Easy to estimate number of users
❌ **Information Leakage:** User ID reveals registration order

### After (UUIDs)

✅ **No Enumeration:** UUIDs are random and unpredictable
✅ **Privacy:** Cannot determine user count or registration order
✅ **Collision Resistant:** 128-bit UUIDs have negligible collision probability
✅ **Industry Standard:** UUIDs are widely used for user identification

## Troubleshooting

### Issue: "Invalid user ID format" errors

**Cause:** Frontend sending integer instead of UUID string

**Solution:** Update frontend to handle UUID strings

### Issue: Users cannot log in after migration

**Cause:** Old JWT tokens are still in use

**Solution:** Clear browser storage and cookies, log in again

### Issue: "User not found" errors

**Cause:** Database queries still using integer user IDs

**Solution:** Verify all code is updated to use UUIDs

## FAQ

### Q: Will user emails change?
**A:** No, emails remain exactly the same.

### Q: Will passwords need to be reset?
**A:** No, password hashes are preserved. Users log in with same credentials.

### Q: How long does migration take?
**A:** Depends on database size. For reference:
- 1,000 users: ~5-10 seconds
- 10,000 users: ~30-60 seconds
- 100,000 users: ~5-10 minutes

### Q: Can we run this migration with zero downtime?
**A:** Not recommended. Brief downtime (5-15 minutes) is safer to ensure data consistency.

### Q: What if the migration fails midway?
**A:** The migration uses database transactions. If it fails, changes will be rolled back automatically. Then restore from backup to be safe.

### Q: Are UUIDs slower than integers?
**A:** Slightly (negligible for most workloads). The security benefits far outweigh the minimal performance impact.

## Support

If you encounter issues during migration:

1. Check migration logs for specific errors
2. Verify database connection and credentials
3. Ensure PostgreSQL version supports UUID (9.1+)
4. Contact database administrator if needed

## Verification Checklist

After migration, verify:

- [ ] All existing users can log in
- [ ] User profiles load correctly
- [ ] Job applications are intact
- [ ] New user registration works
- [ ] JWT tokens contain UUIDs
- [ ] API responses return UUID strings
- [ ] No foreign key constraint violations
- [ ] Application performance is acceptable

---

**Migration Created:** 2025-01-16
**Version:** 1.0
**Status:** Ready for staging environment testing
