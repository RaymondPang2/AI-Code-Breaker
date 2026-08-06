"use client";

import { useEffect, useState } from "react";
import { fetchHealth, type HealthResponse } from "@/lib/api";

type ConnectionState = "checking" | "reachable" | "unreachable";

const STATE_COPY: Record<ConnectionState, string> = {
  checking: "Checking…",
  reachable: "Reachable",
  unreachable: "Unreachable",
};

const STATE_DOT_CLASS: Record<ConnectionState, string> = {
  checking: "bg-amber-400 animate-pulse",
  reachable: "bg-emerald-400",
  unreachable: "bg-rose-500",
};

export default function HealthStatus() {
  const [state, setState] = useState<ConnectionState>("checking");
  const [payload, setPayload] = useState<HealthResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;

    fetchHealth()
      .then((data) => {
        if (cancelled) return;
        setPayload(data);
        setState("reachable");
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setError(err instanceof Error ? err.message : "Unknown error");
        setState("unreachable");
      });

    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <div className="w-full max-w-md rounded-lg border border-slate-800 bg-slate-900/60 p-5">
      <div className="flex items-center gap-3">
        <span
          className={`h-2.5 w-2.5 shrink-0 rounded-full ${STATE_DOT_CLASS[state]}`}
          aria-hidden="true"
        />
        <div>
          <p className="text-sm font-medium text-slate-200">
            Backend service
          </p>
          <p className="text-sm text-slate-400">{STATE_COPY[state]}</p>
        </div>
      </div>

      <pre className="mt-4 overflow-x-auto rounded-md border border-slate-800 bg-black/40 p-3 font-mono text-xs text-slate-300">
        {state === "unreachable"
          ? `error: ${error}`
          : JSON.stringify(payload, null, 2) ?? "waiting for response…"}
      </pre>
    </div>
  );
}
