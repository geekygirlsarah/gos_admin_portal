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
