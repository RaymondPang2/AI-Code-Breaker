"""
Detects whether Docker is actually usable in the current environment —
installed, daemon reachable, and (optionally) the runner image built.

Used to skip Docker-dependent tests with a clear reason rather than
failing them outright when Docker isn't available.
"""

from __future__ import annotations

import shutil
import subprocess

from app.core.config import get_settings


def docker_daemon_available() -> bool:
    """True if the `docker` binary exists and can talk to a running daemon."""
    settings = get_settings()
    if shutil.which(settings.docker_binary) is None:
        return False
    try:
        result = subprocess.run(
            [settings.docker_binary, "info"],
            capture_output=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


def docker_runner_image_available() -> bool:
    """True if runner/build.sh has been run and the image exists locally."""
    if not docker_daemon_available():
        return False
    settings = get_settings()
    try:
        result = subprocess.run(
            [settings.docker_binary, "image", "inspect", settings.docker_runner_image],
            capture_output=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


DOCKER_UNAVAILABLE_REASON = (
    "Docker is not installed or the daemon is not running; skipping "
    "Docker-dependent tests. See runner/README.md."
)
DOCKER_IMAGE_UNAVAILABLE_REASON = (
    "The ai-code-breaker-runner image has not been built; run "
    "./runner/build.sh first. See runner/README.md."
)
