import { describe, expect, it } from "vitest";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import ResultsView from "../ResultsView";
import { fromAnalysisResponse } from "@/lib/resultModel";
import type {
  FunctionExecutionResult,
  SubmissionAnalysisResponse,
  TestComparisonResult,
} from "@/lib/types";

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

function response(
  overrides: Partial<SubmissionAnalysisResponse> = {},
): SubmissionAnalysisResponse {
  return {
    function_name: "second_largest",
    total_tests: 3,
    passed_tests: 2,
    failed_tests: 1,
    inconclusive_tests: 0,
    comparisons: [
      cmp([1, 2, 3], ok(2), ok(2), { match: true, source: "manual" }),
      cmp([0], ok(0), ok(0), { match: true, source: "generated" }),
      cmp([5, 5], ok(5), raised("ValueError", "need two distinct"), {
        source: "ai",
      }),
    ],
    first_failing_input: [5, 5],
    submission_id: "s1",
    analysis_run_id: "a1",
    ai_usage: null,
    counterexample_explanation: null,
    explanation_usage: null,
    ...overrides,
  };
}

function renderView(overrides: Partial<SubmissionAnalysisResponse> = {}) {
  return render(<ResultsView model={fromAnalysisResponse(response(overrides))} />);
}

describe("ResultsView", () => {
  it("shows overview stats by default", () => {
    renderView();
    expect(screen.getByRole("tab", { name: "Overview" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    // The failing input appears on the overview.
    expect(screen.getByText("[5,5]")).toBeInTheDocument();
  });

  it("shows the counterexample when there are failures", () => {
    // failed > 0 -> counterexample shown, no 'no differences' message.
    renderView({ passed_tests: 2, failed_tests: 1, inconclusive_tests: 0 });
    expect(screen.getByText("[5,5]")).toBeInTheDocument();
    expect(
      screen.queryByText(/no behavioral differences/i),
    ).not.toBeInTheDocument();
  });

  it("shows an execution-error warning, not 'no differences', when inconclusive", () => {
    // The reported bug: everything inconclusive must NOT say 'no differences'.
    renderView({
      passed_tests: 0,
      failed_tests: 0,
      inconclusive_tests: 3,
      first_failing_input: null,
      comparisons: [],
    });
    expect(screen.getByText(/execution error/i)).toBeInTheDocument();
    expect(
      screen.queryByText(/no behavioral differences/i),
    ).not.toBeInTheDocument();
  });

  it("shows 'no differences' only when nothing failed and nothing was inconclusive", () => {
    renderView({
      passed_tests: 3,
      failed_tests: 0,
      inconclusive_tests: 0,
      first_failing_input: null,
      comparisons: [],
    });
    expect(
      screen.getByText(/no behavioral differences/i),
    ).toBeInTheDocument();
    expect(screen.queryByText(/execution error/i)).not.toBeInTheDocument();
  });

  it("switches tabs on click", async () => {
    const user = userEvent.setup();
    renderView();
    await user.click(screen.getByRole("tab", { name: "All Tests" }));
    expect(
      screen.getByRole("tab", { name: "All Tests" }),
    ).toHaveAttribute("aria-selected", "true");
    expect(screen.getByText(/showing 3 of 3 tests/i)).toBeInTheDocument();
  });

  it("filters the All Tests table by outcome", async () => {
    const user = userEvent.setup();
    renderView();
    await user.click(screen.getByRole("tab", { name: "All Tests" }));

    await user.selectOptions(
      screen.getByLabelText(/outcome/i),
      "mismatch",
    );
    expect(screen.getByText(/showing 1 of 3 tests/i)).toBeInTheDocument();
  });

  it("filters the All Tests table by source", async () => {
    const user = userEvent.setup();
    renderView();
    await user.click(screen.getByRole("tab", { name: "All Tests" }));

    await user.selectOptions(screen.getByLabelText(/source/i), "ai");
    expect(screen.getByText(/showing 1 of 3 tests/i)).toBeInTheDocument();
  });

  it("expands a test row to reveal candidate and reference details", async () => {
    const user = userEvent.setup();
    renderView();
    await user.click(screen.getByRole("tab", { name: "All Tests" }));

    // The failing row's toggle button (input rendered as JSON).
    const rowToggle = screen.getByRole("button", { name: /\[5,5\]/ });
    expect(rowToggle).toHaveAttribute("aria-expanded", "false");
    await user.click(rowToggle);
    expect(rowToggle).toHaveAttribute("aria-expanded", "true");
    // Reference exception surfaced in the expanded panel.
    expect(screen.getByText(/need two distinct/i)).toBeInTheDocument();
  });

  it("shows expected and actual behavior on the counterexample tab", async () => {
    const user = userEvent.setup();
    renderView();
    await user.click(screen.getByRole("tab", { name: "Counterexample" }));
    expect(screen.getByText(/original counterexample/i)).toBeInTheDocument();
    expect(screen.getByText(/actual · candidate behavior/i)).toBeInTheDocument();
    expect(screen.getByText(/expected · reference behavior/i)).toBeInTheDocument();
  });

  it("labels AI commentary distinctly and marks the patch as a proposal", async () => {
    const user = userEvent.setup();
    renderView({
      counterexample_explanation: {
        source: "ai",
        ai_generated: true,
        summary: "Candidate ignores distinctness.",
        root_cause: "sorted()[-2] repeats the max.",
        walkthrough: ["step one", "step two"],
        suspected_lines: [2],
        suggested_fix: "Deduplicate first.",
        suggested_fix_verified: false,
        suggested_patch: "def second_largest(v):\n    return sorted(set(v))[-2]\n",
        confidence: "high",
      },
    });
    await user.click(screen.getByRole("tab", { name: "AI Explanation" }));

    // The AI-generated provenance is called out.
    expect(screen.getByText(/AI-generated commentary/i)).toBeInTheDocument();
    // The patch is explicitly a non-applied proposal.
    expect(screen.getByText(/proposal · not applied/i)).toBeInTheDocument();
    expect(screen.getByText(/not verified/i)).toBeInTheDocument();
  });

  it("shows an empty explanation state when none is present", async () => {
    const user = userEvent.setup();
    renderView({ counterexample_explanation: null });
    await user.click(screen.getByRole("tab", { name: "AI Explanation" }));
    expect(screen.getByText(/no explanation was generated/i)).toBeInTheDocument();
  });
});
