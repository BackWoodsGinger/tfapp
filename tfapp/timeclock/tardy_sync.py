"""Rebuild tardy occurrences from saved time-entry punches (admin / manual edit)."""

from attendance.services.attendance_engine import sync_tardy_occurrences_for_time_entry

__all__ = ["sync_tardy_occurrences_for_time_entry"]
