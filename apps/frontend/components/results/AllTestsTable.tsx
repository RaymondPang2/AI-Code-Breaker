// The "All Tests" tab: a filterable, expandable table of every comparison.
// Filters (outcome / source / category) are real <select>s with labels;
// rows expand via an accessible button that toggles aria-expanded.

"use client";

import { useMemo, useState } from "react";

import type { TestComparisonResult } from "@/lib/types";
import {
  comparisonOutcome,
  describeResult,
  failureCategory,
  formatRuntimeMs,
  isException,
  safeJson,
  type ComparisonOutcome,
} from "@/lib/format";
import CopyButton from "@/components/ui/CopyButton";

const OUTCOME_LABELS: Record<ComparisonOutcome | "all", string> = {
  all: "All outcomes",
  match: "Passed (match)",
  mismatch: "Failed (mismatch)",
  inconclusive: "Inconclusive",
};

function OutcomeBadge({ outcome }: { outcome: ComparisonOutcome }) {
  const cls =
    outcome === "match"
      ? "bg-emerald-500/15 text-emerald-300"
      : outcome === "mismatch"
        ? "bg-rose-500/15 text-rose-300"
        : "bg-amber-500/15 text-amber-300";
  return (
    <span
      className={`rounded px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wide ${cls}`}
    >
      {outcome}
    </span>
  );
}

function ExecutionDetail({
  title,
  result,
}: {
  title: string;
  result: TestComparisonResult["candidate"];
}) {
  return (
    <div className="rounded border border-slate-800 bg-black/20 p-2.5">
      <div className="mb-1 flex items-center justify-between">
        <span className="font-mono text-[10px] uppercase tracking-widest text-slate-500">
          {title}
        </span>
        {result.runtime_ms != null && (
          <span className="font-mono text-[10px] text-slate-500">
            {formatRuntimeMs(result.runtime_ms)}
          </span>
        )}
      </div>
      {result.status === "success" ? (
        <code className="block break-all font-mono text-xs text-slate-200">
          {safeJson(result.returned_value)}
        </code>
      ) : isException(result) ? (
        <div className="font-mono text-xs">
          <span className="text-rose-300">{result.exception_type}</span>
          {result.exception_message && (
            <span className="text-slate-400">: {result.exception_message}</span>
          )}
        </div>
      ) : (
        <span className="font-mono text-xs text-amber-300">
          {describeResult(result)}
        </span>
      )}
      {result.stdout ? (
        <pre className="mt-1.5 max-h-24 overflow-auto rounded bg-black/30 p-1.5 text-[11px] text-slate-400">
          {result.stdout}
        </pre>
      ) : null}
    </div>
  );
}

function Row({ c, index }: { c: TestComparisonResult; index: number }) {
  const [open, setOpen] = useState(false);
  const outcome = comparisonOutcome(c);
  const rowId = `test-row-${index}`;
  const panelId = `test-panel-${index}`;

  return (
    <div className="rounded-lg border border-slate-800 bg-[#0d1017]">
      <button
        type="button"
        id={rowId}
        aria-expanded={open}
        aria-controls={panelId}
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center gap-3 px-3 py-2.5 text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-400 focus-visible:ring-offset-2 focus-visible:ring-offset-[#0b0d12]"
      >
        <span
          aria-hidden="true"
          className={`text-slate-500 transition-transform ${open ? "rotate-90" : ""}`}
        >
          ▸
        </span>
        <code className="flex-1 truncate font-mono text-xs text-slate-300">
          {safeJson(c.input)}
        </code>
        <span className="hidden font-mono text-[10px] uppercase tracking-widest text-slate-600 sm:inline">
          {c.source}
        </span>
        <OutcomeBadge outcome={outcome} />
      </button>
      {open && (
        <div id={panelId} role="region" aria-labelledby={rowId} className="border-t border-slate-800 px-3 py-3">
          <div className="mb-2 flex flex-wrap items-center gap-2 text-[11px] text-slate-500">
            <span>
              category: <span className="text-slate-300">{failureCategory(c)}</span>
            </span>
            {c.reason ? (
              <span className="text-slate-500">· {c.reason}</span>
            ) : null}
            <CopyButton value={safeJson(c.input)} label="Copy input" />
          </div>
          <div className="grid gap-2 sm:grid-cols-2">
            <ExecutionDetail title="candidate (actual)" result={c.candidate} />
            <ExecutionDetail title="reference (expected)" result={c.reference} />
          </div>
        </div>
      )}
    </div>
  );
}

export default function AllTestsTable({
  comparisons,
}: {
  comparisons: TestComparisonResult[];
}) {
  const [outcomeFilter, setOutcomeFilter] = useState<ComparisonOutcome | "all">("all");
  const [sourceFilter, setSourceFilter] = useState<string>("all");
  const [categoryFilter, setCategoryFilter] = useState<string>("all");

  const sources = useMemo(
    () => Array.from(new Set(comparisons.map((c) => c.source))).sort(),
    [comparisons],
  );
  const categories = useMemo(
    () => Array.from(new Set(comparisons.map((c) => failureCategory(c)))).sort(),
    [comparisons],
  );

  const filtered = useMemo(
    () =>
      comparisons.filter((c) => {
        if (outcomeFilter !== "all" && comparisonOutcome(c) !== outcomeFilter) {
          return false;
        }
        if (sourceFilter !== "all" && c.source !== sourceFilter) return false;
        if (categoryFilter !== "all" && failureCategory(c) !== categoryFilter) {
          return false;
        }
        return true;
      }),
    [comparisons, outcomeFilter, sourceFilter, categoryFilter],
  );

  if (comparisons.length === 0) {
    return (
      <p className="rounded-lg border border-slate-800 bg-[#0d1017] px-4 py-6 text-center text-sm text-slate-500">
        No test comparisons were recorded for this run.
      </p>
    );
  }

  const selectCls =
    "rounded-md border border-slate-800 bg-[#0d1017] px-2 py-1.5 text-xs text-slate-200 focus:border-slate-600 focus:outline-none focus:ring-1 focus:ring-slate-600";

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap gap-3">
        <div className="flex flex-col gap-1">
          <label htmlFor="filter-outcome" className="text-[10px] uppercase tracking-widest text-slate-500">
            Outcome
          </label>
          <select
            id="filter-outcome"
            value={outcomeFilter}
            onChange={(e) => setOutcomeFilter(e.target.value as ComparisonOutcome | "all")}
            className={selectCls}
          >
            {(["all", "match", "mismatch", "inconclusive"] as const).map((o) => (
              <option key={o} value={o}>
                {OUTCOME_LABELS[o]}
              </option>
            ))}
          </select>
        </div>
        <div className="flex flex-col gap-1">
          <label htmlFor="filter-source" className="text-[10px] uppercase tracking-widest text-slate-500">
            Source
          </label>
          <select
            id="filter-source"
            value={sourceFilter}
            onChange={(e) => setSourceFilter(e.target.value)}
            className={selectCls}
          >
            <option value="all">All sources</option>
            {sources.map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </select>
        </div>
        <div className="flex flex-col gap-1">
          <label htmlFor="filter-category" className="text-[10px] uppercase tracking-widest text-slate-500">
            Category
          </label>
          <select
            id="filter-category"
            value={categoryFilter}
            onChange={(e) => setCategoryFilter(e.target.value)}
            className={selectCls}
          >
            <option value="all">All categories</option>
            {categories.map((cat) => (
              <option key={cat} value={cat}>
                {cat}
              </option>
            ))}
          </select>
        </div>
      </div>

      <p className="text-xs text-slate-500" aria-live="polite">
        Showing {filtered.length} of {comparisons.length} tests
      </p>

      <div className="space-y-2">
        {filtered.map((c, i) => (
          <Row key={i} c={c} index={i} />
        ))}
        {filtered.length === 0 && (
          <p className="rounded-lg border border-slate-800 bg-[#0d1017] px-4 py-6 text-center text-sm text-slate-500">
            No tests match the current filters.
          </p>
        )}
      </div>
    </div>
  );
}
