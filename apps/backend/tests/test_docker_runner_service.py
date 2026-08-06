"""
Tests for app.services.docker_runner_service.execute_submission_docker.

Two tiers:
  1. `test_docker_unavailable_returns_internal_error_not_a_crash` — runs in
     ANY environment, Docker or not. It deliberately points at a
     nonexistent `docker` binary to force the "Docker unavailable" code
     path deterministically, rather than relying on Docker actually being
     absent (which happens to be true in this project's own dev sandbox,
     but shouldn't be a silent assumption baked into the test).
  2. Everything else requires a real Docker daemon AND the runner image
     built (`./runner/build.sh`) — each test skips itself with a clear
     reason via docker_test_support if either is unavailable, rather than
     failing.
"""

import subprocess

import pytest

from app.core.config import get_settings
from app.services.docker_runner_service import execute_submission_docker
from tests.docker_test_support import (
    DOCKER_IMAGE_UNAVAILABLE_REASON,
    docker_runner_image_available,
)

requires_runner_image = pytest.mark.skipif(
    not docker_runner_image_available(), reason=DOCKER_IMAGE_UNAVAILABLE_REASON
)

SHORT_TIMEOUT = 3.0


# --- Docker-unavailable fallback: runs everywhere ---------------------------


def test_docker_unavailable_returns_internal_error_not_a_crash(docker_backend, monkeypatch):
    monkeypatch.setenv("DOCKER_BINARY", "/nonexistent/docker-binary-that-does-not-exist")
    get_settings.cache_clear()

    result = execute_submission_docker(
        source_code="def f(xs):\n    return xs\n",
        function_name="f",
        input_=[1, 2, 3],
    )

    assert result.status == "internal_error"
    assert result.exception_type == "DockerUnavailable"
    assert "docker" in result.exception_message.lower()


# --- Required adversarial test suite (needs Docker + the built image) ------


@requires_runner_image
def test_normal_function_succeeds(docker_backend):
    result = execute_submission_docker(
        source_code="def add_all(xs):\n    return sum(xs)\n",
        function_name="add_all",
        input_=[1, 2, 3, 4],
    )
    assert result.status == "success"
    assert result.return_value == 10


@requires_runner_image
def test_infinite_loop_times_out(docker_backend):
    result = execute_submission_docker(
        source_code="def spins(xs):\n    while True:\n        pass\n",
        function_name="spins",
        input_=[1],
        timeout_seconds=SHORT_TIMEOUT,
    )
    assert result.status == "timeout"


@requires_runner_image
def test_massive_allocation_attempt_is_contained(docker_backend):
    """Tries to allocate far more memory than --memory allows. The
    container's own kernel/cgroup memory limit should kill it (or Python
    itself should raise MemoryError) well before it could affect the host
    or exceed the wall-clock timeout."""
    result = execute_submission_docker(
        source_code=(
            "def foo(xs):\n"
            "    huge = bytearray(50 * 1024 * 1024 * 1024)  # 50 GiB\n"
            "    return len(huge)\n"
        ),
        function_name="foo",
        input_=[1],
        timeout_seconds=SHORT_TIMEOUT + 5,
    )
    # Either the container was OOM-killed (empty output -> internal_error,
    # commonly exit code 137) or Python itself raised MemoryError before
    # the kernel had to intervene. Both are acceptable outcomes of
    # "contained" — what must NOT happen is `success` (the allocation
    # actually succeeding) or the host being affected.
    assert result.status in ("internal_error", "runtime_error", "timeout")
    if result.status == "runtime_error":
        assert result.exception_type == "MemoryError"


@requires_runner_image
def test_process_spawning_attempt_is_contained(docker_backend):
    """A fork bomb. --pids-limit must cap runaway process growth — the
    call must not hang the host or spawn unbounded processes."""
    result = execute_submission_docker(
        source_code=(
            "import os\n"
            "def foo(xs):\n"
            "    n = 0\n"
            "    try:\n"
            "        while True:\n"
            "            os.fork()\n"
            "            n += 1\n"
            "    except OSError as exc:\n"
            "        return n\n"
        ),
        function_name="foo",
        input_=[1],
        timeout_seconds=SHORT_TIMEOUT + 5,
    )
    # The fork loop must be stopped by the pids limit (returning a small
    # bounded count via the except clause) rather than succeeding
    # unbounded or hanging until the timeout.
    assert result.status in ("success", "runtime_error")
    if result.status == "success":
        assert isinstance(result.return_value, int)
        assert result.return_value < 100  # nowhere near unbounded growth


@requires_runner_image
def test_filesystem_write_attempt_fails(docker_backend):
    result = execute_submission_docker(
        source_code=(
            "def foo(xs):\n"
            "    with open('/tmp/should_not_be_writable.txt', 'w') as f:\n"
            "        f.write('escaped the sandbox')\n"
            "    return 1\n"
        ),
        function_name="foo",
        input_=[1],
    )
    assert result.status == "runtime_error"
    assert result.exception_type in ("OSError", "PermissionError", "FileNotFoundError")


@requires_runner_image
def test_network_access_attempt_fails(docker_backend):
    result = execute_submission_docker(
        source_code=(
            "import socket\n"
            "def foo(xs):\n"
            "    s = socket.create_connection(('8.8.8.8', 53), timeout=2)\n"
            "    return 1\n"
        ),
        function_name="foo",
        input_=[1],
        timeout_seconds=SHORT_TIMEOUT + 2,
    )
    # --network none means there's no route to anywhere; this should fail
    # fast with a network-related OSError, not succeed and not merely time
    # out waiting on a connection that was never possible.
    assert result.status == "runtime_error"
    assert result.exception_type in ("OSError", "socket.gaierror", "gaierror", "TimeoutError")


@requires_runner_image
def test_excessive_printing_is_capped(docker_backend):
    result = execute_submission_docker(
        source_code=(
            "def foo(xs):\n"
            "    for _ in range(200000):\n"
            "        print('x' * 100)\n"
            "    return 1\n"
        ),
        function_name="foo",
        input_=[1],
        timeout_seconds=SHORT_TIMEOUT + 5,
    )
    assert result.status == "success"
    assert len(result.stdout) < 5_000
    assert "truncated" in result.stdout


@requires_runner_image
def test_syntax_error_is_reported(docker_backend):
    result = execute_submission_docker(
        source_code="def broken(xs:\n    return xs\n",
        function_name="broken",
        input_=[1, 2, 3],
    )
    assert result.status == "syntax_error"
    assert result.exception_type == "SyntaxError"


@requires_runner_image
def test_runtime_exception_is_reported(docker_backend):
    result = execute_submission_docker(
        source_code="def crashes(xs):\n    return xs[999]\n",
        function_name="crashes",
        input_=[1, 2, 3],
    )
    assert result.status == "runtime_error"
    assert result.exception_type == "IndexError"


@requires_runner_image
def test_container_is_removed_after_execution(docker_backend):
    """Runs a few calls, then confirms no acb-runner-* containers are left
    behind — proving --rm (plus the cleanup backstop) actually works."""
    settings = get_settings()
    for _ in range(3):
        execute_submission_docker(
            source_code="def f(xs):\n    return xs\n", function_name="f", input_=[1]
        )

    listing = subprocess.run(
        [settings.docker_binary, "ps", "-a", "--filter", "name=acb-runner-", "--format", "{{.Names}}"],
        capture_output=True,
        text=True,
        timeout=10,
    )
    leftover = [name for name in listing.stdout.splitlines() if name.strip()]
    assert leftover == [], f"leftover containers were not cleaned up: {leftover}"


@requires_runner_image
def test_negative_integers_and_empty_list_still_work(docker_backend):
    result = execute_submission_docker(
        source_code="def foo(xs):\n    return sum(xs)\n",
        function_name="foo",
        input_=[-10, -1, 5],
    )
    assert result.status == "success"
    assert result.return_value == -6

    result = execute_submission_docker(
        source_code="def foo(xs):\n    return len(xs)\n",
        function_name="foo",
        input_=[],
    )
    assert result.status == "success"
    assert result.return_value == 0
