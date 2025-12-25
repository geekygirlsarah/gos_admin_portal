Write-Host "--- Upgrading Pip ---" -ForegroundColor Cyan
python -m pip install --upgrade pip

Write-Host "--- Installing Dependencies ---" -ForegroundColor Cyan
pip install -r requirements.txt
pip install flake8 black isort bandit safety

$ErrorActionPreference = "Stop"

Write-Host "--- Running Linter (flake8) ---" -ForegroundColor Cyan
flake8 . --count --select=E9,F63,F7,F82 --show-source --statistics
if ($LASTEXITCODE -ne 0) {
    Write-Error "flake8 critical failed"
    exit $LASTEXITCODE
}
flake8 . --count --exit-zero --statistics

Write-Host "--- Checking Formatting (black) ---" -ForegroundColor Cyan
black --check --exclude "(venv|venv2|\.venv)" .
if ($LASTEXITCODE -ne 0) {
    Write-Error "black formatting check failed"
    exit $LASTEXITCODE
}

Write-Host "--- Checking Formatting (isort) ---" -ForegroundColor Cyan
isort --check-only --profile black --skip venv --skip venv2 --skip .venv .
if ($LASTEXITCODE -ne 0) {
    Write-Error "isort formatting check failed"
    exit $LASTEXITCODE
}

Write-Host "--- Security Scan (bandit) ---" -ForegroundColor Cyan
bandit -r . -x ./venv,./.venv,./venv2
if ($LASTEXITCODE -ne 0) {
    Write-Error "bandit security scan failed"
    exit $LASTEXITCODE
}

Write-Host "--- Security Scan (safety) ---" -ForegroundColor Cyan
try {
    safety check
} catch {
    Write-Warning "Safety check failed. It may require an API key or found vulnerabilities."
}

Write-Host "--- Django System Check ---" -ForegroundColor Cyan
python manage.py check
if ($LASTEXITCODE -ne 0) {
    Write-Error "Django system check failed"
    exit $LASTEXITCODE
}

Write-Host "--- Running Tests ---" -ForegroundColor Cyan
python manage.py test --noinput
if ($LASTEXITCODE -ne 0) {
    Write-Error "Django tests failed"
    exit $LASTEXITCODE
}

Write-Host "CI Tasks Completed Successfully!" -ForegroundColor Green
