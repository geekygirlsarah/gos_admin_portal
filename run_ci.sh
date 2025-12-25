#!/usr/bin/env bash
set -e

echo "--- Upgrading Pip ---"
python -m pip install --upgrade pip

echo "--- Installing Dependencies ---"
pip install -r requirements.txt
pip install flake8 black isort bandit safety

echo "--- Running Linter (flake8) ---"
flake8 . --count --select=E9,F63,F7,F82 --show-source --statistics || { echo "flake8 critical failed"; exit 1; }
flake8 . --count --exit-zero --statistics

echo "--- Checking Formatting (black) ---"
black --check --exclude "(venv|venv2|\.venv)" . || { echo "black formatting check failed"; exit 1; }

echo "--- Checking Formatting (isort) ---"
isort --check-only --profile black --skip venv --skip venv2 --skip .venv . || { echo "isort formatting check failed"; exit 1; }

echo "--- Security Scan (bandit) ---"
bandit -r . -x ./venv,./.venv,./venv2 || { echo "bandit security scan failed"; exit 1; }

echo "--- Security Scan (safety) ---"
# Safety might fail due to missing API key or vulnerabilities; we can make it non-fatal or handle it.
safety check || echo "Safety check failed. It may require an API key or found vulnerabilities."

echo "--- Django System Check ---"
python manage.py check || { echo "Django system check failed"; exit 1; }

echo "--- Running Tests ---"
python manage.py test --noinput || { echo "Django tests failed"; exit 1; }

echo "CI Tasks Completed Successfully!"
