import { describe, expect, it } from "vitest";
import { parseTestInputs, validateSubmission } from "../validation";
import type { SubmissionFormValues } from "../validation";

const VALID: SubmissionFormValues = {
  functionName: "second_largest",
  specification: "Return the second largest distinct value.",
  candidateCode: "def second_largest(v):\n    return sorted(v)[-2]\n",
  referenceCode: "def second_largest(v):\n    return sorted(set(v))[-2]\n",
  testInputsRaw: "[[1, 2, 3]]",
};

describe("parseTestInputs", () => {
  it("treats empty input as an empty list", () => {
    expect(parseTestInputs("").value).toEqual([]);
    expect(parseTestInputs("   ").value).toEqual([]);
  });

  it("parses a valid nested array", () => {
    expect(parseTestInputs("[[1,2],[3]]").value).toEqual([[1, 2], [3]]);
  });

  it("rejects invalid JSON", () => {
    expect(parseTestInputs("[[1,").error).toBeDefined();
  });

  it("rejects a non-array top level", () => {
    expect(parseTestInputs("42").error).toBeDefined();
  });

  it("rejects booleans and strings as items", () => {
    expect(parseTestInputs("[[true]]").error).toBeDefined();
    expect(parseTestInputs('[["x"]]').error).toBeDefined();
  });

  it("rejects non-array inner elements", () => {
    expect(parseTestInputs("[1,2]").error).toBeDefined();
  });

  it("rejects too many test cases", () => {
    const many = JSON.stringify(Array(21).fill([1]));
    expect(parseTestInputs(many).error).toBeDefined();
  });

  it("accepts negative integers", () => {
    expect(parseTestInputs("[[-5,-1,0]]").value).toEqual([[-5, -1, 0]]);
  });
});

describe("validateSubmission", () => {
  it("passes a well-formed submission", () => {
    const { fieldErrors } = validateSubmission(VALID);
    expect(fieldErrors).toEqual({});
  });

  it("flags an empty function name", () => {
    const { fieldErrors } = validateSubmission({ ...VALID, functionName: "" });
    expect(fieldErrors.function_name).toBeDefined();
  });

  it("flags an invalid identifier", () => {
    const { fieldErrors } = validateSubmission({ ...VALID, functionName: "2foo" });
    expect(fieldErrors.function_name).toBeDefined();
  });

  it("flags a python keyword", () => {
    const { fieldErrors } = validateSubmission({ ...VALID, functionName: "class" });
    expect(fieldErrors.function_name).toBeDefined();
  });

  it("flags missing code", () => {
    const { fieldErrors } = validateSubmission({
      ...VALID,
      candidateCode: "",
      referenceCode: "",
    });
    expect(fieldErrors.candidate_code).toBeDefined();
    expect(fieldErrors.reference_code).toBeDefined();
  });

  it("flags malformed test inputs", () => {
    const { fieldErrors } = validateSubmission({
      ...VALID,
      testInputsRaw: "[[1,",
    });
    expect(fieldErrors.test_inputs).toBeDefined();
  });
});
