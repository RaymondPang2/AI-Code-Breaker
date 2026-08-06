"""
Per-stage execution helper for the analysis job.

Each stage of the pipeline is wrapped so that:
  1. the run's status/progress is updated (and committed) BEFORE the stage
     starts, so the polling GET endpoint reflects live progress;
  2. the stage runs under a soft time budget enforced by a watchdog thread
     (signal-based alarms aren't usable here — the job runs off the main
     thread in the worker, and the Docker runner already uses SIGALRM); if
     the budget is exceeded a StageTimeout is raised;
  3. a cancellation check runs before the stage, so a run cancelled while
     queued/among stages stops promptly.

The watchdog cannot forcibly kill a truly wedged C call, but every stage
here is itself bounded (the Docker runner has its own subprocess timeouts,
the Hypothesis search has an internal deadline), so the watchdog is a
backstop that turns "slightly over budget" into a clean, recorded failure
rather than an indefinite hang.
"""

from __future__ import annotations

import threading
from typing import Callable, TypeVar

from sqlalchemy.orm import Session

from app.core import analysis_status as status
from app.repositories import analysis_repository as repo
from app.worker.errors import JobCancelled, StageTimeout

T = TypeVar("T")


class _StageWatchdog:
    """Runs a target callable with a soft timeout using a worker thread."""

    def __init__(self, timeout_seconds: float):
        self.timeout_seconds = timeout_seconds
        self._result: object = None
        self._exc: BaseException | None = None
        self._done = threading.Event()

    def run(self, fn: Callable[[], T]) -> T:
        def _target() -> None:
            try:
                self._result = fn()
            except BaseException as exc:  # capture to re-raise on caller thread
                self._exc = exc
            finally:
                self._done.set()

        thread = threading.Thread(target=_target, daemon=True)
        thread.start()
        finished = self._done.wait(timeout=self.timeout_seconds)
        if not finished:
            # The worker thread is daemon; we abandon it and report timeout.
            # Underlying bounded operations (runner subprocesses) will be
            # reaped by their own timeouts.
            raise StageTimeout(
                f"stage exceeded its {self.timeout_seconds:.0f}s budget"
            )
        if self._exc is not None:
            raise self._exc
        return self._result  # type: ignore[return-value]


def run_stage(
    session: Session,
    run_id,
    stage: str,
    timeout_seconds: float,
    fn: Callable[[], T],
) -> T:
    """
    Advance the run into `stage` (persisting status + progress), check for
    cancellation, then execute `fn` under a soft timeout. Returns fn()'s
    result, or raises StageTimeout / propagates fn's exception.
    """
    # Cancellation check: if something flipped the run to cancelled while it
    # was queued or between stages, stop now.
    current = repo.get_analysis_run(session, run_id)
    if current is not None and current.status == status.CANCELLED:
        raise JobCancelled("run was cancelled", stage=stage)

    repo.update_analysis_run_status(
        session,
        run_id,
        status=stage,
        progress=status.stage_progress(stage),
    )
    session.commit()

    watchdog = _StageWatchdog(timeout_seconds)
    return watchdog.run(fn)
