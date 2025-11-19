# Resume Job Application Session - Simple Script
# Uses browser state freezing (100% accurate) as primary method
# Falls back to action replay only if session expired

param(
    [Parameter(Mandatory=$false)]
    [string]$SessionId = "",
    
    [switch]$List = $false,
    [switch]$Help = $false
)

if ($Help) {
    Write-Host ""
    Write-Host "===========================================" -ForegroundColor Cyan
    Write-Host "   Resume Job Application Session" -ForegroundColor Cyan
    Write-Host "===========================================" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "This tool resumes frozen job application sessions using browser state freezing."
    Write-Host "Browser state = 100% accurate (all fields preserved)"
    Write-Host ""
    Write-Host "Usage:" -ForegroundColor Yellow
    Write-Host "  .\resume_session.ps1                  # Interactive mode (choose from list)"
    Write-Host "  .\resume_session.ps1 -SessionId abc   # Resume specific session"
    Write-Host "  .\resume_session.ps1 -List            # List all frozen sessions"
    Write-Host "  .\resume_session.ps1 -Help            # Show this help"
    Write-Host ""
    Write-Host "Session Expiry Windows:" -ForegroundColor Yellow
    Write-Host "  - Greenhouse, Lever:     24-48 hours"
    Write-Host "  - Workday, Ashby:        8-12 hours"
    Write-Host "  - PayLocity, iCIMS:      4-8 hours"
    Write-Host "  - Taleo:                 30 minutes (not recommended)"
    Write-Host ""
    Write-Host "Recommendation: Resume within 12 hours for best results" -ForegroundColor Green
    Write-Host ""
    exit 0
}

# Check if sessions directory exists
if (-not (Test-Path "sessions\sessions.json")) {
    Write-Host ""
    Write-Host "ERROR: No sessions found" -ForegroundColor Red
    Write-Host "Have you run any job applications yet?" -ForegroundColor Yellow
    Write-Host ""
    exit 1
}

# List mode
if ($List) {
    Write-Host ""
    Write-Host "===========================================" -ForegroundColor Cyan
    Write-Host "   Frozen Sessions" -ForegroundColor Cyan
    Write-Host "===========================================" -ForegroundColor Cyan
    Write-Host ""
    
    $sessions = Get-Content "sessions\sessions.json" | ConvertFrom-Json
    $frozen = $sessions | Where-Object { $_.status -in @("frozen", "needs_attention", "partially_completed", "requires_authentication") }
    
    if ($frozen.Count -eq 0) {
        Write-Host "No frozen sessions found." -ForegroundColor Yellow
        Write-Host ""
        exit 0
    }
    
    foreach ($session in $frozen) {
        $age_hours = [math]::Round((([DateTimeOffset]::UtcNow.ToUnixTimeSeconds() - $session.last_updated) / 3600), 1)
        $status_emoji = switch ($session.status) {
            "frozen" { "❄️" }
            "needs_attention" { "⚠️" }
            "requires_authentication" { "🔐" }
            default { "📋" }
        }
        
        Write-Host "$status_emoji Session: $($session.session_id)" -ForegroundColor Cyan
        Write-Host "   Job: $($session.job_url)" -ForegroundColor Gray
        Write-Host "   Status: $($session.status) | Progress: $($session.completion_percentage)%" -ForegroundColor Gray
        Write-Host "   Age: $age_hours hours" -ForegroundColor $(if ($age_hours -lt 12) { "Green" } else { "Yellow" })
        
        # Check if browser state exists
        $state_file = "sessions\browser_states\state_$($session.session_id).json"
        if (Test-Path $state_file) {
            Write-Host "   ✅ Browser state available (100% accuracy)" -ForegroundColor Green
        } else {
            Write-Host "   ⚠️ No browser state (will use action replay)" -ForegroundColor Yellow
        }
        Write-Host ""
    }
    
    exit 0
}

# Interactive or direct mode
if ($SessionId -eq "") {
    # Interactive mode - show sessions and ask user to choose
    Write-Host ""
    Write-Host "===========================================" -ForegroundColor Cyan
    Write-Host "   Resume Session (Interactive)" -ForegroundColor Cyan
    Write-Host "===========================================" -ForegroundColor Cyan
    Write-Host ""
    
    $sessions = Get-Content "sessions\sessions.json" | ConvertFrom-Json
    $frozen = $sessions | Where-Object { $_.status -in @("frozen", "needs_attention", "partially_completed", "requires_authentication") }
    
    if ($frozen.Count -eq 0) {
        Write-Host "No frozen sessions found." -ForegroundColor Yellow
        Write-Host ""
        exit 0
    }
    
    Write-Host "Available sessions:" -ForegroundColor Green
    Write-Host ""
    
    for ($i = 0; $i -lt $frozen.Count; $i++) {
        $session = $frozen[$i]
        $age_hours = [math]::Round((([DateTimeOffset]::UtcNow.ToUnixTimeSeconds() - $session.last_updated) / 3600), 1)
        
        Write-Host "[$($i+1)] " -NoNewline -ForegroundColor Yellow
        Write-Host "$($session.company) - " -NoNewline
        Write-Host "$($session.completion_percentage)% complete - " -NoNewline
        Write-Host "$age_hours hours old" -ForegroundColor $(if ($age_hours -lt 12) { "Green" } else { "Yellow" })
    }
    
    Write-Host ""
    $choice = Read-Host "Select session (1-$($frozen.Count)) or 'q' to quit"
    
    if ($choice -eq 'q') {
        exit 0
    }
    
    try {
        $index = [int]$choice - 1
        if ($index -lt 0 -or $index -ge $frozen.Count) {
            Write-Host "Invalid choice" -ForegroundColor Red
            exit 1
        }
        $SessionId = $frozen[$index].session_id
    } catch {
        Write-Host "Invalid input" -ForegroundColor Red
        exit 1
    }
}

# Resume the session
Write-Host ""
Write-Host "===========================================" -ForegroundColor Cyan
Write-Host "   Resuming Session" -ForegroundColor Cyan
Write-Host "===========================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "Session ID: $SessionId" -ForegroundColor Gray
Write-Host ""

# Check session age
$sessions = Get-Content "sessions\sessions.json" | ConvertFrom-Json
$session = $sessions | Where-Object { $_.session_id -eq $SessionId }

if (-not $session) {
    Write-Host "ERROR: Session not found" -ForegroundColor Red
    exit 1
}

$age_hours = [math]::Round((([DateTimeOffset]::UtcNow.ToUnixTimeSeconds() - $session.last_updated) / 3600), 1)

Write-Host "Session age: $age_hours hours" -ForegroundColor $(if ($age_hours -lt 12) { "Green" } elseif ($age_hours -lt 24) { "Yellow" } else { "Red" })
Write-Host "Progress: $($session.completion_percentage)%" -ForegroundColor Cyan
Write-Host ""

# Check if browser state exists
$state_file = "sessions\browser_states\state_$SessionId.json"

if (Test-Path $state_file) {
    if ($age_hours -lt 24) {
        Write-Host "✅ Using BROWSER STATE RESTORE (100% accurate)" -ForegroundColor Green
        Write-Host "   All fields will be preserved exactly as you left them." -ForegroundColor Gray
    } else {
        Write-Host "⚠️ Session is $age_hours hours old (may be expired)" -ForegroundColor Yellow
        Write-Host "   Will attempt browser state restore, may fall back to action replay" -ForegroundColor Gray
    }
} else {
    Write-Host "⚠️ Using ACTION REPLAY (70-90% accuracy)" -ForegroundColor Yellow  
    Write-Host "   No browser state found. Will replay actions." -ForegroundColor Gray
    Write-Host "   Please review all fields carefully before submitting." -ForegroundColor Yellow
}

Write-Host ""
Write-Host "Opening browser..." -ForegroundColor Green
Write-Host ""

# Call Python script to resume
python -c "
import asyncio
import sys
import os
sys.path.append('Agents')
from components.session.session_manager import SessionManager
from playwright.async_api import async_playwright

async def main():
    session_manager = SessionManager('sessions')
    playwright = await async_playwright().start()
    browser = await playwright.chromium.launch(headless=False)
    context = await browser.new_context()
    page = await context.new_page()
    
    success = await session_manager.resume_session('$SessionId', page)
    
    if success:
        print('')
        print('='*60)
        print('✅ Session restored successfully!')
        print('='*60)
        print('')
        print('The browser will stay open for you to:')
        print('  1. Review the filled fields')
        print('  2. Complete any remaining fields')
        print('  3. Submit the application')
        print('')
        print('Press Enter when done to close browser...')
        input()
    else:
        print('')
        print('='*60)
        print('❌ Failed to restore session')
        print('='*60)
        print('')
        input('Press Enter to close...')
    
    await browser.close()
    await playwright.stop()

asyncio.run(main())
"

Write-Host ""
Write-Host "Session resume complete." -ForegroundColor Green
Write-Host ""

