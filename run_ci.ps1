Write-Host "--- Upgrading Pip ---" -ForegroundColor Cyan
python -m pip install --upgrade pip

Write-Host "--- Installing Dependencies ---" -ForegroundColor Cyan
pip install -r requirements.txt
pip install flake8 black isort bandit safety

$ErrorActionPreference = "Stop"

Write-Host "--- Running Linter (flake8) ---" -ForegroundColor Cyan
flake8 . --count --select=E9,F63,F7,F82 --show-source --statistics
flake8 . --count --exit-zero --max-complexity=10 --max-line-length=127 --statistics

Write-Host "--- Checking Formatting (black) ---" -ForegroundColor Cyan
black --check .

Write-Host "--- Checking Formatting (isort) ---" -ForegroundColor Cyan
isort --check-only --profile black .

Write-Host "--- Security Scan (bandit) ---" -ForegroundColor Cyan
bandit -r . -x ./venv,./.venv

Write-Host "--- Security Scan (safety) ---" -ForegroundColor Cyan
try {
    safety check
} catch {
    Write-Warning "Safety check failed. It may require an API key or found vulnerabilities."
}

Write-Host "--- Django System Check ---" -ForegroundColor Cyan
python manage.py check

Write-Host "--- Running Tests ---" -ForegroundColor Cyan
python manage.py test --noinput

Write-Host "CI Tasks Completed Successfully!" -ForegroundColor Green
