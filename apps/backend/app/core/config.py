"""
Application configuration, loaded from environment variables.

New settings should be added here in the milestone that actually
introduces them, not pre-declared ahead of time.

Path resolution note: this module must import cleanly both from a checkout
(where the tree is .../ai-code-breaker/apps/backend/app/core/config.py, with
the repo root several levels up) AND inside the Docker image (where only
`app/` is copied under /app, so the tree is /app/app/core/config.py and the
repo root does not exist above it). We therefore never assume a fixed parent
depth. The repo root is discovered best-effort by walking up for a marker,
and every path is overridable via an environment variable so no layout is
load-bearing.
"""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


def _find_repo_root(start: Path) -> Path | None:
    """Walk up from `start` looking for a directory that looks like the repo
    root (contains a 'runner' dir or a '.git' dir). Returns None if no such
    ancestor exists — e.g. inside the container image, where only `app/` was
    copied. Never raises and never indexes a fixed depth."""
    for parent in [start, *start.parents]:
        if (parent / "runner").is_dir() or (parent / ".git").is_dir():
            return parent
    return None


def _default_runner_script_path() -> Path:
    """Best-effort default location of the local runner script.

    Order of preference:
      1. RUNNER_SCRIPT_PATH env var (handled by the Settings field, not here).
      2. The repo's runner/runner.py, if a repo root can be found.
      3. A path relative to the current working directory as a last resort.

    This is only actually read when EXECUTION_BACKEND=subprocess AND an
    analysis runs; the value being a non-existent path in the container is
    harmless because the container uses the docker backend (worker) or never
    invokes the local script (api). So we return a sensible placeholder rather
    than raising at import time.
    """
    module_dir = Path(__file__).resolve().parent
    repo_root = _find_repo_root(module_dir)
    if repo_root is not None:
        return repo_root / "runner" / "runner.py"
    # No repo root above us (container image). Fall back to a CWD-relative
    # path; this is only used if someone runs the subprocess backend from a
    # checkout, in which case CWD is typically the repo or apps/backend.
    return Path("runner") / "runner.py"


# Optional .env file. We look for one next to the current working directory
# and at the repo root (if discoverable), using the first that exists. A
# missing .env is fine — pydantic-settings treats a non-existent env_file as
# optional, so container startup (no .env present) does not break.
def _optional_env_file() -> str | None:
    candidates = [Path.cwd() / ".env"]
    repo_root = _find_repo_root(Path(__file__).resolve().parent)
    if repo_root is not None:
        candidates.append(repo_root / ".env")
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    # Returning ".env" (a relative name) is also safe — pydantic-settings
    # just won't find it and moves on — but returning None is explicit about
    # "no env file", which is the common container case.
    return None


_DEFAULT_RUNNER_SCRIPT_PATH = _default_runner_script_path()
_ENV_FILE = _optional_env_file()


class Settings(BaseSettings):
    # Comma-separated list of origins allowed to call this API, e.g.
    # "http://localhost:3000,http://127.0.0.1:3000"
    cors_allow_origins_raw: str = Field(
        default="http://localhost:3000", validation_alias="CORS_ALLOW_ORIGINS"
    )

    # Path to the temporary local runner script (see runner/runner.py).
    # Overridable so tests or alternate layouts don't depend on repo shape.
    runner_script_path: Path = Field(
        default=_DEFAULT_RUNNER_SCRIPT_PATH, validation_alias="RUNNER_SCRIPT_PATH"
    )

    # Budget handed to the runner process itself (its internal SIGALRM
    # timeout around the function call), for both execution backends.
    runner_call_timeout_seconds: float = Field(
        default=5.0, validation_alias="RUNNER_CALL_TIMEOUT_SECONDS"
    )

    # Extra time given to subprocess.run()'s own `timeout=` on top of
    # runner_call_timeout_seconds. This is the hard backstop: if the
    # runner's internal SIGALRM handling ever fails to fire, the parent
    # process still forcibly kills the subprocess (or, for the Docker
    # backend, the container) after this much extra time rather than
    # waiting on it indefinitely.
    runner_subprocess_timeout_buffer_seconds: float = Field(
        default=2.0, validation_alias="RUNNER_SUBPROCESS_TIMEOUT_BUFFER_SECONDS"
    )

    # Which execution backend app.services.comparison_service uses.
    # "docker" is the intended, secure default — see runner/README.md.
    # "subprocess" exists for environments without Docker installed
    # (including this project's own dev/test sandbox) and is explicitly
    # NOT a security sandbox; it should never be used for a deployed
    # instance of this project.
    execution_backend: Literal["docker", "subprocess"] = Field(
        default="docker", validation_alias="EXECUTION_BACKEND"
    )

    # Name of the `docker` executable/path. Overridable mainly so tests
    # can force a "Docker is unavailable" condition deterministically,
    # regardless of whether Docker actually happens to be installed on
    # the machine running the tests.
    docker_binary: str = Field(default="docker", validation_alias="DOCKER_BINARY")

    # Tag of the image built from runner/Dockerfile (see runner/build.sh).
    docker_runner_image: str = Field(
        default="ai-code-breaker-runner:latest", validation_alias="DOCKER_RUNNER_IMAGE"
    )

    # Must match the uid/gid baked into runner/Dockerfile's `runner` user.
    docker_runner_uid: int = Field(default=10001, validation_alias="DOCKER_RUNNER_UID")
    docker_runner_gid: int = Field(default=10001, validation_alias="DOCKER_RUNNER_GID")

    docker_runner_memory_mb: int = Field(
        default=256, validation_alias="DOCKER_RUNNER_MEMORY_MB"
    )
    docker_runner_cpus: str = Field(default="1.0", validation_alias="DOCKER_RUNNER_CPUS")
    # The actual fork-bomb / process-spawning defense. 16 comfortably
    # covers a single well-behaved Python process (and a small number of
    # incidental child processes) while making runaway growth impossible.
    docker_runner_pids_limit: int = Field(
        default=16, validation_alias="DOCKER_RUNNER_PIDS_LIMIT"
    )

    # SQLAlchemy database URL. Defaults to the local docker-compose
    # Postgres (see infra/docker-compose.yml). Tests override this with
    # DATABASE_URL pointing at a throwaway SQLite file or a separate test
    # Postgres database — see tests/conftest.py.
    database_url: str = Field(
        default="postgresql+psycopg://acb:acb@localhost:5432/ai_code_breaker",
        validation_alias="DATABASE_URL",
    )

    # Emit SQL to logs. Off by default; handy when debugging locally.
    database_echo: bool = Field(default=False, validation_alias="DATABASE_ECHO")

    # --- Claude / Anthropic (targeted test generation) ---
    #
    # AI test generation is entirely optional: if no API key is configured,
    # the app falls back to deterministic + Hypothesis tests and nothing
    # breaks (see app.services.ai_test_generation_service). The key is read
    # from the environment and NEVER logged.
    anthropic_api_key: str | None = Field(
        default=None, validation_alias="ANTHROPIC_API_KEY"
    )
    # Model name is configured via env, per requirements — not hardcoded.
    anthropic_model: str = Field(
        default="claude-sonnet-4-5", validation_alias="ANTHROPIC_MODEL"
    )
    # Per-request wall-clock timeout (seconds) for the Anthropic client.
    anthropic_timeout_seconds: float = Field(
        default=30.0, validation_alias="ANTHROPIC_TIMEOUT_SECONDS"
    )
    # Cap on how many tokens Claude may generate per targeted-generation
    # request. Test inputs are small, so this stays modest.
    anthropic_max_tokens: int = Field(
        default=1024, validation_alias="ANTHROPIC_MAX_TOKENS"
    )
    # Upper bound on how many tests a single AI call may contribute, before
    # the shared MAX_TOTAL_TESTS cap in test selection also applies.
    ai_max_generated_tests: int = Field(
        default=8, validation_alias="AI_MAX_GENERATED_TESTS"
    )

    # --- Redis / job queue (async analysis) ---
    #
    # Analysis runs as a background job on an RQ worker (see
    # app.worker). The API enqueues the job and returns immediately; the
    # worker updates status/progress on the AnalysisRun row as it goes.
    redis_url: str = Field(
        default="redis://localhost:6379/0", validation_alias="REDIS_URL"
    )
    # Name of the RQ queue analysis jobs are placed on.
    analysis_queue_name: str = Field(
        default="analysis", validation_alias="ANALYSIS_QUEUE_NAME"
    )
    # When true, jobs run inline/synchronously instead of being enqueued to
    # a real worker. Used by tests (fakeredis + synchronous execution) and
    # handy for local debugging without a worker process. See
    # app.queue.get_queue.
    queue_eager: bool = Field(default=False, validation_alias="QUEUE_EAGER")

    # Overall wall-clock ceiling for a whole analysis job (seconds). RQ
    # kills the job if it exceeds this. A hard backstop above the per-stage
    # budgets below.
    job_timeout_seconds: int = Field(
        default=600, validation_alias="JOB_TIMEOUT_SECONDS"
    )
    # How long a finished job's result/metadata is retained in Redis.
    job_result_ttl_seconds: int = Field(
        default=86_400, validation_alias="JOB_RESULT_TTL_SECONDS"
    )
    # Per-stage soft timeouts (seconds). A stage exceeding its budget raises
    # a StageTimeout, which fails the run cleanly with a recorded error
    # rather than hanging. Generous defaults; tune per deployment.
    stage_timeout_generating_tests: int = Field(
        default=60, validation_alias="STAGE_TIMEOUT_GENERATING_TESTS"
    )
    stage_timeout_executing_tests: int = Field(
        default=180, validation_alias="STAGE_TIMEOUT_EXECUTING_TESTS"
    )
    stage_timeout_searching_properties: int = Field(
        default=120, validation_alias="STAGE_TIMEOUT_SEARCHING_PROPERTIES"
    )
    stage_timeout_minimizing: int = Field(
        default=120, validation_alias="STAGE_TIMEOUT_MINIMIZING"
    )
    stage_timeout_explaining: int = Field(
        default=60, validation_alias="STAGE_TIMEOUT_EXPLAINING"
    )
    # Max retry attempts for jobs that fail with a TRANSIENT error (e.g. a
    # Redis blip or a provider timeout). User-code failures are never
    # retried — see app.worker.analysis_job.
    job_max_retries: int = Field(default=2, validation_alias="JOB_MAX_RETRIES")

    # --- Security / abuse limits ---
    #
    # Maximum HTTP request body size in bytes. Requests larger than this are
    # rejected with 413 before the body is processed, independent of the
    # per-field schema limits. Default ~256 KB comfortably fits two
    # 20k-char source files plus spec and manual inputs, with headroom.
    max_request_body_bytes: int = Field(
        default=262_144, validation_alias="MAX_REQUEST_BODY_BYTES"
    )
    # Token-bucket rate limit for state-changing endpoints (submission and
    # analysis creation), per client identity: `rate_limit_per_minute`
    # sustained requests, bursting up to `rate_limit_burst`.
    rate_limit_per_minute: int = Field(
        default=20, validation_alias="RATE_LIMIT_PER_MINUTE"
    )
    rate_limit_burst: int = Field(default=10, validation_alias="RATE_LIMIT_BURST")
    # Whether rate limiting is enforced. Disabled in tests for determinism
    # unless a test opts in.
    rate_limit_enabled: bool = Field(
        default=True, validation_alias="RATE_LIMIT_ENABLED"
    )
    # Maximum in-flight (non-terminal) analyses a single identity may have at
    # once. Further creations are rejected with 429 until some finish.
    max_concurrent_analyses_per_client: int = Field(
        default=3, validation_alias="MAX_CONCURRENT_ANALYSES_PER_CLIENT"
    )
    # Total lifetime analyses an anonymous client may create (coarse
    # anonymous quota). 0 disables the quota.
    anonymous_analysis_quota: int = Field(
        default=100, validation_alias="ANONYMOUS_ANALYSIS_QUOTA"
    )
    # Header a client may send to identify itself for quota/rate-limit
    # purposes. Not a secret and not authentication — just a stable key so a
    # well-behaved client isn't lumped in with everyone behind its IP.
    client_id_header: str = Field(
        default="X-Client-Id", validation_alias="CLIENT_ID_HEADER"
    )

    # --- Observability ---
    # Log level and format. Format 'json' emits one structured object per
    # line (production); 'text' is human-readable (development).
    log_level: str = Field(default="INFO", validation_alias="LOG_LEVEL")
    log_format: str = Field(default="json", validation_alias="LOG_FORMAT")

    # Load the discovered optional .env file (None inside the container,
    # where configuration comes purely from environment variables). A missing
    # env file is not an error.
    model_config = SettingsConfigDict(env_file=_ENV_FILE, populate_by_name=True)

    @property
    def anthropic_configured(self) -> bool:
        """True only if an API key is present. Callers use this to decide
        whether to attempt AI generation at all — absence is a normal,
        supported state, not an error."""
        return bool(self.anthropic_api_key)

    @property
    def cors_allow_origins(self) -> list[str]:
        return [
            origin.strip()
            for origin in self.cors_allow_origins_raw.split(",")
            if origin.strip()
        ]


@lru_cache
def get_settings() -> Settings:
    """Cached so Settings is only constructed/parsed once per process."""
    return Settings()
