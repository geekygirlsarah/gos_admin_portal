#!/usr/bin/env bash
set -o errexit

# Start command for the Render web service. Point Render's "Start Command"
# at this script (./start.sh) instead of the inline gunicorn command.
#
# Reason for --max-requests: the uvicorn worker never returns heap memory to
# the OS, so slow per-request accumulation (allocator arenas, connections,
# dependency-internal caches) grows until the instance hits its memory cap
# and Render kills it with SIGKILL (exit 137). Recycling the worker every
# N requests resets the heap and keeps memory bounded.
exec python -m gunicorn GoSAdminPortal.asgi:application \
  -k uvicorn.workers.UvicornWorker \
  --workers 1 \
  --max-requests 1000 \
  --max-requests-jitter 100 \
  --access-logfile - \
  --graceful-timeout 30