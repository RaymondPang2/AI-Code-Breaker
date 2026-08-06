"""
Shared parsing of runner-process output into RunnerResult.

Both execution backends — the bare-subprocess runner
(app.services.runner_service) and the Docker-based runner
(app.services.docker_runner_service) — launch the exact same
runner/runner.py script and get back the exact same JSON protocol. This
module is the one place that turns "raw stdout/stderr/exit code from
whatever process we launched" into a validated RunnerResult, so the two
backends can't quietly drift in how they handle malformed output.
"""

from __future__ import annotations

import json

from pydantic import ValidationError

from app.schemas.runner import RunnerResult


def parse_runner_stdout(stdout: str, stderr: str, returncode: int) -> RunnerResult:
    """
    Parse a runner process's stdout as a RunnerResult, falling back to a
    structured internal_error if it produced something other than the
    expected single JSON object (e.g. it crashed before printing anything,
    or was killed by the OS/cgroup for exceeding a resource limit).
    """
    cleaned_stdout = stdout.strip()
    if not cleaned_stdout:
        return RunnerResult(
            status="internal_error",
            exception_type="EmptyRunnerOutput",
            exception_message=_empty_output_message(returncode, stderr),
        )

    try:
        payload = json.loads(cleaned_stdout)
    except json.JSONDecodeError as exc:
        return RunnerResult(
            status="internal_error",
            exception_type="MalformedRunnerOutput",
            exception_message=f"runner output was not valid JSON: {exc}",
        )

    try:
        return RunnerResult.model_validate(payload)
    except ValidationError as exc:
        # Valid JSON, but not shaped like a RunnerResult (wrong field
        # types, an unrecognized status value, etc.). Treated the same as
        # any other harness malfunction rather than letting a schema error
        # propagate out of the service layer as an unhandled 500.
        return RunnerResult(
            status="internal_error",
            exception_type="InvalidRunnerOutputShape",
            exception_message=f"runner output did not match the expected shape: {exc}",
        )


def _empty_output_message(returncode: int, stderr: str) -> str:
    hint = ""
    if returncode == 137:
        # 137 = 128 + SIGKILL(9). Common causes: the container's memory
        # limit was exceeded (OOM-killed by the kernel/cgroup) or the
        # process was forcibly killed (e.g. our own timeout backstop).
        # This is a best-effort hint, not a certainty — we genuinely can't
        # tell those apart from the exit code alone.
        hint = " (exit code 137 — likely killed for exceeding a resource limit, e.g. memory)"
    return (
        f"runner process produced no output (exit code {returncode}){hint}; "
        f"stderr: {stderr[:500]}"
    )
