"""
Canonical analysis job statuses and the stage sequence.

Statuses are plain strings (stored in AnalysisRun.status) rather than a DB
enum, so adding one later is a code change, not a migration. The worker
moves a run through the active stages in order; terminal states end it.
"""

from __future__ import annotations

# Terminal + lifecycle states.
QUEUED = "queued"
COMPLETED = "completed"
FAILED = "failed"
CANCELLED = "cancelled"

# Active processing stages (also used as the `status` while that stage runs).
GENERATING_TESTS = "generating_tests"
EXECUTING_TESTS = "executing_tests"
SEARCHING_PROPERTIES = "searching_properties"
MINIMIZING = "minimizing"
EXPLAINING = "explaining"

# All valid statuses (for validation / documentation).
ALL_STATUSES = (
    QUEUED,
    GENERATING_TESTS,
    EXECUTING_TESTS,
    SEARCHING_PROPERTIES,
    MINIMIZING,
    EXPLAINING,
    COMPLETED,
    FAILED,
    CANCELLED,
)

# The ordered active stages a normal run passes through. Not every run hits
# every stage (e.g. search/minimize/explain are conditional), but this is
# the canonical order and drives the coarse progress percentage.
STAGE_SEQUENCE = (
    GENERATING_TESTS,
    EXECUTING_TESTS,
    SEARCHING_PROPERTIES,
    MINIMIZING,
    EXPLAINING,
)

TERMINAL_STATUSES = frozenset({COMPLETED, FAILED, CANCELLED})


def is_terminal(status: str) -> bool:
    return status in TERMINAL_STATUSES


def stage_progress(stage: str) -> float:
    """Coarse 0..1 progress for a given active stage, by position in the
    canonical sequence. Terminal states map to 1.0 (completed) or the last
    known stage's progress otherwise."""
    if stage == COMPLETED:
        return 1.0
    if stage == QUEUED:
        return 0.0
    if stage in STAGE_SEQUENCE:
        # +1 so entering the first stage shows some progress, and finishing
        # the last active stage approaches (but doesn't hit) 1.0 until the
        # run is marked completed.
        idx = STAGE_SEQUENCE.index(stage)
        return round((idx + 1) / (len(STAGE_SEQUENCE) + 1), 3)
    return 0.0
