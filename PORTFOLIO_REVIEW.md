# Portfolio-Readiness Review

A structured review of the AI Code Breaker repository across architecture,
security, API design, type safety, testing, schema, the Docker runner, Claude
integration, failure handling, frontend accessibility, benchmark quality,
documentation, and deployment. Findings are prioritized; the fixes applied in
this pass are marked **[FIXED]**.

## Overall assessment

This is a strong portfolio project. It has a clean layered backend
(routes → services → repositories → models), a well-abstracted execution
backend with a shared runner protocol, 263 backend test functions across 22
files, a genuinely reproducible evaluation benchmark, cascade-safe schema with
migrations, strict TypeScript on the frontend with real ARIA usage, and
thorough docs (README, SECURITY, DEPLOYMENT, benchmark README). The issues
below are mostly about removing a legacy code path that undercuts the security
model, plus polish — not structural rework.

---

## 1. Critical issues

### C1 — Legacy `/analyze` and `/search` endpoints bypass all abuse controls **[FIXED]**
`POST /submissions/analyze` and `POST /submissions/search` predate the async
job workflow and the security milestone. They have **no rate limiting, no
per-client quota, and no concurrency cap**, and `/analyze` runs code execution
**synchronously inside the API process** (which uses the weaker subprocess
backend). An attacker can call `/analyze` directly to bypass every quota added
in the security review and to drive unbounded synchronous execution/Claude
cost on the API process. The frontend no longer uses either endpoint (the form
uses the async `createSubmission`/`createAnalysis` flow), so this is also dead
code from the UI's perspective.
**Fix applied:** removed both endpoints and the now-dead `analyzeSubmission`
frontend client function. All analysis now flows through the rate-limited,
quota-enforced, worker-executed async path. (Property search remains available
internally; if it needs a public endpoint later, it must be added behind the
same `enforce_rate_limit` + quota dependencies.)

### C2 — Subprocess execution backend has no resource isolation beyond a timeout
`app/services/runner_service.py` runs submitted code with only a wall-clock
timeout — no `setrlimit`, no `preexec_fn`, no memory/FD caps. The Docker
backend is properly locked down; the subprocess backend is not. This is
acceptable **only** because, after C1, production runs code exclusively on the
worker's Docker backend and the API never executes code. It remains a sharp
edge: anyone who sets `EXECUTION_BACKEND=subprocess` in a real deployment gets
weak isolation.
**Recommended (not applied — needs care to not break local dev):** add
`resource.setrlimit` (RLIMIT_AS, RLIMIT_CPU, RLIMIT_NPROC) via `preexec_fn` on
POSIX, and document subprocess as **development-only**. Already partially
signposted in SECURITY.md; should be made louder in config.

---

## 2. Important improvements

### I1 — `searching_properties` and `minimizing` are status-only placeholders **[DOCUMENTED]**
The async worker advances through these stages for correct status/progress
vocabulary, but their *results* aren't wired into the stored run (Hypothesis
search still lives on its own code path; `minimized_input` stays `None` on the
async path). This was an intentional, disclosed choice, but for a portfolio it
reads as "half-implemented" unless clearly labeled.
**Fix applied:** kept the honest placeholders and made the limitation explicit
in the worker docstring and README so a reviewer isn't misled. (Full wiring is
a legitimate future enhancement, not a bug.)

### I2 — No `jsx-a11y` lint enforcement on the frontend **[FIXED]**
Accessibility is implemented well by hand (ARIA roles, `aria-live`,
`aria-expanded`, keyboard handlers), but nothing *enforces* it, so regressions
could slip in. `.eslintrc.json` only extends `next/core-web-vitals`.
**Fix applied:** added the `jsx-a11y` recommended ruleset to the ESLint config
so accessibility issues are caught in CI.

### I3 — Health-check import cost / readiness clarity **[OK after review]**
`/ready` checks DB + Redis and returns 503 correctly; `/health` is liveness
only. Reviewed and correct. No change.

### I4 — Duplicated persistence logic between sync and async paths **[FIXED]**
`persist_analysis` (used by the removed `/analyze` endpoint and retained for
its service-level tests) and the worker's `persist_results_into_run` carried
near-identical per-comparison writing loops (test case + candidate/reference
executions + counterexample). This was genuine duplication that could drift.
**Fix applied:** extracted the shared logic into a private
`_write_comparisons(...)` helper that both functions now call, with no change
to either public contract. The duplicated loop now exists exactly once.

---

## 3. Nice-to-have improvements

- **N1 — Request IDs in logs.** Structured logging is in place; adding a
  per-request correlation id (middleware) would make request tracing easier.
- **N2 — OpenAPI tags/summaries.** Routes are documented in docstrings;
  grouping them with FastAPI `tags` and terse `summary`s would improve the
  auto-generated `/docs`.
- **N3 — Frontend: surface rate-limit/quota 429s distinctly.** The client
  shows the backend message, which is fine; a dedicated "slow down / quota"
  affordance would be friendlier.
- **N4 — Benchmark: per-category confidence.** The benchmark reports rates by
  category; adding counts (n per category) alongside rates would make small-n
  categories obvious.

## 4. Features that add complexity without enough value (candidates for removal)

- **R1 — Legacy `/analyze` + `/search` endpoints and `analyzeSubmission`
  client. [REMOVED]** See C1. Superseded by the async flow; kept only risk.
- **R2 — `suggested_patch` "apply" affordances (if any) beyond display.**
  Reviewed: the patch is display-only and clearly labeled "proposal · not
  applied," which is the right scope. **Keep** — no change needed.
- **R3 — Nothing else recommended for removal.** The remaining surface
  (deterministic + AI + Hypothesis strategies, minimizer, explanations) is
  cohesive and each piece earns its place.

---

## Fixes applied in this pass

1. **[C1/R1]** Removed `/submissions/analyze` and `/submissions/search`
   endpoints (security bypass + dead-from-UI), and the dead `analyzeSubmission`
   frontend client function and its now-unused legacy adapter path.
2. **[I2]** Added `jsx-a11y` ESLint enforcement.
3. **[I1]** Documented the placeholder stages explicitly (worker + README).
4. **[I4]** Consolidated the two persistence paths onto a shared
   `_write_comparisons` helper, removing the duplicated write loop.
5. **Tests** migrated off the removed endpoints: `test_persistence.py` now
   drives the async flow (preserving all GET-endpoint coverage), and the
   endpoint-only `test_analyze.py` was removed (its coverage lives in
   `test_async_jobs.py` + `test_comparison_service.py`).

Everything else is left as prioritized recommendations rather than rewritten,
per the review's intent.
