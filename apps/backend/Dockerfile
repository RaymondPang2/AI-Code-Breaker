# apps/backend/Dockerfile
#
# Production image for BOTH the API service and the analysis worker — they
# run the same codebase with different entrypoints (see the compose files
# under infra/). Multi-stage: dependencies are built in one layer and copied
# into a slim final image that carries no build toolchain.
#
# Runs as a non-root user. Note that the WORKER still needs access to a
# Docker daemon to launch runner containers (EXECUTION_BACKEND=docker); the
# API service does not and must never be given the Docker socket. See
# SECURITY.md and infra/README.md — mounting the Docker socket is highly
# privileged (near-root on the host) and must be scoped to the worker only.

# ---- builder ----
FROM python:3.12-slim AS builder

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app
# Build context is the REPOSITORY ROOT (see infra/docker-compose*.yml), so the
# backend lives under apps/backend/ and the shared runner/ is a sibling. This
# is required: the runner script must ship in the image (copied below).
COPY apps/backend/requirements.txt ./
# Build wheels into a venv we copy wholesale into the final stage.
RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install --upgrade pip \
    && /opt/venv/bin/pip install --no-cache-dir -r requirements.txt

# ---- final ----
FROM python:3.12-slim AS final

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/opt/venv/bin:$PATH" \
    LOG_FORMAT=json \
    RUNNER_SCRIPT_PATH=/app/runner/runner.py

# Create an unprivileged user to run the app.
RUN useradd --create-home --uid 10001 appuser
WORKDIR /app

COPY --from=builder /opt/venv /opt/venv
COPY apps/backend/app ./app
COPY apps/backend/alembic ./alembic
COPY apps/backend/alembic.ini ./
# The execution runner script must be present in the image: the worker
# launches it as a subprocess (and the Docker backend uses it in the runner
# container). Without this the runner process can't find its script, every
# execution returns an internal error, and analyses show up as INCONCLUSIVE.
# RUNNER_SCRIPT_PATH (above) pins it to this absolute path regardless of CWD.
COPY runner ./runner

USER appuser

# The API listens on 8000; the worker overrides the command and exposes
# nothing. Migrations are applied explicitly (see the deployment checklist),
# NOT baked into the image CMD, so a rollback can pin an image without
# re-running migrations.
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
