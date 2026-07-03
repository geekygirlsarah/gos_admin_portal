#!/usr/bin/env bash
set -o errexit

#rm -rf appvenv
#python3 -m venv appvenv

#appvenv/bin/pip install --no-cache-dir -r requirements.txt
python -m pip install --no-cache-dir -r requirements.txt

#appvenv/bin/python manage.py collectstatic --no-input
#appvenv/bin/python manage.py migrate
python manage.py collectstatic --no-input
python manage.py migrate
