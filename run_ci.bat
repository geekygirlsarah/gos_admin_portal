@echo off
echo --- Upgrading Pip ---
python -m pip install --upgrade pip

echo --- Installing Dependencies ---
pip install -r requirements.txt

echo --- Running Linter (flake8) ---
flake8 . --count --select=E9,F63,F7,F82 --show-source --statistics
if %ERRORLEVEL% neq 0 (
    echo flake8 critical failed
    exit /b %ERRORLEVEL%
)

echo --- Checking Formatting (black) ---
black --check --exclude "(venv|venv2|\.venv)" .
if %ERRORLEVEL% neq 0 (
    echo black formatting check failed
    exit /b %ERRORLEVEL%
)

echo --- Checking Formatting (isort) ---
isort --check-only --profile black --skip venv --skip venv2 --skip .venv .
if %ERRORLEVEL% neq 0 (
    echo isort formatting check failed
    exit /b %ERRORLEVEL%
)

echo --- Security Scan (bandit) ---
bandit -r . -x ./venv,./.venv,./venv2
if %ERRORLEVEL% neq 0 (
    echo bandit security scan failed
    exit /b %ERRORLEVEL%
)

echo --- Security Scan (safety) ---
powershell -Command "if (Test-Path 'requirements.txt') { $b = [System.IO.File]::ReadAllBytes((Resolve-Path 'requirements.txt')); if ($b.Length -ge 2 -and $b[0] -eq 0xff -and $b[1] -eq 0xfe) { Write-Host 'Converting requirements.txt to UTF-8...'; [System.IO.File]::WriteAllLines((Resolve-Path 'requirements.txt'), (Get-Content 'requirements.txt'), (New-Object System.Text.UTF8Encoding($false))) } }"
safety check || echo safety check failed.

echo --- Static Analysis (semgrep) ---
semgrep --config auto . || echo semgrep findings found.

echo --- Django System Check ---
python manage.py check
if %ERRORLEVEL% neq 0 (
    echo Django system check failed
    exit /b %ERRORLEVEL%
)

echo --- Running Tests (Parallel) ---
python manage.py test --noinput --parallel
if %ERRORLEVEL% neq 0 (
    echo Django tests failed
    exit /b %ERRORLEVEL%
)

echo CI Tasks Completed Successfully!
