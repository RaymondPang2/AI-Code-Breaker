import "@testing-library/jest-dom/vitest";
import { vi } from "vitest";
import React from "react";

// Monaco is heavy and depends on browser APIs jsdom doesn't provide, so we
// replace it in tests with a minimal textarea that preserves the value /
// onChange contract our CodeEditor wrapper relies on. This mocks a
// third-party editor for TEST purposes only — production code is untouched.
vi.mock("@monaco-editor/react", () => ({
  default: ({
    value,
    onChange,
  }: {
    value: string;
    onChange: (v: string | undefined) => void;
  }) =>
    React.createElement("textarea", {
      "data-testid": "monaco-mock",
      value,
      onChange: (e: React.ChangeEvent<HTMLTextAreaElement>) =>
        onChange(e.target.value),
    }),
}));
