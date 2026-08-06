// A single normalized view-model the results UI renders, plus adapters from
// the two backend shapes that can produce it:
//
//   - the live POST /analyze response (SubmissionAnalysisResponse), and
//   - the persisted GET run (AnalysisRunRead), used by the shareable URL.
//
// The two carry overlapping-but-different fields. Rather than have the UI
// branch on which source it got, both are adapted into ResultModel here.
// Fields a given source genuinely doesn't have are left null/empty — the UI
// then shows an honest "not available" rather than a fabricated value.

import type {
  AnalysisRunRead,
  CounterexampleExplanation,
  FunctionExecutionResult,
  SubmissionAnalysisResponse,
  TestComparisonResult,
} from "./types";
import { strategiesFromComparisons, strategiesFromConfiguration } from "./format";

export interface ResultModel {
  functionName: string | null;
  totalTests: number;
  passedTests: number;
  failedTests: number;
  // Tests where a runner/infrastructure error prevented a real comparison.
  // Distinct from failedTests so passed + failed + inconclusive == total.
  inconclusiveTests: number;
  comparisons: TestComparisonResult[];
  firstFailingInput: number[] | null;
  // Counterexample forms. originalInput mirrors firstFailingInput for the
  // live response; minimizedInput is only available from a persisted run.
  originalInput: number[] | null;
  minimizedInput: number[] | null;
  // The candidate/reference results on the failing input, when identifiable.
  candidateResult: FunctionExecutionResult | null;
  referenceResult: FunctionExecutionResult | null;
  explanation: CounterexampleExplanation | null;
  aiUsage: SubmissionAnalysisResponse["ai_usage"];
  explanationUsage: SubmissionAnalysisResponse["explanation_usage"];
  strategies: string[];
  elapsedSeconds: number | null;
  seed: number | null;
  submissionId: string | null;
  analysisRunId: string | null;
  // True when this model came from a persisted run (some fields, like live
  // comparisons, may be reconstructed rather than first-class).
  fromPersisted: boolean;
}

// Adapt the live analyze response.
export function fromAnalysisResponse(
  r: SubmissionAnalysisResponse,
): ResultModel {
  const failing = findFailingComparison(r.comparisons, r.first_failing_input);
  return {
    functionName: r.function_name,
    totalTests: r.total_tests,
    passedTests: r.passed_tests,
    failedTests: r.failed_tests,
    // Prefer the server count; fall back to deriving from comparisons for
    // resilience against older payloads that predate the field.
    inconclusiveTests:
      r.inconclusive_tests ??
      r.comparisons.filter((c) => c.internal_error).length,
    comparisons: r.comparisons,
    firstFailingInput: r.first_failing_input,
    originalInput: r.first_failing_input,
    minimizedInput: null, // not carried by the live analyze response
    candidateResult: failing?.candidate ?? null,
    referenceResult: failing?.reference ?? null,
    explanation: r.counterexample_explanation,
    aiUsage: r.ai_usage,
    explanationUsage: r.explanation_usage,
    strategies: strategiesFromComparisons(r.comparisons),
    elapsedSeconds: null, // not carried by the live analyze response
    seed: null,
    submissionId: r.submission_id,
    analysisRunId: r.analysis_run_id,
    fromPersisted: false,
  };
}

// Adapt a persisted run. Executions are grouped back into comparison rows by
// test case so the same table can render them.
export function fromPersistedRun(run: AnalysisRunRead): ResultModel {
  const comparisons = comparisonsFromExecutions(run);
  const firstCounter = run.counterexamples[0] ?? null;
  const firstFailing = firstCounter
    ? firstCounter.minimized_input ?? firstCounter.original_input
    : null;
  return {
    functionName: null, // the run doesn't embed the function name; the page
    // fetches the submission separately for that.
    totalTests: run.total_tests,
    passedTests: run.passed_tests,
    failedTests: run.failed_tests,
    inconclusiveTests:
      run.inconclusive_tests ??
      comparisons.filter((c) => c.internal_error).length,
    comparisons,
    firstFailingInput: firstFailing,
    originalInput: firstCounter?.original_input ?? null,
    minimizedInput: firstCounter?.minimized_input ?? null,
    candidateResult: firstCounter?.candidate_result ?? null,
    referenceResult: firstCounter?.reference_result ?? null,
    explanation: firstCounter?.explanation ?? null,
    aiUsage: null, // usage isn't persisted on the run
    explanationUsage: null,
    strategies: strategiesFromConfiguration(run.configuration),
    elapsedSeconds: run.elapsed_seconds,
    seed: run.seed,
    submissionId: run.submission_id,
    analysisRunId: run.id,
    fromPersisted: true,
  };
}

function findFailingComparison(
  comparisons: TestComparisonResult[],
  firstFailingInput: number[] | null,
): TestComparisonResult | null {
  if (firstFailingInput === null) return null;
  const target = JSON.stringify(firstFailingInput);
  return (
    comparisons.find(
      (c) => !c.match && !c.internal_error && JSON.stringify(c.input) === target,
    ) ?? null
  );
}

// Rebuild comparison rows from a persisted run's flat execution list. Each
// (test_case_id) has a candidate and a reference execution; we pair them and
// recompute match/internal_error from the stored statuses so the All Tests
// table works for persisted runs too.
function comparisonsFromExecutions(run: AnalysisRunRead): TestComparisonResult[] {
  const byCase = new Map<
    string,
    { candidate?: FunctionExecutionResult; reference?: FunctionExecutionResult; input: number[] }
  >();
  for (const ex of run.executions) {
    const entry = byCase.get(ex.test_case_id) ?? { input: ex.input };
    if (ex.role === "candidate") entry.candidate = ex.normalized_result;
    else if (ex.role === "reference") entry.reference = ex.normalized_result;
    entry.input = ex.input;
    byCase.set(ex.test_case_id, entry);
  }

  const rows: TestComparisonResult[] = [];
  for (const { candidate, reference, input } of byCase.values()) {
    if (!candidate || !reference) continue;
    const internalError =
      candidate.status === "internal_error" ||
      reference.status === "internal_error";
    const match = comparisonMatches(candidate, reference);
    rows.push({
      input,
      // Source/category/reason aren't stored per execution, so they're marked
      // unknown rather than invented.
      source: "manual",
      category: "—",
      reason: "",
      candidate,
      reference,
      match,
      internal_error: internalError,
    });
  }
  return rows;
}

// Recompute match using the same rules the backend comparison engine uses
// (value equality on success; exception-type equality; timeout never
// matches; internal_error never matches). This mirrors, and never
// contradicts, the backend — it only reconstructs a boolean the persisted
// executions imply.
function comparisonMatches(
  candidate: FunctionExecutionResult,
  reference: FunctionExecutionResult,
): boolean {
  if (
    candidate.status === "internal_error" ||
    reference.status === "internal_error"
  ) {
    return false;
  }
  if (candidate.status === "timeout" || reference.status === "timeout") {
    return false;
  }
  if (candidate.status === "success" && reference.status === "success") {
    return JSON.stringify(candidate.returned_value) === JSON.stringify(reference.returned_value);
  }
  const exc = new Set(["runtime_error", "syntax_error", "load_error"]);
  if (exc.has(candidate.status) && exc.has(reference.status)) {
    return candidate.exception_type === reference.exception_type;
  }
  return false;
}
