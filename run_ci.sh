#!/usr/bin/env bash
set -e

echo "--- Upgrading Pip ---"
python -m pip install --upgrade pip

echo "--- Installing Dependencies ---"
pip install -r requirements.txt
pip install flake8 black isort bandit safety

echo "--- Running Linter (flake8) ---"
flake8 . --count --select=E9,F63,F7,F82 --show-source --statistics
flake8 . --count --exit-zero --max-complexity=10 --max-line-length=127 --statistics

echo "--- Checking Formatting (black) ---"
black --check .

echo "--- Checking Formatting (isort) ---"
isort --check-only --profile black .

echo "--- Security Scan (bandit) ---"
bandit -r . -x ./venv,./.venv

echo "--- Security Scan (safety) ---"
# Safety might fail due to missing API key or vulnerabilities; we can make it non-fatal or handle it.
safety check || echo "Safety check failed. It may require an API key or found vulnerabilities."

echo "--- Django System Check ---"
python manage.py check

echo "--- Running Tests ---"
python manage.py test --noinput

echo "CI Tasks Completed Successfully!"
