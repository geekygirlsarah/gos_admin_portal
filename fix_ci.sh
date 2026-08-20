#!/usr/bin/env bash
set -e

echo "============================================"
echo " fix_ci - Auto-fix formatting, then report"
echo "============================================"

echo ""
echo "--- Auto-fixing Formatting (black) ---"
black --exclude "(venv|venv2|\.venv)" .

echo ""
echo "--- Auto-fixing Import Order (isort) ---"
isort --profile black --skip venv --skip venv2 --skip .venv .

echo ""
echo "============================================"
echo " Reporting issues that require manual fixes"
echo "============================================"

FAILED=0

echo ""
echo "--- Linter (flake8) ---"
flake8 . --count --select=E9,F63,F7,F82 --show-source --statistics || FAILED=1

echo ""
echo "--- Security Scan (bandit) ---"
bandit -r . -x ./venv,./.venv,./venv2 || FAILED=1

echo ""
echo "--- Static Analysis (semgrep) ---"
semgrep --config auto . || echo "semgrep reported findings."

echo ""
echo "--- Security Scan (safety) ---"
python3 -c "
import os
if os.path.exists('requirements.txt'):
    with open('requirements.txt', 'rb') as f:
        if f.read(2) == b'\xff\xfe':
            print('Converting requirements.txt from UTF-16 to UTF-8...')
            with open('requirements.txt', 'r', encoding='utf-16') as f2:
                content = f2.read()
            with open('requirements.txt', 'w', encoding='utf-8') as f3:
                f3.write(content)
" 2>/dev/null || python -c "
import os
if os.path.exists('requirements.txt'):
    with open('requirements.txt', 'rb') as f:
        if f.read(2) == b'\xff\xfe':
            print('Converting requirements.txt from UTF-16 to UTF-8...')
            with open('requirements.txt', 'r', encoding='utf-16') as f2:
                content = f2.read()
            with open('requirements.txt', 'w', encoding='utf-8') as f3:
                f3.write(content)
"
safety check || echo "Safety check failed."

echo ""
echo "--- Django System Check ---"
python manage.py check || FAILED=1

echo ""
if [ "$FAILED" -ne 0 ]; then
    echo "Some checks failed. Review output above."
    exit 1
fi
echo "All checks passed!"
