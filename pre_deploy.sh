#!/usr/bin/env bash
set -o errexit

# Pre-deploy command for the Render web service. Point Render's
# "Pre-Deploy Command" at this script (./pre_deploy.sh).
#
# Migrations run here, after the build and before the new release starts
# serving traffic, instead of in build.sh. This way schema changes apply once
# per deploy (not per build instance) and only once the new code is live.
python manage.py migrate
