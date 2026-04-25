#!/bin/sh
# ── docker-entrypoint.sh ──────────────────────────────────────────────────────
# Runs Alembic migrations then starts the uvicorn server.
# Called as the CMD in the API Dockerfile so migrations always run on container
# start, even when deployed directly via Docker (not Railway's startCommand).
# ─────────────────────────────────────────────────────────────────────────────

set -e

echo "Running database migrations..."
alembic upgrade head

echo "Starting API server..."
exec uvicorn backend.main:app \
    --host 0.0.0.0 \
    --port "${PORT:-8000}" \
    --workers 1 \
    --log-level info
