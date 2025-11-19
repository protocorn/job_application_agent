# Job Application Recorder - Quick Start Script
# This script makes it easy to run and record job applications

param(
    [string]$JobUrl = "",
    [string]$JobUrls = "",
    [string]$UserId = "",
    [switch]$Headless = $false,
    [switch]$Debug = $false,
    [switch]$KeepOpen = $false,
    [switch]$Help = $false
)

# Show help
if ($Help) {
    Write-Host ""
    Write-Host "=============================================" -ForegroundColor Cyan
    Write-Host "   Job Application Recorder Tool" -ForegroundColor Cyan
    Write-Host "=============================================" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "This tool automatically fills job application forms and records all actions."
    Write-Host "Later, you can replay these recordings to see the form fill itself!"
    Write-Host ""
    Write-Host "Usage:" -ForegroundColor Yellow
    Write-Host "  .\record_job_application.ps1 -JobUrl `"https://job-url.com`""
    Write-Host "  .\record_job_application.ps1 -JobUrls `"url1,url2,url3`"  # Multiple jobs"
    Write-Host "  .\record_job_application.ps1 -JobUrl `"url`" -Debug       # Debug mode"
    Write-Host "  .\record_job_application.ps1 -Help                        # Show this help"
    Write-Host ""
    Write-Host "Parameters:" -ForegroundColor Yellow
    Write-Host "  -JobUrl       Single job URL to process"
    Write-Host "  -JobUrls      Multiple job URLs (comma-separated)"
    Write-Host "  -UserId       User ID to load profile from database"
    Write-Host "  -Headless     Run browser in headless mode (no visible window)"
    Write-Host "  -Debug        Debug mode (wait for Enter during human intervention)"
    Write-Host "  -KeepOpen     Keep browser open after completion"
    Write-Host "  -Help         Show this help message"
    Write-Host ""
    Write-Host "Examples:" -ForegroundColor Yellow
    Write-Host "  # Record single job application (visible browser)"
    Write-Host "  .\record_job_application.ps1 -JobUrl `"https://greenhouse-job.com`""
    Write-Host ""
    Write-Host "  # Record multiple jobs"
    Write-Host "  .\record_job_application.ps1 -JobUrls `"url1,url2,url3`""
    Write-Host ""
    Write-Host "  # Record with specific user profile"
    Write-Host "  .\record_job_application.ps1 -JobUrl `"url`" -UserId `"user123`""
    Write-Host ""
    Write-Host "  # Debug mode (pause at human interventions)"
    Write-Host "  .\record_job_application.ps1 -JobUrl `"url`" -Debug"
    Write-Host ""
    Write-Host "After Recording:" -ForegroundColor Yellow
    Write-Host "  - Actions are automatically saved to 'sessions/' directory"
    Write-Host "  - Use .\replay_job_application.ps1 to replay them"
    Write-Host ""
    exit 0
}

# Validate input
if ($JobUrl -eq "" -and $JobUrls -eq "") {
    Write-Host ""
    Write-Host "ERROR: You must provide either -JobUrl or -JobUrls" -ForegroundColor Red
    Write-Host ""
    Write-Host "Examples:" -ForegroundColor Yellow
    Write-Host "  .\record_job_application.ps1 -JobUrl `"https://greenhouse-job.com`""
    Write-Host "  .\record_job_application.ps1 -JobUrls `"url1,url2,url3`""
    Write-Host ""
    Write-Host "Run with -Help for more information." -ForegroundColor Gray
    Write-Host ""
    exit 1
}

# Determine URLs to use
$urls = ""
if ($JobUrls -ne "") {
    $urls = $JobUrls
} else {
    $urls = $JobUrl
}

# Count URLs
$urlCount = ($urls -split ",").Count

# Build command
$cmd = "python Agents\job_application_agent_test.py --links `"$urls`""

if (-not $Headless) {
    $cmd += " --headful"
}

if ($Debug) {
    $cmd += " --debug"
}

if ($KeepOpen) {
    $cmd += " --keep-open"
}

if ($UserId -ne "") {
    $cmd += " --user-id `"$UserId`""
}

# Show banner
Write-Host ""
Write-Host "=============================================" -ForegroundColor Cyan
Write-Host "   Job Application Recorder" -ForegroundColor Cyan
Write-Host "=============================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "Starting job application agent..." -ForegroundColor Green
Write-Host "Jobs to process: $urlCount" -ForegroundColor Gray
Write-Host "Browser mode: $(if ($Headless) { 'Headless (no window)' } else { 'Headful (visible)' })" -ForegroundColor Gray
if ($UserId -ne "") {
    Write-Host "User ID: $UserId" -ForegroundColor Gray
}
if ($Debug) {
    Write-Host "Debug mode: Enabled" -ForegroundColor Yellow
}
if ($KeepOpen) {
    Write-Host "Keep open: Enabled" -ForegroundColor Yellow
}
Write-Host ""
Write-Host "Recording to: sessions/ directory" -ForegroundColor Green
Write-Host ""

# Execute
Invoke-Expression $cmd

# Show completion message
Write-Host ""
Write-Host "=============================================" -ForegroundColor Cyan
Write-Host "   Recording Complete!" -ForegroundColor Cyan
Write-Host "=============================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "Actions have been recorded to the sessions/ directory." -ForegroundColor Green
Write-Host ""
Write-Host "Next steps:" -ForegroundColor Yellow
Write-Host "  1. View recorded sessions:"
Write-Host "     .\replay_job_application.ps1"
Write-Host ""
Write-Host "  2. Replay a specific session to see the form fill automatically:"
Write-Host "     .\replay_job_application.ps1 -SessionId <session-id>"
Write-Host ""
Write-Host "  3. View session files:"
Write-Host "     dir sessions\action_logs"
Write-Host ""

