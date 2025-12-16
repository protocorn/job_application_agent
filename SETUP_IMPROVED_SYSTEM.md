# Quick Setup Guide - Improved Resource Management System

## Installation

### 1. Install Dependencies

```powershell
# Install the new dependency (psutil for system monitoring)
pip install psutil>=5.9.0

# Or install all VNC dependencies
pip install -r requirements_vnc.txt
```

### 2. Verify Installation

```powershell
# Check if psutil is installed
python -c "import psutil; print('psutil version:', psutil.__version__)"
```

## Configuration

### No Configuration Required! ✅

The system uses sensible defaults and automatically initializes when the server starts.

### Optional: Adjust Resource Limits

If you need to change the defaults, edit these files:

**Resource Manager** (`server/resource_manager.py`):
```python
def get_resource_manager() -> ResourceManager:
    return ResourceManager(
        max_workers=10,  # ← Change to allow more concurrent jobs
        retry_config=RetryConfig(
            max_attempts=3,  # ← Change retry attempts
            initial_delay=2.0,
            max_delay=30.0
        )
    )
```

**Connection Pool** (`server/vnc_connection_pool.py`):
```python
def get_connection_pool() -> VNCConnectionPool:
    return VNCConnectionPool(
        max_total_connections=20,  # ← Change max VNC sessions
        connection_timeout=3600,   # ← Change session timeout (seconds)
        idle_timeout=300           # ← Change idle timeout (seconds)
    )
```

**Health Monitor** (`server/health_monitor.py`):
```python
def get_health_monitor() -> HealthMonitor:
    return HealthMonitor(
        check_interval=30,         # ← Change monitoring frequency
        cpu_threshold=80.0,        # ← Change CPU alert threshold
        memory_threshold=85.0      # ← Change memory alert threshold
    )
```

## Running the Server

### Start Normally

```powershell
# The system initializes automatically!
python server/api_server.py
```

### Expected Startup Logs

You should see:

```
================================================================================
🚀 Initializing Job Application Agent System
================================================================================
✅ Resource Manager initialized
   Max workers: 10
   Retry config: max_attempts=3
   Circuit breaker threshold: 5
✅ VNC Connection Pool initialized
   Max connections: 20
   Connection timeout: 3600s
   Idle timeout: 300s
✅ Health Monitor initialized
   Check interval: 30s
   CPU threshold: 80.0%
   Memory threshold: 85.0%
✅ Registered error recovery callbacks
================================================================================
✅ System initialization complete
================================================================================
```

## Verification

### 1. Check Health Endpoint

```powershell
# Basic health check
curl http://localhost:5000/health
```

**Expected Response:**
```json
{
  "status": "ok",
  "vnc_enabled": true,
  "timestamp": 1702345678.123,
  "resource_management": {
    "enabled": true,
    "resource_manager": {
      "max_workers": 10,
      "active_threads": 0,
      "completed_threads": 0,
      "circuit_breaker_state": "closed"
    },
    "connection_pool": {
      "total_sessions": 0,
      "total_active_connections": 0,
      "available_capacity": 20
    },
    "health_status": "healthy"
  }
}
```

### 2. Test a Job Application

```powershell
# Submit a test job (replace YOUR_TOKEN with actual token)
$headers = @{
    "Authorization" = "Bearer YOUR_TOKEN"
    "Content-Type" = "application/json"
}

$body = @{
    jobs = @(
        @{
            url = "https://example.com/job1"
            tailorResume = $false
        }
    )
} | ConvertTo-Json

Invoke-RestMethod -Uri "http://localhost:5000/api/vnc/batch-apply-with-preferences" `
    -Method Post `
    -Headers $headers `
    -Body $body
```

### 3. Monitor System Status (Authenticated)

```powershell
# Get detailed system status
$token = "YOUR_TOKEN"
$headers = @{
    "Authorization" = "Bearer $token"
}

Invoke-RestMethod -Uri "http://localhost:5000/api/system/status" `
    -Method Get `
    -Headers $headers | ConvertTo-Json -Depth 10
```

## Monitoring During Operation

### Watch System Health

```powershell
# Monitor health in real-time (updates every 5 seconds)
while ($true) {
    Clear-Host
    Write-Host "=== System Health Monitor ===" -ForegroundColor Cyan
    Write-Host "Time: $(Get-Date)" -ForegroundColor Gray
    Write-Host ""
    
    $health = Invoke-RestMethod -Uri "http://localhost:5000/health"
    $health | ConvertTo-Json -Depth 5 | Write-Host
    
    Start-Sleep -Seconds 5
}
```

### Watch Resource Usage

```powershell
# Monitor resource usage (requires authentication)
$token = "YOUR_TOKEN"
$headers = @{ "Authorization" = "Bearer $token" }

while ($true) {
    Clear-Host
    Write-Host "=== Resource Usage Monitor ===" -ForegroundColor Cyan
    Write-Host "Time: $(Get-Date)" -ForegroundColor Gray
    Write-Host ""
    
    $status = Invoke-RestMethod -Uri "http://localhost:5000/api/system/status" `
        -Headers $headers
    
    # Extract key metrics
    $rm = $status.resource_manager
    $pool = $status.connection_pool
    $health = $status.health.current_metrics
    
    Write-Host "Resource Manager:" -ForegroundColor Yellow
    Write-Host "  Active Threads: $($rm.active_threads)/$($rm.max_workers)"
    Write-Host "  Completed: $($rm.completed_threads)"
    Write-Host "  Circuit Breaker: $($rm.circuit_breaker_state)" -ForegroundColor $(if($rm.circuit_breaker_state -eq "closed"){"Green"}else{"Red"})
    Write-Host ""
    
    Write-Host "Connection Pool:" -ForegroundColor Yellow
    Write-Host "  Active Sessions: $($pool.total_sessions)"
    Write-Host "  Active Connections: $($pool.total_active_connections)"
    Write-Host "  Available: $($pool.available_capacity)"
    Write-Host ""
    
    Write-Host "System Health:" -ForegroundColor Yellow
    Write-Host "  Status: $($status.health.current_status)" -ForegroundColor $(
        switch ($status.health.current_status) {
            "healthy" { "Green" }
            "degraded" { "Yellow" }
            "unhealthy" { "Red" }
            "critical" { "Magenta" }
        }
    )
    Write-Host "  CPU: $([math]::Round($health.cpu_percent, 1))%"
    Write-Host "  Memory: $([math]::Round($health.memory_percent, 1))%"
    Write-Host "  Threads: $($health.active_threads)"
    Write-Host "  Error Rate: $([math]::Round($health.error_rate * 100, 2))%"
    Write-Host ""
    
    Write-Host "Press Ctrl+C to stop" -ForegroundColor Gray
    Start-Sleep -Seconds 5
}
```

## Troubleshooting

### Issue: "psutil not found"

**Solution:**
```powershell
pip install psutil>=5.9.0
```

### Issue: System not initializing

**Check logs for:**
```
❌ Failed to initialize resource management: ...
```

**Common causes:**
- Missing dependencies
- Import errors
- Python version issues (requires Python 3.7+)

**Solution:**
```powershell
# Verify Python version
python --version  # Should be 3.7+

# Reinstall dependencies
pip install -r requirements_vnc.txt --force-reinstall
```

### Issue: "Thread pool at capacity"

**This is normal!** The system is working as designed. Jobs will queue and process sequentially.

**To increase capacity:**
Edit `server/resource_manager.py` and increase `max_workers`.

### Issue: "Connection pool at capacity"

**This is normal!** The system is preventing resource exhaustion.

**Options:**
1. Wait for idle sessions to cleanup (5 minutes)
2. Increase `max_total_connections` in `vnc_connection_pool.py`
3. Manually close unused sessions via API

### Issue: Circuit breaker is OPEN

**Meaning:** System detected repeated failures and is temporarily blocking requests.

**Check:**
```powershell
# Check error logs
# Look for recent errors that triggered the circuit breaker
```

**Solution:**
- Wait for timeout (default: 2 minutes)
- Circuit breaker will automatically try to recover
- If problem persists, check underlying service health

## Performance Tuning

### For High Load (Many Concurrent Jobs)

```python
# server/resource_manager.py
max_workers=20  # Increase from 10
```

```python
# server/vnc_connection_pool.py
max_total_connections=40  # Increase from 20
```

### For Low Memory Systems

```python
# server/resource_manager.py
max_workers=5  # Decrease from 10
```

```python
# server/vnc_connection_pool.py
max_total_connections=10  # Decrease from 20
idle_timeout=180  # Cleanup faster (3 min instead of 5)
```

### For Better Reliability

```python
# server/resource_manager.py
retry_config=RetryConfig(
    max_attempts=5,      # More retries
    initial_delay=3.0,   # Longer initial delay
    max_delay=60.0       # Longer max delay
)
```

## Testing Your Setup

### 1. Basic Functionality Test

```powershell
# Start server
python server/api_server.py

# In another terminal, check health
curl http://localhost:5000/health

# Should return status "ok" with resource_management enabled
```

### 2. Load Test (Simulated)

```powershell
# Submit multiple jobs to test thread pool
for ($i=1; $i -le 5; $i++) {
    Write-Host "Submitting job $i..."
    # Submit job via API (adjust based on your auth)
    Start-Sleep -Seconds 1
}

# Check system status
curl http://localhost:5000/health
```

### 3. Error Recovery Test

```powershell
# Submit an intentionally failing job
# Check logs for:
# - Error reported to health monitor
# - Retry attempts
# - Eventual success or graceful failure
```

## Next Steps

1. ✅ **System is ready!** Start processing jobs
2. 📊 **Monitor**: Use health endpoints to track performance
3. 🔧 **Tune**: Adjust limits based on your system's capacity
4. 📖 **Learn**: Read `FIXES_AND_IMPROVEMENTS.md` for details

## Quick Reference

### Key Files

- `server/resource_manager.py` - Thread pool and retry logic
- `server/vnc_connection_pool.py` - VNC session management
- `server/health_monitor.py` - System monitoring
- `server/system_initializer.py` - Initialization and shutdown

### Key Endpoints

- `GET /health` - Basic health check
- `GET /api/system/status` - Detailed status (auth required)
- `POST /api/vnc/batch-apply-with-preferences` - Submit jobs

### Key Metrics

- **Max Workers**: 10 concurrent operations
- **Max Connections**: 20 VNC sessions
- **Retry Attempts**: 3 per operation
- **Circuit Breaker**: 5 failures trigger open
- **Session Timeout**: 1 hour
- **Idle Timeout**: 5 minutes

---

**Need Help?** Check `FIXES_AND_IMPROVEMENTS.md` for detailed documentation.

**Last Updated:** December 15, 2024
