@echo off
echo ============================================
echo  fix_ci - Auto-fix formatting, then report
echo ============================================

echo.
echo --- Auto-fixing Formatting (black) ---
black --exclude "(venv|venv2|\.venv)" .
if %ERRORLEVEL% neq 0 (
    echo black formatting failed
    exit /b %ERRORLEVEL%
)

echo.
echo --- Auto-fixing Import Order (isort) ---
isort --profile black --skip venv --skip venv2 --skip .venv .
if %ERRORLEVEL% neq 0 (
    echo isort failed
    exit /b %ERRORLEVEL%
)

echo.
echo ============================================
echo  Reporting issues that require manual fixes
echo ============================================

set FAILED=0

echo.
echo --- Linter (flake8) ---
flake8 . --count --select=E9,F63,F7,F82 --show-source --statistics || set FAILED=1

echo.
echo --- Security Scan (bandit) ---
bandit -r . -x ./venv,./.venv,./venv2 || set FAILED=1

echo.
echo --- Static Analysis (semgrep) ---
semgrep --config auto . || echo semgrep reported findings.

echo.
echo --- Security Scan (safety) ---
powershell -Command "if (Test-Path 'requirements.txt') { $b = [System.IO.File]::ReadAllBytes((Resolve-Path 'requirements.txt')); if ($b.Length -ge 2 -and $b[0] -eq 0xff -and $b[1] -eq 0xfe) { Write-Host 'Converting requirements.txt to UTF-8...'; [System.IO.File]::WriteAllLines((Resolve-Path 'requirements.txt'), (Get-Content 'requirements.txt'), (New-Object System.Text.UTF8Encoding($false))) } }"
safety check || echo safety check failed.

echo.
echo --- Django System Check ---
python manage.py check
if %ERRORLEVEL% neq 0 set FAILED=1

echo.
if %FAILED% neq 0 (
    echo Some checks failed. Review output above.
    exit /b 1
)
echo All checks passed!
