// The full analysis results view: a 5-tab interface over a normalized
// ResultModel. Verified execution facts and AI-generated commentary are
// visually and structurally separated — AI content always sits behind an
// "AI-generated" badge and an explicit "not a verified result" caveat.
//
// Tab semantics follow the WAI-ARIA tabs pattern: role="tablist" /
// role="tab" / role="tabpanel", arrow-key navigation, and roving tabindex.

"use client";

import { useId, useRef, useState } from "react";

import type { ResultModel } from "@/lib/resultModel";
import {
  describeResult,
  failureCategory,
  formatDuration,
  formatRuntimeMs,
  hasUsageDetail,
  isException,
  safeJson,
} from "@/lib/format";
import type { AiUsage, FunctionExecutionResult } from "@/lib/types";
import CopyButton from "@/components/ui/CopyButton";
import AllTestsTable from "./AllTestsTable";

const TABS = [
  "Overview",
  "Counterexample",
  "All Tests",
  "Execution Details",
  "AI Explanation",
] as const;
type TabName = (typeof TABS)[number];

export default function ResultsView({ model }: { model: ResultModel }) {
  const [active, setActive] = useState<TabName>("Overview");
  const baseId = useId();
  const tabRefs = useRef<(HTMLButtonElement | null)[]>([]);

  function onKeyDown(e: React.KeyboardEvent, index: number) {
    let next = index;
    if (e.key === "ArrowRight") next = (index + 1) % TABS.length;
    else if (e.key === "ArrowLeft") next = (index - 1 + TABS.length) % TABS.length;
    else if (e.key === "Home") next = 0;
    else if (e.key === "End") next = TABS.length - 1;
    else return;
    e.preventDefault();
    setActive(TABS[next]);
    tabRefs.current[next]?.focus();
  }

  return (
    <section aria-label="Analysis results" className="space-y-4">
      <div
        role="tablist"
        aria-label="Analysis result sections"
        className="flex flex-wrap gap-1 border-b border-slate-800"
      >
        {TABS.map((tab, i) => {
          const selected = tab === active;
          return (
            <button
              key={tab}
              ref={(el) => {
                tabRefs.current[i] = el;
              }}
              role="tab"
              id={`${baseId}-tab-${i}`}
              aria-selected={selected}
              aria-controls={`${baseId}-panel-${i}`}
              tabIndex={selected ? 0 : -1}
              onClick={() => setActive(tab)}
              onKeyDown={(e) => onKeyDown(e, i)}
              className={`-mb-px rounded-t-md border-b-2 px-3 py-2 text-sm font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-400 ${
                selected
                  ? "border-emerald-400 text-slate-100"
                  : "border-transparent text-slate-400 hover:text-slate-200"
              }`}
            >
              {tab}
            </button>
          );
        })}
      </div>

      {TABS.map((tab, i) => (
        <div
          key={tab}
          role="tabpanel"
          id={`${baseId}-panel-${i}`}
          aria-labelledby={`${baseId}-tab-${i}`}
          hidden={tab !== active}
          tabIndex={0}
          className="focus-visible:outline-none"
        >
          {tab === active && <TabContent tab={tab} model={model} />}
        </div>
      ))}
    </section>
  );
}

function TabContent({ tab, model }: { tab: TabName; model: ResultModel }) {
  switch (tab) {
    case "Overview":
      return <OverviewTab model={model} />;
    case "Counterexample":
      return <CounterexampleTab model={model} />;
    case "All Tests":
      return <AllTestsTable comparisons={model.comparisons} />;
    case "Execution Details":
      return <ExecutionDetailsTab model={model} />;
    case "AI Explanation":
      return <ExplanationTab model={model} />;
  }
}

// --- Overview ---------------------------------------------------------------

function StatCard({
  label,
  value,
  tone = "neutral",
}: {
  label: string;
  value: number | string;
  tone?: "neutral" | "pass" | "fail";
}) {
  const toneClass =
    tone === "pass"
      ? "text-emerald-400"
      : tone === "fail"
        ? "text-rose-400"
        : "text-slate-200";
  return (
    <div className="rounded-lg border border-slate-800 bg-[#0d1017] px-4 py-3">
      <div className="font-mono text-[10px] uppercase tracking-widest text-slate-500">
        {label}
      </div>
      <div className={`mt-1 text-2xl font-semibold tabular-nums ${toneClass}`}>
        {value}
      </div>
    </div>
  );
}

function OverviewTab({ model }: { model: ResultModel }) {
  const duration = formatDuration(model.elapsedSeconds);
  return (
    <div className="space-y-4">
      <div className="grid grid-cols-4 gap-3">
        <StatCard label="tests" value={model.totalTests} />
        <StatCard label="passed" value={model.passedTests} tone="pass" />
        <StatCard label="failed" value={model.failedTests} tone="fail" />
        <StatCard label="inconclusive" value={model.inconclusiveTests} />
      </div>

      <dl className="grid gap-3 sm:grid-cols-2">
        <div className="rounded-lg border border-slate-800 bg-[#0d1017] px-4 py-3">
          <dt className="font-mono text-[10px] uppercase tracking-widest text-slate-500">
            Analysis duration
          </dt>
          <dd className="mt-1 text-sm text-slate-200">
            {duration ?? (
              <span className="text-slate-600">not available for this view</span>
            )}
          </dd>
        </div>
        <div className="rounded-lg border border-slate-800 bg-[#0d1017] px-4 py-3">
          <dt className="font-mono text-[10px] uppercase tracking-widest text-slate-500">
            Strategies used
          </dt>
          <dd className="mt-1 flex flex-wrap gap-1.5">
            {model.strategies.length > 0 ? (
              model.strategies.map((s) => (
                <span
                  key={s}
                  className="rounded bg-slate-800/60 px-1.5 py-0.5 text-xs text-slate-300"
                >
                  {s}
                </span>
              ))
            ) : (
              <span className="text-sm text-slate-600">none recorded</span>
            )}
          </dd>
        </div>
      </dl>

      {model.failedTests > 0 && model.firstFailingInput !== null ? (
        <div className="rounded-lg border border-rose-500/40 bg-rose-500/5 px-4 py-3">
          <div className="flex items-center justify-between">
            <span className="text-sm font-medium text-rose-300">
              First failing input
            </span>
            <CopyButton
              value={safeJson(model.firstFailingInput)}
              label="Copy failing input"
            />
          </div>
          <code className="mt-1 block break-all font-mono text-sm text-slate-200">
            {safeJson(model.firstFailingInput)}
          </code>
        </div>
      ) : null}

      {model.inconclusiveTests > 0 ? (
        <div className="rounded-lg border border-amber-500/40 bg-amber-500/5 px-4 py-3 text-sm text-amber-300">
          <span className="font-medium">Execution error — </span>
          {model.inconclusiveTests} of {model.totalTests}{" "}
          {model.inconclusiveTests === 1 ? "test" : "tests"} could not be
          compared because the runner failed to execute the code. These are
          not passes or failures; the analysis was inconclusive for them.
          Check the Execution Details tab for the specific runner error.
        </div>
      ) : null}

      {model.failedTests === 0 && model.inconclusiveTests === 0 ? (
        <div className="rounded-lg border border-emerald-500/40 bg-emerald-500/5 px-4 py-3 text-sm text-emerald-300">
          No behavioral differences were found across the tested inputs.
        </div>
      ) : null}
    </div>
  );
}

// --- Counterexample ---------------------------------------------------------

function ResultBlock({
  title,
  tone,
  result,
}: {
  title: string;
  tone: "candidate" | "reference";
  result: FunctionExecutionResult | null;
}) {
  const accent =
    tone === "reference" ? "border-emerald-500/30" : "border-rose-500/30";
  return (
    <div className={`rounded-lg border ${accent} bg-[#0d1017] p-3`}>
      <div className="mb-1.5 font-mono text-[10px] uppercase tracking-widest text-slate-500">
        {title}
      </div>
      {result === null ? (
        <span className="text-sm text-slate-600">not available</span>
      ) : result.status === "success" ? (
        <code className="block break-all font-mono text-sm text-slate-200">
          {safeJson(result.returned_value)}
        </code>
      ) : isException(result) ? (
        <div className="font-mono text-sm">
          <span className="text-rose-300">{result.exception_type}</span>
          {result.exception_message && (
            <span className="text-slate-400">: {result.exception_message}</span>
          )}
        </div>
      ) : (
        <span className="font-mono text-sm text-amber-300">
          {describeResult(result)}
        </span>
      )}
    </div>
  );
}

function CounterexampleTab({ model }: { model: ResultModel }) {
  if (model.firstFailingInput === null) {
    // Distinguish "genuinely agreed" from "couldn't be compared". Claiming
    // agreement when every test was inconclusive would be misleading.
    if (model.inconclusiveTests > 0 && model.failedTests === 0) {
      return (
        <p className="rounded-lg border border-amber-500/40 bg-amber-500/5 px-4 py-6 text-center text-sm text-amber-300">
          No counterexample available — {model.inconclusiveTests} of{" "}
          {model.totalTests}{" "}
          {model.inconclusiveTests === 1 ? "test" : "tests"} could not be
          executed (runner error), so no comparison was possible. See the
          Execution Details tab.
        </p>
      );
    }
    return (
      <p className="rounded-lg border border-emerald-500/40 bg-emerald-500/5 px-4 py-6 text-center text-sm text-emerald-300">
        No counterexample — candidate and reference agreed on every tested input.
      </p>
    );
  }
  return (
    <div className="space-y-4">
      <div className="grid gap-3 sm:grid-cols-2">
        <div className="rounded-lg border border-slate-800 bg-[#0d1017] px-4 py-3">
          <div className="flex items-center justify-between">
            <span className="font-mono text-[10px] uppercase tracking-widest text-slate-500">
              Original counterexample
            </span>
            {model.originalInput && (
              <CopyButton value={safeJson(model.originalInput)} label="Copy original input" />
            )}
          </div>
          <code className="mt-1 block break-all font-mono text-sm text-slate-200">
            {model.originalInput ? safeJson(model.originalInput) : "—"}
          </code>
        </div>
        <div className="rounded-lg border border-slate-800 bg-[#0d1017] px-4 py-3">
          <div className="flex items-center justify-between">
            <span className="font-mono text-[10px] uppercase tracking-widest text-slate-500">
              Minimal counterexample
            </span>
            {model.minimizedInput && (
              <CopyButton value={safeJson(model.minimizedInput)} label="Copy minimal input" />
            )}
          </div>
          <code className="mt-1 block break-all font-mono text-sm text-slate-200">
            {model.minimizedInput
              ? safeJson(model.minimizedInput)
              : model.fromPersisted
                ? "not minimized"
                : "not available for this view"}
          </code>
        </div>
      </div>

      <div className="grid gap-3 sm:grid-cols-2">
        <ResultBlock
          title="expected · reference behavior"
          tone="reference"
          result={model.referenceResult}
        />
        <ResultBlock
          title="actual · candidate behavior"
          tone="candidate"
          result={model.candidateResult}
        />
      </div>
    </div>
  );
}

// --- Execution Details ------------------------------------------------------

function UsageTable({ usage }: { usage: AiUsage }) {
  const rows: [string, string][] = [];
  if (usage.model) rows.push(["model", usage.model]);
  if (usage.input_tokens != null) rows.push(["input tokens", String(usage.input_tokens)]);
  if (usage.output_tokens != null) rows.push(["output tokens", String(usage.output_tokens)]);
  if (usage.latency_ms != null)
    rows.push(["latency", `${Math.round(usage.latency_ms)} ms`]);
  if (usage.request_count != null)
    rows.push(["requests", String(usage.request_count)]);
  if (usage.available != null)
    rows.push(["available", usage.available ? "yes" : "no"]);
  if (usage.error) rows.push(["note", usage.error]);
  return (
    <table className="w-full text-left text-xs">
      <tbody>
        {rows.map(([k, v]) => (
          <tr key={k} className="border-b border-slate-800/60 last:border-0">
            <th scope="row" className="py-1 pr-4 font-normal text-slate-500">
              {k}
            </th>
            <td className="py-1 font-mono text-slate-300">{v}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function ExecutionDetailsTab({ model }: { model: ResultModel }) {
  const failing = model.comparisons.filter((c) => !c.match && !c.internal_error);
  return (
    <div className="space-y-4">
      <dl className="grid gap-3 sm:grid-cols-3">
        <div className="rounded-lg border border-slate-800 bg-[#0d1017] px-3 py-2.5">
          <dt className="font-mono text-[10px] uppercase tracking-widest text-slate-500">
            duration
          </dt>
          <dd className="mt-0.5 text-sm text-slate-200">
            {formatDuration(model.elapsedSeconds) ?? (
              <span className="text-slate-600">—</span>
            )}
          </dd>
        </div>
        <div className="rounded-lg border border-slate-800 bg-[#0d1017] px-3 py-2.5">
          <dt className="font-mono text-[10px] uppercase tracking-widest text-slate-500">
            seed
          </dt>
          <dd className="mt-0.5 text-sm text-slate-200">
            {model.seed != null ? model.seed : <span className="text-slate-600">—</span>}
          </dd>
        </div>
        <div className="rounded-lg border border-slate-800 bg-[#0d1017] px-3 py-2.5">
          <dt className="font-mono text-[10px] uppercase tracking-widest text-slate-500">
            confirmed failures
          </dt>
          <dd className="mt-0.5 text-sm text-slate-200">{failing.length}</dd>
        </div>
      </dl>

      <div>
        <h3 className="mb-2 text-sm font-semibold text-slate-300">
          Per-execution runtime
        </h3>
        {model.comparisons.length === 0 ? (
          <p className="text-sm text-slate-600">No executions recorded.</p>
        ) : (
          <div className="overflow-x-auto rounded-lg border border-slate-800">
            <table className="w-full text-left text-xs">
              <thead className="bg-black/20 text-slate-500">
                <tr>
                  <th scope="col" className="px-3 py-2 font-medium">input</th>
                  <th scope="col" className="px-3 py-2 font-medium">candidate</th>
                  <th scope="col" className="px-3 py-2 font-medium">reference</th>
                </tr>
              </thead>
              <tbody>
                {model.comparisons.map((c, i) => (
                  <tr key={i} className="border-t border-slate-800/60">
                    <td className="px-3 py-1.5">
                      <code className="font-mono text-slate-300">{safeJson(c.input)}</code>
                    </td>
                    <td className="px-3 py-1.5 font-mono text-slate-400">
                      {formatRuntimeMs(c.candidate.runtime_ms) ?? "—"}
                    </td>
                    <td className="px-3 py-1.5 font-mono text-slate-400">
                      {formatRuntimeMs(c.reference.runtime_ms) ?? "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {(hasUsageDetail(model.aiUsage) || hasUsageDetail(model.explanationUsage)) && (
        <div className="grid gap-3 sm:grid-cols-2">
          {hasUsageDetail(model.aiUsage) && model.aiUsage && (
            <div className="rounded-lg border border-slate-800 bg-[#0d1017] p-3">
              <h4 className="mb-1.5 text-xs font-semibold text-slate-300">
                Test-generation usage
              </h4>
              <UsageTable usage={model.aiUsage} />
            </div>
          )}
          {hasUsageDetail(model.explanationUsage) && model.explanationUsage && (
            <div className="rounded-lg border border-slate-800 bg-[#0d1017] p-3">
              <h4 className="mb-1.5 text-xs font-semibold text-slate-300">
                Explanation usage
              </h4>
              <UsageTable usage={model.explanationUsage} />
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// --- AI Explanation ---------------------------------------------------------

function ExplanationTab({ model }: { model: ResultModel }) {
  const exp = model.explanation;
  if (!exp) {
    return (
      <p className="rounded-lg border border-slate-800 bg-[#0d1017] px-4 py-6 text-center text-sm text-slate-500">
        No explanation was generated for this run. Enable “Claude explanation”
        (or the deterministic fallback runs when a counterexample is found).
      </p>
    );
  }
  const aiGenerated = exp.ai_generated;
  return (
    <div className="space-y-4">
      {/* Provenance banner: the clearest signal of verified vs AI content. */}
      <div
        className={`rounded-lg border px-4 py-2.5 text-xs ${
          aiGenerated
            ? "border-violet-500/40 bg-violet-500/5 text-violet-200"
            : "border-slate-700 bg-slate-800/30 text-slate-300"
        }`}
      >
        {aiGenerated ? (
          <>
            <span className="font-semibold">AI-generated commentary.</span> This
            section is written by Claude to help interpret the result. It is not
            a verified fact — the verified pass/fail outcome lives in the other
            tabs.
          </>
        ) : (
          <>
            <span className="font-semibold">Deterministic explanation.</span>{" "}
            Generated from the execution facts without AI.
          </>
        )}
      </div>

      <div className="rounded-lg border border-slate-800 bg-[#0d1017] p-4">
        <div className="mb-2 flex flex-wrap items-center gap-2">
          <h3 className="text-sm font-semibold text-slate-200">Summary</h3>
          <span
            className={`rounded px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wide ${
              aiGenerated
                ? "bg-violet-500/15 text-violet-300"
                : "bg-slate-700/40 text-slate-400"
            }`}
          >
            {aiGenerated ? "AI-generated" : "deterministic"}
          </span>
          <span className="font-mono text-[10px] uppercase tracking-widest text-slate-600">
            confidence: {exp.confidence}
          </span>
        </div>
        <p className="text-sm text-slate-200">{exp.summary}</p>
        {exp.root_cause && (
          <>
            <h4 className="mt-3 text-xs font-semibold text-slate-400">Root cause</h4>
            <p className="mt-1 text-sm text-slate-300">{exp.root_cause}</p>
          </>
        )}
      </div>

      {exp.walkthrough.length > 0 && (
        <div className="rounded-lg border border-slate-800 bg-[#0d1017] p-4">
          <h4 className="mb-2 text-xs font-semibold text-slate-400">Walkthrough</h4>
          <ol className="list-inside list-decimal space-y-1 text-sm text-slate-300">
            {exp.walkthrough.map((step, i) => (
              <li key={i}>{step}</li>
            ))}
          </ol>
        </div>
      )}

      {exp.suspected_lines.length > 0 && (
        <div className="rounded-lg border border-slate-800 bg-[#0d1017] p-4">
          <h4 className="mb-1 text-xs font-semibold text-slate-400">
            Suspected lines
          </h4>
          <p className="font-mono text-sm text-slate-300">
            {exp.suspected_lines.join(", ")}
          </p>
        </div>
      )}

      {exp.suggested_fix && (
        <div className="rounded-lg border border-amber-500/30 bg-amber-500/5 p-4">
          <div className="mb-1 flex items-center gap-2">
            <h4 className="text-xs font-semibold text-amber-300">Suggested fix</h4>
            <span className="rounded bg-amber-500/15 px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wide text-amber-300">
              proposal · not verified
            </span>
          </div>
          <p className="text-sm text-slate-300">{exp.suggested_fix}</p>
        </div>
      )}

      {exp.suggested_patch && (
        <div className="rounded-lg border border-amber-500/30 bg-amber-500/5 p-4">
          <div className="mb-2 flex items-center justify-between">
            <div className="flex items-center gap-2">
              <h4 className="text-xs font-semibold text-amber-300">
                Suggested patch
              </h4>
              <span className="rounded bg-amber-500/15 px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wide text-amber-300">
                proposal · not applied
              </span>
            </div>
            <CopyButton value={exp.suggested_patch} label="Copy suggested patch" />
          </div>
          <pre className="max-h-80 overflow-auto rounded bg-black/30 p-3 font-mono text-xs text-slate-200">
            {exp.suggested_patch}
          </pre>
          <p className="mt-2 text-[11px] text-slate-500">
            This patch is a suggestion only. It has not been tested and is never
            applied to your code automatically — review it before use.
          </p>
        </div>
      )}
    </div>
  );
}
