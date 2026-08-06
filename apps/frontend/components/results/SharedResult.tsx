// Client view for a shared/persisted result. Fetches the stored analysis run
// and its submission by ID, then renders the same ResultsView used inline on
// the submission page. Provides explicit loading, error, and empty states —
// and never fabricates a result: a 404 becomes a clear "not found" message.

"use client";

import { useEffect, useState } from "react";

import { ApiError, fetchAnalysisRun, fetchSubmission } from "@/lib/api";
import { fromPersistedRun } from "@/lib/resultModel";
import type { AnalysisRunRead, SubmissionRead } from "@/lib/types";
import ResultsView from "@/components/results/ResultsView";
import CopyButton from "@/components/ui/CopyButton";

type LoadState =
  | { phase: "loading" }
  | { phase: "error"; message: string; notFound: boolean }
  | { phase: "ready"; run: AnalysisRunRead; submission: SubmissionRead | null };

export default function SharedResult({
  submissionId,
  analysisId,
}: {
  submissionId: string;
  analysisId: string;
}) {
  const [state, setState] = useState<LoadState>({ phase: "loading" });

  useEffect(() => {
    const controller = new AbortController();
    let cancelled = false;

    async function load() {
      setState({ phase: "loading" });
      try {
        // Fetch the run first (the essential data). The submission (for the
        // function name / code) is best-effort — if it fails we still show
        // the run rather than nothing.
        const run = await fetchAnalysisRun(submissionId, analysisId, controller.signal);
        let submission: SubmissionRead | null = null;
        try {
          submission = await fetchSubmission(submissionId, controller.signal);
        } catch {
          submission = null;
        }
        if (!cancelled) setState({ phase: "ready", run, submission });
      } catch (err) {
        if (err instanceof DOMException && err.name === "AbortError") return;
        const notFound = err instanceof ApiError && err.status === 404;
        const message =
          err instanceof ApiError
            ? err.message
            : "Something went wrong loading this result.";
        if (!cancelled) setState({ phase: "error", message, notFound });
      }
    }

    load();
    return () => {
      cancelled = true;
      controller.abort();
    };
  }, [submissionId, analysisId]);

  if (state.phase === "loading") {
    return (
      <div
        role="status"
        aria-live="polite"
        className="flex items-center gap-3 rounded-lg border border-slate-800 bg-[#0d1017] px-4 py-6 text-sm text-slate-400"
      >
        <span
          aria-hidden="true"
          className="h-4 w-4 animate-spin rounded-full border-2 border-slate-500 border-t-transparent"
        />
        Loading saved analysis…
      </div>
    );
  }

  if (state.phase === "error") {
    return (
      <div
        role="alert"
        className="rounded-lg border border-rose-500/40 bg-rose-500/5 px-4 py-6 text-sm text-rose-200"
      >
        <p className="font-medium">
          {state.notFound ? "This result could not be found." : "Couldn’t load this result."}
        </p>
        <p className="mt-1 text-rose-300/80">{state.message}</p>
        <a
          href="/"
          className="mt-3 inline-block rounded-md border border-slate-700 px-3 py-1.5 text-xs font-medium text-slate-200 hover:bg-slate-800 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-400 focus-visible:ring-offset-2 focus-visible:ring-offset-[#0b0d12]"
        >
          ← Start a new analysis
        </a>
      </div>
    );
  }

  const { run, submission } = state;
  const model = fromPersistedRun(run);
  // The persisted model doesn't embed the function name; fill it from the
  // separately-fetched submission when available.
  if (submission) model.functionName = submission.function_name;

  return (
    <div className="space-y-5">
      <header className="space-y-1">
        <p className="font-mono text-xs uppercase tracking-[0.3em] text-slate-500">
          $ saved result
        </p>
        <h1 className="text-2xl font-semibold tracking-tight text-slate-50">
          {model.functionName ? (
            <>Analysis of <span className="font-mono">{model.functionName}</span></>
          ) : (
            "Saved analysis"
          )}
        </h1>
        <p className="text-xs text-slate-500">
          Run {run.id} · saved {new Date(run.created_at).toLocaleString()}
        </p>
      </header>

      {submission && (
        <div className="grid gap-3 lg:grid-cols-2">
          <CodePanel title="Candidate" code={submission.candidate_code} />
          <CodePanel title="Reference" code={submission.reference_code} />
        </div>
      )}

      <ResultsView model={model} />

      <a
        href="/"
        className="inline-block rounded-md border border-slate-700 px-3 py-1.5 text-xs font-medium text-slate-200 hover:bg-slate-800 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-400 focus-visible:ring-offset-2 focus-visible:ring-offset-[#0b0d12]"
      >
        ← Start a new analysis
      </a>
    </div>
  );
}

function CodePanel({ title, code }: { title: string; code: string }) {
  return (
    <div className="rounded-lg border border-slate-800 bg-[#0d1017]">
      <div className="flex items-center justify-between border-b border-slate-800 px-3 py-1.5">
        <span className="font-mono text-[10px] uppercase tracking-widest text-slate-500">
          {title}
        </span>
        <CopyButton value={code} label={`Copy ${title.toLowerCase()} code`} />
      </div>
      <pre className="max-h-64 overflow-auto p-3 font-mono text-xs text-slate-200">
        {code}
      </pre>
    </div>
  );
}
