// Builds a shareable URL to the persisted results page for a run, and lets
// the user copy it. The URL uses the stored submission + analysis IDs, so it
// re-fetches the saved run rather than re-running analysis. Rendered only
// when both IDs are present (i.e. the run was persisted).

"use client";

import { useEffect, useState } from "react";

import CopyButton from "@/components/ui/CopyButton";

export default function ShareLink({
  submissionId,
  analysisId,
}: {
  submissionId: string;
  analysisId: string;
}) {
  const path = `/results/${submissionId}/${analysisId}`;
  // Build an absolute URL on the client (window is unavailable during SSR).
  const [absoluteUrl, setAbsoluteUrl] = useState(path);
  useEffect(() => {
    if (typeof window !== "undefined") {
      setAbsoluteUrl(`${window.location.origin}${path}`);
    }
  }, [path]);

  return (
    <div className="flex flex-wrap items-center gap-2 rounded-lg border border-slate-800 bg-[#0d1017] px-3 py-2">
      <span className="font-mono text-[10px] uppercase tracking-widest text-slate-500">
        Shareable result
      </span>
      <a
        href={path}
        className="flex-1 truncate font-mono text-xs text-emerald-300 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-400 focus-visible:ring-offset-2 focus-visible:ring-offset-[#0b0d12]"
      >
        {absoluteUrl}
      </a>
      <CopyButton value={absoluteUrl} label="Copy shareable link" />
    </div>
  );
}
