"""
Prompt construction for counterexample explanation.

Like test generation, this is reference-code-free: Claude sees the
candidate (with line numbers, so it can cite them), the minimized failing
input, and what BOTH implementations *returned* (normalized results +
sanitized exception text) — but never the reference implementation's
source. It's explaining a bug the runner already confirmed; it is not
re-deciding correctness.
"""

from __future__ import annotations

import json

from app.schemas.explanation import ExplanationRequest

EXPLANATION_SYSTEM_PROMPT = (
    "You are a debugging assistant. A differential-testing system has "
    "ALREADY CONFIRMED, by real execution, that a candidate Python function "
    "disagrees with a trusted reference implementation on a specific input. "
    "Your job is to explain WHY the candidate is wrong on that input.\n\n"
    "You are given the specification, the candidate code (with line "
    "numbers), the failing input, and what each implementation returned or "
    "raised. You are NOT given the reference source code, and you must not "
    "guess at it. Do not dispute the confirmed result: the candidate IS "
    "wrong on this input — explain the cause, don't relitigate it.\n\n"
    "Respond with ONLY a JSON object, no prose, no markdown fences, exactly "
    "in this shape:\n"
    "{\n"
    '  "summary": "one sentence",\n'
    '  "root_cause": "specific explanation",\n'
    '  "walkthrough": ["step one", "step two"],\n'
    '  "suspected_lines": [<candidate line numbers>],\n'
    '  "suggested_fix": "high-level fix",\n'
    '  "confidence": "low | medium | high"\n'
    "}\n\n"
    "suspected_lines must be line numbers that exist in the candidate code "
    "shown. The suggested_fix is a high-level proposal only; do not claim it "
    "is correct or tested. Keep the summary to a single sentence."
)

# Extra instruction appended when a patch proposal is requested.
_PATCH_INSTRUCTION = (
    "\n\nAdditionally include a \"suggested_patch\" field containing a "
    "proposed corrected version of the candidate function as a string. This "
    "is a PROPOSAL ONLY and will not be applied automatically — it will be "
    "shown to the user as a suggestion to review."
)


def _number_lines(source: str) -> str:
    """Prefix each line with a 1-based line number so the model can cite
    specific lines accurately."""
    lines = source.splitlines()
    width = len(str(len(lines))) if lines else 1
    return "\n".join(f"{str(i).rjust(width)}| {line}" for i, line in enumerate(lines, start=1))


def build_explanation_prompt(request: ExplanationRequest) -> str:
    """Assemble the user-turn prompt. Contains the candidate (line-numbered)
    and both results — never the reference source."""
    parts = [
        f"Function name: {request.function_name}",
        f"Specification:\n{request.specification}",
        f"Candidate implementation (line-numbered):\n{_number_lines(request.candidate_code)}",
        f"Minimized failing input: {json.dumps(request.minimized_failing_input)}",
        "Candidate result on that input (normalized):\n"
        + json.dumps(request.normalized_candidate_result, indent=2),
        "Reference result on that input (normalized — this is the correct "
        "behaviour):\n" + json.dumps(request.normalized_reference_result, indent=2),
    ]
    if request.candidate_exception_detail:
        parts.append(f"Candidate exception detail: {request.candidate_exception_detail}")
    if request.reference_exception_detail:
        parts.append(f"Reference exception detail: {request.reference_exception_detail}")
    parts.append(
        f"The candidate code has {request.candidate_line_count} lines; only "
        "cite line numbers within that range."
    )
    prompt = "\n\n".join(parts)
    if request.request_suggested_patch:
        prompt += _PATCH_INSTRUCTION
    return prompt
