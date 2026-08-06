"""
Error taxonomy for analysis jobs.

The retry policy hinges on one distinction:

  - TransientJobError: something infrastructural and likely to succeed on a
    retry — a Redis blip, a provider timeout/unavailability, a transient DB
    error. These MAY be retried.

  - PermanentJobError: a determinate failure that a retry cannot fix —
    most importantly, anything caused by the *user's submitted code* or
    input (a candidate that won't import, malformed configuration). These
    are NEVER retried; retrying just burns the same failure again.

Deterministic user-code failures surface as PermanentJobError so the retry
machinery leaves them alone. A StageTimeout is treated as permanent by
default (a stage that blew its budget once will likely do so again on the
same input), but the overall-job timeout is enforced separately by RQ.
"""

from __future__ import annotations


class AnalysisJobError(Exception):
    """Base class for analysis job failures."""

    #: Whether the retry machinery may re-run the job for this error.
    retryable: bool = False

    def __init__(self, message: str, *, stage: str | None = None):
        super().__init__(message)
        self.stage = stage


class TransientJobError(AnalysisJobError):
    """Infrastructural failure that may succeed on retry (Redis/DB blip,
    provider timeout). Retryable."""

    retryable = True


class PermanentJobError(AnalysisJobError):
    """Determinate failure a retry cannot fix — notably anything caused by
    the user's submitted code or input. Never retried."""

    retryable = False


class StageTimeout(PermanentJobError):
    """A single stage exceeded its per-stage time budget. Treated as
    permanent: the same input would time out again."""


class JobCancelled(AnalysisJobError):
    """The run was cancelled (e.g. by the user) before/while processing.
    Not an error to retry — it's an intentional terminal state."""

    retryable = False
