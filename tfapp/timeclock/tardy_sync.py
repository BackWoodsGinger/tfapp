"""Rebuild tardy occurrences from saved time-entry punches (admin / manual edit)."""

from django.db import transaction

from attendance.models import CustomUser, revert_tardy_occurrences_for_adjust_punch


def sync_tardy_occurrences_for_time_entry(entry):
    """
    Remove existing tardy absences for this user and date (refunding PTO/personal),
    then re-apply start-of-shift and lunch tardy rules from the entry's punches.
    """
    with transaction.atomic():
        u = CustomUser.objects.select_for_update().get(pk=entry.user_id)
        revert_tardy_occurrences_for_adjust_punch(u, entry.date)
    entry.refresh_from_db()
    if entry.clock_in:
        entry.check_tardy()
    if entry.lunch_in:
        entry.check_lunch_tardy()
