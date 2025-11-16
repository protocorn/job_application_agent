# IP-Based Rate Limiting - Security Enhancement

## Overview

This security enhancement adds IP-based rate limiting to prevent brute force attacks across multiple user accounts. Previously, attackers could try 5 passwords for user1@example.com, then 5 for user2@example.com, etc., effectively getting unlimited login attempts.

## Security Issue Fixed

### Before (Account-Only Rate Limiting)

❌ **User Enumeration Vulnerability:**
- Attacker tries 5 passwords for `user1@example.com` → Account locked
- Attacker tries 5 passwords for `user2@example.com` → Account locked
- Attacker tries 5 passwords for `user3@example.com` → Account locked
- **Result:** Attacker gets 5 attempts per username = effectively unlimited attempts

### After (IP + Account Rate Limiting)

✅ **IP-Based Protection:**
- Attacker tries 10 passwords across ANY usernames from same IP → **IP blocked for 5 minutes**
- Attacker tries 15 passwords across ANY usernames from same IP → **IP blocked for 1 hour**
- Individual accounts still lock after 5 failed attempts

## How It Works

### Two-Layer Protection

#### Layer 1: IP-Based Rate Limiting (New)

**Short-term limit (Fast Response):**
- **10 failed login attempts** from same IP within **5 minutes**
- Blocks: Further attempts for the remainder of the 5-minute window
- Message: "Too many login attempts. Please wait a few minutes before trying again"

**Long-term limit (Persistent Protection):**
- **15 failed login attempts** from same IP within **1 hour**
- Blocks: IP for **1 hour** from the time threshold is reached
- Message: "IP temporarily blocked for X minutes due to excessive failed login attempts"

#### Layer 2: Account-Based Rate Limiting (Existing)

- **5 failed login attempts** per account
- Locks: Account for **15 minutes**
- Message: "Account temporarily locked due to too many failed login attempts"

### Rate Limit Flow

```
User attempts login from IP: 203.0.113.45
    ↓
Check IP rate limit (checked FIRST)
    ↓
    ├─ IP blocked? → Return 429 with error message
    ↓
Check account rate limit
    ↓
    ├─ Account locked? → Return 429 with error message
    ↓
Authenticate credentials
    ↓
    ├─ Success? → Clear IP & account counters, return token
    ├─ Failure? → Increment IP & account counters, return 401
```

## Configuration

Located in [server/security_manager.py](server/security_manager.py):

```python
SECURITY_CONFIG = {
    # Account-based limits (per email/username)
    'max_login_attempts': 5,              # Max attempts before account lock
    'lockout_duration': 900,              # 15 minutes

    # IP-based limits (across all accounts)
    'max_login_attempts_per_ip_short': 10,  # Max attempts in short window
    'ip_short_window': 300,                  # 5 minutes
    'max_login_attempts_per_ip': 15,        # Max attempts in long window
    'ip_lockout_duration': 3600,            # 1 hour
}
```

### Adjusting Limits

You can tune these values based on your security requirements:

**More Restrictive (Higher Security):**
```python
'max_login_attempts_per_ip_short': 5,   # Block after 5 attempts in 5 min
'max_login_attempts_per_ip': 10,        # Block for 1 hour after 10 attempts
'ip_lockout_duration': 7200,            # 2 hour IP lockout
```

**More Permissive (Better UX):**
```python
'max_login_attempts_per_ip_short': 15,  # Block after 15 attempts in 5 min
'max_login_attempts_per_ip': 25,        # Block for 1 hour after 25 attempts
'ip_lockout_duration': 1800,            # 30 minute IP lockout
```

## Implementation Details

### Files Modified

1. **[server/security_manager.py](server/security_manager.py)**
   - Added `check_ip_login_attempts()` method
   - Added `_record_ip_login_attempt()` method
   - Updated `record_login_attempt()` to accept `ip_address` parameter
   - Added configuration for IP-based limits

2. **[server/api_server.py](server/api_server.py)**
   - Updated `/api/auth/login` endpoint to check IP limits
   - Added IP address tracking for login attempts
   - Enhanced error responses with remaining attempts info

### Redis Keys Used

IP-based rate limiting uses Redis to track attempts:

```
ip_login_attempts:long:<ip_address>  # Long-term counter (1 hour)
ip_login_attempts:short:<ip_address> # Short-term counter (5 minutes)
```

**Example data structure:**
```json
{
  "count": 3,
  "first_attempt": "2025-01-16T10:30:00",
  "last_attempt": "2025-01-16T10:35:00",
  "locked_until": null  // or timestamp if locked
}
```

## API Response Changes

### Login Success
```json
{
  "success": true,
  "message": "Authentication successful",
  "user": { "id": "...", "email": "..." },
  "token": "jwt.token.here"
}
```

### Login Failure (Account Locked)
```json
{
  "success": false,
  "error": "Account temporarily locked due to too many failed login attempts. Please try again in 15 minutes.",
  "remaining_attempts": 0
}
```

### Login Failure (IP Blocked - Short Term)
**HTTP Status:** 429 Too Many Requests
```json
{
  "success": false,
  "error": "Too many login attempts. Please wait a few minutes before trying again"
}
```

### Login Failure (IP Blocked - Long Term)
**HTTP Status:** 429 Too Many Requests
```json
{
  "success": false,
  "error": "IP temporarily blocked for 45 minutes due to excessive failed login attempts"
}
```

### Login Failure (Invalid Credentials)
**HTTP Status:** 401 Unauthorized
```json
{
  "success": false,
  "error": "Invalid email or password",
  "remaining_attempts": 3,          // Account attempts remaining
  "ip_remaining_attempts": 12       // IP attempts remaining
}
```

## Testing

### Manual Testing

**Test 1: Account Lockout (Existing Functionality)**
```bash
# Try 5 failed logins for same account
for i in {1..5}; do
  curl -X POST http://localhost:5000/api/auth/login \
    -H "Content-Type: application/json" \
    -d '{"email":"test@example.com","password":"wrong"}'
done

# Expected: Account locked after 5 attempts
```

**Test 2: IP Short-Term Lockout (New)**
```bash
# Try 10 failed logins across different accounts from same IP
for i in {1..10}; do
  curl -X POST http://localhost:5000/api/auth/login \
    -H "Content-Type: application/json" \
    -d "{\"email\":\"user$i@example.com\",\"password\":\"wrong\"}"
done

# Expected: IP blocked after 10 attempts (5 minute cooldown)
```

**Test 3: IP Long-Term Lockout (New)**
```bash
# Try 15 failed logins across different accounts
for i in {1..15}; do
  curl -X POST http://localhost:5000/api/auth/login \
    -H "Content-Type: application/json" \
    -d "{\"email\":\"user$i@example.com\",\"password\":\"wrong\"}"
done

# Expected: IP blocked after 15 attempts (1 hour cooldown)
```

**Test 4: Successful Login Clears Counters**
```bash
# Make 3 failed attempts
for i in {1..3}; do
  curl -X POST http://localhost:5000/api/auth/login \
    -H "Content-Type: application/json" \
    -d '{"email":"test@example.com","password":"wrong"}'
done

# Then succeed
curl -X POST http://localhost:5000/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"test@example.com","password":"correct_password"}'

# Expected: Counters reset, can attempt again
```

### Automated Testing

```python
import requests
import pytest

def test_ip_rate_limiting():
    """Test that IP gets blocked after too many attempts"""
    url = "http://localhost:5000/api/auth/login"

    # Make 10 failed attempts from same IP
    for i in range(10):
        response = requests.post(url, json={
            "email": f"user{i}@example.com",
            "password": "wrong"
        })

    # 11th attempt should be blocked
    response = requests.post(url, json={
        "email": "another@example.com",
        "password": "wrong"
    })

    assert response.status_code == 429
    assert "Too many login attempts" in response.json()['error']

def test_account_rate_limiting():
    """Test that account gets locked after 5 failures"""
    url = "http://localhost:5000/api/auth/login"

    # Make 5 failed attempts for same account
    for i in range(5):
        response = requests.post(url, json={
            "email": "test@example.com",
            "password": "wrong"
        })

    # 6th attempt should be blocked
    response = requests.post(url, json={
        "email": "test@example.com",
        "password": "wrong"
    })

    assert response.status_code == 429
    assert "Account temporarily locked" in response.json()['error']
```

## Monitoring & Logging

### Security Events Logged

All login attempts are logged to [server/logs/security.log](server/logs/security.log):

**Successful Login:**
```json
{
  "timestamp": "2025-01-16T10:30:00",
  "event_type": "login_success",
  "user_id": 123,
  "ip_address": "203.0.113.45",
  "details": {
    "ip_attempts_cleared": true
  }
}
```

**Failed Login:**
```json
{
  "timestamp": "2025-01-16T10:30:00",
  "event_type": "login_failure",
  "details": {
    "identifier": "user@example.com",
    "attempts": 3,
    "ip_address": "203.0.113.45"
  }
}
```

**IP Lockout:**
```json
{
  "timestamp": "2025-01-16T10:30:00",
  "event_type": "account_locked",
  "details": {
    "type": "ip_lockout",
    "ip_address": "203.0.113.45",
    "attempts": 15,
    "duration_seconds": 3600
  }
}
```

### Redis Monitoring

Check current IP attempt counts:
```bash
redis-cli GET "ip_login_attempts:long:203.0.113.45"
redis-cli GET "ip_login_attempts:short:203.0.113.45"
```

Clear IP lockout manually (admin use):
```bash
redis-cli DEL "ip_login_attempts:long:203.0.113.45"
redis-cli DEL "ip_login_attempts:short:203.0.113.45"
```

## Production Considerations

### Behind Proxies/Load Balancers

If your app is behind a proxy (Nginx, CloudFlare, AWS ELB), make sure to get the real client IP:

```python
# In api_server.py, update IP extraction:
def get_client_ip():
    """Get real client IP address"""
    # Check X-Forwarded-For header (added by proxies)
    if request.headers.get('X-Forwarded-For'):
        return request.headers['X-Forwarded-For'].split(',')[0].strip()
    # Check X-Real-IP header
    if request.headers.get('X-Real-IP'):
        return request.headers['X-Real-IP']
    # Fall back to remote_addr
    return request.remote_addr

# Then in login endpoint:
client_ip = get_client_ip()
```

### IPv6 Support

The system automatically supports IPv6 addresses. No changes needed.

### Shared IPs (Office Networks, NAT)

**Issue:** Multiple legitimate users behind same IP (office network, NAT) might trigger IP lockout.

**Solutions:**

1. **Increase IP limits** for better tolerance:
   ```python
   'max_login_attempts_per_ip': 50  # Higher limit for shared IPs
   ```

2. **Whitelist known IPs:**
   ```python
   WHITELISTED_IPS = ['203.0.113.0/24']  # Office network

   if client_ip in WHITELISTED_IPS:
       # Skip IP rate limiting
       pass
   ```

3. **Use geolocation** to distinguish users within same network

### Rate Limit Bypass (VPN/Proxy)

Attackers can bypass IP limits using VPNs/proxies. Additional protections:

1. **CAPTCHA** after multiple failures
2. **Device fingerprinting**
3. **Behavioral analysis**
4. **Email verification** for password resets
5. **2FA (Two-Factor Authentication)**

## Security Benefits

### Attack Scenarios Prevented

✅ **Credential Stuffing**
- Attackers with stolen username/password lists are stopped after 15 attempts

✅ **Brute Force Across Accounts**
- Can't enumerate users by trying different accounts

✅ **Distributed Attacks Mitigated**
- Even with multiple IPs, attackers significantly slowed down

✅ **Account Enumeration Harder**
- Can't probe which accounts exist by testing many emails

### Attack Scenarios NOT Fully Prevented

⚠️ **Distributed Brute Force (Botnets)**
- Attackers using thousands of different IPs can still attack
- **Mitigation:** Add CAPTCHA, require email verification

⚠️ **Low-and-Slow Attacks**
- Attackers staying just below rate limits
- **Mitigation:** Monitor security logs for patterns

## FAQ

### Q: Will legitimate users get blocked?
**A:** Very unlikely. Limits are set high enough (10-15 attempts) that normal users won't hit them. Even if someone forgets their password, they have several attempts before lockout.

### Q: What if a user is blocked unfairly?
**A:**
- **Account lockout**: Expires automatically after 15 minutes
- **IP lockout**: Expires after 5 minutes (short) or 1 hour (long)
- **Admin override**: Can manually clear Redis keys

### Q: Does this protect against DDoS?
**A:** Partially. This prevents login-specific DDoS, but you should also have:
- Web application firewall (WAF)
- DDoS protection (CloudFlare, AWS Shield)
- General rate limiting on all endpoints

### Q: How does this work with password reset?
**A:** Password reset is a separate endpoint. You should apply similar IP rate limiting there too.

### Q: Can I disable IP rate limiting for specific users?
**A:** Yes, modify `check_ip_login_attempts()` to check a whitelist:
```python
WHITELISTED_USERS = ['admin@example.com']
if identifier in WHITELISTED_USERS:
    return True, 999, ""
```

### Q: What about IPv6?
**A:** Fully supported. Redis stores IPv6 addresses same as IPv4.

## Rollback Plan

If issues occur in production:

1. **Disable IP rate limiting:**
   ```python
   # In api_server.py, comment out:
   # ip_allowed, ip_remaining, ip_reason = security_manager.check_ip_login_attempts(client_ip)
   ```

2. **Increase limits temporarily:**
   ```python
   'max_login_attempts_per_ip': 1000  # Effectively disable
   ```

3. **Clear all IP locks:**
   ```bash
   redis-cli KEYS "ip_login_attempts:*" | xargs redis-cli DEL
   ```

---

**Implementation Date:** 2025-01-16
**Version:** 1.0
**Status:** Production Ready
