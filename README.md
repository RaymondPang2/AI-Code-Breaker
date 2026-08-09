# AI Code Breaker

Differential testing for Python functions — find inputs where a candidate implementation and a known-correct reference disagree, then minimize and explain the failure.

## Demo link:

Access the live demo at [your-demo-url.com](https://your-demo-url.com) _(replace with your deployed URL — the app also runs locally, see [Setup](#setup))_

## Table of Content:

- [About The App](#about-the-app)
- [Screenshots](#screenshots)
- [Technologies](#technologies)
- [Setup](#setup)
- [Approach](#approach)
- [Status](#status)
- [Credits](#credits)
- [License](#license)

## About The App

**AI Code Breaker** is an app that finds bugs by comparison. You give it a natural-language spec, a candidate Python implementation, and a known-correct reference implementation. It generates test inputs, runs both implementations in isolated sandboxes, and finds concrete inputs where their behavior differs — then shrinks each failure to a minimal reproducer and explains it.

Its defining rule: an LLM never decides whether code is correct. Claude proposes test inputs and writes explanations, but every pass/fail verdict comes from actually running both implementations and comparing results under deterministic rules. Claude's output is advisory and is validated before it is trusted. Functions take a single `list[int]` argument.

## Screenshots

![Counterexample view](docs/screenshots/04-counterexample.png)

More screenshots — the submission form, staged progress, results overview, all-tests table, AI explanation, and a benchmark run — live in [`docs/screenshots/`](docs/screenshots/).

## Technologies

I used `Python`, `FastAPI`, `SQLAlchemy`, `PostgreSQL`, `Redis`, `RQ`, `Hypothesis`, `Docker`, `Next.js`, `React`, `TypeScript`, and `Tailwind`, ...

## Setup

- download or clone the repository
- copy the env file: run `cp .env.example .env` (add an optional `ANTHROPIC_API_KEY` — the app runs deterministically without one)
- start everything: run `docker compose -f infra/docker-compose.yml up --build`
- check it's up: `docker compose -f infra/docker-compose.yml ps` (api + worker should be `Up`)
- open the frontend at `http://localhost:3000` and the API at `http://localhost:8000`
- ...

_Prefer running the pieces directly? `pip install -r requirements.txt && alembic upgrade head && uvicorn app.main:app --reload` for the backend, `npm install && npm run dev` for the frontend. Full details in [`DEPLOYMENT.md`](DEPLOYMENT.md)._

## Approach

I adopted a strict **execution-is-the-oracle** approach for finding bugs: an LLM is never allowed to judge correctness. Candidate and reference code run in isolated per-input Docker sandboxes (no network, read-only, non-root, capped resources), their outputs are normalized and compared under one deterministic rule set, and results are bucketed as passed / failed / inconclusive so accounting is always honest. Test inputs come from three strategies — deterministic edge cases, Hypothesis property search, and Claude-proposed inputs (validated before use) — and any failure is shrunk to a minimal reproducer. The whole thing is evaluated against a labelled 36-case benchmark rather than anecdotes, and ...

## Status

**AI Code Breaker** is still in progress. `Version 2` will wire the property-search and minimization stages fully into the async pipeline and close the mutation / state-leakage detection gaps.

## Credits

List of contributors:

- [Your Name](https://github.com/your-handle)
- [Anthropic Claude](https://www.anthropic.com) — AI test proposals and explanations
- [Hypothesis](https://hypothesis.readthedocs.io) — property-based test generation and shrinking

## License

MIT license @ [author](https://github.com/your-handle)

---

**Final Words:**

Thank you for staying with me up to this point. Suggestions and feedback are always welcomed. 😃
