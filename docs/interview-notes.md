# Interview Notes

Design decisions behind AI Code Breaker and the reasoning for each. Written to
be read by an engineer who hasn't seen the codebase.

**Contents**
1. [Why AI is not the correctness oracle](#1-why-ai-is-not-the-correctness-oracle)
2. [Why candidate code is isolated](#2-why-candidate-code-is-isolated)
3. [How behavior is normalized before comparison](#3-how-behavior-is-normalized-before-comparison)
4. [How Hypothesis discovers and shrinks failures](#4-how-hypothesis-discovers-and-shrinks-failures)
5. [How the fallback minimizer works](#5-how-the-fallback-minimizer-works)
6. [Why a worker queue is needed](#6-why-a-worker-queue-is-needed)
7. [What Docker protects against](#7-what-docker-protects-against)
8. [What Docker does not fully protect against](#8-what-docker-does-not-fully-protect-against)
9. [How malformed Claude outputs are handled](#9-how-malformed-claude-outputs-are-handled)
10. [How the system behaves when Claude is unavailable](#10-how-the-system-behaves-when-claude-is-unavailable)
11. [How the project is evaluated](#11-how-the-project-is-evaluated)
12. [What I would change for production scale](#12-what-i-would-change-for-production-scale)

---

## 1. Why AI is not the correctness oracle

**The short version:** an LLM asked "is this code correct?" produces an answer
with no evidence attached. You cannot distinguish a right answer from a
confident wrong one without doing the verification yourself — at which point
the model added nothing to the verdict.

So the architecture draws a hard line. **Claude never decides pass or fail.**
Every verdict comes from executing both implementations on a concrete input and
comparing the results under deterministic rules. Claude is allowed exactly two
jobs:

- **Propose test inputs.** Suggestions, nothing more. Each proposal is
  schema-validated, deduplicated, and then run through the *same* comparison
  engine as every deterministically generated input. An AI-proposed input that
  finds a bug isn't trusted because Claude suggested it — it's trusted because
  both implementations ran on it and disagreed.
- **Explain a bug that execution already proved.** The explanation step only
  runs *after* a counterexample is confirmed. It's stored alongside the
  verified execution results, never in place of them, and is labelled advisory
  in both the schema and the UI.

The payoff is in the failure modes. If Claude hallucinates, the worst case is a
wasted test case or an unhelpful paragraph. There is no path where a
hallucination becomes a false verdict, because the verdict is computed
somewhere Claude cannot reach.

This also gives prompt-injection resistance almost for free. Submitted code and
specifications are attacker-controlled text that ends up in a prompt. A
successful injection could make the model say anything — but the model's output
doesn't gate anything. It can't approve buggy code, skip a test, or bypass a
rate limit, because none of those decisions consult it. (The reference
implementation is also never sent to the model, so injection can't exfiltrate
it.)

**Interview framing:** "I treated the LLM as an untrusted input generator and a
documentation writer, not as a judge. The judge is `==` on two real execution
results."

---

## 2. Why candidate code is isolated

The product *is* running code that someone else wrote and I have not reviewed.
Isolation isn't a hardening pass added at the end; it's the core requirement.

Three distinct problems, all solved by execution isolation:

**Security.** Submitted code may be actively hostile: reading files, opening
network connections, spawning processes, or attacking the host. Every input
runs in an ephemeral container with no network, a read-only root filesystem, a
non-root user, and every Linux capability dropped.

**Availability.** Even non-malicious code breaks things. Infinite loops, fork
bombs, and runaway allocations are ordinary bugs in the exact population of
code this tool is built to test. Memory, CPU, and process-count limits plus
layered timeouts mean a pathological submission degrades one analysis rather
than the host.

**Measurement integrity.** This one is easy to overlook. If candidate and
reference ran in the same process as the harness, a candidate that mutates
global state, monkey-patches a builtin, or leaves a file behind could change
how the *reference* subsequently behaves — and the differential result would be
meaningless. A fresh container per input guarantees each measurement is
independent.

That last property has a cost, and I'd rather name it than hide it: because
each input gets a clean process, bugs that only manifest *across* calls —
input mutation and state leakage — are invisible to this design. The benchmark
scores 0% on both categories and says so.

---

## 3. How behavior is normalized before comparison

"The implementations disagree" needs a precise definition, or the tool reports
noise. Comparison happens in two steps.

**Step 1 — normalize at the boundary.** The runner executes the function inside
the sandbox and emits a single JSON object over stdout: a status, plus a return
value, exception type, exception message, stdout, stderr, and runtime. That
crossing is deliberate. Arbitrary Python objects don't survive a process
boundary, so the runner produces a JSON-serializable projection, and a shared
parser (`runner_protocol.py`) turns raw stdout/stderr/exit-code into a
validated `RunnerResult`. Both execution backends use that same parser so they
cannot drift.

**Step 2 — compare under one set of rules.** `comparison_rules.py` holds the
only definition of agreement, applied in priority order:

1. **Internal error on either side → not a match, but flagged inconclusive.**
   This means *our harness* failed, not that the implementations disagree. It's
   returned as a separate boolean so it is never reported as a confirmed bug.
2. **Timeout on either side → never a match.** Not even timeout against
   timeout. A timeout tells you execution didn't finish; it tells you nothing
   about whether the two would have agreed.
3. **Both succeeded → compare JSON-decoded return values** with ordinary
   equality.
4. **Both raised → match only if the exception *type* agrees.** Messages are
   informational. Two different wordings of `ValueError` are agreement;
   `ValueError` versus `IndexError` is a real behavioral difference, and one of
   the bug categories in the benchmark is exactly this.
5. **Anything else → confirmed disagreement.** Success on one side against an
   exception on the other, and so on.

The rules live in one module specifically so the deterministic analyzer and the
Hypothesis search share them. "Candidate must equal reference under our
normalized rules" only means something if there is exactly one set of rules;
duplicating the logic across two services would let them silently diverge.

The distinction I'd emphasize is **inconclusive versus disagreement**. Folding
harness failures into "bug found" is how a differential tester earns a
reputation for false positives. The benchmark's zero false-bug-reports result
depends on keeping them separate.

---

## 4. How Hypothesis discovers and shrinks failures

Deterministic edge cases (empty list, single element, duplicates, negatives,
boundary values) catch a lot, but they only cover what I thought to enumerate.
Hypothesis covers what I didn't.

**Discovery.** The search draws from `st.lists(st.integers(min_value, max_value),
min_size, max_size)` — configurable bounds, so generated inputs stay inside the
supported interface. The property under test is the differential one: *for all
generated inputs, candidate and reference agree under the normalized rules.*
Hypothesis explores until it finds an input that falsifies it or exhausts its
example budget.

Two configuration choices matter:

- **`deadline=None`.** Hypothesis's default per-example deadline is a few
  hundred milliseconds, which is reasonable for in-process property testing and
  completely wrong here — every example launches containers. Without disabling
  it, Hypothesis would flag its own harness as flaky.
- **`database=None`.** Hypothesis normally persists failing examples between
  runs and replays them. That's a good default for a test suite and a bad one
  for a service: it makes a run depend on hidden state from previous runs. With
  the database off and an explicit seed applied via `hypothesis_seed`, the same
  seed reproduces the same search.

The search also carries its own wall-clock check inside the property, so a
pathological submission can't stretch the run past its timeout even if
individual examples stay under budget.

One implementation detail worth mentioning because it surprises people: a
`@given`-wrapped function communicates failure by *raising*, not returning. To
use Hypothesis outside pytest, you call the wrapped function and catch the
falsification, recording the counterexample in a small state object. That's the
standard pattern for driving Hypothesis programmatically.

**Shrinking.** When Hypothesis falsifies a property it automatically shrinks
the counterexample, re-running the property on progressively simpler inputs and
keeping any that still fail. So `[47, -3, 1000, 5, 5]` becomes something like
`[5, 5]`. This is genuinely valuable — a minimal reproducer is the difference
between "your function is broken somewhere" and "your function is broken on
repeated maxima."

---

## 5. How the fallback minimizer works

Hypothesis's shrinker is powerful, but it only applies to counterexamples
Hypothesis itself found, and it's internal machinery I can't easily explain or
audit. Counterexamples also arrive from deterministic generation and from
AI-proposed inputs. So there's a second, deliberately simple minimizer that
works on any failing `list[int]`.

**The contract:** given a failing input, return the simplest input that still
fails, having *verified every step by actually re-running it.* The minimizer
never assumes a simplification still reproduces the bug — it proposes a
candidate, executes both implementations on it, and keeps the candidate only if
the comparison still disagrees.

**Five strategies, applied in a fixed order:**

1. **Remove contiguous chunks**, largest first — delta-debugging style, falling
   back to finer granularity. Fast big wins on long inputs.
2. **Remove single elements**, left to right.
3. **Replace elements with simpler canonical values** — moves values toward a
   canonical form, structured so it can never cycle.
4. **Reduce magnitude** — halve each element toward zero (`100 → 50 → 25 → …
   → 0`), left to right.
5. **Remove duplicate occurrences** — for any value appearing more than once,
   try dropping occurrences, ordered by value and then occurrence index.

The driver is a **greedy fixed-point loop**: run every strategy in order,
repeat until a full pass produces no improvement. When a candidate succeeds,
that strategy restarts from the newly reduced input rather than continuing —
because removing one chunk shifts every subsequent index, so continuing with
stale offsets would skip candidates.

**Determinism is the design goal.** Strategies run in a fixed order, each emits
candidates in a fixed order, and ties break on explicit keys. The same failing
input always minimizes to the same result. That makes it explainable — I can
say precisely why a given reproducer is what it is — and testable.

**Bounded by construction.** Every simplification requires real executions, so
the minimizer tracks two independent budgets: total execution count and
wall-clock time. When either is exhausted it stops and returns the best input
found so far, reporting `stopped_due_to_budget` and a reason. It never returns
an unverified result and never runs unbounded.

The result reports two reduction measures: **length reduction** and **numeric
complexity reduction** (sum of absolute values). Both matter, because strategies
3 and 4 make an input simpler without making it shorter.

---

## 6. Why a worker queue is needed

An analysis can take a long time — many test inputs, each launching containers,
plus optional network calls to Claude. Doing that inside a request handler
causes four distinct problems:

**Request timeouts.** Load balancers and browsers give up long before a
thorough analysis finishes. Synchronous analysis caps how thorough you can be
at whatever the proxy tolerates.

**Blocked capacity.** Every in-flight analysis occupies a worker thread in the
API process. A handful of slow submissions starve health checks and cheap reads.

**Coupled scaling and a security boundary.** The API is I/O-bound; execution is
CPU- and memory-bound and needs the Docker socket. Splitting them lets each
scale on its own signal, and — more importantly — means the **internet-facing
service never executes user code and never receives the socket.** The queue
isn't only an availability mechanism; it's where a security boundary lives.

**No progress or cancellation.** A synchronous call is opaque. With a persisted
run, the client polls and sees real staged progress (`queued` →
`generating_tests` → `executing_tests` → `searching_properties` → `minimizing`
→ `explaining` → `completed`, or `failed`/`cancelled`) and can cancel.

The implementation is RQ on Redis. Two details I'd raise unprompted:

- **Idempotency.** The job id is derived from the run id (`analysis:{run_id}`),
  so a duplicate enqueue doesn't double-execute. Before persisting, the job
  clears any existing children of that run, so a retry that got partway through
  converges to the same final state instead of writing duplicate rows.
- **A retry taxonomy that respects the cause.** `TransientJobError` (Redis or
  DB blip, provider timeout) re-raises so RQ retries. `PermanentJobError`
  — notably anything caused by the user's submitted code — is recorded and
  returned, never retried, because a retry would deterministically fail
  identically and just burn resources. `StageTimeout` is permanent for the same
  reason. `JobCancelled` is an intentional terminal state, not an error.

For tests there's an eager mode (`QUEUE_EAGER=1`) that runs jobs inline, so the
full async workflow is exercised end to end without Redis or a worker process.

---

## 7. What Docker protects against

Each execution gets a fresh container, and the flags are chosen deliberately:

| Flag | Protects against |
|---|---|
| `--network none` | Exfiltrating stolen data, calling home, pulling a second-stage payload, or attacking internal services. No interface exists. |
| `--read-only` | Persisting anything, tampering with the runner, or leaving artifacts for the next execution. |
| `--user <non-root>` | Everything that assumes root inside the container; combines with dropped capabilities to make privileged operations fail. |
| `--memory` + `--memory-swap` (equal) | Runaway allocation. Equal values disable swap, so a memory hog can't degrade the host by thrashing instead of being killed. |
| `--memory-swappiness 0` | Same reasoning — keep the limit a real limit. |
| `--cpus` | Monopolizing CPU and starving other work. |
| `--pids-limit` | Fork bombs. This is the actual defense; a timeout alone doesn't help when the process table is already full. |
| `--cap-drop ALL` | Raw sockets, mount operations, ptrace, and every other capability-gated escalation path. |
| `--security-opt no-new-privileges` | setuid/setgid escalation inside the container. |
| never `--privileged` | The single flag that would undo all of the above. |
| `--rm` + explicit `docker rm -f` | Container accumulation and leaked state between runs. |
| **Fresh container per input** | Cross-test contamination — one input's side effects can't influence another's measurement. |

Timeouts are layered rather than singular: the runner enforces its own alarm
internally, and the launching process applies a hard timeout on top. If the
in-container mechanism fails, the outer kill still fires. A timeout that depends
on the sandboxed code cooperating isn't a timeout.

---

## 8. What Docker does not fully protect against

This is the part I'd want to volunteer in an interview rather than be asked.

**Containers share the host kernel.** A container is namespaces plus cgroups
plus capability restrictions around a process that is still executing against
the *same kernel* as everything else on the box. A kernel vulnerability reached
through a syscall is a container escape. Dropping capabilities and disabling
networking shrink the attack surface substantially; they don't make it a
different machine. Docker is a strong boundary against ordinary hostile code
and a soft one against a determined attacker with a kernel exploit.

**The Docker socket is near-root.** The worker mounts `/var/run/docker.sock` so
it can launch runner containers. Anything that can talk to the Docker daemon
can generally obtain root on the host — for instance by starting a privileged
container that mounts the host filesystem. So the socket is scoped to the
worker only and never given to the internet-facing API. That's a meaningful
mitigation, but it's a reduction in blast radius, not elimination: it means an
API compromise doesn't immediately yield the host, while a *worker* compromise
still might.

**No seccomp profile beyond the default,** and no gVisor/Firecracker layer.
Syscall filtering is coarser than it could be.

**Side channels and shared resources** — timing, cache behavior, and host-level
contention — are not addressed at all.

**Correctness blind spots aren't security, but they're isolation-shaped.**
Because each input runs in a fresh process, input-mutation and state-leakage
bugs are undetectable. Isolation buys measurement integrity and pays for it in
detection coverage.

**What I'd actually recommend for a serious public service:** per-job
microVMs (Firecracker) or gVisor on a dedicated execution node with egress
filtering, and replacing socket mounting with a brokered least-privilege runner
service so a compromised worker can't issue arbitrary Docker commands.
`SECURITY.md` states plainly that the project is experimental and not suitable
for highly hostile untrusted code without that infrastructure.

---

## 9. How malformed Claude outputs are handled

The rule: **model output is untrusted input and is validated like any other.**
There is no path where model text is executed, and no path where an unvalidated
proposal reaches a result.

Validation runs as a funnel, and each stage has a defined failure behavior:

1. **Parse as JSON.** Not valid JSON → `AIProviderMalformedResponse`. The batch
   is discarded and the analysis continues with deterministic tests.
2. **Check the envelope.** Must be an object containing a `tests` key, and
   `tests` must be an array. Otherwise the same malformed-response path.
3. **Validate each proposal independently.** Every item is validated against a
   Pydantic model enforcing the supported shape: a list of integers, within a
   bounded value range, within a length cap. A non-object item is skipped; a
   validation failure is skipped. **One malformed test never sinks the batch** —
   the valid siblings are still used.
4. **Deduplicate and truncate.** Proposals are deduplicated by input value and
   truncated to the configured maximum, so the model can't inflate cost by
   returning a thousand near-identical cases.

Everything surviving that funnel is still just a *candidate input*. It goes
through the same execution and comparison path as every other input, so a
nonsense-but-well-formed proposal costs one execution and produces no bug.

This isn't theoretical. The benchmark measures **invalid AI test rate as a
first-class metric**, and the baseline run reports **27.1% of AI-proposed
tests rejected** — with zero false bug reports. Roughly a quarter of what the
model proposed was unusable, the validation layer dropped all of it, and
results were unaffected. That number is a good answer to "how do you know your
validation works?"

Provider-level failures get their own exception taxonomy —
`AIProviderTimeout`, `AIProviderRateLimited`, `AIProviderUnavailable`,
`AIProviderMalformedResponse` — so callers can distinguish "try again later"
from "this response was garbage." Error messages are sanitized rather than
echoing raw provider exceptions, which can carry request details.

---

## 10. How the system behaves when Claude is unavailable

**Everything works.** AI is an enhancement layer, not a dependency. There is no
code path where an unavailable provider fails an analysis.

Unavailability takes several forms, all handled the same way:

- **No API key configured.** Constructing the provider raises
  `AIProviderUnavailable`. This is the *default* local development state — the
  project runs fine with no Anthropic account at all.
- **SDK not installed** — same path.
- **Connection error or provider 5xx** → `AIProviderUnavailable`.
- **Timeout** → `AIProviderTimeout`. Requests carry an explicit timeout so a
  hanging provider can't stall a job.
- **Rate limited** → `AIProviderRateLimited`.

In every case the AI test-generation service returns an empty outcome marked
unavailable with a reason, and the analysis proceeds with deterministic
generation only. For explanations, the service falls back to a **deterministic
explanation** built from the actual execution results, tagged `source:
"deterministic"` and `ai_generated: false`. The UI shows the same
clearly-labelled explanation panel either way, so a user can always tell which
kind they're looking at.

Two consequences worth stating:

- **Degraded, not broken.** Losing Claude means fewer targeted test inputs and
  a less fluent explanation. Detection capability drops but doesn't vanish —
  the benchmark measures the deterministic strategy at 88.9% on its own, versus
  66.7% for AI-proposed inputs.
- **This is also the cost-control lever.** If spend spikes, you can unset the
  API key and the service keeps working deterministically. That's in the
  deployment runbook as a mitigation.

---

## 11. How the project is evaluated

Not with a demo. With a **reproducible benchmark against a labelled dataset**,
committed to the repo.

**The dataset:** 36 pairs across **13 bug categories** — off-by-one, duplicate
handling, empty input, negative values, incorrect exception behavior, mutation
of input, sorting assumptions, boundary conditions, floating-point behavior,
incorrect loop termination, incorrect search bounds, state leakage, and order
preservation. Each case has a natural-language spec, a correct reference, a
candidate with exactly one deliberate bug, a category, and at least one known
counterexample.

**The dataset is itself tested.** `test_benchmark_dataset.py` executes every
candidate against its reference to assert the known counterexample genuinely
diverges and that listed agreements genuinely agree. This caught **four real
bugs in my own test data** the first time I ran it — counterexamples that
didn't actually differ and "agreements" that did. Without that harness the
benchmark would have been measuring against partly-wrong ground truth.

**What's measured:** detection rate overall, by strategy, and by category; time
to first counterexample; total execution count; counterexample size and
minimization reduction; invalid AI test rate; Claude latency, request count,
and estimated tokens; false bug reports; and timeouts.

**Reproducibility.** Every seed-dependent input — deterministic generation, AI
proposals, the property search — derives from one `--seed`. The same seed
produces identical results. The AI strategy uses a deterministic mock provider
by default, so the benchmark runs offline, free, and repeatably; `--use-real-ai`
opts into live calls.

**Baseline (seed 0):** 88.9% detection (32/36), **zero false bug reports**,
zero timeouts. Deterministic 88.9%, Hypothesis 86.1%, AI-proposed 66.7%. Eleven
of thirteen categories at 100%.

**The honesty constraints are part of the design.** Rates are computed over all
cases including misses; the generated Markdown report *always* contains a
"Misses and limitations" section naming every undetected case; the four misses
(all mutation and state-leakage) are architectural blind spots that stay in the
dataset rather than being quietly removed to improve the number.

Around that sit **252 backend test functions across 21 files** — comparison
rules, minimizer behavior, the async job lifecycle, security controls,
persistence, and config path resolution — plus frontend component tests.

**Interview framing:** "I can tell you the detection rate, which categories it
fails, and why — and I didn't choose the number."

---

## 12. What I would change for production scale

Roughly in order of what I'd do first.

**Replace the sandbox architecture.** The highest-value change. Move execution
to per-job **microVMs (Firecracker) or gVisor** on a dedicated, egress-filtered
node, and replace Docker-socket mounting with a **brokered runner service** that
accepts only "run this function on this input" and holds the privileges itself.
That converts the worker from near-root to least-privilege and removes the
kernel-sharing weakness discussed above.

**Move rate limiting to Redis.** The current token bucket is in-process, so it's
correct for one API instance and wrong the moment you scale horizontally — each
replica would enforce its own budget. Redis is already in the stack; the
limiter interface is small enough to swap. The concurrency and quota checks are
already DB-backed and correct across processes.

**Add real authentication.** Today identity is an opt-in client header falling
back to IP, which deters casual abuse but is spoofable and unfair behind shared
NATs. Real accounts with sessions would enable per-user quotas, durable
ownership of stored code, and a genuine multi-tenant story.

**Scale workers independently, with queue priorities.** Workers are the
expensive tier. Autoscale on queue depth rather than request rate, and split
queues so a long analysis can't head-of-line-block a short one. Container reuse
via a warm pool would cut per-execution overhead — carefully, since fresh
containers are what guarantee measurement integrity.

**Close the correctness blind spots.** Detect input mutation by capturing the
argument before the call and comparing it after; detect state leakage by
running a sequence of calls in one process and comparing behavior across the
sequence. Both need execution-model changes, which is exactly why they're
unfixed today — and both are already sitting in the benchmark as 0% categories
waiting to be moved.

**Finish the async pipeline.** `searching_properties` and `minimizing` are
currently status placeholders on the async path; their results aren't folded
into the stored run. Wiring them through would put Hypothesis search and
minimization on the main flow rather than a side path.

**Real observability.** Request-id correlation through API → queue → worker,
distributed tracing across the stages, and metrics on detection rate, queue
depth, execution latency, and Claude spend. Structured JSON logging is already
in place, so this is mostly propagation and dashboards.

**Data lifecycle.** Stored code is user content: add a retention policy with
automatic expiry, make deletion propagate to backups, and consider encrypting
submitted code at rest. Partition or archive `executions`, which is by far the
fastest-growing table.

**Harden cost control.** Today there are per-analysis AI budgets and per-client
quotas. At scale I'd add a global circuit breaker on Claude spend that trips to
deterministic-only mode automatically — the graceful-degradation path already
exists, so it's a matter of triggering it on a budget signal rather than a
provider error.

**Broaden the interface.** One `list[int]` argument was the right scoping call
for a portfolio project — it made execution, serialization, generation, and
shrinking all tractable, and I'd defend it. But a production tool needs richer
signatures, which means a generation and shrinking strategy per supported type
and a much more careful serialization boundary. That's a substantial project in
its own right, not an incremental change.
