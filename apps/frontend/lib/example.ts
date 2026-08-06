// The canonical "second_largest" example used by the Load Example button.
// The candidate has a real, well-known bug: sorted(values)[-2] returns the
// second element after sorting, which is wrong whenever the maximum value
// repeats (e.g. [5, 5, 5]) — it should return the second largest DISTINCT
// value. The reference handles distinctness correctly.

import type { SubmissionFormValues } from "./validation";

export const SECOND_LARGEST_EXAMPLE: SubmissionFormValues = {
  functionName: "second_largest",
  specification:
    "Return the second largest distinct value in the list. If there are " +
    "fewer than two distinct values, raise a ValueError.",
  candidateCode: `def second_largest(values):
    return sorted(values)[-2]
`,
  referenceCode: `def second_largest(values):
    unique = sorted(set(values))
    if len(unique) < 2:
        raise ValueError("need at least two distinct values")
    return unique[-2]
`,
  testInputsRaw: `[[3, 1, 2], [5, 5, 5], [9, 9, 4, 7]]`,
};
