# runner/

Contains everything involved in executing submitted code: the shared
execution harness (`runner.py`), the container image that now runs it by
default (`Dockerfile`), and a build script.

## Execution backends

| Backend | Where code runs | Security sandbox? | Used when |
|---|---|---|---|
| **Docker** (default) | Ephemeral, locked-down container | Meaningful, but not perfect — see below | `EXECUTION_BACKEND=docker` (default) |
| Bare subprocess | Plain OS process on the backend host | **No** | `EXECUTION_BACKEND=subprocess` — fallback for environments without Docker installed |

Both backends launch the *exact same* `runner.py` script and speak the
exact same JSON-over-stdin/stdout protocol — see `runner.py`'s own module
docstring for the wire format. Only how that script is isolated differs.

## Building the image

```bash
./runner/build.sh
# or, equivalently, from anywhere in the repo:
docker build -t ai-code-breaker-runner:latest -f runner/Dockerfile runner/
```

The backend expects the image to be tagged `ai-code-breaker-runner:latest`
(overridable via `DOCKER_RUNNER_IMAGE`). Nothing builds this automatically
yet — run it once locally before using the Docker backend or its tests.

## What isolates the container

All of the actual security boundary is in the `docker run` flags applied
by `apps/backend/app/services/docker_runner_service.py`, not in the image
itself:

| Requirement | Flag(s) | Why |
|---|---|---|
| No network access | `--network none` | The container has no route anywhere, including DNS — connection attempts fail immediately rather than hanging. |
| Non-root user | `--user 10001:10001` | Matches the non-root `USER` baked into the image (belt-and-suspenders). |
| Read-only root filesystem | `--read-only` | Submitted code cannot modify anything in the container's own filesystem. |
| Writable temp dir only when required | *(not mounted — see below)* | Nothing in the current execution model needs to write anything. |
| Memory limit | `--memory`, `--memory-swap` (equal to `--memory`, disabling swap), `--memory-swappiness 0` | A runaway allocation gets OOM-killed by the kernel/cgroup instead of exhausting host memory or being bypassed via swap. |
| CPU limit | `--cpus` | Bounds how much host CPU one execution can consume. |
| Process limit | `--pids-limit` | The actual fork-bomb defense — caps total process count regardless of what the code tries to spawn. |
| Strict wall-clock timeout | Internal `SIGALRM` in `runner.py`, **plus** `subprocess.run(..., timeout=...)` around the whole `docker run`, **plus** an explicit `docker rm -f` if that fires | Defense in depth: the internal timeout produces a clean, structured `timeout` result; the outer layers guarantee the container dies even if the internal one somehow doesn't fire. |
| Dropped capabilities | `--cap-drop ALL` | Removes every Linux capability (`CAP_NET_RAW`, `CAP_SYS_ADMIN`, etc.) the container process would otherwise have. |
| No privileged mode | *(`--privileged` is never passed, anywhere)* | — |
| No host directory mounts | *(no `-v` / `--mount` flags at all)* | The container cannot see any host file, sensitive or not — there's nothing to mount. |
| Bounded stdout/stderr | Enforced inside `runner.py` itself (`_CappedWriter`) | Docker has no native "cap output size" flag; this is an application-layer control, same as the subprocess backend. |
| Structured JSON I/O | `runner.py`'s stdin/stdout protocol, unchanged | — |
| Container removed after execution | `--rm`, plus an explicit `docker rm -f <name>` backstop on the timeout path | `--rm` alone doesn't fire if we had to forcibly detach from a hung `docker run`; the backstop guarantees no leftover containers accumulate. |

### Why no writable temp directory by default

The current execution model is a pure function call — JSON in, JSON out —
and nothing about it needs to touch the filesystem. Rather than mount a
writable `tmpfs` "just in case," the default is fully read-only, which
also makes a filesystem write attempt fail deterministically and
observably (see the tests). `DockerRunner` has room for a size-capped
`tmpfs` mount to be added if a future milestone genuinely needs scratch
space; it isn't wired up because nothing needs it yet.

## ⚠️ This is not perfect isolation — documented risks

Docker containers are a real, substantial improvement over a bare
subprocess, but they are **not** a security boundary as strong as a
hardware VM, and this project does not claim otherwise:

- **Shared kernel.** All containers on a host share one Linux kernel.
  A kernel vulnerability (there have been several real ones enabling
  container escapes: e.g. CVE-2019-5736 in runc, CVE-2022-0185, various
  cgroup/namespace bugs) can let code inside a container affect the host.
  Docker cannot patch a kernel bug for you.
- **cgroups and namespaces are complex, evolving subsystems.** Bugs in
  their implementation have historically allowed sandbox escapes. Keeping
  the host's Docker/kernel version patched is a real, ongoing
  responsibility this project does not automate.
- **The Docker daemon itself typically runs as root on the host.** This
  project's FastAPI process only ever *shells out to the `docker` CLI* —
  it never talks to the Docker socket directly or runs as root itself —
  but whatever has access to the Docker daemon on this host effectively
  has significant power over that host. Restricting who/what can invoke
  `docker` is a host-level concern outside this project's code.
- **Side channels.** CPU/cache timing side channels between containers on
  the same host are a known, hard-to-fully-close class of issue; none of
  the flags above address them.
- **Resource limits reduce, but don't eliminate, denial-of-service risk**
  against the host running many containers concurrently (e.g. many
  submissions analyzed at once). `MAX_CONCURRENT_RUNNERS` in
  `comparison_service.py` bounds how many run at once from this
  application, but a host running many *separate* application instances
  would need its own capacity planning.
- **This has not been audited or red-teamed.** It reflects a reasonable,
  documented set of standard hardening flags — not a guarantee.

**Bottom line:** this is appropriate for a personal project or a small,
trusted set of users. It is explicitly **not** the "public deployment
configuration" the project will eventually need — that's a distinct,
later milestone (rate limiting, resource quotas across concurrent users,
monitoring, likely additional isolation such as gVisor/Firecracker for
genuinely adversarial multi-tenant traffic).

## Status

- **Now:** Docker is the default execution backend (`EXECUTION_BACKEND=docker`).
  The bare-subprocess backend (`runner_service.py`) still exists as an
  explicit, documented fallback for environments without Docker — it is
  **not** a security sandbox; see its own module docstring.
- **Later:** rate limiting and resource quotas across concurrent users,
  monitoring/alerting on container behavior, and — for genuinely
  adversarial public traffic — stronger isolation than cgroups/namespaces
  alone (e.g. gVisor, Firecracker microVMs).
