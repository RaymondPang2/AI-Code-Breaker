# AI Code Breaker — Technical Reference

> Deep implementation reference. For the project overview, architecture diagrams, benchmark results, and setup, see the [README](../README.md). For interview-style explanations of the design decisions, see [interview-notes.md](interview-notes.md).

Given a natural-language function specification, a candidate Python
implementation, and a correct reference Python implementation, AI Code
Breaker generates test inputs, safely executes both implementations in
isolation, finds inputs where their behavior differs, minimizes the failing
input, and explains the confirmed bug.

Claude may *propose* test inputs and explanations, but a deterministic
comparison against the reference implementation is what decides pass/fail.
Submitted code is never executed inside the API server process.

## Status

**Comparison engine v8 — with Claude-generated counterexample
explanations.** `POST /submissions/analyze` runs candidate vs. reference,
persists every run to PostgreSQL, and optionally (a) asks Claude to propose
targeted test inputs (`use_ai_tests`) and (b) asks Claude to *explain* a
confirmed counterexample (`explain_counterexamples`). Claude is only ever
called after real execution has confirmed a mismatch; it explains the bug
and never decides pass/fail or overwrites the verified results. When Claude
is unavailable, a deterministic fallback explanation is produced instead,
so analysis never depends on the API being up. An optional suggested patch
is offered strictly as a proposal — never applied automatically. Stored
runs are retrievable via `GET /submissions/{id}` and
`GET /submissions/{id}/analyses/{analysis_id}`.

## Monorepo layout

```
apps/
  frontend/   Next.js, TypeScript, Tailwind CSS
  backend/    FastAPI, Python, Pydantic, pytest, Hypothesis, SQLAlchemy, Alembic, Anthropic SDK
runner/       execution harness + Docker image (see runner/README.md)
infra/        local infrastructure — PostgreSQL via docker-compose
```

## Stack

- Frontend: Next.js, TypeScript, Tailwind CSS, Monaco Editor (added later)
- Backend: Python, FastAPI, Pydantic
- Database: PostgreSQL via SQLAlchemy 2.x + Alembic migrations (see
  `infra/` for local Postgres)
- Testing: pytest (with an isolated SQLite test database); a deterministic
  categorized generator (`TestCaseGenerator`); Hypothesis for
  property-based differential search
- Code execution: ephemeral Docker containers by default (see
  `runner/README.md` for the security model and documented limitations);
  a bare-subprocess fallback exists for environments without Docker but is
  explicitly not a security sandbox
- AI: Claude via the official Anthropic Python SDK, for targeted test-input
  generation only (proposes inputs, never judges correctness) — optional
  and configured through environment variables


## Running locally

Run each app directly. The backend needs a PostgreSQL database and a Redis
instance (for the analysis job queue), both of which `infra/` provides via
docker-compose.

### Infrastructure (Postgres + Redis)

```bash
# from the repo root — starts PostgreSQL (5432) and Redis (6379)
docker compose -f infra/docker-compose.yml up -d postgres redis
```

Default credentials/URLs match the defaults in
`apps/backend/app/core/config.py`, so no extra configuration is needed. See
`infra/README.md`. (The same compose file also defines full `api` and
`worker` services if you'd rather run everything in containers — see the
"Async job workflow" section below.)

### Backend

```bash
cd apps/backend
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp ../../.env.example .env   # optional, defaults already work locally
alembic upgrade head          # applies all migrations (through 0003)
uvicorn app.main:app --reload
```

Backend runs at http://localhost:8000 — check http://localhost:8000/health.

### Worker

Analysis runs as a background job on an RQ worker. Start one in a second
terminal (same virtualenv/env as the backend):

```bash
cd apps/backend
source .venv/bin/activate
python -m app.worker.run_worker
```

The worker connects to Redis (`REDIS_URL`), pulls analysis jobs off the
queue, and updates each run's status/progress as it goes. For quick local
debugging without a worker, set `QUEUE_EAGER=1` and jobs run inline in the
API process instead (this is also how the test suite runs).

### Frontend

```bash
cd apps/frontend
npm install
npm run dev
```

Frontend runs at http://localhost:3000 and shows whether it can reach the
backend's `/health` endpoint.

## API

> **Note (portfolio review):** the older synchronous `POST /submissions/analyze`
> and `POST /submissions/search` endpoints have been **removed**. They bypassed
> the rate-limiting/quota controls and ran code execution inside the API
> process. All analysis now goes through the asynchronous job flow —
> `POST /submissions` → `POST /submissions/{id}/analyses` →
> `GET /submissions/{id}/analyses/{analysis_id}` — documented under
> "Async job workflow" below. Some examples in the older sections that follow
> reference the removed endpoints and are retained only as conceptual
> background; use the async flow in practice.

### `POST /submissions/validate`

Validates and normalizes a submission's shape. Does not execute any code.

### `POST /submissions/analyze`

Runs a candidate and a reference implementation on every selected test
input — each inside its own ephemeral Docker container by default (see
`runner/`) — and compares their results. Every function submitted here is
assumed to accept exactly one positional `list[int]` argument.

Test inputs come from up to three optional sources, combined:
- `test_inputs`: manually supplied inputs, run in the order given.
- `generate_tests` (default `false`) plus `generation_seed` (default `0`):
  if enabled, `TestCaseGenerator` produces one deterministic input per
  required category (empty list, singleton, duplicate maximum, sorted,
  reverse-sorted, integer boundaries, and more — see below) and appends
  any not already covered by a manual input. The same seed always
  produces the same generated inputs. Generation is opt-in so the number
  of executions for a request is always exactly what you asked for unless
  you turn it on.
- `use_ai_tests` (default `false`): if enabled and Claude is configured,
  Claude proposes additional targeted inputs (see "AI-generated targeted
  tests" below). These are validated, deduped, appended last, and run
  through the same comparison engine — purely additive, never displacing
  manual or deterministic inputs.

**Persistence:** each call is saved to PostgreSQL — the submission, its
test cases, every execution (candidate + reference per input), and any
counterexample. The response includes `submission_id` and
`analysis_run_id`, which you can use with the GET endpoints below to fetch
the stored result later.

**⚠️ Security note:** code submitted to this endpoint runs inside an
ephemeral, locked-down Docker container by default (see `runner/README.md`
for the security model *and its documented limitations*). A bare-subprocess
fallback exists for environments without Docker but is not a security
sandbox. Either way, don't expose this API to untrusted public traffic
without the additional hardening described in `runner/README.md`.

#### Example: implementations that agree

```python
# Candidate and reference (identical here, so every input should match)
def double_all(xs):
    return [x * 2 for x in xs]
```

```bash
curl -X POST http://localhost:8000/submissions/analyze \
  -H "Content-Type: application/json" \
  -d '{
    "function_name": "double_all",
    "specification": "Return a new list with every element doubled.",
    "candidate_code": "def double_all(xs):\n    return [x * 2 for x in xs]\n",
    "reference_code": "def double_all(xs):\n    return [x * 2 for x in xs]\n",
    "test_inputs": [[1, 2, 3], [], [-5, 0, 5]]
  }'
```

Every comparison matches; `failed_tests` is `0` and `first_failing_input`
is `null`.

#### Example: a real bug, caught

```python
# Candidate — looks reasonable, but breaks on duplicate values
def second_largest(values):
    return sorted(values)[-2]
```

```python
# Reference — correctly requires at least two *distinct* values
def second_largest(values):
    unique = sorted(set(values))
    if len(unique) < 2:
        raise ValueError("Need at least two distinct values")
    return unique[-2]
```

```bash
curl -X POST http://localhost:8000/submissions/analyze \
  -H "Content-Type: application/json" \
  -d '{
    "function_name": "second_largest",
    "specification": "Return the second largest distinct value in the list.",
    "candidate_code": "def second_largest(values):\n    return sorted(values)[-2]\n",
    "reference_code": "def second_largest(values):\n    unique = sorted(set(values))\n    if len(unique) < 2:\n        raise ValueError(\"Need at least two distinct values\")\n    return unique[-2]\n",
    "test_inputs": [[3, 1, 2], [5, 5, 5]]
  }'
```

`[3, 1, 2]` matches on both sides (`2`). `[5, 5, 5]` exposes the bug: the
candidate returns `5` (a duplicate), while the reference correctly raises
`ValueError`. The response reports `failed_tests: 1` and
`first_failing_input: [5, 5, 5]`.

#### Example: the same bug, found without writing a single test by hand

Same candidate/reference as above, but no manual `test_inputs` at all —
`generate_tests` does all the work:

```bash
curl -X POST http://localhost:8000/submissions/analyze \
  -H "Content-Type: application/json" \
  -d '{
    "function_name": "second_largest",
    "specification": "Return the second largest distinct value in the list.",
    "candidate_code": "def second_largest(values):\n    return sorted(values)[-2]\n",
    "reference_code": "def second_largest(values):\n    unique = sorted(set(values))\n    if len(unique) < 2:\n        raise ValueError(\"Need at least two distinct values\")\n    return unique[-2]\n",
    "test_inputs": [],
    "generate_tests": true,
    "generation_seed": 0
  }'
```

With `generation_seed: 0`, this reliably finds `failed_tests: 6` out of 15
generated inputs. `first_failing_input` is `[]` (the `empty_list`
category): the candidate raises `IndexError` (indexing past the end of a
too-short sorted list), while the reference deliberately raises
`ValueError` — both fail, but for different reasons, which is itself a
real bug (the candidate never validates its input; the reference's error
contract is a deliberate part of the specification).

The classic version of this bug — the candidate assumes the maximum is
unique — is independently caught by three different categories:

| category | generated input (seed 0) | candidate | reference | match |
|---|---|---|---|---|
| `duplicate_maximum` | `[-25, 20, 20, -24]` | `20` | `-24` | ❌ |
| `all_values_equal` | `[12, 12, 12, 12]` | `12` | raises `ValueError` | ❌ |
| `repeated_patterns` | `[10, 21, 10, 21, 10, 21]` | `21` | `10` | ❌ |

Every one of those is a structurally different way of expressing "the
maximum repeats" — which is exactly the shape of bug `second_largest`
has. No one had to think of `[5, 5, 5]` by hand for the generator to find
it.

### `POST /submissions/search`

Uses [Hypothesis](https://hypothesis.readthedocs.io/) to automatically
search for a `list[int]` input where candidate and reference disagree,
instead of relying on hand-picked or categorized inputs. Hypothesis's role
is strictly limited to *generating* (and shrinking) input values — it
never sees, imports, or executes `candidate_code` / `reference_code`.
Every generated input is executed through the same runner backend
`/submissions/analyze` uses (an ephemeral Docker container by default; see
`runner/README.md`), and compared under the exact same rules (see
`app/services/comparison_rules.py`, shared by both endpoints).

```bash
curl -X POST http://localhost:8000/submissions/search \
  -H "Content-Type: application/json" \
  -d '{
    "function_name": "second_largest",
    "specification": "Return the second largest distinct value in the list.",
    "candidate_code": "def second_largest(values):\n    return sorted(values)[-2]\n",
    "reference_code": "def second_largest(values):\n    unique = sorted(set(values))\n    if len(unique) < 2:\n        raise ValueError(\"Need at least two distinct values\")\n    return unique[-2]\n",
    "max_examples": 80,
    "min_int_value": -5,
    "max_int_value": 5,
    "seed": 2
  }'
```

Request fields (all except the four inherited from `CodeSubmissionBase` —
`function_name`, `specification`, `candidate_code`, `reference_code` — are
optional, conservatively bounded):

| Field | Default | Hard cap | Purpose |
|---|---|---|---|
| `max_examples` | 50 | 200 | How many generated inputs to try. |
| `min_list_size` / `max_list_size` | 0 / 30 | 200 | Bounded list sizes. |
| `min_int_value` / `max_int_value` | -1000 / 1000 | ±10,000 | Bounded integer range. |
| `seed` | none | — | If supplied, the search is fully deterministic — the same seed always finds the same input (or lack of one). Omit it for Hypothesis's normal (non-reproducible-across-runs) behavior. |
| `timeout_seconds` | 30.0 | 120.0 | Overall wall-clock budget for the whole search, including shrinking — not a per-example deadline. See "Hypothesis, shrinking, and the runner" below for why. |
| `apply_deterministic_minimization` | false | — | If true, run the deterministic minimizer (see "Counterexample minimization" below) as an extra pass on any counterexample found. |

Response:

```json
{
  "function_name": "second_largest",
  "counterexample_found": true,
  "minimal_failing_input": [0, 0],
  "candidate_result": { "status": "success", "returned_value": 0, ... },
  "reference_result": { "status": "runtime_error", "exception_type": "ValueError", ... },
  "examples_attempted": 7,
  "elapsed_seconds": 1.84,
  "timed_out": false,
  "seed_used": 2,
  "minimization": null
}
```

When `apply_deterministic_minimization` is true and a counterexample is
found, `minimization` holds the minimizer's result (original input,
minimized input, verification executions, length/complexity reductions,
and stop reason) — see "Counterexample minimization" below.

### Hypothesis, shrinking, and the runner

Hypothesis assumes properties are cheap, in-memory function calls — its
default per-example deadline is 200ms and it expects to comfortably run
thousands of examples. Here, every example costs at least one, usually
two, *out-of-process* runner launches (real container startup for the
Docker backend). Three things reconcile that mismatch:

1. **`deadline=None`.** Hypothesis's per-example timing health check is
   disabled outright rather than tuned, since no fixed per-example value
   would be both safe for Docker's variable startup latency and tight
   enough to be meaningful.
2. **A conservative `max_examples`** (50 default, 200 hard cap) bounds the
   worst case to a predictable number of runner launches.
3. **An overall wall-clock budget, implemented by us, not Hypothesis.**
   Hypothesis intentionally has no built-in "stop the whole run after N
   seconds" setting. The standard workaround — used here — is to check
   elapsed time as the *first* line of the property function and raise a
   dedicated `_TimeBudgetExceeded` exception once the budget is blown,
   caught outside the Hypothesis-driven call. Checking it first matters:
   once tripped, every subsequent call (regardless of which input
   Hypothesis is trying next) raises immediately without launching
   another container, so "wasted" shrink attempts after the budget trips
   cost microseconds, not more container starts.

**Shrinking itself needs no special integration** — Hypothesis shrinks by
calling the property function again with smaller candidates and checking
whether it still raises; whether that call does in-memory computation or
launches a container is invisible to the shrinker. The practical
consequence is entirely about time: shrinking a genuine failure typically
costs dozens of extra calls, each one more container launches, which is
exactly why the overall timeout matters more here than it would for a
typical in-memory Hypothesis property.

**One documented edge case:** if the time budget expires in the narrow
window between finding a real counterexample and Hypothesis internally
replaying it once more to confirm it isn't flaky, that replay now raises
`_TimeBudgetExceeded` instead of the original failure — a different
outcome for the "same" input across two calls, which is exactly what
Hypothesis's own flakiness detector (`hypothesis.errors.Flaky`) exists to
catch. This is handled explicitly: the confirmed counterexample was
already captured in a side-channel the moment it was first found, so the
response is still correct (and `timed_out: true` is set) even if
Hypothesis's own bookkeeping gets disrupted. See the "ARCHITECTURE NOTE"
in `app/services/hypothesis_search_service.py` for the full detail.

### `GET /submissions/{id}`

Fetches a persisted submission by its UUID: function name, specification,
both code samples, and creation time. Returns `404` if no submission has
that ID, `422` if the ID isn't a valid UUID.

### `GET /submissions/{id}/analyses/{analysis_id}`

Fetches one persisted analysis run for a submission, including every
execution (each implementation's sanitized normalized result on each test
case) and any counterexamples. The run is only returned if it actually
belongs to the given submission — a valid `analysis_id` under the wrong
`submission_id` returns `404`, so run IDs can't be read out of context by
guessing.

Only sanitized fields are ever returned: no internal file paths, container
IDs, or raw tracebacks (the stored results were sanitized upstream by
`runner/runner.py`). The read schemas in `app/schemas/persistence.py` are
an explicit allowlist — a field has to be added there on purpose to be
exposed.

## Async job workflow

Analysis runs asynchronously. Rather than block an HTTP request while the
candidate and reference execute (potentially for several seconds across
generation, execution, search, minimization, and explanation), the API
enqueues a job and returns immediately; a worker does the work and updates
the run's status as it goes; the client polls for progress and results.

### Why RQ

The queue is [RQ (Redis Queue)](https://python-rq.org/). This project needs
one thing from a queue — run a Python function on a worker, off the request
path, with retrievable status — and RQ delivers exactly that with Redis as
the only new dependency and plain functions as jobs. There's no
task-definition DSL and no separate result backend to operate. Celery and
Dramatiq are both more capable, but that capability is overhead a
single-worker portfolio tool doesn't need. RQ's `Job` object also gives us
custom job IDs (used here for idempotency) and failure introspection for
free. The rationale lives in `app/queue.py`.

### Flow

1. `POST /submissions` — create a submission (spec + candidate + reference).
   Returns `{ submission_id }` with `201`.
2. `POST /submissions/{id}/analyses` — create an analysis job with the run
   options (`generate_tests`, `use_ai_tests`, `explain_counterexamples`,
   manual `test_inputs`, ...). Returns `{ submission_id, analysis_id,
   status: "queued" }` with `202` **immediately** — no work has happened yet.
3. A worker picks up the job and runs the stages, updating status/progress.
4. `GET /submissions/{id}/analyses/{analysis_id}` — returns the run's
   current `status`, `progress` (0..1), `error` (if failed), and — once
   complete — the full executions and counterexamples.
5. `POST /submissions/{id}/analyses/{analysis_id}/cancel` — request
   cancellation; the worker stops at the next stage boundary.

### Statuses

A run moves through: `queued` → `generating_tests` → `executing_tests` →
`searching_properties` → `minimizing` → `explaining` → `completed`, or ends
in `failed` or `cancelled`. The canonical list and the progress mapping
live in `app/core/analysis_status.py`.

> **Honest status note.** `searching_properties` and `minimizing` are
> currently **status/progress placeholders** on the async path: the run
> advances through them (so the status vocabulary and progress bar are
> complete), but their *results* are not yet wired into the stored run —
> Hypothesis property search lives on its own service path, and
> `minimized_input` stays `null` on the async path rather than being
> fabricated. Wiring these two stages' outputs into the persisted run is a
> known, deliberate follow-up, not a bug.

### Guarantees

- **Progress + errors are stored** on the run (`status`, `progress`,
  `error`, `started_at`, `finished_at`), so a poll always reflects live
  state and a failure carries a short, sanitized reason.
- **Idempotent, no duplicate execution.** The RQ job ID is derived
  deterministically from the run ID (`analysis:{run_id}`), so re-enqueuing
  the same run is a no-op rather than a second execution. The job itself
  no-ops if the run is already terminal, and it clears any partial output
  before rewriting results, so running it more than once converges to the
  same stored state.
- **Per-stage timeouts.** Each stage runs under a soft time budget
  (configurable; see the `STAGE_TIMEOUT_*` settings); exceeding it fails
  the run cleanly instead of hanging. RQ also enforces an overall
  `JOB_TIMEOUT_SECONDS` backstop.
- **Retry only transient failures.** Infrastructure hiccups
  (`TransientJobError` — a Redis/DB blip, a provider timeout) re-raise so
  RQ retries them with backoff. Deterministic failures caused by the user's
  submitted code (`PermanentJobError`) are recorded and *not* retried —
  retrying would only reproduce the same failure. See
  `app/worker/errors.py` and `app/worker/analysis_job.py`.

### Eager mode (tests / local debugging)

Set `QUEUE_EAGER=1` and jobs execute inline in-process instead of being
enqueued to a worker — no Redis or worker needed. The test suite uses this
(`tests/conftest.py`) so the whole async workflow is exercised
deterministically end to end.

## Security and abuse protections

The tool runs user-submitted code, so security is central. The full threat
model, mitigations, and — importantly — the **limitations** live in
[`SECURITY.md`](SECURITY.md). It states plainly that this is an experimental
portfolio project and **not suitable for running highly hostile untrusted
code without stronger sandbox infrastructure**.

Controls implemented (see `SECURITY.md` for the threat-model mapping):

- **Submitted-code isolation** — each input runs in a fresh Docker container
  with no network, a read-only root fs, a non-root user, memory/CPU/pids
  limits, all capabilities dropped, and no privilege escalation.
- **Request limits** — a body-size middleware rejects oversized requests
  (413) before buffering, on top of per-field schema caps.
- **Rate limiting** — a per-client token bucket on submission/analysis
  creation (429 when exceeded).
- **Concurrency + anonymous quota** — a cap on in-flight analyses per client
  and a lifetime quota for anonymous (IP-keyed) clients; identified clients
  send an opt-in `X-Client-Id` header.
- **Per-analysis budgets** — bounded test counts, per-stage and overall job
  timeouts, capped AI token/test budgets, and AI graceful degradation.
- **Secret management** — the Anthropic key is read only from the
  environment, never logged, never returned, never placed in a prompt.
- **Privacy + retention** — stored code is private by default; owners can
  delete a submission and all its data (`DELETE /submissions/{id}`), or mint
  an opt-in public share link with an unguessable token
  (`POST /submissions/{id}/share`, read via `GET /submissions/shared/{token}`).
- **Prompt-injection resistance** — Claude output is advisory only and never
  decides pass/fail or gates any control; verified results come from real
  execution and deterministic comparison.
- **Log/error sanitization** — runner output and job errors are stripped of
  tracebacks, host paths, and container IDs; API errors are short and generic.

New security-related settings are documented in `.env.example`
(`MAX_REQUEST_BODY_BYTES`, `RATE_LIMIT_*`, `MAX_CONCURRENT_ANALYSES_PER_CLIENT`,
`ANONYMOUS_ANALYSIS_QUOTA`, `CLIENT_ID_HEADER`).

## Production deployment

Full instructions, checklists, and cost/backup notes live in
[`DEPLOYMENT.md`](DEPLOYMENT.md). In brief, the target architecture is five
pieces — a separately-hosted **frontend**, a FastAPI **API** service, a
**worker** service, **PostgreSQL**, and **Redis** — with the **Docker runner
available only to the worker**.

- **Images.** Production Dockerfiles for the backend (`apps/backend/Dockerfile`,
  multi-stage, non-root, shared by API and worker) and the frontend
  (`apps/frontend/Dockerfile`, Next.js standalone). Development and production
  configs are kept separate: `infra/docker-compose.yml` (dev) vs.
  `infra/docker-compose.prod.yml` (prod, secrets via env only).
- **Health & readiness.** `GET /health` is a liveness probe (process up, no
  dependency checks — safe for restart decisions). `GET /ready` is a readiness
  probe that verifies the database and Redis and returns 503 if either is
  down, with a per-dependency breakdown — for load-balancer routing.
- **Migrations.** Applied as an explicit, separate step
  (`alembic upgrade head`), never baked into the service command, so an image
  rollback doesn't imply a schema change. See `DEPLOYMENT.md`.
- **Structured logging.** `LOG_FORMAT=json` emits one JSON object per line;
  connection URLs are redacted of credentials and the API key is never logged.
- **CI.** `.github/workflows/ci.yml` runs backend tests, a benchmark smoke
  run, and frontend lint + type-check + test + build on every push/PR — no
  secrets or external infrastructure required (tests use `QUEUE_EAGER=1`,
  SQLite, and the mock AI provider).
- **Secrets & the Docker socket.** No secrets live in the repo or images —
  they're supplied via environment variables at deploy time. The worker's
  Docker-socket mount is **highly privileged** (near-root on the host) and is
  scoped to the worker only; the internet-facing API never receives it. For a
  serious public service, `DEPLOYMENT.md` and `SECURITY.md` recommend a
  stronger remote sandbox (rootless/remote daemon, gVisor, or Firecracker
  microVMs) instead of host-socket execution.

## Counterexample minimization

Hypothesis already shrinks the inputs it finds, but that shrinker is
powerful, internal, and not especially auditable. On top of it,
`app/services/minimizer_service.py` provides a **deterministic, explainable
fallback minimizer** for `list[int]`: a fixed, ordered sequence of
simplification strategies, each producing candidates in a fixed order,
each candidate verified by rerunning both implementations in the isolated
runner. It's opt-in on `/submissions/search` via
`"apply_deterministic_minimization": true`, and also usable directly.

The five strategies, applied in this order and repeated until a full pass
changes nothing (a fixed point):

1. **Remove chunks** — delta-debugging style: remove contiguous chunks,
   halving the chunk size, so big reductions land fast.
2. **Remove individual elements** — one at a time, left to right.
3. **Replace with simpler values** — swap each element toward a canonical
   `0`, then `1`, then `-1` (only ever toward smaller magnitude).
4. **Reduce magnitude** — halve each element toward zero (`100 → 50 → 25 →
   … → 0` across repeated passes).
5. **Remove duplicate occurrences** — for any repeated value, try dropping
   its extra copies while keeping at least one.

Every proposed simplification is only *kept* if the disagreement survives
it — the minimizer re-confirms each step through the runner and never
returns an input it hasn't re-verified still fails. A runner
`internal_error` on a candidate is treated as "does not still fail" (never
kept), so the minimizer can never simplify on the basis of a harness
glitch rather than a genuine, reproduced disagreement.

It's greedy and deterministic: strategies and candidates are generated in
a fixed order, the first candidate that preserves the failure is taken,
and there's no randomness anywhere — the same starting input yields the
same minimized result every time. Two independent budgets bound the work
(each verification is up to two runner launches): `max_executions`
(default 300) and `timeout_seconds` (default 30). The result reports
whether it reached a fixed point or stopped on `execution_budget` /
`timeout`.

The response reports the original failing input, the minimized failing
input, the number of verification executions, the reduction in length, the
reduction in numeric complexity (sum of absolute values), and why it
stopped.

One subtlety worth knowing: minimization is per-strategy-greedy, so it
finds *a* local minimum, not necessarily *the* globally smallest failing
input — e.g. reducing `[5, 50, 3, 20, 8, 100, 1]` under a "some element ≥
10" failure lands on `[12]` (halving `100 → 50 → 25 → 12`, since `12 // 2 =
6` no longer fails), not the theoretical `[10]`. That's the expected
behavior of a fixed-strategy greedy minimizer, and it's still a dramatic,
fully-verified reduction.

## Test generation

`TestCaseGenerator` (`app/services/test_case_generator.py`) deterministically
produces one input for each of these required categories:

| category | what it exercises |
|---|---|
| `empty_list` | no elements at all |
| `singleton` | exactly one element |
| `two_elements` | the smallest non-trivial input |
| `duplicate_values` | some values repeat, others stay unique |
| `all_values_equal` | every element identical |
| `duplicate_maximum` | the maximum value repeats |
| `duplicate_minimum` | the minimum value repeats |
| `already_sorted` | ascending input |
| `reverse_sorted` | descending input |
| `negative_values` | every value negative |
| `zeros` | every value zero |
| `mixed_positive_and_negative` | both signs present |
| `integer_boundary_style_values` | 32-/64-bit signed integer boundaries |
| `repeated_patterns` | a short pattern repeated several times |
| `moderate_size_list` | 30–60 elements, beyond the hand-picked tiny cases |

Determinism: `TestCaseGenerator(seed=42).generate()` always produces
exactly the same 15 inputs, in the same order, no matter how many times
it's called or on which machine. Categories that don't involve arbitrary
choices (`empty_list`, `zeros`, `integer_boundary_style_values`) are the
same for every seed by design.

Deduplication: if two categories happen to produce the identical input
(rare, but possible for some seeds), only the first is kept. This applies
globally, not just within the generator — a generated input identical to
one the caller already supplied manually is skipped, and the manual one
(with `source: "manual"`) is what appears in the response.

Combining and capping: manual inputs always run, in the order supplied.
Generated inputs (if `generate_tests` is true) fill in anything not
already covered, up to `MAX_TOTAL_TESTS` (30) combined — see
`app/services/test_selection_service.py`.

## Docker execution

Submitted code runs inside an ephemeral, locked-down Docker container by
default. Build the image once before running the backend or its
Docker-dependent tests:

```bash
./runner/build.sh
```

See `runner/README.md` for the full list of `docker run` security flags
and, importantly, the **documented risks this does not eliminate** —
Docker containers are a real improvement over a bare subprocess, but this
project does not claim they're a perfect sandbox.

Without Docker installed (or without the image built), the backend falls
back to `EXECUTION_BACKEND=subprocess` if set — but that path is
explicitly **not** a security sandbox and exists only for local
development convenience; see `runner_service.py`'s module docstring.

## AI-generated counterexample explanations

When `explain_counterexamples` is set on `/submissions/analyze` and a
counterexample is confirmed by real execution, the backend asks Claude to
explain *why* the candidate is wrong on that input. The boundaries mirror
the test-generation ones, with an important extra guarantee:

- **Claude runs only after deterministic execution has confirmed the
  mismatch.** It explains a proven bug; it never gates, re-decides, or
  overrides the verdict. The confirmed `candidate_result` and
  `reference_result` are passed to Claude read-only and are stored
  untouched — the explanation is a *separate* column, so it can never
  overwrite the verified execution facts.
- **Claude never sees the reference source.** It's given the spec, the
  candidate (line-numbered), the minimized failing input, and what each
  side *returned or raised* — never the reference implementation's code.
- **Structured, validated output.** Claude must return `{summary,
  root_cause, walkthrough, suspected_lines, suggested_fix, confidence}`.
  Cited line numbers are validated against the actual candidate source;
  out-of-range numbers are dropped rather than shown.
- **The suggested fix is never claimed correct.** `suggested_fix_verified`
  is always `false` — nothing in this pipeline tests the fix. An optional
  `suggested_patch` (enabled with `suggest_patch`) is offered strictly as a
  proposal and is never applied to your code.
- **Deterministic fallback.** If Claude is unconfigured, times out, is
  rate-limited/unavailable, or returns unusable output, a plain
  deterministic explanation is built from the execution facts alone (and
  labelled `source: "deterministic"`, `ai_generated: false`). Requesting an
  explanation never causes analysis to fail.

Every explanation is labelled with its `source` (`ai` or `deterministic`)
and an `ai_generated` flag, stored on the counterexample, and returned by
both `/analyze` and the analysis GET endpoint. The `explanation_usage`
field reports model/token/latency/availability for the attempt (never
secrets). Uses the same `ANTHROPIC_*` configuration as test generation.

## AI-generated targeted tests

When `use_ai_tests` is set on `/submissions/analyze` and Claude is
configured, the backend asks Claude to propose additional test inputs
aimed at exposing bugs the deterministic categories might miss. This is
built around a few firm boundaries:

- **Claude proposes inputs only. It never judges correctness.** Every
  AI-proposed input runs through the exact same Docker comparison engine
  as manual and deterministic inputs, and is labeled passed/failed purely
  by that real execution. Claude's `reason` for each input is advisory
  metadata, never a verdict.
- **Claude never sees the reference implementation.** The request built
  for the provider (`app/services/ai_orchestration.py` →
  `build_ai_request`) contains only the spec, the *candidate*, the
  categories already tried, and a compact summary of inputs that didn't
  fail. The request model has no `reference_code` field at all, so it
  structurally cannot leak.
- **Untrusted output is validated hard.** Claude's response must be JSON
  of the form `{"tests": [{"input": [ints], "category": "...", "reason":
  "..."}]}`. Each proposed input is validated against the supported schema
  — strictly `list[int]`, bounded length, integer range ±10,000 — with
  bools, floats (e.g. `2.0`), strings, oversized lists, and out-of-range
  values all rejected. Invalid items are dropped individually; a bad item
  never sinks the batch. Survivors are deduplicated and capped.
- **AI is optional and best-effort.** If no API key is configured, or the
  provider times out / is rate-limited / is unavailable / returns
  malformed output, analysis proceeds normally with deterministic +
  Hypothesis + manual tests. Enabling `use_ai_tests` never causes a
  request to fail; the response's `ai_usage` field reports what happened
  (model, token usage, latency, request count, availability, and a short
  non-sensitive error label when applicable). The API key is never logged.

Configuration is entirely environment-driven (`ANTHROPIC_API_KEY`,
`ANTHROPIC_MODEL`, `ANTHROPIC_TIMEOUT_SECONDS`, `ANTHROPIC_MAX_TOKENS`,
`AI_MAX_GENERATED_TESTS` — see `.env.example`). The provider is behind an
`AITestProvider` protocol (`app/services/ai_provider.py`) with a real
`AnthropicTestProvider` (official SDK) and a `MockTestProvider`; the test
suite uses the mock exclusively and never makes live API calls.

## Persistence and schema

Analysis runs are stored in PostgreSQL via SQLAlchemy 2.x, with Alembic
migrations (`apps/backend/alembic/`). All primary keys are UUIDs; all
timestamps are timezone-aware UTC. Database access lives behind repository
functions (`app/repositories/`) and a persistence service
(`app/services/persistence_service.py`) — routes and other services never
issue SQL directly.

Five tables:

```
Submission ─1──*─ AnalysisRun ─1──*─ Execution
     │                  │
     │                  └────*─ Counterexample
     │
     └──────*─ TestCase ──────*─ Execution
```

- **Submission** — the immutable record of what was submitted (function
  name, specification, candidate/reference code, created_at). One
  submission can be analyzed many times.
- **TestCase** — one input that was run for a submission (input JSON,
  category, source, reason). Test inputs are a property of *what's being
  tested*, so they hang off the submission, not the run.
- **AnalysisRun** — one execution of the pipeline over a submission
  (status, totals, elapsed time, seed, and a JSON `configuration`
  snapshot).
- **Execution** — one implementation (candidate **or** reference) running
  on one test case within one run. It carries the sanitized normalized
  result, runtime, and timeout flag, and links to both its run and its
  test case. A unique constraint on `(analysis_run_id, test_case_id,
  role)` guarantees exactly one candidate and one reference execution per
  input per run.
- **Counterexample** — a confirmed failing input for a run (original +
  minimized input, both verified results, and a nullable JSON
  `explanation` holding the structured AI-generated or deterministic
  explanation — stored alongside, never in place of, the verified
  results).

Because an `Execution` references both an `AnalysisRun` and a `TestCase`,
those two associations are what let a single stored test input be shared
by its candidate and reference executions while still belonging to exactly
one run. Indexes cover the foreign keys and the common "list a
submission's runs, newest first" and "list submissions, newest first"
access patterns. Every foreign key is `ON DELETE CASCADE`, so deleting a
submission cleanly removes its entire graph.

Tests never touch a real Postgres: `tests/conftest.py` points
`DATABASE_URL` at a throwaway file-backed SQLite database and
creates/drops all tables around each test. The portable `GUID`/JSON column
types (`app/db/base.py`) mean identical model code runs on SQLite in tests
and PostgreSQL in production.

## Comparison rules

Backend:

```bash
cd apps/backend
pytest
```

Every test runs against the bare-subprocess backend by default (no Docker
required) via an autouse fixture in `tests/conftest.py` — this keeps the
whole existing suite runnable in any environment, including
`tests/test_hypothesis_search.py` (real Hypothesis generation/shrinking
against real, out-of-process runner launches — just not containerized ones
by default) and the minimizer tests: `tests/test_minimizer_service.py`
(pure strategy generators + the driver with a mocked runner, so exact
minimized outputs can be asserted deterministically) and
`tests/test_minimizer_integration.py` (real known-buggy functions reduced
end to end through the runner). Docker-specific tests
(`tests/test_docker_runner_service.py`) opt into the real Docker backend
themselves and **self-skip with a clear reason** if Docker isn't installed
or the runner image (`./runner/build.sh`) hasn't been built yet — they
won't fail, they'll show as `SKIPPED`.

Database tests (`tests/test_persistence.py`, and the persistence path of
`tests/test_analyze.py`) run against an isolated SQLite database created
per test session — no PostgreSQL required to run the suite. See
"Persistence and schema" above.

AI tests (`tests/test_ai_test_generation.py`) use `MockTestProvider`
exclusively — no `ANTHROPIC_API_KEY` and no network are needed, and the
suite never makes a live API call. They cover the required scenarios:
valid response, malformed response, invalid integers, oversized lists,
duplicate tests, provider timeout, and provider unavailable, plus that
reference code is never sent and that failures degrade gracefully.

Explanation tests (`tests/test_ai_explanation.py`) use
`MockExplanationProvider` — again no key and no network. They cover a valid
structured explanation, malformed/schema-invalid output, line-number
validation (out-of-range dropped, deduped, sorted), the deterministic
fallback on timeout/rate-limit/unavailable/no-provider, the suggested patch
as a proposal only, and the invariants that the explanation never carries
execution results back and never contains reference code. Persistence of
the explanation (stored alongside — never overwriting — the verified
results) is covered in `tests/test_persistence.py`.

Frontend:

```bash
cd apps/frontend
npm run lint
npm run typecheck
```

## Architecture notes

See `docs/architecture.md` (added as the project grows) for the reasoning
behind key decisions, such as why submitted code will always run in a
separate, isolated process (`runner/`) rather than inside the FastAPI
process.
