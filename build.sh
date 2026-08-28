#!/usr/bin/env bash
# Set up script to fail if any of these commands fail
set -o errexit

# Install pip, then install requirements
python -m pip install --upgrade pip
python -m pip install --no-cache-dir -r requirements.txt

# Collect static files for serving, then migrate
python manage.py collectstatic --no-input
python manage.py migrate
