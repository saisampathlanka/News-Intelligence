# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# NewsIntel Dockerfile — v5
# Multi-stage: builder compiles deps, runtime is minimal non-root image.
#
# Key fixes vs naive builds:
#   - spaCy model downloaded to /root/.local (--user) so it copies correctly
#     to the runtime stage via COPY --from=builder /root/.local
#   - All pip installs use --user so packages land in one predictable location
#   - Non-root appuser (UID 1001) for runtime security
#   - HEALTHCHECK start-period=60s allows spaCy model load time
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

# ── Stage 1: builder ──────────────────────────────────────────────────────────
FROM python:3.11.9-slim AS builder

WORKDIR /build

# System build tools needed for psycopg2 C extension
RUN apt-get update && apt-get install -y --no-install-recommends \
        gcc \
        libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .

# Install all packages + spaCy model to /root/.local (--user flag is critical)
# Without --user, packages go to /usr/local which is NOT copied to runtime stage.
# pip user scheme: /root/.local/lib/python3.11/site-packages/
RUN pip install --no-cache-dir --prefer-binary --user -r requirements.txt

# Download spaCy model with --user so it lands under /root/.local alongside packages
# 'python -m spacy download' calls pip internally; --user ensures the same location.
RUN python -m spacy download en_core_web_sm --user


# ── Stage 2: runtime ──────────────────────────────────────────────────────────
FROM python:3.11.9-slim AS runtime

WORKDIR /app

# Only the PostgreSQL runtime lib — no compiler, no headers
RUN apt-get update && apt-get install -y --no-install-recommends \
        libpq5 \
    && rm -rf /var/lib/apt/lists/*

# Create non-root user
RUN groupadd --gid 1001 appgroup \
    && useradd --uid 1001 --gid appgroup --shell /bin/sh --create-home appuser

# Copy ALL user-installed packages (pip + spaCy model) from builder
COPY --from=builder /root/.local /home/appuser/.local

# Copy application source — owned by appuser, never root
COPY --chown=appuser:appgroup . .

# Make entrypoint executable
RUN chmod +x /app/docker-entrypoint.sh

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/home/appuser/.local/bin:$PATH" \
    # Ensure Python finds packages in the copied user location
    PYTHONUSERBASE="/home/appuser/.local" \
    ENV=production \
    DEBUG=false

USER appuser

EXPOSE 8000

# start-period=60s because spaCy loads en_core_web_sm (~12MB) on first import
# which can take 5-15s; without this the health check kills the container
# before it's ready.
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD python -c \
        "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')" \
    || exit 1

# Run alembic migrations then start uvicorn.
# Using the entrypoint script ensures migrations always run whether deployed via
# Docker directly, docker-compose, or Railway (railway.toml startCommand also
# chains them, but this is the Dockerfile-level safety net).
CMD ["/app/docker-entrypoint.sh"]

