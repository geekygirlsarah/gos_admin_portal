Write-Host "--- Upgrading Pip ---" -ForegroundColor Cyan
python -m pip install --upgrade pip

Write-Host "--- Installing Dependencies ---" -ForegroundColor Cyan
python -m pip install -r requirements.txt

$ErrorActionPreference = "Stop"

Write-Host "--- Running Checks (flake8, black, isort, bandit) in Parallel ---" -ForegroundColor Cyan
$p_flake8 = Start-Process -PassThru -NoNewWindow -FilePath "python" -ArgumentList "-m flake8 . --count --select=E9,F63,F7,F82 --show-source --statistics"
$p_black  = Start-Process -PassThru -NoNewWindow -FilePath "python" -ArgumentList "-m black --check --exclude `"(venv|venv2|\.venv)`" ."
$p_isort  = Start-Process -PassThru -NoNewWindow -FilePath "python" -ArgumentList "-m isort --check-only --profile black --skip venv --skip venv2 --skip .venv ."
$p_bandit = Start-Process -PassThru -NoNewWindow -FilePath "python" -ArgumentList "-m bandit -r . -x ./venv,./.venv,./venv2"

$p_flake8, $p_black, $p_isort, $p_bandit | Wait-Process

$failed = $false
if ($p_flake8.ExitCode -ne 0) { $failed = $true; Write-Host "flake8 failed" -ForegroundColor Red }
if ($p_black.ExitCode -ne 0)  { $failed = $true; Write-Host "black failed" -ForegroundColor Red }
if ($p_isort.ExitCode -ne 0)  { $failed = $true; Write-Host "isort failed" -ForegroundColor Red }
if ($p_bandit.ExitCode -ne 0) { $failed = $true; Write-Host "bandit failed" -ForegroundColor Red }

if ($failed) {
    Write-Error "One or more parallel checks failed."
    exit 1
}

Write-Host "--- Static Analysis (semgrep) ---" -ForegroundColor Cyan
semgrep --config auto .

Write-Host "--- Security Scan (safety) ---" -ForegroundColor Cyan
# Ensure requirements.txt is UTF-8 (no BOM) to avoid safety failure if exported from PowerShell
if (Test-Path "requirements.txt") {
    $bytes = [System.IO.File]::ReadAllBytes((Resolve-Path "requirements.txt"))
    if ($bytes.Length -ge 2 -and $bytes[0] -eq 0xff -and $bytes[1] -eq 0xfe) {
        Write-Host "Converting requirements.txt from UTF-16 to UTF-8..." -ForegroundColor Yellow
        [System.IO.File]::WriteAllLines((Resolve-Path "requirements.txt"), (Get-Content "requirements.txt"), (New-Object System.Text.UTF8Encoding($false)))
    }
}
safety check

Write-Host "--- Django System Check ---" -ForegroundColor Cyan
python manage.py check
if ($LASTEXITCODE -ne 0) {
    Write-Error "Django system check failed"
    exit $LASTEXITCODE
}

Write-Host "--- Running Tests (Parallel) ---" -ForegroundColor Cyan
python manage.py test --noinput --parallel
if ($LASTEXITCODE -ne 0) {
    Write-Error "Django tests failed"
    exit $LASTEXITCODE
}

# End of the script
Write-Host "CI Tasks Completed Successfully!" -ForegroundColor Green
