// Typed client for the FastAPI backend. This is the ONLY place that talks
// to the network; components import functions from here rather than calling
// fetch directly. No analysis results are ever fabricated here — every
// value returned to the UI comes from a real backend response.

import type {
  AnalysisRunRead,
  ApiValidationErrorItem,
  SubmissionRead,
} from "./types";

// Base URL of the FastAPI backend. Overridable via env so it can differ
// between plain `npm run dev` on the host (localhost) and, later, a
// containerized setup defined under infra/.
export const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

// A stable, per-browser client id. It's not authentication — it's a
// convenience key so this browser's submissions are grouped together for
// quota purposes and so the owner (this browser) can delete or share its own
// work. Generated once and persisted in localStorage. Falls back to a
// per-session value if storage is unavailable.
const CLIENT_ID_HEADER = "X-Client-Id";
let _sessionClientId: string | null = null;

function getClientId(): string {
  if (typeof window === "undefined") {
    // SSR: no stable per-browser id available; use an ephemeral one.
    _sessionClientId ??= `anon-${Math.random().toString(36).slice(2)}`;
    return _sessionClientId;
  }
  try {
    const KEY = "acb-client-id";
    let id = window.localStorage.getItem(KEY);
    if (!id) {
      id =
        (window.crypto?.randomUUID?.() as string | undefined) ??
        `c-${Date.now()}-${Math.random().toString(36).slice(2)}`;
      window.localStorage.setItem(KEY, id);
    }
    return id;
  } catch {
    _sessionClientId ??= `anon-${Math.random().toString(36).slice(2)}`;
    return _sessionClientId;
  }
}

// Common headers for API calls: JSON plus the client id.
function apiHeaders(extra?: Record<string, string>): Record<string, string> {
  return {
    "Content-Type": "application/json",
    [CLIENT_ID_HEADER]: getClientId(),
    ...extra,
  };
}

export interface HealthResponse {
  status: string;
  service: string;
}

export async function fetchHealth(): Promise<HealthResponse> {
  const response = await fetch(`${API_BASE_URL}/health`, {
    cache: "no-store",
  });

  if (!response.ok) {
    throw new Error(`Health check failed with status ${response.status}`);
  }

  return response.json();
}

// A structured error the UI can render clearly. `fieldErrors` maps a field
// name (e.g. "function_name") to a human message when the backend rejected
// a specific field; `message` is the top-level summary.
export class ApiError extends Error {
  status: number;
  fieldErrors: Record<string, string>;

  constructor(
    message: string,
    status: number,
    fieldErrors: Record<string, string> = {},
  ) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.fieldErrors = fieldErrors;
  }
}

function parseValidationErrors(
  detail: ApiValidationErrorItem[],
): Record<string, string> {
  const fieldErrors: Record<string, string> = {};
  for (const item of detail) {
    // loc is like ["body", "function_name"] or ["body", "test_inputs", 0].
    const field = item.loc
      .filter((part) => part !== "body")
      .map(String)
      .join(".");
    if (field && !fieldErrors[field]) {
      fieldErrors[field] = item.msg;
    }
  }
  return fieldErrors;
}

// --- Persisted-run fetching (for the shareable result URL) ------------------
// These GET a stored analysis run and its submission by ID. Used by the
// /results/[submissionId]/[analysisId] page, which renders a run that was
// already computed and saved — no re-execution, and still no fabricated
// data (a 404 is surfaced as an ApiError, never a placeholder result).

async function getJson<T>(path: string, signal?: AbortSignal): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${API_BASE_URL}${path}`, {
      cache: "no-store",
      headers: apiHeaders(),
      signal,
    });
  } catch (err) {
    if (err instanceof DOMException && err.name === "AbortError") {
      throw err;
    }
    throw new ApiError(
      `Could not reach the backend at ${API_BASE_URL}. Is it running?`,
      0,
    );
  }

  if (response.ok) {
    return (await response.json()) as T;
  }

  let detail: unknown;
  try {
    detail = (await response.json())?.detail;
  } catch {
    detail = undefined;
  }
  const message =
    typeof detail === "string"
      ? detail
      : response.status === 404
        ? "Not found."
        : `Request failed with status ${response.status}.`;
  throw new ApiError(message, response.status);
}

export function fetchSubmission(
  submissionId: string,
  signal?: AbortSignal,
): Promise<SubmissionRead> {
  return getJson<SubmissionRead>(
    `/submissions/${encodeURIComponent(submissionId)}`,
    signal,
  );
}

export function fetchAnalysisRun(
  submissionId: string,
  analysisId: string,
  signal?: AbortSignal,
): Promise<AnalysisRunRead> {
  return getJson<AnalysisRunRead>(
    `/submissions/${encodeURIComponent(submissionId)}/analyses/${encodeURIComponent(
      analysisId,
    )}`,
    signal,
  );
}

// --- Async analysis workflow ------------------------------------------------
// Create a submission, then start an analysis job. The job runs on a worker;
// the client polls fetchAnalysisRun for status + results (see useAnalysisPolling).

import type {
  AnalysisJobCreatedResponse,
  AnalysisOptions,
  SubmissionCreatedResponse,
} from "./types";

export async function createSubmission(
  content: {
    function_name: string;
    specification: string;
    candidate_code: string;
    reference_code: string;
    // The create endpoint reuses the full submission schema for validation;
    // the run options below are ignored at creation and supplied per-analysis.
    test_inputs?: number[][];
    generate_tests?: boolean;
    generation_seed?: number;
    use_ai_tests?: boolean;
    explain_counterexamples?: boolean;
    suggest_patch?: boolean;
  },
  signal?: AbortSignal,
): Promise<SubmissionCreatedResponse> {
  return postJson<SubmissionCreatedResponse>("/submissions", content, signal);
}

export async function createAnalysis(
  submissionId: string,
  options: AnalysisOptions,
  signal?: AbortSignal,
): Promise<AnalysisJobCreatedResponse> {
  return postJson<AnalysisJobCreatedResponse>(
    `/submissions/${encodeURIComponent(submissionId)}/analyses`,
    options,
    signal,
  );
}

export async function cancelAnalysis(
  submissionId: string,
  analysisId: string,
  signal?: AbortSignal,
): Promise<AnalysisRunRead> {
  return postJson<AnalysisRunRead>(
    `/submissions/${encodeURIComponent(submissionId)}/analyses/${encodeURIComponent(
      analysisId,
    )}/cancel`,
    {},
    signal,
  );
}

async function postJson<T>(
  path: string,
  body: unknown,
  signal?: AbortSignal,
): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${API_BASE_URL}${path}`, {
      method: "POST",
      headers: apiHeaders(),
      body: JSON.stringify(body),
      signal,
    });
  } catch (err) {
    if (err instanceof DOMException && err.name === "AbortError") throw err;
    throw new ApiError(
      `Could not reach the backend at ${API_BASE_URL}. Is it running?`,
      0,
    );
  }

  if (response.ok) {
    return (await response.json()) as T;
  }

  let bodyDetail: unknown;
  try {
    bodyDetail = (await response.json())?.detail;
  } catch {
    bodyDetail = undefined;
  }
  if (response.status === 422 && Array.isArray(bodyDetail)) {
    throw new ApiError(
      "The backend rejected the submission. See the highlighted fields.",
      422,
      parseValidationErrors(bodyDetail as ApiValidationErrorItem[]),
    );
  }
  const message =
    typeof bodyDetail === "string"
      ? bodyDetail
      : `Request failed with status ${response.status}.`;
  throw new ApiError(message, response.status);
}
