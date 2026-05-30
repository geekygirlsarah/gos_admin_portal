#!/usr/bin/env bash
set -e

echo "--- Upgrading Pip ---"
python -m pip install --upgrade pip

echo "--- Installing Dependencies ---"
pip install -r requirements.txt

echo "--- Running Checks in Parallel ---"
# Launch independent checks in background
flake8 . --count --select=E9,F63,F7,F82 --show-source --statistics &
p1=$!
black --check --exclude "(venv|venv2|\.venv)" . &
p2=$!
isort --check-only --profile black --skip venv --skip venv2 --skip .venv . &
p3=$!
bandit -r . -x ./venv,./.venv,./venv2 &
p4=$!

# Wait for background jobs and collect results
wait $p1 || ( echo "flake8 critical failed"; exit 1 )
wait $p2 || ( echo "black formatting check failed"; exit 1 )
wait $p3 || ( echo "isort formatting check failed"; exit 1 )
wait $p4 || ( echo "bandit security scan failed"; exit 1 )

echo "--- Static Analysis (semgrep) ---"
# Semgrep can be slow and output-heavy, keeping it sequential or separate
semgrep --config auto . || echo "semgrep static analysis reported findings. Proceeding..."

echo "--- Security Scan (safety) ---"
safety check || echo "Safety check failed. It may require an API key or found vulnerabilities."

echo "--- Django System Check ---"
python manage.py check || ( echo "Django system check failed"; exit 1 )

echo "--- Running Tests (Parallel) ---"
python manage.py test --noinput --parallel || ( echo "Django tests failed"; exit 1 )

echo "CI Tasks Completed Successfully!"
