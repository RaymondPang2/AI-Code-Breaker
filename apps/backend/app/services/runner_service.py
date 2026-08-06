"""
Service layer for executing one submitted function on one input via the
temporary local runner (runner/runner.py), as a bare Python subprocess.

============================== STATUS ==============================
This is the ORIGINAL execution backend. It has been superseded by
app.services.docker_runner_service as the default execution path for
app.services.comparison_service (see app.core.config.execution_backend).
It's kept because:
  - it's a useful fallback for environments without Docker installed
    (including this project's own test/dev sandbox),
  - its tests still exercise the runner.py protocol itself, independent
    of Docker.
It is NOT what a deployed instance of this project should use — see
runner/README.md for why a bare subprocess is not a security sandbox.
======================================================================
"""

from __future__ import annotations

import subprocess
import sys

from app.core.config import get_settings
from app.schemas.runner import RunnerInvocation, RunnerResult
from app.services.runner_protocol import parse_runner_stdout


def execute_submission(
    source_code: str,
    function_name: str,
    input_: list[int],
    timeout_seconds: float | None = None,
) -> RunnerResult:
    """
    Run one function, from one source file, on one input — in a fresh
    subprocess — and return a structured result.

    This never raises for "expected" failure modes (syntax errors, runtime
    exceptions, timeouts, etc.) — those all come back as a RunnerResult
    with the appropriate `status`. It can still raise for genuinely
    unexpected conditions, e.g. the runner script itself being missing.
    """
    settings = get_settings()
    call_timeout = timeout_seconds if timeout_seconds is not None else settings.runner_call_timeout_seconds

    invocation = RunnerInvocation(
        source_code=source_code,
        function_name=function_name,
        input=input_,
        timeout_seconds=call_timeout,
    )

    # Hard backstop: even if the runner's own internal SIGALRM timeout
    # fails to fire for some reason, subprocess.run's `timeout=` will kill
    # the whole process after call_timeout + buffer. This is what makes the
    # timeout "strict" — it doesn't depend on the child process cooperating.
    subprocess_timeout = call_timeout + settings.runner_subprocess_timeout_buffer_seconds

    try:
        completed = subprocess.run(
            [sys.executable, str(settings.runner_script_path)],
            input=invocation.model_dump_json(),
            capture_output=True,
            text=True,
            timeout=subprocess_timeout,
        )
    except subprocess.TimeoutExpired:
        # The child never got the chance to emit its own JSON (it was
        # killed), so the service constructs the timeout result itself.
        return RunnerResult(
            status="timeout",
            exception_type="TimeoutError",
            exception_message=(
                f"execution exceeded the hard subprocess timeout of {subprocess_timeout:g}s"
            ),
        )

    return parse_runner_stdout(completed.stdout, completed.stderr, completed.returncode)
