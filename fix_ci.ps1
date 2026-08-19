Write-Host "============================================" -ForegroundColor Cyan
Write-Host " fix_ci - Auto-fix formatting, then report" -ForegroundColor Cyan
Write-Host "============================================" -ForegroundColor Cyan

Write-Host "`n--- Auto-fixing Formatting (black) ---" -ForegroundColor Cyan
python -m black --exclude "(venv|venv2|\.venv)" .
if ($LASTEXITCODE -ne 0) {
    Write-Error "black formatting failed"
    exit $LASTEXITCODE
}

Write-Host "`n--- Auto-fixing Import Order (isort) ---" -ForegroundColor Cyan
python -m isort --profile black --skip venv --skip venv2 --skip .venv .
if ($LASTEXITCODE -ne 0) {
    Write-Error "isort failed"
    exit $LASTEXITCODE
}

Write-Host "`n============================================" -ForegroundColor Cyan
Write-Host " Reporting issues that require manual fixes" -ForegroundColor Cyan
Write-Host "============================================" -ForegroundColor Cyan

$failed = $false

Write-Host "`n--- Linter (flake8) ---" -ForegroundColor Cyan
python -m flake8 . --count --select=E9,F63,F7,F82 --show-source --statistics
if ($LASTEXITCODE -ne 0) { $failed = $true }

Write-Host "`n--- Security Scan (bandit) ---" -ForegroundColor Cyan
python -m bandit -r . -x ./venv,./.venv,./venv2
if ($LASTEXITCODE -ne 0) { $failed = $true }

Write-Host "`n--- Static Analysis (semgrep) ---" -ForegroundColor Cyan
semgrep --config auto .
if ($LASTEXITCODE -ne 0) { Write-Host "semgrep reported findings." -ForegroundColor Yellow }

Write-Host "`n--- Security Scan (safety) ---" -ForegroundColor Cyan
if (Test-Path "requirements.txt") {
    $bytes = [System.IO.File]::ReadAllBytes((Resolve-Path "requirements.txt"))
    if ($bytes.Length -ge 2 -and $bytes[0] -eq 0xff -and $bytes[1] -eq 0xfe) {
        Write-Host "Converting requirements.txt from UTF-16 to UTF-8..." -ForegroundColor Yellow
        [System.IO.File]::WriteAllLines((Resolve-Path "requirements.txt"), (Get-Content "requirements.txt"), (New-Object System.Text.UTF8Encoding($false)))
    }
}
safety check
if ($LASTEXITCODE -ne 0) { Write-Host "safety check failed." -ForegroundColor Yellow }

Write-Host "`n--- Django System Check ---" -ForegroundColor Cyan
python manage.py check
if ($LASTEXITCODE -ne 0) { $failed = $true }

Write-Host "`n" -NoNewline
if ($failed) {
    Write-Host "Some checks failed. Review output above." -ForegroundColor Red
    exit 1
}
Write-Host "All checks passed!" -ForegroundColor Green
