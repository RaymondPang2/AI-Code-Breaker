"""
Pydantic models for the runner subprocess protocol (see runner/runner.py).

These mirror the JSON shapes the runner script reads from stdin and writes
to stdout. Keeping them here — rather than importing anything from
runner.py — is deliberate: the runner is intentionally dependency-free
(stdlib only) so it can later run unmodified inside a minimal Docker image.
This module is the backend's typed view of that same contract, not a
shared implementation.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

RunnerStatus = Literal[
    "success",
    "syntax_error",
    "load_error",
    "runtime_error",
    "timeout",
    "unserializable_output",
    "internal_error",
]


class RunnerInvocation(BaseModel):
    """What the service layer sends to the runner subprocess over stdin."""

    source_code: str
    function_name: str
    input: list[int]
    timeout_seconds: float = Field(gt=0)


class RunnerResult(BaseModel):
    """What the service layer parses back from the runner subprocess's stdout."""

    status: RunnerStatus
    return_value: Any = None
    exception_type: str | None = None
    exception_message: str | None = None
    stdout: str = ""
    stderr: str = ""
    runtime_ms: float | None = None
