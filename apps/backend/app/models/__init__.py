"""
Re-exports all ORM models and Base so Alembic's autogenerate can discover
the full metadata from a single import (`from app.models import Base`).
"""

from app.db.base import Base
from app.models.entities import (
    AnalysisRun,
    Counterexample,
    Execution,
    Submission,
    TestCase,
)

__all__ = [
    "Base",
    "Submission",
    "TestCase",
    "AnalysisRun",
    "Execution",
    "Counterexample",
]
