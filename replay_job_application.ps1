# Job Application Action Replay - Quick Start Script
# This script makes it easy to replay recorded job application sessions

param(
    [string]$SessionsDir = "sessions",
    [string]$SessionId = "",
    [switch]$Slow = $false,
    [switch]$Help = $false
)

# Show help
if ($Help) {
    Write-Host ""
    Write-Host "===========================================" -ForegroundColor Cyan
    Write-Host "   Job Application Action Replay Tool" -ForegroundColor Cyan
    Write-Host "===========================================" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "This tool replays recorded job application sessions in a visible browser."
    Write-Host "You'll see the form filling automatically in real-time!"
    Write-Host ""
    Write-Host "Usage:" -ForegroundColor Yellow
    Write-Host "  .\replay_job_application.ps1                    # Interactive mode (menu)"
    Write-Host "  .\replay_job_application.ps1 -SessionId abc123  # Replay specific session"
    Write-Host "  .\replay_job_application.ps1 -Slow              # Slow mode for better visibility"
    Write-Host "  .\replay_job_application.ps1 -Help              # Show this help"
    Write-Host ""
    Write-Host "Examples:" -ForegroundColor Yellow
    Write-Host "  # Start interactive menu"
    Write-Host "  .\replay_job_application.ps1"
    Write-Host ""
    Write-Host "  # Replay session in slow mode"
    Write-Host "  .\replay_job_application.ps1 -SessionId abc123-def456 -Slow"
    Write-Host ""
    Write-Host "  # Use custom sessions directory"
    Write-Host "  .\replay_job_application.ps1 -SessionsDir C:\my-sessions"
    Write-Host ""
    exit 0
}

# Check if sessions directory exists
if (-not (Test-Path $SessionsDir)) {
    Write-Host ""
    Write-Host "ERROR: Sessions directory not found: $SessionsDir" -ForegroundColor Red
    Write-Host ""
    Write-Host "Did you run a job application first? Try:" -ForegroundColor Yellow
    Write-Host "  python Agents\job_application_agent_test.py --links `"https://job-url.com`" --headful"
    Write-Host ""
    exit 1
}

# Build command
$cmd = "python Agents\action_replay_interface.py"
$cmd += " --sessions-dir `"$SessionsDir`""

if ($SessionId -ne "") {
    $cmd += " --session-id `"$SessionId`""
}

if ($Slow) {
    $cmd += " --slow"
}

# Show banner
Write-Host ""
Write-Host "===========================================" -ForegroundColor Cyan
Write-Host "   Job Application Action Replay" -ForegroundColor Cyan  
Write-Host "===========================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "Starting replay interface..." -ForegroundColor Green
Write-Host "Sessions directory: $SessionsDir" -ForegroundColor Gray
if ($SessionId -ne "") {
    Write-Host "Session ID: $SessionId" -ForegroundColor Gray
}
if ($Slow) {
    Write-Host "Mode: Slow (500ms between actions)" -ForegroundColor Gray
} else {
    Write-Host "Mode: Normal (100ms between actions)" -ForegroundColor Gray
}
Write-Host ""

# Execute
Invoke-Expression $cmd

# Exit
Write-Host ""
Write-Host "Replay interface closed." -ForegroundColor Green
Write-Host ""

