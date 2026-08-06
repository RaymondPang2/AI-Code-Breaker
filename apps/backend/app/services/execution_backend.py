"""
Picks which execution backend actually runs submitted code, based on
app.core.config.execution_backend.

Shared by app.services.comparison_service and
app.services.hypothesis_search_service so both respect the same setting
identically — including in tests, where tests/conftest.py pins this to
the "subprocess" backend by default so the suite doesn't require Docker.
"""

from __future__ import annotations

from typing import Callable

from app.core.config import get_settings
from app.schemas.runner import RunnerResult
from app.services.docker_runner_service import execute_submission_docker
from app.services.runner_service import execute_submission as execute_submission_subprocess

ExecuteFn = Callable[..., RunnerResult]


def get_execute_function() -> ExecuteFn:
    settings = get_settings()
    return (
        execute_submission_docker
        if settings.execution_backend == "docker"
        else execute_submission_subprocess
    )
