# infra/

Local infrastructure for development.

## PostgreSQL

`docker-compose.yml` runs a local PostgreSQL 16 for the backend.

```bash
# from the repo root
docker compose -f infra/docker-compose.yml up -d
```

Credentials/database match the default `DATABASE_URL` in
`apps/backend/app/core/config.py`
(`postgresql+psycopg://acb:acb@localhost:5432/ai_code_breaker`), so the
backend connects with no extra configuration. Data persists in the
`acb_pgdata` named volume across restarts.

To stop it (keeping data):

```bash
docker compose -f infra/docker-compose.yml down
```

To stop it and delete all data:

```bash
docker compose -f infra/docker-compose.yml down -v
```

## Running migrations

Once Postgres is up, apply migrations from the backend directory:

```bash
cd apps/backend
alembic upgrade head
```

Tests do not use this database — they use an isolated, throwaway SQLite
database created per test session (see `apps/backend/tests/conftest.py`).

## Status

- **Now:** PostgreSQL only. The backend and frontend run directly during
  development (see the root README).
- **Later:** the runner's Docker image and (eventually) the backend and
  frontend may be wired into Compose too.
