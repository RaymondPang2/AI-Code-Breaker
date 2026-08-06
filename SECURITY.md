# Security

AI Code Breaker executes user-submitted Python code to find behavioral bugs.
That makes it inherently security-sensitive: the core feature is running code
written by someone you don't trust. This document is an honest account of the
threat model, what's protected, and — just as important — what is **not**.

> **Experimental software.** This is a student portfolio project. It applies
> reasonable, layered protections suitable for a low-traffic demo, but it is
> **not** hardened for running highly hostile untrusted code at scale.
> Running determined, adversarial code against this service without stronger
> sandbox infrastructure (gVisor/Firecracker/microVMs, a dedicated
> ephemeral execution cluster, network egress filtering, seccomp profiles)
> is **not recommended**. See "Limitations" below.

## Threat model

### Assets

1. **The host running the API and worker** — its CPU, memory, disk, network,
   and any credentials reachable from it.
2. **The Anthropic API key** — a billable secret. Leakage or abuse costs
   money.
3. **Stored user content** — submitted specifications and source code, plus
   analysis results, in PostgreSQL.
4. **Service availability** — the API and worker staying responsive for
   legitimate users.
5. **Other users' data** — one user must not read or delete another's
   submissions/results.

### Attackers

- **A1 — Malicious submitter.** Sends hostile Python as candidate/reference
  code, trying to break out of execution isolation, reach the network,
  exfiltrate secrets, or damage/DoS the host.
- **A2 — Abusive API client.** Scripts the public API to exhaust resources:
  huge payloads, floods of analyses, or driving up the Claude bill.
- **A3 — Curious/aggressive reader.** Tries to read other users' stored code
  or results by guessing IDs or share links.
- **A4 — Prompt injector.** Embeds instructions in the specification or in
  submitted code hoping to hijack the Claude calls (make the model exfiltrate
  data, ignore constraints, or produce attacker-controlled output that is
  then trusted).
- **A5 — Passive observer of logs/errors.** Hopes error messages or logs leak
  secrets, host paths, tracebacks, or other users' data.

### Attack surfaces

- The HTTP API (submission creation, analysis creation, reads, share links).
- The code-execution path (candidate/reference run in the runner).
- The Docker daemon the worker talks to.
- The Claude API calls (prompt content, usage/billing).
- The database (query scoping, stored content).
- Logs and error responses.

### Mitigations (implemented)

| # | Risk | Mitigation |
|---|------|-----------|
| M1 | Submitted-code breakout / host damage (A1) | Code runs in the Docker runner: `--network none`, `--read-only` root fs, non-root `--user`, `--memory`/`--memory-swap` (swap off), `--cpus`, `--pids-limit`, `--cap-drop ALL`, `--security-opt no-new-privileges`, never `--privileged`, `--rm`. Each input runs in a fresh container. |
| M2 | Oversized requests / memory DoS (A2) | HTTP body-size cap middleware (413 over the limit) **plus** per-field size limits in the schema (function name ≤100, spec ≤2000, each source ≤20000, ≤20 manual test cases, list size ≤1000). |
| M3 | Request floods / bill exhaustion (A2) | Per-client rate limiting (token bucket, keyed by API identity or client IP) on submission and analysis creation; anonymous quota for keyless clients. |
| M4 | Too many concurrent analyses (A2) | Per-identity cap on in-flight (non-terminal) analyses; new analyses beyond the cap are rejected with 429. |
| M5 | Runaway execution/AI cost per analysis (A1/A2) | Per-stage execution timeouts, an overall job timeout, a bounded test count (`MAX_TOTAL_TESTS`), a capped AI test count and token budget, and AI graceful-degradation on rate limit/unavailability. |
| M6 | Secret leakage (A5) | The Anthropic key is read **only** from the environment, never logged, never returned in any response, and never included in a prompt. Error text is sanitized (see M8). |
| M7 | Cross-user data access (A3) | Reads are scoped: an analysis is only returned when it belongs to the given submission; stored content is owned by the identity that created it, and reads/deletes are checked against ownership. Share links are opt-in and use unguessable tokens. |
| M8 | Log/error info leak (A5) | Runner output is sanitized (no tracebacks, host paths, or container IDs). API errors return short, generic messages; internal detail is not echoed. Job errors are truncated and stripped of tracebacks before storage. |
| M9 | Prompt injection via spec/code (A4) | Claude output is treated as **advisory only** — it never decides pass/fail and never gates any security control. Verified pass/fail comes from real execution + deterministic comparison. Submitted content is passed as clearly delimited data, and the reference implementation is never sent to the model. |
| M10 | Data retention / privacy | A deletion endpoint lets an owner delete a submission and all its analyses/executions/counterexamples. Retention guidance is documented. |
| M11 | Docker daemon exposure | The worker needs the Docker socket to launch runner containers; this is the biggest residual risk and is documented as such (see Limitations). The API service does **not** get the socket. |

## Limitations (read this)

- **The sandbox is defense-in-depth, not a guarantee.** Docker with dropped
  capabilities and no network is much stronger than a bare subprocess, but a
  container escape (kernel bug, misconfiguration) would reach the host. For
  genuinely hostile code, use a stronger isolation layer (gVisor,
  Firecracker/microVMs, or a disposable per-job VM).
- **Docker socket access on the worker is powerful.** A process that can talk
  to the Docker daemon can typically escalate to root on the host. In a real
  deployment, run the worker on an isolated node, use a rootless/remote
  daemon, or replace socket access with a brokered, least-privilege runner
  service.
- **Isolation is per-input, not per-session.** State-leakage and
  input-mutation bugs may not be caught (each input runs fresh) — a
  correctness limitation, documented in the benchmark, not a security hole.
- **Authentication is lightweight.** The default is an anonymous per-client
  quota (by API key header if provided, else client IP). This deters casual
  abuse but is not a substitute for real user accounts; IP-based limits are
  imperfect behind shared NATs/proxies.
- **This service is experimental and is not suitable for running highly
  hostile untrusted code without stronger sandbox infrastructure.**

## Reporting

This is a portfolio project, not a production service. If you find an issue,
open an issue describing it (without including working exploit payloads).

## Secret management

All secrets are provided via environment variables (or a local `.env` that is
git-ignored). No secret is committed to the repository, logged, or returned by
the API. See `.env.example` for the variables. Rotate the Anthropic key if you
suspect exposure.
