# Windows-specific clean script for IDIS UI
# Kills Node processes and removes build artifacts to fix EPERM errors

Write-Host "Cleaning IDIS UI for Windows..." -ForegroundColor Cyan

# Stop any running Node processes
Write-Host "Stopping Node processes..." -ForegroundColor Yellow
Get-Process node -ErrorAction SilentlyContinue | Stop-Process -Force
Start-Sleep -Seconds 1

# Remove node_modules
if (Test-Path "node_modules") {
    Write-Host "Removing node_modules..." -ForegroundColor Yellow
    Remove-Item -Recurse -Force "node_modules"
}

# Remove .next build cache
if (Test-Path ".next") {
    Write-Host "Removing .next..." -ForegroundColor Yellow
    Remove-Item -Recurse -Force ".next"
}

Write-Host "Clean complete! Now run: npm ci" -ForegroundColor Green
