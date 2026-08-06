#!/usr/bin/env python3
"""
runner/runner.py — temporary local execution runner for submitted code.

============================== SECURITY WARNING =============================
This runner isolates submitted code from the FastAPI process by running it
in a separate OS process (see protocol below) — but a plain Python
subprocess is NOT a security sandbox. It shares the host filesystem,
network, environment variables, and user permissions with the machine
running it. Submitted code can still read/write files, make network calls,
spawn further processes, or exhaust CPU/memory on the host.

This is a TEMPORARY development-only measure whose only job is to keep
submitted code out of the FastAPI server's own process and memory space.
It is explicitly NOT safe for public deployment. It must be replaced with
real OS-level isolation (a locked-down Docker container: no network, a
read-only filesystem, resource limits, a non-root user) before this project
accepts submissions from anyone other than its own developer.
===============================================================================

Protocol
--------
This script speaks JSON over stdin/stdout and nothing else. It is launched
as a fresh subprocess per call (`python3 runner.py`), reads exactly one
JSON object from stdin, and writes exactly one JSON object to stdout.

Request (stdin), one JSON object:
    {
        "source_code": "def foo(xs):\n    return sum(xs)\n",
        "function_name": "foo",
        "input": [1, 2, 3],
        "timeout_seconds": 5.0            # optional, defaults below
    }

Response (stdout), one JSON object:
    {
        "status": "success" | "syntax_error" | "load_error"
                 | "runtime_error" | "timeout" | "unserializable_output"
                 | "internal_error",
        "return_value": <JSON value> | null,
        "exception_type": <str> | null,
        "exception_message": <str> | null,   # short, sanitized
        "stdout": <str>,                     # captured print() output, capped
        "stderr": <str>,                     # captured stderr output, capped
        "runtime_ms": <float> | null         # time spent inside the call
    }

Status meanings:
    success               - the function returned a JSON-serializable value.
    syntax_error           - source_code failed to compile.
    load_error              - source_code compiled but raised while its
                              top-level statements ran, or function_name
                              was not defined / not callable afterward.
    runtime_error           - the function was called but raised.
    timeout                 - the function call exceeded timeout_seconds.
    unserializable_output   - the function returned successfully, but the
                              value can't be represented as JSON.
    internal_error          - this harness itself failed unexpectedly (e.g.
                              malformed request JSON). Should be rare; if it
                              shows up often, the harness has a bug.

This script has no dependency on the rest of the codebase (no FastAPI, no
Pydantic — stdlib only) so it can be dropped into a minimal container image
unchanged when Docker isolation replaces it.

Platform note: timeout enforcement uses SIGALRM, which is POSIX-only. This
runner is for local development on Linux/macOS; it will not work unmodified
on Windows. That's acceptable for a temporary, developer-only tool.
"""

from __future__ import annotations

import contextlib
import io
import json
import signal
import sys
import time
from typing import Any

STDOUT_STDERR_CHAR_LIMIT = 4_000
EXCEPTION_MESSAGE_CHAR_LIMIT = 500
DEFAULT_TIMEOUT_SECONDS = 5.0

# Deliberately fake filename used when compiling submitted code. Any
# SyntaxError or traceback referencing "line N of <file>" will show this
# instead of a real path on this host's filesystem.
SUBMITTED_CODE_FILENAME = "<submitted_code>"


class _CallTimeout(Exception):
    """Raised internally when the SIGALRM handler fires. Kept distinct from
    the builtin TimeoutError so it can't be caught (or spoofed) by a
    `except TimeoutError` in submitted code."""


class _CappedWriter(io.TextIOBase):
    """
    A write-only text stream that silently stops accumulating text after a
    character limit.

    Submitted code can call print() in a loop; without a cap, that output
    would grow without bound in this process's memory and in the pipe the
    parent process reads from. This keeps both bounded regardless of how
    much the submitted code tries to write.
    """

    def __init__(self, limit: int) -> None:
        self._limit = limit
        self._parts: list[str] = []
        self._length = 0
        self._truncated = False

    def write(self, text: str) -> int:
        if not self._truncated:
            remaining = self._limit - self._length
            if remaining <= 0:
                self._truncated = True
            else:
                chunk = text[:remaining]
                self._parts.append(chunk)
                self._length += len(chunk)
                if len(text) > remaining:
                    self._truncated = True
        return len(text)

    def getvalue(self) -> str:
        text = "".join(self._parts)
        if self._truncated:
            text += "\n...[output truncated]"
        return text


def _alarm_handler(signum: int, frame: Any) -> None:
    raise _CallTimeout("execution exceeded the time limit")


def _sanitize_exception(exc: BaseException) -> tuple[str, str]:
    """
    Reduce an exception to (type name, short message) instead of a full
    traceback. A traceback would include this runner's own file paths and
    internal call stack — information about the host that has no business
    reaching whoever submitted the code.
    """
    message = str(exc)
    if len(message) > EXCEPTION_MESSAGE_CHAR_LIMIT:
        message = message[:EXCEPTION_MESSAGE_CHAR_LIMIT] + "...[truncated]"
    return type(exc).__name__, message


def _result(
    status: str,
    *,
    return_value: Any = None,
    exception_type: str | None = None,
    exception_message: str | None = None,
    stdout: str = "",
    stderr: str = "",
    runtime_ms: float | None = None,
) -> dict:
    return {
        "status": status,
        "return_value": return_value,
        "exception_type": exception_type,
        "exception_message": exception_message,
        "stdout": stdout,
        "stderr": stderr,
        "runtime_ms": runtime_ms,
    }


def run(request: dict) -> dict:
    """Execute one submission against one input. Never raises — every
    failure mode is translated into a result dict instead."""

    source_code = request["source_code"]
    function_name = request["function_name"]
    call_input = request["input"]
    timeout_seconds = float(request.get("timeout_seconds", DEFAULT_TIMEOUT_SECONDS))

    out_stream = _CappedWriter(STDOUT_STDERR_CHAR_LIMIT)
    err_stream = _CappedWriter(STDOUT_STDERR_CHAR_LIMIT)

    # --- Step 1: compile -----------------------------------------------
    # Catches syntax errors before anything is ever executed.
    try:
        code_obj = compile(source_code, SUBMITTED_CODE_FILENAME, "exec")
    except SyntaxError as exc:
        exc_type, message = _sanitize_exception(exc)
        return _result("syntax_error", exception_type=exc_type, exception_message=message)

    # --- Step 2: load (run the module's top-level statements) ----------
    # Defines the function (and anything it imports/depends on) in a fresh
    # namespace. Not time-limited in this temporary runner — see module
    # docstring; the timeout budget covers the function call itself.
    namespace: dict[str, Any] = {}
    try:
        with contextlib.redirect_stdout(out_stream), contextlib.redirect_stderr(err_stream):
            exec(code_obj, namespace)
    except Exception as exc:
        exc_type, message = _sanitize_exception(exc)
        return _result(
            "load_error",
            exception_type=exc_type,
            exception_message=message,
            stdout=out_stream.getvalue(),
            stderr=err_stream.getvalue(),
        )

    if function_name not in namespace:
        return _result(
            "load_error",
            exception_type="FunctionNotFoundError",
            exception_message=f"function '{function_name}' was not defined by the submitted code",
            stdout=out_stream.getvalue(),
            stderr=err_stream.getvalue(),
        )

    func = namespace[function_name]
    if not callable(func):
        return _result(
            "load_error",
            exception_type="NotCallableError",
            exception_message=f"'{function_name}' was defined but is not callable",
            stdout=out_stream.getvalue(),
            stderr=err_stream.getvalue(),
        )

    # --- Step 3: call the function once, under a hard wall-clock budget -
    previous_handler = signal.signal(signal.SIGALRM, _alarm_handler)
    signal.setitimer(signal.ITIMER_REAL, timeout_seconds)
    start = time.perf_counter()
    try:
        with contextlib.redirect_stdout(out_stream), contextlib.redirect_stderr(err_stream):
            return_value = func(call_input)
    except _CallTimeout:
        return _result(
            "timeout",
            exception_type="TimeoutError",
            exception_message=f"execution exceeded {timeout_seconds:g}s",
            stdout=out_stream.getvalue(),
            stderr=err_stream.getvalue(),
        )
    except Exception as exc:
        exc_type, message = _sanitize_exception(exc)
        return _result(
            "runtime_error",
            exception_type=exc_type,
            exception_message=message,
            stdout=out_stream.getvalue(),
            stderr=err_stream.getvalue(),
            runtime_ms=(time.perf_counter() - start) * 1000,
        )
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous_handler)

    runtime_ms = (time.perf_counter() - start) * 1000

    # --- Step 4: confirm the return value can cross the JSON boundary ---
    # allow_nan=False also rejects float('inf')/float('nan'): valid Python
    # values, but not valid strict JSON.
    try:
        json.dumps(return_value, allow_nan=False)
    except (TypeError, ValueError):
        return _result(
            "unserializable_output",
            exception_type=type(return_value).__name__,
            exception_message=(
                f"return value of type '{type(return_value).__name__}' is not JSON-serializable"
            ),
            stdout=out_stream.getvalue(),
            stderr=err_stream.getvalue(),
            runtime_ms=runtime_ms,
        )

    return _result(
        "success",
        return_value=return_value,
        stdout=out_stream.getvalue(),
        stderr=err_stream.getvalue(),
        runtime_ms=runtime_ms,
    )


def main() -> None:
    raw = sys.stdin.read()
    try:
        request = json.loads(raw)
    except json.JSONDecodeError as exc:
        json.dump(
            _result(
                "internal_error",
                exception_type="JSONDecodeError",
                exception_message=f"request was not valid JSON: {exc}",
            ),
            sys.stdout,
        )
        return

    try:
        result = run(request)
    except Exception as exc:
        # The harness itself must never crash silently — if something here
        # is broken, report it the same structured way instead of leaving
        # the parent process to interpret a stack trace on stderr.
        exc_type, message = _sanitize_exception(exc)
        result = _result("internal_error", exception_type=exc_type, exception_message=message)

    json.dump(result, sys.stdout)


if __name__ == "__main__":
    main()
