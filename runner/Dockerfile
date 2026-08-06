# runner/Dockerfile
#
# Builds the ephemeral execution image for AI Code Breaker.
#
# ============================== IMPORTANT ===================================
# This image, by itself, provides almost none of the actual isolation. The
# real security boundary comes from the `docker run` flags applied by
# apps/backend/app/services/docker_runner_service.py:
#   --network none            (no network access)
#   --read-only                (read-only root filesystem)
#   --user 10001:10001         (non-root — also baked into this image)
#   --memory / --memory-swap   (memory limit, swap disabled)
#   --cpus                     (CPU limit)
#   --pids-limit                (process limit — the fork-bomb defense)
#   --cap-drop ALL              (dropped Linux capabilities)
#   --security-opt no-new-privileges
#   (never --privileged, never a host volume mount)
#
# This Dockerfile only prepares a minimal, predictable environment for
# those flags to constrain. See runner/README.md for the full rationale
# and the isolation model's documented limitations.
# =============================================================================
#
# Build (from the repo root):
#   docker build -t ai-code-breaker-runner:latest -f runner/Dockerfile runner/
# Or use the convenience script:
#   ./runner/build.sh

FROM python:3.12-slim

# A fixed, non-root uid/gid, matching what DockerRunner passes via
# `docker run --user`. --no-create-home / nologin: this user only ever
# needs to run one python process and read one file; it has no reason to
# have a home directory or an interactive shell.
RUN groupadd --gid 10001 runner \
    && useradd --uid 10001 --gid runner --no-create-home --shell /usr/sbin/nologin runner

WORKDIR /app

# The execution harness is the exact same stdlib-only script used by the
# (now superseded) bare-subprocess runner — its own module docstring
# always intended this: "can be dropped into a minimal container image
# unchanged." No third-party dependencies are installed in this image:
# nothing here needs them, and every package not installed is attack
# surface that doesn't exist.
COPY runner.py /app/run.py

# Never write .pyc bytecode caches. The root filesystem is read-only at
# runtime (via `docker run --read-only`), and no tmpfs is mounted by
# default in this milestone — so Python must not need to write anywhere,
# including its own bytecode cache, for the happy path to work at all.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

USER runner

ENTRYPOINT ["python3", "/app/run.py"]
