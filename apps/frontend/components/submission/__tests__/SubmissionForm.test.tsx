import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import SubmissionForm from "../SubmissionForm";
import * as api from "@/lib/api";
import type { AnalysisRunRead } from "@/lib/types";

// Mock the API client so no real network call happens. This mocks the
// CLIENT for tests only; production code never mocks results.
vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof api>("@/lib/api");
  return {
    ...actual,
    createSubmission: vi.fn(),
    createAnalysis: vi.fn(),
    fetchAnalysisRun: vi.fn(),
    cancelAnalysis: vi.fn(),
  };
});

const mockedCreateSubmission = vi.mocked(api.createSubmission);
const mockedCreateAnalysis = vi.mocked(api.createAnalysis);
const mockedFetchRun = vi.mocked(api.fetchAnalysisRun);

function completedRun(overrides: Partial<AnalysisRunRead> = {}): AnalysisRunRead {
  return {
    id: "a-1",
    submission_id: "s-1",
    status: "completed",
    progress: 1,
    error: null,
    total_tests: 1,
    passed_tests: 0,
    failed_tests: 1,
    inconclusive_tests: 0,
    elapsed_seconds: 0.2,
    seed: null,
    configuration: {},
    created_at: "2026-01-01T00:00:00Z",
    started_at: "2026-01-01T00:00:00Z",
    finished_at: "2026-01-01T00:00:01Z",
    executions: [],
    counterexamples: [
      {
        id: "c-1",
        original_input: [5, 5, 5],
        minimized_input: null,
        candidate_result: {
          status: "success",
          returned_value: 5,
          exception_type: null,
          exception_message: null,
          stdout: "",
          stderr: "",
          runtime_ms: 1,
        },
        reference_result: {
          status: "runtime_error",
          returned_value: null,
          exception_type: "ValueError",
          exception_message: null,
          stdout: "",
          stderr: "",
          runtime_ms: 1,
        },
        explanation: null,
      },
    ],
    ...overrides,
  };
}

beforeEach(() => {
  mockedCreateSubmission.mockReset();
  mockedCreateAnalysis.mockReset();
  mockedFetchRun.mockReset();
});

describe("SubmissionForm (async flow)", () => {
  it("loads the second_largest example into the fields", async () => {
    const user = userEvent.setup();
    render(<SubmissionForm />);
    await user.click(screen.getByRole("button", { name: /load example/i }));
    expect(screen.getByLabelText(/function name/i)).toHaveValue("second_largest");
    const spec = screen.getByLabelText(/specification/i) as HTMLTextAreaElement;
    expect(spec.value).toContain("second largest");
  });

  it("blocks submission and shows errors when required fields are empty", async () => {
    const user = userEvent.setup();
    render(<SubmissionForm />);
    await user.click(screen.getByRole("button", { name: /^analyze$/i }));
    expect(mockedCreateSubmission).not.toHaveBeenCalled();
    expect(
      await screen.findByText(/fix the highlighted fields/i),
    ).toBeInTheDocument();
  });

  it("creates a submission, starts analysis, polls, and renders results", async () => {
    const user = userEvent.setup();
    mockedCreateSubmission.mockResolvedValueOnce({ submission_id: "s-1" });
    mockedCreateAnalysis.mockResolvedValueOnce({
      submission_id: "s-1",
      analysis_id: "a-1",
      status: "queued",
    });
    mockedFetchRun.mockResolvedValue(completedRun());

    render(<SubmissionForm />);
    await user.click(screen.getByRole("button", { name: /load example/i }));
    await user.click(screen.getByRole("button", { name: /^analyze$/i }));

    await waitFor(() => expect(mockedCreateSubmission).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(mockedCreateAnalysis).toHaveBeenCalledTimes(1));
    await waitFor(() =>
      expect(screen.getByRole("tab", { name: "Overview" })).toBeInTheDocument(),
    );
  });

  it("preserves user input after a backend error starting analysis", async () => {
    const user = userEvent.setup();
    mockedCreateSubmission.mockRejectedValueOnce(
      new api.ApiError("Backend rejected it.", 422, {
        function_name: "bad name",
      }),
    );
    render(<SubmissionForm />);
    await user.click(screen.getByRole("button", { name: /load example/i }));
    await user.click(screen.getByRole("button", { name: /^analyze$/i }));

    await waitFor(() => expect(mockedCreateSubmission).toHaveBeenCalled());
    expect(screen.getByLabelText(/function name/i)).toHaveValue("second_largest");
    expect(await screen.findByText(/backend rejected it/i)).toBeInTheDocument();
  });

  it("sends toggle state through to createAnalysis", async () => {
    const user = userEvent.setup();
    mockedCreateSubmission.mockResolvedValueOnce({ submission_id: "s-1" });
    mockedCreateAnalysis.mockResolvedValueOnce({
      submission_id: "s-1",
      analysis_id: "a-1",
      status: "queued",
    });
    mockedFetchRun.mockResolvedValue(completedRun());

    render(<SubmissionForm />);
    await user.click(screen.getByRole("button", { name: /load example/i }));
    await user.click(
      screen.getByRole("switch", { name: /claude-targeted tests/i }),
    );
    await user.click(screen.getByRole("button", { name: /^analyze$/i }));

    await waitFor(() => expect(mockedCreateAnalysis).toHaveBeenCalled());
    const [, options] = mockedCreateAnalysis.mock.calls[0];
    expect(options.use_ai_tests).toBe(true);
  });

  it("shows a live progress bar with the current stage while polling", async () => {
    const user = userEvent.setup();
    mockedCreateSubmission.mockResolvedValueOnce({ submission_id: "s-1" });
    mockedCreateAnalysis.mockResolvedValueOnce({
      submission_id: "s-1",
      analysis_id: "a-1",
      status: "queued",
    });
    mockedFetchRun
      .mockResolvedValueOnce(
        completedRun({ status: "executing_tests", progress: 0.4 }),
      )
      .mockResolvedValue(completedRun());

    render(<SubmissionForm />);
    await user.click(screen.getByRole("button", { name: /load example/i }));
    await user.click(screen.getByRole("button", { name: /^analyze$/i }));

    await waitFor(() =>
      expect(screen.getByRole("progressbar")).toBeInTheDocument(),
    );
    expect(await screen.findByText(/executing tests/i)).toBeInTheDocument();
  });
});
