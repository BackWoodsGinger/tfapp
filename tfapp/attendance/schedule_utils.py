"""
Compatibility layer: schedule helpers live in attendance.services.time_processing.
Import from here remains stable for existing call sites.
"""
from attendance.services.time_processing import (  # noqa: F401
    TIME_FMT,
    clock_in_at_or_after_scheduled_lunch_in,
    clock_in_requires_approver,
    clock_in_requires_approver_for_entry,
    effective_schedule_reference_date,
    entry_requires_payroll_lunch_import_review,
    crosses_midnight_for_day,
    earliest_clock_in_allowed,
    get_scheduled_end_time_for_day,
    get_scheduled_lunch_in_for_day,
    get_scheduled_lunch_out_for_day,
    get_scheduled_shift_end_datetime,
    get_scheduled_start_for_day,
    monday_typical_shift_label,
    schedule_row_has_lunch,
    scheduled_duration_hours_for_day,
    scheduled_hours_for_range,
    scheduled_lunch_datetimes_for_entry,
    suggested_punch_times_for_day,
    work_through_lunch_approved_for_day,
)
