"""
Pydantic model for one test input selected to run through candidate and
reference, along with where it came from.

Kept in its own module (rather than folded into submission.py) because
both a manually supplied input and a generated input need to carry the
same metadata shape — this is the common contract between
app.services.test_case_generator and app.services.test_selection_service.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

TestCaseSource = Literal["manual", "generated", "ai"]


class SelectedTestCase(BaseModel):
    """One input that will be run through both implementations."""

    input: list[int]
    source: TestCaseSource = Field(
        description="Whether this input was supplied by the caller or produced by TestCaseGenerator."
    )
    category: str = Field(
        description=(
            "For generated inputs, which required category produced this "
            "input (e.g. 'duplicate_maximum'). For manual inputs, always "
            "'manual'. For AI-proposed inputs, a short category string "
            "Claude supplied."
        )
    )
    reason: str = Field(
        description="Human-readable explanation of what this input is intended to exercise."
    )
