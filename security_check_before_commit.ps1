# Security Check Script - Run Before Committing to Git
# Checks for sensitive data, API keys, and security issues

Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "SECURITY CHECK BEFORE COMMIT" -ForegroundColor Cyan
Write-Host "============================================================`n" -ForegroundColor Cyan

$issuesFound = 0

# 1. Check if .env is tracked
Write-Host "[1/8] Checking if .env is tracked in Git..." -ForegroundColor Yellow
$envTracked = git ls-files | Select-String -Pattern "^\.env$"
if ($envTracked) {
    Write-Host "❌ CRITICAL: .env is tracked in Git!" -ForegroundColor Red
    Write-Host "   Run: git rm --cached .env" -ForegroundColor Red
    $issuesFound++
} else {
    Write-Host "✅ .env is not tracked" -ForegroundColor Green
}

# 2. Check if .env was ever committed
Write-Host "`n[2/8] Checking if .env was ever committed..." -ForegroundColor Yellow
$envHistory = git log --all --full-history -- .env 2>$null
if ($envHistory) {
    Write-Host "❌ CRITICAL: .env was committed in the past!" -ForegroundColor Red
    Write-Host "   You MUST remove it from Git history and rotate ALL API keys!" -ForegroundColor Red
    Write-Host "   See SECURITY_REVIEW.md for instructions" -ForegroundColor Red
    $issuesFound++
} else {
    Write-Host "✅ .env was never committed" -ForegroundColor Green
}

# 3. Check for token.json files
Write-Host "`n[3/8] Checking for token.json files..." -ForegroundColor Yellow
$tokenFiles = git ls-files | Select-String -Pattern "token\.json"
if ($tokenFiles) {
    Write-Host "❌ WARNING: token.json files are tracked!" -ForegroundColor Red
    Write-Host "   Found: $tokenFiles" -ForegroundColor Red
    Write-Host "   Run: git rm --cached Agents/token.json server/token.json" -ForegroundColor Red
    $issuesFound++
} else {
    Write-Host "✅ No token.json files tracked" -ForegroundColor Green
}

# 4. Check for hardcoded API keys in Python files
Write-Host "`n[4/8] Scanning for hardcoded API keys in code..." -ForegroundColor Yellow
$apiKeyPatterns = @(
    "AIzaSy",  # Google API keys start with this
    "GOCSPX-",  # Google OAuth secrets
    "sk-",  # OpenAI keys
    "eyJhbGciOi"  # JWT tokens
)

$foundKeys = @()
foreach ($pattern in $apiKeyPatterns) {
    $matches = git grep -n $pattern -- "*.py" "*.js" 2>$null
    if ($matches) {
        $foundKeys += $matches
    }
}

if ($foundKeys.Count -gt 0) {
    Write-Host "❌ CRITICAL: Found potential hardcoded API keys!" -ForegroundColor Red
    Write-Host "   Review these files:" -ForegroundColor Red
    $foundKeys | ForEach-Object { Write-Host "   $_" -ForegroundColor Red }
    $issuesFound++
} else {
    Write-Host "✅ No hardcoded API keys found" -ForegroundColor Green
}

# 5. Check for sensitive files in staging
Write-Host "`n[5/8] Checking staged files..." -ForegroundColor Yellow
$stagedFiles = git diff --cached --name-only

$sensitivePatterns = @("\.env", "token\.json", "credentials\.json", "\.log", "\.pem", "\.key")
$sensitiveStagedFiles = @()

foreach ($file in $stagedFiles) {
    foreach ($pattern in $sensitivePatterns) {
        if ($file -match $pattern) {
            $sensitiveStagedFiles += $file
        }
    }
}

if ($sensitiveStagedFiles.Count -gt 0) {
    Write-Host "❌ WARNING: Sensitive files are staged for commit!" -ForegroundColor Red
    $sensitiveStagedFiles | ForEach-Object { Write-Host "   $_" -ForegroundColor Red }
    Write-Host "   Run: git reset HEAD <file> to unstage" -ForegroundColor Red
    $issuesFound++
} else {
    Write-Host "✅ No sensitive files staged" -ForegroundColor Green
}

# 6. Check for large files (resumes, backups)
Write-Host "`n[6/8] Checking for large files..." -ForegroundColor Yellow
$largeFiles = git ls-files | Where-Object {
    $file = $_
    if (Test-Path $file) {
        $size = (Get-Item $file).Length
        $size -gt 5MB
    }
}

if ($largeFiles) {
    Write-Host "⚠️  WARNING: Large files found (>5MB):" -ForegroundColor Yellow
    $largeFiles | ForEach-Object { 
        $size = [math]::Round((Get-Item $_).Length / 1MB, 2)
        Write-Host "   $_ ($size MB)" -ForegroundColor Yellow
    }
    Write-Host "   Consider adding to .gitignore if not needed" -ForegroundColor Yellow
} else {
    Write-Host "✅ No large files found" -ForegroundColor Green
}

# 7. Check for user data directories
Write-Host "`n[7/8] Checking for user data directories..." -ForegroundColor Yellow
$userDataDirs = @("Resumes/", "Cache/", "backups/", "server/sessions/")
$trackedUserData = @()

foreach ($dir in $userDataDirs) {
    $tracked = git ls-files $dir 2>$null
    if ($tracked) {
        $trackedUserData += $dir
    }
}

if ($trackedUserData.Count -gt 0) {
    Write-Host "⚠️  WARNING: User data directories are tracked:" -ForegroundColor Yellow
    $trackedUserData | ForEach-Object { Write-Host "   $_" -ForegroundColor Yellow }
    Write-Host "   These should be in .gitignore" -ForegroundColor Yellow
} else {
    Write-Host "✅ No user data directories tracked" -ForegroundColor Green
}

# 8. Verify .gitignore exists and is comprehensive
Write-Host "`n[8/8] Verifying .gitignore..." -ForegroundColor Yellow
if (Test-Path ".gitignore") {
    $gitignoreContent = Get-Content ".gitignore" -Raw
    $requiredPatterns = @(".env", "token.json", "*.log", "Resumes/", "Cache/", "backups/")
    $missingPatterns = @()
    
    foreach ($pattern in $requiredPatterns) {
        if ($gitignoreContent -notmatch [regex]::Escape($pattern)) {
            $missingPatterns += $pattern
        }
    }
    
    if ($missingPatterns.Count -gt 0) {
        Write-Host "⚠️  WARNING: .gitignore missing patterns:" -ForegroundColor Yellow
        $missingPatterns | ForEach-Object { Write-Host "   $_" -ForegroundColor Yellow }
    } else {
        Write-Host "✅ .gitignore is comprehensive" -ForegroundColor Green
    }
} else {
    Write-Host "❌ CRITICAL: .gitignore file not found!" -ForegroundColor Red
    $issuesFound++
}

# Summary
Write-Host "`n============================================================" -ForegroundColor Cyan
if ($issuesFound -eq 0) {
    Write-Host "✅ SECURITY CHECK PASSED!" -ForegroundColor Green
    Write-Host "============================================================" -ForegroundColor Cyan
    Write-Host "You can safely commit your changes." -ForegroundColor Green
    Write-Host "`nRecommended commit message:" -ForegroundColor Cyan
    Write-Host '  git commit -m "feat: Add production infrastructure and per-user Mimikree credentials"' -ForegroundColor White
    exit 0
} else {
    Write-Host "❌ SECURITY CHECK FAILED!" -ForegroundColor Red
    Write-Host "============================================================" -ForegroundColor Cyan
    Write-Host "Found $issuesFound critical issue(s)" -ForegroundColor Red
    Write-Host "`nDO NOT COMMIT until you fix the issues above!" -ForegroundColor Red
    Write-Host "See SECURITY_REVIEW.md for detailed instructions" -ForegroundColor Yellow
    exit 1
}

