import { describe, expect, it } from "vitest";

import {
  comparisonOutcome,
  describeResult,
  failureCategory,
  formatDuration,
  formatRuntimeMs,
  strategiesFromComparisons,
  strategiesFromConfiguration,
} from "../format";
import { fromAnalysisResponse, fromPersistedRun } from "../resultModel";
import type {
  AnalysisRunRead,
  FunctionExecutionResult,
  SubmissionAnalysisResponse,
  TestComparisonResult,
} from "../types";

const ok = (v: unknown): FunctionExecutionResult => ({
  status: "success",
  returned_value: v,
  exception_type: null,
  exception_message: null,
  stdout: "",
  stderr: "",
  runtime_ms: 2,
});
const raised = (t: string, m?: string): FunctionExecutionResult => ({
  status: "runtime_error",
  returned_value: null,
  exception_type: t,
  exception_message: m ?? null,
  stdout: "",
  stderr: "",
  runtime_ms: 1,
});
const cmp = (
  input: number[],
  candidate: FunctionExecutionResult,
  reference: FunctionExecutionResult,
  extra: Partial<TestComparisonResult> = {},
): TestComparisonResult => ({
  input,
  source: "manual",
  category: "c",
  reason: "",
  candidate,
  reference,
  match: false,
  internal_error: false,
  ...extra,
});

describe("format", () => {
  it("describes results", () => {
    expect(describeResult(ok(5))).toBe("5");
    expect(describeResult(raised("IndexError", "boom"))).toBe("IndexError: boom");
  });

  it("categorizes failures from verified statuses only", () => {
    expect(failureCategory(cmp([1], ok(1), ok(2)))).toBe("Different return values");
    expect(failureCategory(cmp([1], raised("V"), ok(2)))).toBe(
      "Candidate raised, reference returned",
    );
    expect(failureCategory(cmp([1], ok(1), ok(1), { match: true }))).toBe("Match");
  });

  it("surfaces the specific runner error, not a bare inconclusive", () => {
    // Regression: a runner/infrastructure failure (internal_error) must show
    // WHICH side failed and the concrete runner error, so it's diagnosable —
    // this was the "everything INCONCLUSIVE, no detail" symptom.
    const internalError = (msg: string): FunctionExecutionResult => ({
      status: "internal_error",
      returned_value: null,
      exception_type: "EmptyRunnerOutput",
      exception_message: msg,
      stdout: "",
      stderr: "",
      runtime_ms: null,
    });
    const c = cmp([-5, -2, -9], internalError("runner process produced no output"), ok(-2), {
      internal_error: true,
    });
    const category = failureCategory(c);
    expect(category).toContain("Runner error");
    expect(category).toContain("candidate");
    expect(category).toContain("EmptyRunnerOutput");
    // The outcome classification stays "inconclusive" (it's not a confirmed
    // behavioral bug), but the category now carries the specific cause.
    expect(comparisonOutcome(c)).toBe("inconclusive");
  });

  it("classifies outcomes", () => {
    expect(comparisonOutcome(cmp([1], ok(1), ok(1), { match: true }))).toBe("match");
    expect(comparisonOutcome(cmp([1], ok(1), ok(2)))).toBe("mismatch");
    expect(comparisonOutcome(cmp([1], ok(1), ok(1), { internal_error: true }))).toBe(
      "inconclusive",
    );
  });

  it("extracts strategies from a configuration", () => {
    expect(strategiesFromConfiguration({ generate_tests: true })).toEqual([
      "Deterministic edge cases",
    ]);
    expect(strategiesFromConfiguration({})).toEqual([]);
    expect(strategiesFromConfiguration(null)).toEqual([]);
  });

  it("infers strategies from comparison sources", () => {
    expect(
      strategiesFromComparisons([
        cmp([1], ok(1), ok(1), { source: "generated" }),
        cmp([2], ok(2), ok(2), { source: "ai" }),
      ]),
    ).toEqual(["Deterministic edge cases", "Claude-targeted tests"]);
  });

  it("formats durations and runtimes, returning null when absent", () => {
    expect(formatDuration(0.25)).toBe("250 ms");
    expect(formatDuration(1.5)).toBe("1.50 s");
    expect(formatDuration(null)).toBeNull();
    expect(formatRuntimeMs(43)).toBe("43 ms");
    expect(formatRuntimeMs(null)).toBeNull();
  });
});

describe("resultModel", () => {
  const response: SubmissionAnalysisResponse = {
    function_name: "f",
    total_tests: 2,
    passed_tests: 1,
    failed_tests: 1,
    inconclusive_tests: 0,
    comparisons: [
      cmp([1], ok(1), ok(1), { match: true }),
      cmp([5, 5], ok(5), raised("ValueError")),
    ],
    first_failing_input: [5, 5],
    submission_id: "s1",
    analysis_run_id: "a1",
    ai_usage: null,
    counterexample_explanation: null,
    explanation_usage: null,
  };

  it("adapts the live analyze response", () => {
    const m = fromAnalysisResponse(response);
    expect(m.candidateResult?.returned_value).toBe(5);
    expect(m.referenceResult?.exception_type).toBe("ValueError");
    expect(m.minimizedInput).toBeNull(); // not carried by the live response
    expect(m.fromPersisted).toBe(false);
  });

  it("adapts a persisted run and recomputes match from executions", () => {
    const run: AnalysisRunRead = {
      id: "a1",
      submission_id: "s1",
      status: "completed",
      progress: 1,
      error: null,
      started_at: null,
      finished_at: null,
      total_tests: 1,
      passed_tests: 0,
      failed_tests: 1,
      inconclusive_tests: 0,
      elapsed_seconds: 0.5,
      seed: 0,
      configuration: { generate_tests: true },
      created_at: "2026-01-01T00:00:00Z",
      executions: [
        {
          id: "e1",
          role: "candidate",
          test_case_id: "t1",
          input: [5, 5],
          normalized_result: ok(5),
          runtime_ms: 2,
          timed_out: false,
        },
        {
          id: "e2",
          role: "reference",
          test_case_id: "t1",
          input: [5, 5],
          normalized_result: raised("ValueError"),
          runtime_ms: 1,
          timed_out: false,
        },
      ],
      counterexamples: [
        {
          id: "c1",
          original_input: [5, 5],
          minimized_input: [5, 5],
          candidate_result: ok(5),
          reference_result: raised("ValueError"),
          explanation: null,
        },
      ],
    };
    const m = fromPersistedRun(run);
    expect(m.elapsedSeconds).toBe(0.5);
    expect(m.minimizedInput).toEqual([5, 5]);
    expect(m.comparisons).toHaveLength(1);
    expect(m.comparisons[0].match).toBe(false); // recomputed
    expect(m.strategies).toEqual(["Deterministic edge cases"]);
    expect(m.fromPersisted).toBe(true);
  });
});
