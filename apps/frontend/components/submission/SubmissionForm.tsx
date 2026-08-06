"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import {
  ApiError,
  cancelAnalysis,
  createAnalysis,
  createSubmission,
} from "@/lib/api";
import { SECOND_LARGEST_EXAMPLE } from "@/lib/example";
import { useAnalysisPolling } from "@/lib/useAnalysisPolling";
import { fromPersistedRun } from "@/lib/resultModel";
import {
  validateSubmission,
  type SubmissionFormValues,
} from "@/lib/validation";

import CodeEditor from "./CodeEditor";
import ResultsView from "../results/ResultsView";
import ShareLink from "../results/ShareLink";
import AnalysisProgress from "../results/AnalysisProgress";
import FieldError from "../ui/FieldError";
import Toggle from "../ui/Toggle";

const EMPTY_FORM: SubmissionFormValues = {
  functionName: "",
  specification: "",
  candidateCode: "",
  referenceCode: "",
  testInputsRaw: "",
};

interface ToggleState {
  generateTests: boolean;
  hypothesis: boolean; // reserved: drives the search endpoint in a later wiring
  aiTests: boolean;
  explain: boolean;
}

const EMPTY_TOGGLES: ToggleState = {
  generateTests: true,
  hypothesis: false,
  aiTests: false,
  explain: false,
};

export default function SubmissionForm() {
  const [form, setForm] = useState<SubmissionFormValues>(EMPTY_FORM);
  const [toggles, setToggles] = useState<ToggleState>(EMPTY_TOGGLES);
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({});
  const [formError, setFormError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  // The ids of the in-flight / completed analysis; polling is driven off these.
  const [ids, setIds] = useState<{ submissionId: string; analysisId: string } | null>(
    null,
  );
  const abortRef = useRef<AbortController | null>(null);

  const { run, polling, error: pollError } = useAnalysisPolling(
    ids?.submissionId ?? null,
    ids?.analysisId ?? null,
  );

  function update<K extends keyof SubmissionFormValues>(
    key: K,
    value: SubmissionFormValues[K],
  ) {
    setForm((prev) => ({ ...prev, [key]: value }));
  }

  function loadExample() {
    setForm(SECOND_LARGEST_EXAMPLE);
    setFieldErrors({});
    setFormError(null);
    setIds(null);
  }

  function clearForm() {
    setForm(EMPTY_FORM);
    setToggles(EMPTY_TOGGLES);
    setFieldErrors({});
    setFormError(null);
    setIds(null);
  }

  async function handleAnalyze() {
    setFormError(null);

    // Client-side validation first, so obvious errors never cost a round trip.
    const { fieldErrors: clientErrors, parsedTestInputs } =
      validateSubmission(form);
    if (Object.keys(clientErrors).length > 0) {
      setFieldErrors(clientErrors);
      setFormError("Fix the highlighted fields, then analyze.");
      return;
    }
    setFieldErrors({});

    const controller = new AbortController();
    abortRef.current = controller;
    setSubmitting(true);
    setIds(null);

    try {
      // 1. Create the submission (immutable content).
      const created = await createSubmission(
        {
          function_name: form.functionName.trim(),
          specification: form.specification.trim(),
          candidate_code: form.candidateCode,
          reference_code: form.referenceCode,
        },
        controller.signal,
      );
      // 2. Start an analysis job; the API returns immediately with a queued id.
      const job = await createAnalysis(
        created.submission_id,
        {
          test_inputs: parsedTestInputs ?? [],
          generate_tests: toggles.generateTests,
          generation_seed: 0,
          use_ai_tests: toggles.aiTests,
          explain_counterexamples: toggles.explain,
          suggest_patch: false,
        },
        controller.signal,
      );
      // 3. Hand the ids to the polling hook, which drives status + results.
      setIds({
        submissionId: job.submission_id,
        analysisId: job.analysis_id,
      });
    } catch (err) {
      if (err instanceof DOMException && err.name === "AbortError") {
        return; // user navigated away mid-request; not an error
      }
      if (err instanceof ApiError) {
        if (Object.keys(err.fieldErrors).length > 0) {
          setFieldErrors(err.fieldErrors);
        }
        setFormError(err.message);
      } else {
        setFormError("Something went wrong starting the analysis. Please try again.");
      }
      // Form state is NOT reset here — input is preserved after errors.
    } finally {
      setSubmitting(false);
      abortRef.current = null;
    }
  }

  const handleCancel = useCallback(async () => {
    if (!ids) return;
    try {
      await cancelAnalysis(ids.submissionId, ids.analysisId);
    } catch {
      // Best-effort; polling will reflect the resulting status either way.
    }
  }, [ids]);

  // Whether an analysis is active (submitting the request, or polling a
  // non-terminal run).
  const active = submitting || polling;

  return (
    <div className="space-y-8">
    <div className="grid gap-8 lg:grid-cols-[minmax(0,1fr)_minmax(0,26rem)]">
      {/* --- Form column --- */}
      <div className="space-y-6">
        <div className="flex flex-wrap items-center gap-3">
          <button
            type="button"
            onClick={loadExample}
            className="rounded-md border border-slate-700 bg-slate-800/50 px-3 py-1.5 text-sm font-medium text-slate-200 transition-colors hover:bg-slate-800 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-400 focus-visible:ring-offset-2 focus-visible:ring-offset-[#0b0d12]"
          >
            Load example
          </button>
          <button
            type="button"
            onClick={clearForm}
            className="rounded-md px-3 py-1.5 text-sm font-medium text-slate-400 transition-colors hover:text-slate-200 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-slate-500 focus-visible:ring-offset-2 focus-visible:ring-offset-[#0b0d12]"
          >
            Clear
          </button>
        </div>

        {/* Function name */}
        <div>
          <label
            htmlFor="function-name"
            className="mb-1.5 block text-sm font-medium text-slate-300"
          >
            Function name
          </label>
          <input
            id="function-name"
            type="text"
            value={form.functionName}
            onChange={(e) => update("functionName", e.target.value)}
            placeholder="second_largest"
            spellCheck={false}
            autoComplete="off"
            aria-invalid={Boolean(fieldErrors.function_name)}
            aria-describedby={
              fieldErrors.function_name ? "function-name-error" : undefined
            }
            className="w-full rounded-lg border border-slate-800 bg-[#0d1017] px-3 py-2 font-mono text-sm text-slate-100 placeholder:text-slate-600 focus:border-slate-600 focus:outline-none focus:ring-1 focus:ring-slate-600 aria-[invalid=true]:border-rose-500/70"
          />
          <FieldError id="function-name-error" message={fieldErrors.function_name} />
        </div>

        {/* Specification */}
        <div>
          <label
            htmlFor="specification"
            className="mb-1.5 block text-sm font-medium text-slate-300"
          >
            Specification
          </label>
          <textarea
            id="specification"
            value={form.specification}
            onChange={(e) => update("specification", e.target.value)}
            rows={3}
            placeholder="Describe what the function should do, in plain language."
            aria-invalid={Boolean(fieldErrors.specification)}
            aria-describedby={
              fieldErrors.specification ? "specification-error" : undefined
            }
            className="w-full resize-y rounded-lg border border-slate-800 bg-[#0d1017] px-3 py-2 text-sm text-slate-100 placeholder:text-slate-600 focus:border-slate-600 focus:outline-none focus:ring-1 focus:ring-slate-600 aria-[invalid=true]:border-rose-500/70"
          />
          <FieldError id="specification-error" message={fieldErrors.specification} />
        </div>

        {/* Editors */}
        <div>
          <CodeEditor
            id="candidate-code"
            label="Candidate implementation"
            value={form.candidateCode}
            onChange={(v) => update("candidateCode", v)}
            invalid={Boolean(fieldErrors.candidate_code)}
            ariaDescribedBy={
              fieldErrors.candidate_code ? "candidate-code-error" : undefined
            }
          />
          <FieldError id="candidate-code-error" message={fieldErrors.candidate_code} />
        </div>

        <div>
          <CodeEditor
            id="reference-code"
            label="Reference implementation"
            value={form.referenceCode}
            onChange={(v) => update("referenceCode", v)}
            invalid={Boolean(fieldErrors.reference_code)}
            ariaDescribedBy={
              fieldErrors.reference_code ? "reference-code-error" : undefined
            }
          />
          <FieldError id="reference-code-error" message={fieldErrors.reference_code} />
        </div>

        {/* Manual test inputs */}
        <div>
          <CodeEditor
            id="test-inputs"
            label="Manual test inputs (JSON)"
            value={form.testInputsRaw}
            onChange={(v) => update("testInputsRaw", v)}
            language="json"
            heightClass="h-28"
            invalid={Boolean(fieldErrors.test_inputs)}
            ariaDescribedBy={
              fieldErrors.test_inputs
                ? "test-inputs-error"
                : "test-inputs-hint"
            }
          />
          {!fieldErrors.test_inputs && (
            <p id="test-inputs-hint" className="mt-1.5 text-xs text-slate-500">
              A JSON array of integer arrays, e.g. [[1, 2, 3], [5, 5, 5]]. Optional.
            </p>
          )}
          <FieldError id="test-inputs-error" message={fieldErrors.test_inputs} />
        </div>
      </div>

      {/* --- Controls / results column --- */}
      <div className="space-y-6 lg:sticky lg:top-6 lg:self-start">
        <fieldset className="space-y-4 rounded-xl border border-slate-800 bg-[#0d1017] p-4">
          <legend className="px-1 text-xs font-medium uppercase tracking-widest text-slate-500">
            Test sources
          </legend>
          <Toggle
            id="toggle-deterministic"
            label="Deterministic edge cases"
            description="Categorized inputs like empty list, duplicates, boundaries."
            checked={toggles.generateTests}
            onChange={(v) => setToggles((t) => ({ ...t, generateTests: v }))}
          />
          <Toggle
            id="toggle-hypothesis"
            label="Hypothesis search"
            description="Runs via a separate search endpoint — coming to this form soon."
            checked={toggles.hypothesis}
            onChange={(v) => setToggles((t) => ({ ...t, hypothesis: v }))}
            disabled
          />
          <Toggle
            id="toggle-ai-tests"
            label="Claude-targeted tests"
            description="Ask Claude to propose extra inputs. Optional."
            checked={toggles.aiTests}
            onChange={(v) => setToggles((t) => ({ ...t, aiTests: v }))}
          />
          <Toggle
            id="toggle-explain"
            label="Claude explanation"
            description="Explain the first confirmed counterexample."
            checked={toggles.explain}
            onChange={(v) => setToggles((t) => ({ ...t, explain: v }))}
          />
        </fieldset>

        <div className="space-y-3">
          {active ? (
            <div className="space-y-3">
              <button
                type="button"
                disabled
                aria-busy="true"
                className="flex w-full items-center justify-center gap-2 rounded-lg bg-emerald-600/40 px-4 py-2.5 text-sm font-semibold text-emerald-100"
              >
                <span
                  aria-hidden="true"
                  className="h-4 w-4 animate-spin rounded-full border-2 border-emerald-200 border-t-transparent"
                />
                {submitting ? "Starting…" : "Analyzing…"}
              </button>
              {ids && polling && (
                <button
                  type="button"
                  onClick={handleCancel}
                  className="w-full rounded-lg border border-slate-700 px-4 py-2 text-sm font-medium text-slate-300 transition-colors hover:bg-slate-800 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-slate-500 focus-visible:ring-offset-2 focus-visible:ring-offset-[#0b0d12]"
                >
                  Cancel
                </button>
              )}
            </div>
          ) : (
            <button
              type="button"
              onClick={handleAnalyze}
              className="w-full rounded-lg bg-emerald-500 px-4 py-2.5 text-sm font-semibold text-emerald-950 transition-colors hover:bg-emerald-400 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-300 focus-visible:ring-offset-2 focus-visible:ring-offset-[#0b0d12]"
            >
              Analyze
            </button>
          )}

          {(formError || pollError) && (
            <div
              role="alert"
              className="rounded-lg border border-rose-500/40 bg-rose-500/10 px-3 py-2 text-sm text-rose-200"
            >
              {formError ?? pollError}
            </div>
          )}
        </div>
      </div>
    </div>

      {/* --- Progress + results (full width, below the form/controls grid) --- */}
      {ids && (
        <div className="space-y-4 border-t border-slate-800 pt-8">
          <AnalysisProgress run={run} polling={polling} />
          {run && (run.status === "completed" || run.counterexamples.length > 0) && (
            <div className="space-y-3">
              <ShareLink
                submissionId={ids.submissionId}
                analysisId={ids.analysisId}
              />
              <ResultsView model={fromPersistedRun(run)} />
            </div>
          )}
        </div>
      )}
    </div>
  );
}
