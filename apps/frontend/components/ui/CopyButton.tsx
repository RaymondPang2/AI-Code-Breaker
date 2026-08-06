// A small copy-to-clipboard button with a transient "Copied" confirmation.
// Accessible: it's a real <button> with an aria-label, and the confirmation
// is announced via aria-live. Falls back silently if the clipboard API is
// unavailable (e.g. insecure context) rather than throwing.

"use client";

import { useCallback, useEffect, useRef, useState } from "react";

interface CopyButtonProps {
  value: string;
  label?: string; // accessible label, e.g. "Copy candidate code"
  className?: string;
}

export default function CopyButton({
  value,
  label = "Copy",
  className = "",
}: CopyButtonProps) {
  const [copied, setCopied] = useState(false);
  const timeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    return () => {
      if (timeoutRef.current) clearTimeout(timeoutRef.current);
    };
  }, []);

  const onCopy = useCallback(async () => {
    try {
      await navigator.clipboard.writeText(value);
      setCopied(true);
      if (timeoutRef.current) clearTimeout(timeoutRef.current);
      timeoutRef.current = setTimeout(() => setCopied(false), 1500);
    } catch {
      // Clipboard unavailable (insecure context / permissions). Do nothing
      // visible rather than crash — copying is a convenience, not critical.
    }
  }, [value]);

  return (
    <button
      type="button"
      onClick={onCopy}
      aria-label={copied ? `${label} (copied)` : label}
      className={`inline-flex items-center gap-1 rounded border border-slate-700 bg-slate-800/40 px-1.5 py-0.5 text-[10px] font-medium text-slate-300 transition-colors hover:bg-slate-800 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-400 focus-visible:ring-offset-2 focus-visible:ring-offset-[#0b0d12] ${className}`}
    >
      <span aria-hidden="true">{copied ? "✓" : "⧉"}</span>
      <span>{copied ? "Copied" : "Copy"}</span>
      <span aria-live="polite" className="sr-only">
        {copied ? "Copied to clipboard" : ""}
      </span>
    </button>
  );
}
