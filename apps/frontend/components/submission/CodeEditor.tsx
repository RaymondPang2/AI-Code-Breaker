// A thin wrapper over Monaco tuned for short Python snippets. Monaco is
// loaded dynamically (client-only) so it never runs during SSR. A labelled
// container and a loading placeholder keep the control understandable
// before the editor mounts.

"use client";

import Editor from "@monaco-editor/react";

interface CodeEditorProps {
  id: string;
  label: string;
  value: string;
  onChange: (value: string) => void;
  ariaDescribedBy?: string;
  invalid?: boolean;
  language?: string;
  heightClass?: string;
}

export default function CodeEditor({
  id,
  label,
  value,
  onChange,
  ariaDescribedBy,
  invalid = false,
  language = "python",
  heightClass = "h-56",
}: CodeEditorProps) {
  return (
    <div>
      <div className="mb-1.5 flex items-center justify-between">
        <label
          htmlFor={id}
          className="text-sm font-medium text-slate-300"
        >
          {label}
        </label>
        <span className="font-mono text-[10px] uppercase tracking-widest text-slate-600">
          {language}
        </span>
      </div>
      <div
        className={`overflow-hidden rounded-lg border ${
          invalid ? "border-rose-500/70" : "border-slate-800"
        } bg-[#0d1017] focus-within:border-slate-600`}
      >
        <div className={heightClass} id={id} aria-describedby={ariaDescribedBy}>
          <Editor
            language={language}
            value={value}
            onChange={(next) => onChange(next ?? "")}
            theme="vs-dark"
            loading={
              <div className="flex h-full items-center justify-center text-xs text-slate-600">
                Loading editor…
              </div>
            }
            options={{
              minimap: { enabled: false },
              fontSize: 13,
              fontFamily: "var(--font-mono), monospace",
              lineNumbers: "on",
              scrollBeyondLastLine: false,
              tabSize: 4,
              renderLineHighlight: "line",
              padding: { top: 12, bottom: 12 },
              scrollbar: { alwaysConsumeMouseWheel: false },
              automaticLayout: true,
            }}
          />
        </div>
      </div>
    </div>
  );
}
