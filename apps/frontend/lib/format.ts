// Pure display helpers shared across the results views. No React here so
// these are trivially unit-testable. Nothing fabricates data — every helper
// only reformats values it is given, and returns null/empty when a value is
// absent so callers can render an honest "not available" state.

import type {
  AiUsage,
  FunctionExecutionResult,
  TestComparisonResult,
} from "./types";

// Human phrase for a runner status.
export function statusLabel(status: string): string {
  switch (status) {
    case "success":
      return "returned a value";
    case "timeout":
      return "timed out";
    case "internal_error":
      return "runner error";
    case "unserializable_output":
      return "unserializable output";
    case "runtime_error":
      return "raised an exception";
    case "syntax_error":
      return "syntax error";
    case "load_error":
      return "failed to load";
    default:
      return status.replace(/_/g, " ");
  }
}

// A compact one-line description of an execution result, e.g. "5" or
// "IndexError: list index out of range" or "timed out".
export function describeResult(result: FunctionExecutionResult): string {
  if (result.status === "success") {
    return safeJson(result.returned_value);
  }
  if (result.exception_type) {
    return result.exception_message
      ? `${result.exception_type}: ${result.exception_message}`
      : result.exception_type;
  }
  return statusLabel(result.status);
}

// True when a result represents a raised exception (as opposed to a normal
// return, timeout, or internal error).
export function isException(result: FunctionExecutionResult): boolean {
  return (
    result.status === "runtime_error" ||
    result.status === "syntax_error" ||
    result.status === "load_error"
  );
}

// JSON.stringify that never throws (falls back to String()).
export function safeJson(value: unknown): string {
  try {
    return JSON.stringify(value);
  } catch {
    return String(value);
  }
}

// The classification of a single comparison, used for the failure category
// and row styling.
export type ComparisonOutcome = "match" | "mismatch" | "inconclusive";

export function comparisonOutcome(c: TestComparisonResult): ComparisonOutcome {
  if (c.internal_error) return "inconclusive";
  return c.match ? "match" : "mismatch";
}

// A short, human failure category for a mismatch, derived only from the
// verified execution statuses (not from any AI commentary).
export function failureCategory(c: TestComparisonResult): string {
  if (c.internal_error) {
    // A runner/infrastructure failure must be shown specifically, not as a
    // bare "inconclusive". Name the side(s) that errored and the concrete
    // runner error (e.g. "EmptyRunnerOutput: runner process produced no
    // output ...") so the cause is diagnosable from the results view.
    const parts: string[] = [];
    if (c.candidate.status === "internal_error") {
      parts.push(`candidate: ${describeResult(c.candidate)}`);
    }
    if (c.reference.status === "internal_error") {
      parts.push(`reference: ${describeResult(c.reference)}`);
    }
    return parts.length > 0
      ? `Runner error — ${parts.join("; ")}`
      : "Inconclusive (runner error)";
  }
  if (c.match) return "Match";
  const cand = c.candidate.status;
  const ref = c.reference.status;
  if (cand === "success" && ref === "success") {
    return "Different return values";
  }
  if (isException(c.candidate) && ref === "success") {
    return "Candidate raised, reference returned";
  }
  if (cand === "success" && isException(c.reference)) {
    return "Candidate returned, reference raised";
  }
  if (isException(c.candidate) && isException(c.reference)) {
    return "Different exception types";
  }
  if (cand === "timeout" || ref === "timeout") {
    return "Timeout mismatch";
  }
  return "Behavioral difference";
}

// Extract the strategies used from a persisted run's `configuration` object.
// Only reports strategies the configuration actually records; unknown keys
// are ignored rather than guessed at.
export function strategiesFromConfiguration(
  configuration: Record<string, unknown> | null | undefined,
): string[] {
  if (!configuration) return [];
  const strategies: string[] = [];
  if (configuration.generate_tests === true) {
    strategies.push("Deterministic edge cases");
  }
  const manual = configuration.manual_test_count;
  if (typeof manual === "number" && manual > 0) {
    strategies.push(`Manual inputs (${manual})`);
  }
  if (configuration.use_ai_tests === true) {
    strategies.push("Claude-targeted tests");
  }
  return strategies;
}

// Strategies inferred from a live analyze response's comparison sources
// (used when we don't have a persisted configuration object). Reports only
// sources actually present in the results.
export function strategiesFromComparisons(
  comparisons: TestComparisonResult[],
): string[] {
  const sources = new Set(comparisons.map((c) => c.source));
  const out: string[] = [];
  if (sources.has("manual")) out.push("Manual inputs");
  if (sources.has("generated")) out.push("Deterministic edge cases");
  if (sources.has("ai")) out.push("Claude-targeted tests");
  return out;
}

// Format a duration given in seconds (persisted) into a compact string.
export function formatDuration(seconds: number | null | undefined): string | null {
  if (seconds === null || seconds === undefined) return null;
  if (seconds < 1) return `${Math.round(seconds * 1000)} ms`;
  return `${seconds.toFixed(2)} s`;
}

// Format a runtime given in milliseconds (per execution).
export function formatRuntimeMs(ms: number | null | undefined): string | null {
  if (ms === null || ms === undefined) return null;
  if (ms < 1) return "<1 ms";
  return `${Math.round(ms)} ms`;
}

// Whether an AiUsage object carries anything worth displaying.
export function hasUsageDetail(usage: AiUsage | null | undefined): boolean {
  if (!usage) return false;
  return (
    usage.model != null ||
    usage.input_tokens != null ||
    usage.output_tokens != null ||
    usage.latency_ms != null ||
    (usage.request_count != null && usage.request_count > 0) ||
    usage.error != null
  );
}
