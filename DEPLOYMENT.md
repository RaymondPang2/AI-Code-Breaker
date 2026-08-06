# Deployment

How to deploy AI Code Breaker. This is a student portfolio project; the guide
targets a small, single-region deployment. Read [`SECURITY.md`](SECURITY.md)
first — running user-submitted code has real risks, and the sandbox here is
defense-in-depth, not a guarantee.

> **No secrets in this repo.** Every secret is supplied at deploy time through
> environment variables (or a root-owned, git-ignored env file). Nothing
> sensitive is committed. `.env.example` documents the variables with safe
> placeholder values only.

## Target architecture

```
                 ┌─────────────┐
   users ───────▶│  Frontend   │  (hosted separately: Vercel / CDN / container)
                 └──────┬──────┘
                        │ HTTPS (NEXT_PUBLIC_API_URL)
                 ┌──────▼──────┐        ┌───────────┐
                 │  API (FastAPI)◀──────▶  Postgres  │
                 └──────┬──────┘        └───────────┘
                        │ enqueue            ▲
                 ┌──────▼──────┐             │
                 │    Redis    │             │
                 └──────┬──────┘             │
                        │ dequeue            │
                 ┌──────▼──────┐             │
                 │   Worker    │─────────────┘
                 │             │
                 │  Docker runner (candidate/reference execution)
                 └─────────────┘
                   ▲ Docker socket — WORKER ONLY, never the API
```

Five deployable pieces: the **frontend** (hosted separately), the **API**
service, the **worker** service, **PostgreSQL**, and **Redis**. The **Docker
runner** is available only to the worker.

## Environment variables

All configuration is via environment variables. The backend reads them through
`app/core/config.py`; the frontend reads `NEXT_PUBLIC_*` at build time.
`.env.example` is the source of truth for names and defaults.

### Backend (API + worker)

| Variable | Required | Default | Notes |
|---|---|---|---|
| `DATABASE_URL` | **yes** | — | `postgresql+psycopg://user:pass@host:5432/db`. Secret. |
| `REDIS_URL` | **yes** (prod) | `redis://localhost:6379/0` | Job queue. Secret if it carries a password. |
| `EXECUTION_BACKEND` | yes | `docker` | **`subprocess` for the API**, `docker` for the worker. |
| `DOCKER_RUNNER_IMAGE` | worker | `ai-code-breaker-runner:latest` | Runner image tag. |
| `ANTHROPIC_API_KEY` | no | — | **Secret.** Absent → deterministic fallbacks; nothing breaks. Never logged. |
| `ANTHROPIC_MODEL` | no | `claude-sonnet-4-5` | Model id. |
| `ANTHROPIC_MAX_TOKENS` | no | `1024` | Per-call output cap (cost control). |
| `AI_MAX_GENERATED_TESTS` | no | `8` | AI test cap (cost control). |
| `CORS_ALLOW_ORIGINS` | **yes** (prod) | `http://localhost:3000` | Comma-separated. Set to the frontend origin(s). Never `*`. |
| `QUEUE_EAGER` | no | `false` | **Never set in prod.** Inline jobs for tests/local only. |
| `JOB_TIMEOUT_SECONDS` | no | `600` | Overall job ceiling. |
| `STAGE_TIMEOUT_*` | no | see `.env.example` | Per-stage budgets. |
| `MAX_REQUEST_BODY_BYTES` | no | `262144` | Body-size cap (413 over). |
| `RATE_LIMIT_PER_MINUTE` / `RATE_LIMIT_BURST` | no | `20` / `10` | Per-client rate limit. |
| `RATE_LIMIT_ENABLED` | no | `true` | Keep `true` in prod. |
| `MAX_CONCURRENT_ANALYSES_PER_CLIENT` | no | `3` | Concurrency cap. |
| `ANONYMOUS_ANALYSIS_QUOTA` | no | `100` | Lifetime quota for anonymous clients (cost control). |
| `LOG_LEVEL` | no | `INFO` | Standard levels. |
| `LOG_FORMAT` | no | `json` | `json` in prod, `text` in dev. |

### Frontend

| Variable | Required | Notes |
|---|---|---|
| `NEXT_PUBLIC_API_URL` | **yes** | The deployed API's base URL. Inlined at **build** time. A URL, not a secret. |

### Secret handling

Provide secrets through your platform's secret store (Vercel/Render/Fly/K8s
Secrets, or a root-owned `--env-file`). Do **not** commit them, bake them into
images, or put them in the compose files. The Anthropic key is read only from
the environment, never logged, never returned by the API, never placed in a
prompt. Rotate it if you suspect exposure (see the rollback checklist).

## Database migrations

Migrations are Alembic (`apps/backend/alembic`). They are applied as an
explicit, **separate** step — never baked into the service `CMD` — so that
rolling an image back does not implicitly change the schema.

Apply the latest schema (idempotent; applies 0001→0004 as needed):

```bash
# one-shot, using the API image so the app + alembic are present
docker run --rm --env-file /etc/acb/prod.env \
  ai-code-breaker-backend:latest \
  alembic upgrade head
```

Or, if running the stack with compose, exec into a temporary container:

```bash
docker compose -f infra/docker-compose.prod.yml --env-file /etc/acb/prod.env \
  run --rm api alembic upgrade head
```

Order of operations on deploy: **migrate first, then roll out the new image**
(the app expects the new schema). Migrations here are additive/backward-safe
(new nullable/defaulted columns), so a brief overlap of old+new app code is
tolerable; still, prefer expand-then-contract for any future breaking change.

## Deployment checklist

1. **Pre-flight**
   - [ ] CI is green (backend tests, frontend lint/typecheck/test/build).
   - [ ] Secrets are set in the platform's secret store (not in the repo).
   - [ ] `CORS_ALLOW_ORIGINS` lists exactly the frontend origin(s).
   - [ ] `EXECUTION_BACKEND=subprocess` on the API, `docker` on the worker.
   - [ ] The Docker socket is mounted **only** on the worker.
   - [ ] `QUEUE_EAGER` is **unset**; `RATE_LIMIT_ENABLED=true`.
2. **Build & publish images**
   - [ ] Build backend image; tag with the release SHA (not just `latest`).
   - [ ] Build the runner image (`runner/Dockerfile`) and make it available
         to the worker host.
   - [ ] Build the frontend with `NEXT_PUBLIC_API_URL` set to the API URL.
3. **Database**
   - [ ] Take a backup / snapshot **before** migrating (see Backups).
   - [ ] Run `alembic upgrade head` as a one-shot task; confirm it succeeds.
4. **Roll out**
   - [ ] Deploy the API; wait for `/ready` to return 200.
   - [ ] Deploy the worker; confirm it connects to Redis (structured log line).
   - [ ] Deploy/publish the frontend.
5. **Verify**
   - [ ] `GET /health` → 200; `GET /ready` → 200 with `database` + `redis` ok.
   - [ ] Submit the built-in example end to end; confirm a result renders.
   - [ ] Check structured logs are flowing and contain no secrets/tracebacks.

## Rollback checklist

1. **App rollback (no schema change)** — the common case.
   - [ ] Re-deploy the previous image tag (SHA) for API and worker.
   - [ ] Confirm `/ready` is 200. Because migrations are additive and applied
         separately, the previous app runs fine against the newer schema.
2. **Schema rollback** — only if a migration caused the problem.
   - [ ] Prefer fixing forward. If you must revert, run
         `alembic downgrade <previous_revision>` from the matching image, then
         redeploy the matching app image. Restore from backup if data was lost.
3. **Secret compromise**
   - [ ] Rotate `ANTHROPIC_API_KEY` (and DB/Redis credentials if implicated)
         in the secret store; redeploy so services pick up the new values.
   - [ ] Review logs/billing for anomalous usage.
4. **Runaway cost / abuse**
   - [ ] Lower `ANONYMOUS_ANALYSIS_QUOTA` / `RATE_LIMIT_*`, or temporarily set
         `ANTHROPIC_API_KEY` empty to force deterministic fallbacks (the app
         keeps working without AI).

## Backup considerations

- **PostgreSQL is the source of truth.** Take regular automated backups
  (managed-DB snapshots, or `pg_dump` on a schedule) and **always** snapshot
  immediately before running migrations. Test a restore periodically — an
  untested backup is a guess.
- **Redis is transient.** It holds the job queue and ephemeral job metadata,
  not durable data. The prod compose enables AOF for a soft safety net, but
  losing Redis only loses in-flight/queued jobs, which users can re-submit.
  Do not treat Redis as a store of record.
- **Stored code is user content.** Back it up with Postgres, and honor the
  deletion path (`DELETE /submissions/{id}`) — deletions must propagate to
  backups per your retention policy.
- **Images.** Keep the last few tagged image builds so a rollback has
  something to roll back to.

## Cost-control notes

- **Claude usage is the main variable cost.** It is bounded by
  `AI_MAX_GENERATED_TESTS`, `ANTHROPIC_MAX_TOKENS`, and the per-analysis AI
  budget; AI is optional and degrades to deterministic behavior if the key is
  absent or rate-limited. Set a billing alert on the Anthropic account.
- **Per-client quotas** (`ANONYMOUS_ANALYSIS_QUOTA`, rate limits, concurrency
  cap) bound how much any one client can spend on your behalf.
- **Compute.** The worker is the expensive service (it runs code); scale it
  independently of the API. Idle Postgres/Redis on the smallest tier is cheap.
- **CI** cancels superseded runs (`concurrency` in the workflow) to avoid
  burning minutes on stale commits.
- **Egress/storage.** Stored code is small text; the main storage cost is
  backups' retention window — tune it to your needs.

## Highly privileged: the Docker socket

The worker mounts `/var/run/docker.sock` to launch runner containers. **This
is near-root on the host** — anything that can talk to the Docker daemon can
generally escalate to root. Consequences and guidance:

- The socket is mounted on the **worker only**. The internet-facing **API must
  never** get it.
- Run the worker on an **isolated node** dedicated to execution, not alongside
  other trusted services.
- Prefer a **rootless** or **remote** Docker daemon over the raw host socket.

### Recommended for a serious public service

For anything beyond a low-traffic demo, replace host-socket execution with a
stronger sandbox:

- **microVMs** (Firecracker) or **gVisor** for kernel-level isolation of each
  execution, on a disposable, network-egress-filtered execution node.
- A **brokered runner service** with least privilege instead of direct daemon
  access, so a compromised worker can't drive arbitrary Docker commands.
- Per-job **ephemeral VMs** that are destroyed after each analysis.

This project is explicitly **experimental and not suitable for running highly
hostile untrusted code without stronger sandbox infrastructure.**
