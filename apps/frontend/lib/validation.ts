// Client-side validation of obvious input errors, so a user gets immediate
// feedback without a round trip. This deliberately mirrors — but does not
// replace — the backend's authoritative validation: the backend is still
// the source of truth, and its errors are surfaced too (see ApiError).

import { LIMITS } from "./types";

export interface ValidationResult {
  // Field name -> message. Empty object means no client-side errors.
  fieldErrors: Record<string, string>;
  // Parsed test inputs, only present when the JSON parsed cleanly.
  parsedTestInputs?: number[][];
}

// Python keywords rejected as function names (mirrors the backend's
// keyword.iskeyword check). Kept in sync deliberately; the backend remains
// authoritative if this list ever drifts.
const PYTHON_KEYWORDS = new Set([
  "False", "None", "True", "and", "as", "assert", "async", "await",
  "break", "class", "continue", "def", "del", "elif", "else", "except",
  "finally", "for", "from", "global", "if", "import", "in", "is", "lambda",
  "nonlocal", "not", "or", "pass", "raise", "return", "try", "while",
  "with", "yield",
]);

const IDENTIFIER_RE = /^[A-Za-z_][A-Za-z0-9_]*$/;

export interface SubmissionFormValues {
  functionName: string;
  specification: string;
  candidateCode: string;
  referenceCode: string;
  testInputsRaw: string;
}

export function validateSubmission(
  values: SubmissionFormValues,
): ValidationResult {
  const fieldErrors: Record<string, string> = {};

  const name = values.functionName.trim();
  if (!name) {
    fieldErrors.function_name = "Enter the function name.";
  } else if (name.length > LIMITS.MAX_FUNCTION_NAME_LENGTH) {
    fieldErrors.function_name = `Keep the name under ${LIMITS.MAX_FUNCTION_NAME_LENGTH} characters.`;
  } else if (!IDENTIFIER_RE.test(name)) {
    fieldErrors.function_name =
      "Use a valid Python identifier (letters, digits, underscores; can't start with a digit).";
  } else if (PYTHON_KEYWORDS.has(name)) {
    fieldErrors.function_name = "That's a Python keyword — pick another name.";
  }

  const spec = values.specification.trim();
  if (!spec) {
    fieldErrors.specification = "Describe what the function should do.";
  } else if (spec.length > LIMITS.MAX_SPECIFICATION_LENGTH) {
    fieldErrors.specification = `Keep the specification under ${LIMITS.MAX_SPECIFICATION_LENGTH} characters.`;
  }

  if (!values.candidateCode.trim()) {
    fieldErrors.candidate_code = "Paste the candidate implementation.";
  } else if (values.candidateCode.length > LIMITS.MAX_SOURCE_CODE_LENGTH) {
    fieldErrors.candidate_code = `Keep the code under ${LIMITS.MAX_SOURCE_CODE_LENGTH} characters.`;
  }

  if (!values.referenceCode.trim()) {
    fieldErrors.reference_code = "Paste the reference implementation.";
  } else if (values.referenceCode.length > LIMITS.MAX_SOURCE_CODE_LENGTH) {
    fieldErrors.reference_code = `Keep the code under ${LIMITS.MAX_SOURCE_CODE_LENGTH} characters.`;
  }

  const parsed = parseTestInputs(values.testInputsRaw);
  if (parsed.error) {
    fieldErrors.test_inputs = parsed.error;
  }

  return { fieldErrors, parsedTestInputs: parsed.value };
}

interface ParseResult {
  value?: number[][];
  error?: string;
}

// Parse the manual test-input editor's contents. Empty is valid (means "no
// manual inputs"). Otherwise it must be a JSON array of arrays of integers.
export function parseTestInputs(raw: string): ParseResult {
  const trimmed = raw.trim();
  if (!trimmed) {
    return { value: [] };
  }

  let parsed: unknown;
  try {
    parsed = JSON.parse(trimmed);
  } catch {
    return {
      error:
        "Test inputs must be valid JSON, e.g. [[1, 2, 3], [5, 5, 5]].",
    };
  }

  if (!Array.isArray(parsed)) {
    return { error: "Test inputs must be a JSON array of arrays." };
  }
  if (parsed.length > LIMITS.MAX_TEST_CASES) {
    return {
      error: `At most ${LIMITS.MAX_TEST_CASES} test inputs are allowed (got ${parsed.length}).`,
    };
  }

  const result: number[][] = [];
  for (let i = 0; i < parsed.length; i++) {
    const inner = parsed[i];
    if (!Array.isArray(inner)) {
      return { error: `Test input #${i + 1} must be an array of integers.` };
    }
    if (inner.length > LIMITS.MAX_INPUT_LIST_SIZE) {
      return {
        error: `Test input #${i + 1} has ${inner.length} elements (max ${LIMITS.MAX_INPUT_LIST_SIZE}).`,
      };
    }
    for (let j = 0; j < inner.length; j++) {
      const item = inner[j];
      // Reject booleans (JSON true/false) and non-integers, matching the
      // backend which rejects bools-as-ints and floats like 2.0.
      if (typeof item !== "number" || !Number.isInteger(item)) {
        return {
          error: `Test input #${i + 1} item #${j + 1} must be an integer.`,
        };
      }
    }
    result.push(inner as number[]);
  }

  return { value: result };
}
