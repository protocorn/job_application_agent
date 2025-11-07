# Cleanup Script - Remove Sensitive Data from Git
# Run this to clean up your repository before publishing

Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "GIT CLEANUP - REMOVE SENSITIVE DATA" -ForegroundColor Cyan
Write-Host "============================================================`n" -ForegroundColor Cyan

Write-Host "⚠️  WARNING: This script will modify your Git history!" -ForegroundColor Yellow
Write-Host "⚠️  Make sure you have a backup before proceeding!" -ForegroundColor Yellow
Write-Host "`nPress Enter to continue or Ctrl+C to cancel..."
$null = Read-Host

# Step 1: Remove .env from tracking
Write-Host "`n[Step 1/5] Removing .env from Git tracking..." -ForegroundColor Cyan
try {
    git rm --cached .env 2>$null
    Write-Host "✅ .env removed from tracking" -ForegroundColor Green
} catch {
    Write-Host "ℹ️  .env was not tracked" -ForegroundColor Gray
}

# Step 2: Remove token.json files
Write-Host "`n[Step 2/5] Removing token.json files..." -ForegroundColor Cyan
try {
    git rm --cached Agents/token.json 2>$null
    git rm --cached server/token.json 2>$null
    Write-Host "✅ token.json files removed from tracking" -ForegroundColor Green
} catch {
    Write-Host "ℹ️  token.json files were not tracked" -ForegroundColor Gray
}

# Step 3: Remove user data directories
Write-Host "`n[Step 3/5] Removing user data directories..." -ForegroundColor Cyan
$userDataDirs = @("Resumes/*", "Cache/*", "server/sessions/*", "logs/*")
foreach ($dir in $userDataDirs) {
    try {
        git rm -r --cached $dir 2>$null
        Write-Host "✅ Removed $dir from tracking" -ForegroundColor Green
    } catch {
        Write-Host "ℹ️  $dir was not tracked" -ForegroundColor Gray
    }
}

# Step 4: Commit the removals
Write-Host "`n[Step 4/5] Committing changes..." -ForegroundColor Cyan
git add .gitignore
git commit -m "security: Remove sensitive files from tracking and update .gitignore"
Write-Host "✅ Changes committed" -ForegroundColor Green

# Step 5: Clean Git history (OPTIONAL - only if .env was committed before)
Write-Host "`n[Step 5/5] Checking if history cleanup is needed..." -ForegroundColor Cyan
$envHistory = git log --all --full-history -- .env 2>$null

if ($envHistory) {
    Write-Host "⚠️  .env was found in Git history!" -ForegroundColor Yellow
    Write-Host "`nDo you want to remove it from history? (Y/N)" -ForegroundColor Yellow
    Write-Host "WARNING: This will rewrite Git history!" -ForegroundColor Red
    $response = Read-Host

    if ($response -eq 'Y' -or $response -eq 'y') {
        Write-Host "`nRemoving .env from Git history..." -ForegroundColor Cyan
        Write-Host "This may take a few minutes..." -ForegroundColor Yellow
        
        # Use filter-branch to remove .env
        git filter-branch --force --index-filter `
            "git rm --cached --ignore-unmatch .env" `
            --prune-empty --tag-name-filter cat -- --all
        
        Write-Host "✅ .env removed from history" -ForegroundColor Green
        
        # Cleanup
        Write-Host "`nCleaning up..." -ForegroundColor Cyan
        git reflog expire --expire=now --all
        git gc --prune=now --aggressive
        
        Write-Host "✅ Cleanup complete" -ForegroundColor Green
        
        Write-Host "`n⚠️  IMPORTANT: You MUST rotate ALL API keys now!" -ForegroundColor Red
        Write-Host "The old keys were exposed in Git history." -ForegroundColor Red
        Write-Host "`nKeys to rotate:" -ForegroundColor Yellow
        Write-Host "  1. Google API Key (Gemini)" -ForegroundColor White
        Write-Host "  2. Google OAuth Client ID & Secret" -ForegroundColor White
        Write-Host "  3. Encryption Key" -ForegroundColor White
        Write-Host "  4. JWT Secret Key" -ForegroundColor White
        Write-Host "  5. TheMuse API Key" -ForegroundColor White
        Write-Host "  6. TheirStack API Key" -ForegroundColor White
        
        Write-Host "`n⚠️  If you push to GitHub, use: git push origin --force --all" -ForegroundColor Yellow
        Write-Host "Only do this if you're the sole contributor!" -ForegroundColor Yellow
    } else {
        Write-Host "⚠️  Skipped history cleanup" -ForegroundColor Yellow
        Write-Host "Your .env is still in Git history!" -ForegroundColor Yellow
    }
} else {
    Write-Host "✅ .env was never in Git history" -ForegroundColor Green
}

Write-Host "`n============================================================" -ForegroundColor Cyan
Write-Host "CLEANUP COMPLETE!" -ForegroundColor Green
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "`nNext steps:" -ForegroundColor Cyan
Write-Host "1. Verify with: .\security_check_before_commit.ps1" -ForegroundColor White
Write-Host "2. If history was cleaned, rotate ALL API keys" -ForegroundColor White
Write-Host "3. Update your .env with new keys" -ForegroundColor White
Write-Host "4. Test the application still works" -ForegroundColor White
Write-Host "5. Make your final commit" -ForegroundColor White
Write-Host "6. Push to GitHub: git push origin main" -ForegroundColor White

