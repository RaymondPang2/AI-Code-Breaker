// Types mirroring the backend's Pydantic contract
// (apps/backend/app/schemas/submission.py). Kept in one place so the API
// client and the UI share a single source of truth. If the backend
// contract changes, this file changes with it — nothing here is invented.

export type RunnerStatus =
  | "success"
  | "syntax_error"
  | "load_error"
  | "runtime_error"
  | "timeout"
  | "unserializable_output"
  | "internal_error";

export type TestCaseSource = "manual" | "generated" | "ai";

// Limits mirror apps/backend/app/schemas/submission.py so the client can
// reject obviously-invalid input before a round trip.
export const LIMITS = {
  MAX_FUNCTION_NAME_LENGTH: 100,
  MAX_SPECIFICATION_LENGTH: 2000,
  MAX_SOURCE_CODE_LENGTH: 20000,
  MAX_TEST_CASES: 20,
  MAX_INPUT_LIST_SIZE: 1000,
} as const;

export interface SubmissionRequest {
  function_name: string;
  specification: string;
  candidate_code: string;
  reference_code: string;
  test_inputs: number[][];
  generate_tests: boolean;
  generation_seed: number;
  use_ai_tests: boolean;
  explain_counterexamples: boolean;
  suggest_patch: boolean;
}

export interface FunctionExecutionResult {
  status: RunnerStatus;
  returned_value: unknown;
  exception_type: string | null;
  exception_message: string | null;
  stdout: string;
  stderr: string;
  runtime_ms: number | null;
}

export interface TestComparisonResult {
  input: number[];
  source: TestCaseSource;
  category: string;
  reason: string;
  candidate: FunctionExecutionResult;
  reference: FunctionExecutionResult;
  match: boolean;
  internal_error: boolean;
}

export interface CounterexampleExplanation {
  source: "ai" | "deterministic";
  ai_generated: boolean;
  summary: string;
  root_cause: string;
  walkthrough: string[];
  suspected_lines: number[];
  suggested_fix: string;
  suggested_fix_verified: boolean;
  suggested_patch: string | null;
  confidence: "low" | "medium" | "high";
}

export interface AiUsage {
  model?: string | null;
  input_tokens?: number | null;
  output_tokens?: number | null;
  latency_ms?: number | null;
  request_count?: number;
  available?: boolean;
  error?: string | null;
}

export interface SubmissionAnalysisResponse {
  function_name: string;
  total_tests: number;
  passed_tests: number;
  failed_tests: number;
  inconclusive_tests: number;
  comparisons: TestComparisonResult[];
  first_failing_input: number[] | null;
  submission_id: string | null;
  analysis_run_id: string | null;
  ai_usage: AiUsage | null;
  counterexample_explanation: CounterexampleExplanation | null;
  explanation_usage: AiUsage | null;
}

// --- Persisted read models (GET /submissions/{id}/analyses/{analysis_id}) ---
// Mirror apps/backend/app/schemas/persistence.py. Used by the shareable
// result URL, which re-fetches a stored run rather than re-running it.
// These carry a few fields the live analyze response does not: elapsed
// time, seed, the configuration snapshot (strategies used), and the
// minimized counterexample.

export interface ExecutionRead {
  id: string;
  role: string; // "candidate" | "reference"
  test_case_id: string;
  input: number[];
  normalized_result: FunctionExecutionResult;
  runtime_ms: number | null;
  timed_out: boolean;
}

export interface CounterexampleRead {
  id: string;
  original_input: number[];
  minimized_input: number[] | null;
  candidate_result: FunctionExecutionResult;
  reference_result: FunctionExecutionResult;
  explanation: CounterexampleExplanation | null;
}

// Canonical analysis job statuses (mirror app/core/analysis_status.py).
export type AnalysisStatus =
  | "queued"
  | "generating_tests"
  | "executing_tests"
  | "searching_properties"
  | "minimizing"
  | "explaining"
  | "completed"
  | "failed"
  | "cancelled";

export const TERMINAL_STATUSES: AnalysisStatus[] = [
  "completed",
  "failed",
  "cancelled",
];

export function isTerminalStatus(status: string): boolean {
  return (TERMINAL_STATUSES as string[]).includes(status);
}

export interface AnalysisRunRead {
  id: string;
  submission_id: string;
  status: AnalysisStatus | string;
  progress: number;
  error: string | null;
  total_tests: number;
  passed_tests: number;
  failed_tests: number;
  inconclusive_tests: number;
  elapsed_seconds: number | null;
  seed: number | null;
  configuration: Record<string, unknown>;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  executions: ExecutionRead[];
  counterexamples: CounterexampleRead[];
}

export interface SubmissionCreatedResponse {
  submission_id: string;
}

export interface AnalysisJobCreatedResponse {
  submission_id: string;
  analysis_id: string;
  status: string;
}

export interface AnalysisOptions {
  test_inputs: number[][];
  generate_tests: boolean;
  generation_seed: number;
  use_ai_tests: boolean;
  explain_counterexamples: boolean;
  suggest_patch: boolean;
}

export interface SubmissionRead {
  id: string;
  function_name: string;
  specification: string;
  candidate_code: string;
  reference_code: string;
  created_at: string;
}

// FastAPI validation errors (422) arrive as a list of these.
export interface ApiValidationErrorItem {
  loc: (string | number)[];
  msg: string;
  type: string;
}
