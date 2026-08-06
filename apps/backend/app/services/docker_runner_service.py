"""
Service layer for executing one submitted function on one input inside an
ephemeral Docker container.

This is the DEFAULT execution backend for app.services.comparison_service
(see app.core.config.execution_backend). It launches runner/runner.py —
the exact same execution harness used by the bare-subprocess backend —
inside a container built from runner/Dockerfile, constrained by the
`docker run` flags below.

============================== SECURITY WARNING ==============================
Docker containers are a real, meaningful isolation boundary — much
stronger than a bare subprocess — but they are NOT a perfect one. This
service does not claim otherwise. See runner/README.md for what these
flags do and do not protect against (shared-kernel attack surface,
historical container-escape CVEs, cgroup/namespace implementation bugs,
etc.) before relying on this for genuinely untrusted, adversarial,
publicly-submitted code.
================================================================================

The container itself:
  - has no network access (--network none)
  - runs as a fixed non-root user (--user, matching the image's own
    non-root USER)
  - has a read-only root filesystem (--read-only) and no tmpfs is mounted
    — nothing in the current execution model needs to write anything
  - is memory-limited with swap disabled (--memory / --memory-swap /
    --memory-swappiness 0)
  - is CPU-limited (--cpus)
  - has a hard process-count limit (--pids-limit) — the actual defense
    against fork bombs / runaway process spawning
  - has every Linux capability dropped (--cap-drop ALL) and cannot gain
    new privileges (--security-opt no-new-privileges)
  - is never run with --privileged
  - has no host directories mounted (no -v / --mount flags at all)
  - is always removed after exit (--rm), with an explicit `docker rm -f`
    backstop if it had to be force-killed for exceeding its timeout
"""

from __future__ import annotations

import subprocess
import uuid

from app.core.config import get_settings
from app.schemas.runner import RunnerInvocation, RunnerResult
from app.services.runner_protocol import parse_runner_stdout


def execute_submission_docker(
    source_code: str,
    function_name: str,
    input_: list[int],
    timeout_seconds: float | None = None,
) -> RunnerResult:
    """
    Run one function, from one source file, on one input — inside a fresh,
    locked-down container — and return a structured result.

    Like the bare-subprocess backend, this never raises for "expected"
    failure modes (syntax errors, runtime exceptions, timeouts, resource
    limits, etc.) — those all come back as a RunnerResult with the
    appropriate `status`. It also never raises when Docker itself is
    unavailable (not installed, daemon not running) — that becomes a
    structured internal_error too, since it's an operational condition
    the caller should be able to handle the same way as any other harness
    failure, not a crash.
    """
    settings = get_settings()
    call_timeout = (
        timeout_seconds if timeout_seconds is not None else settings.runner_call_timeout_seconds
    )
    subprocess_timeout = call_timeout + settings.runner_subprocess_timeout_buffer_seconds

    invocation = RunnerInvocation(
        source_code=source_code,
        function_name=function_name,
        input=input_,
        timeout_seconds=call_timeout,
    )

    container_name = f"acb-runner-{uuid.uuid4().hex[:12]}"
    docker_cmd = _build_docker_run_command(settings, container_name)

    try:
        completed = subprocess.run(
            docker_cmd,
            input=invocation.model_dump_json(),
            capture_output=True,
            text=True,
            timeout=subprocess_timeout,
        )
    except subprocess.TimeoutExpired:
        # Killing the `docker run` CLI process does NOT stop the
        # container — it just detaches from it. `docker rm -f` forcibly
        # kills (SIGKILL) and removes it in one step, which is the actual
        # hard backstop for a container that didn't respect its internal
        # SIGALRM timeout for any reason.
        _force_remove_container(settings, container_name)
        return RunnerResult(
            status="timeout",
            exception_type="TimeoutError",
            exception_message=(
                f"execution exceeded the hard subprocess timeout of {subprocess_timeout:g}s"
            ),
        )
    except OSError as exc:
        # Most commonly FileNotFoundError: `docker` isn't on PATH. Could
        # also be a permissions error talking to the Docker socket. Either
        # way, this is an operational problem with the execution backend
        # itself, not something about the submitted code — reported the
        # same way as any other internal_error.
        return RunnerResult(
            status="internal_error",
            exception_type="DockerUnavailable",
            exception_message=(
                f"could not launch Docker ({settings.docker_binary}): {exc}. "
                "Ensure Docker is installed, the daemon is running, and the "
                f"runner image ({settings.docker_runner_image}) has been built — "
                "see runner/build.sh."
            ),
        )
    finally:
        # Best-effort cleanup on the happy path too: --rm should already
        # have removed the container, so this is normally a harmless no-op
        # ("no such container"). It exists so a container that exited in
        # some unusual way (e.g. force-killed by something other than the
        # branch above) can never be left behind.
        _force_remove_container(settings, container_name)

    return parse_runner_stdout(completed.stdout, completed.stderr, completed.returncode)


def _build_docker_run_command(settings, container_name: str) -> list[str]:
    return [
        settings.docker_binary,
        "run",
        "--rm",
        "--name",
        container_name,
        "-i",
        "--network",
        "none",
        "--read-only",
        "--user",
        f"{settings.docker_runner_uid}:{settings.docker_runner_gid}",
        "--memory",
        f"{settings.docker_runner_memory_mb}m",
        "--memory-swap",
        f"{settings.docker_runner_memory_mb}m",  # == --memory: disables swap
        "--memory-swappiness",
        "0",
        "--cpus",
        settings.docker_runner_cpus,
        "--pids-limit",
        str(settings.docker_runner_pids_limit),
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges",
        settings.docker_runner_image,
    ]


def _force_remove_container(settings, container_name: str) -> None:
    try:
        subprocess.run(
            [settings.docker_binary, "rm", "-f", container_name],
            capture_output=True,
            timeout=10,
        )
    except (subprocess.TimeoutExpired, OSError):
        # Cleanup is best-effort. If Docker itself is unavailable (the
        # OSError path above already reported that), there's nothing more
        # useful to do here than silently accept it.
        pass
