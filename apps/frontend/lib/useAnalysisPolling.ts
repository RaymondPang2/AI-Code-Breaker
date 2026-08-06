// Polls an analysis run until it reaches a terminal state (completed /
// failed / cancelled), with capped exponential backoff. Starts fast (so a
// quick run feels responsive) and backs off (so a long run doesn't hammer
// the API). Stops on terminal status, on unmount, and surfaces errors.

"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { ApiError, fetchAnalysisRun } from "@/lib/api";
import { isTerminalStatus, type AnalysisRunRead } from "@/lib/types";

// Backoff schedule (ms). Poll quickly at first, then ease off. The last
// value repeats for the remainder of a long-running job.
const BACKOFF_MS = [800, 1200, 2000, 3000, 5000] as const;
const MAX_INTERVAL = BACKOFF_MS[BACKOFF_MS.length - 1];

export interface PollingState {
  run: AnalysisRunRead | null;
  polling: boolean;
  error: string | null;
}

export function useAnalysisPolling(
  submissionId: string | null,
  analysisId: string | null,
): PollingState & { refetch: () => void } {
  const [run, setRun] = useState<AnalysisRunRead | null>(null);
  const [polling, setPolling] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const timeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const attemptRef = useRef(0);
  const cancelledRef = useRef(false);

  const clearTimer = () => {
    if (timeoutRef.current) {
      clearTimeout(timeoutRef.current);
      timeoutRef.current = null;
    }
  };

  const poll = useCallback(async () => {
    if (!submissionId || !analysisId || cancelledRef.current) return;
    try {
      const next = await fetchAnalysisRun(submissionId, analysisId);
      if (cancelledRef.current) return;
      setRun(next);
      setError(null);

      if (isTerminalStatus(next.status)) {
        setPolling(false);
        clearTimer();
        return;
      }
      // Schedule the next poll with backoff.
      const interval =
        BACKOFF_MS[Math.min(attemptRef.current, BACKOFF_MS.length - 1)] ??
        MAX_INTERVAL;
      attemptRef.current += 1;
      timeoutRef.current = setTimeout(poll, interval);
    } catch (err) {
      if (cancelledRef.current) return;
      // A transient fetch error shouldn't kill polling outright — back off
      // and retry a few times, but surface the message.
      const message =
        err instanceof ApiError ? err.message : "Lost contact with the backend.";
      setError(message);
      // 404 is terminal (the run doesn't exist); stop.
      if (err instanceof ApiError && err.status === 404) {
        setPolling(false);
        clearTimer();
        return;
      }
      attemptRef.current += 1;
      const interval = Math.min(
        MAX_INTERVAL,
        BACKOFF_MS[Math.min(attemptRef.current, BACKOFF_MS.length - 1)] ??
          MAX_INTERVAL,
      );
      timeoutRef.current = setTimeout(poll, interval);
    }
  }, [submissionId, analysisId]);

  const start = useCallback(() => {
    clearTimer();
    attemptRef.current = 0;
    cancelledRef.current = false;
    if (submissionId && analysisId) {
      setPolling(true);
      setError(null);
      poll();
    }
  }, [submissionId, analysisId, poll]);

  useEffect(() => {
    cancelledRef.current = false;
    if (submissionId && analysisId) {
      start();
    }
    return () => {
      cancelledRef.current = true;
      clearTimer();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [submissionId, analysisId]);

  return { run, polling, error, refetch: start };
}
