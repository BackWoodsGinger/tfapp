"""
Structured attendance interpretation (non-persistent). Expand as detection logic moves out of models.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any


@dataclass
class AttendanceResult:
    """Outcome of interpreting punches + schedule for a workday or shift window."""

    worked_hours: Decimal
    tardy_minutes: int
    payable_hours: Decimal
    pto_candidate_hours: Decimal
    exchange_eligible: bool
    occurrences: list[Any] = field(default_factory=list)
