@echo off
echo --- Upgrading Pip ---
python -m pip install --upgrade pip

echo --- Installing Dependencies ---
pip install -r requirements.txt
pip install flake8 black isort bandit safety

echo --- Running Linter (flake8) ---
flake8 . --count --select=E9,F63,F7,F82 --show-source --statistics
if %ERRORLEVEL% neq 0 (
    echo flake8 critical failed
    exit /b %ERRORLEVEL%
)
flake8 . --count --exit-zero --statistics

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
safety check
if %ERRORLEVEL% neq 0 (
    echo safety check failed. Note: If this is an API key issue, check your environment.
    rem Safety might fail without an API key or on vulnerabilities. 
    rem In CI it's a separate action, locally it might need config.
)

echo --- Django System Check ---
python manage.py check
if %ERRORLEVEL% neq 0 (
    echo Django system check failed
    exit /b %ERRORLEVEL%
)

echo --- Running Tests ---
python manage.py test --noinput
if %ERRORLEVEL% neq 0 (
    echo Django tests failed
    exit /b %ERRORLEVEL%
)

echo CI Tasks Completed Successfully!
