# Quick Resume Script - Opens frozen browser state
# This is the simple version that just works!

param(
    [string]$SessionId = "",
    [switch]$Help = $false
)

if ($Help) {
    Write-Host ""
    Write-Host "===========================================" -ForegroundColor Cyan
    Write-Host "   Resume Frozen Browser State" -ForegroundColor Cyan
    Write-Host "===========================================" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "Resume a frozen job application with 100% accuracy!" -ForegroundColor Green
    Write-Host ""
    Write-Host "Usage:" -ForegroundColor Yellow
    Write-Host "  .\resume.ps1                  # Interactive (choose from list)"
    Write-Host "  .\resume.ps1 -SessionId abc   # Resume specific session"
    Write-Host "  .\resume.ps1 -Help            # Show this help"
    Write-Host ""
    Write-Host "What gets restored:" -ForegroundColor Yellow
    Write-Host "  ✅ All cookies (authentication)"
    Write-Host "  ✅ localStorage (persistent data)"
    Write-Host "  ✅ sessionStorage (temporary data)"
    Write-Host "  ✅ All filled form fields"
    Write-Host "  ✅ Exact page location"
    Write-Host ""
    Write-Host "Result: Browser opens with EXACT same state!" -ForegroundColor Green
    Write-Host ""
    exit 0
}

Write-Host ""
Write-Host "===========================================" -ForegroundColor Cyan
Write-Host "   Resume Frozen Browser State" -ForegroundColor Cyan
Write-Host "===========================================" -ForegroundColor Cyan
Write-Host ""

# Build command
$cmd = "python Agents\resume_browser_state.py"

if ($SessionId -ne "") {
    $cmd += " --session-id `"$SessionId`""
}

# Execute
Invoke-Expression $cmd

Write-Host ""
Write-Host "Done!" -ForegroundColor Green
Write-Host ""

