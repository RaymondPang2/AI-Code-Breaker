// Live progress for an in-flight analysis job: the current stage, a
// progress bar, and clear terminal states (failed / cancelled). Accessible:
// the status region is aria-live so screen readers hear stage changes, and
// the bar exposes progressbar semantics.

"use client";

import type { AnalysisRunRead } from "@/lib/types";

const STAGE_LABELS: Record<string, string> = {
  queued: "Queued",
  generating_tests: "Generating tests",
  executing_tests: "Executing tests",
  searching_properties: "Searching properties",
  minimizing: "Minimizing counterexample",
  explaining: "Explaining",
  completed: "Completed",
  failed: "Failed",
  cancelled: "Cancelled",
};

export default function AnalysisProgress({
  run,
  polling,
}: {
  run: AnalysisRunRead | null;
  polling: boolean;
}) {
  // Before the first poll returns, show a queued placeholder.
  const status = run?.status ?? "queued";
  const progress = run?.progress ?? 0;
  const label = STAGE_LABELS[status] ?? status;

  const failed = status === "failed";
  const cancelled = status === "cancelled";
  const completed = status === "completed";
  const pct = Math.round(Math.min(1, Math.max(0, progress)) * 100);

  const barColor = failed
    ? "bg-rose-500"
    : cancelled
      ? "bg-slate-500"
      : completed
        ? "bg-emerald-500"
        : "bg-emerald-400";

  return (
    <div className="rounded-lg border border-slate-800 bg-[#0d1017] px-4 py-3">
      <div className="mb-2 flex items-center justify-between">
        <div className="flex items-center gap-2">
          {polling && !completed && !failed && !cancelled && (
            <span
              aria-hidden="true"
              className="h-3.5 w-3.5 animate-spin rounded-full border-2 border-emerald-300 border-t-transparent"
            />
          )}
          <span className="text-sm font-medium text-slate-200">{label}</span>
        </div>
        <span className="font-mono text-xs text-slate-500 tabular-nums">
          {pct}%
        </span>
      </div>

      <div
        role="progressbar"
        aria-valuenow={pct}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-label="Analysis progress"
        className="h-1.5 w-full overflow-hidden rounded-full bg-slate-800"
      >
        <div
          className={`h-full rounded-full transition-all duration-500 ${barColor}`}
          style={{ width: `${pct}%` }}
        />
      </div>

      {/* Screen-reader announcement of stage changes. */}
      <p aria-live="polite" className="sr-only">
        Analysis status: {label}
      </p>

      {failed && run?.error && (
        <p className="mt-2 text-xs text-rose-300">
          {run.error}
        </p>
      )}
      {cancelled && (
        <p className="mt-2 text-xs text-slate-400">
          Analysis was cancelled.
        </p>
      )}
    </div>
  );
}
