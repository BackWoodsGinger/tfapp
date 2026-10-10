"""
Ongoing leave or lay-off (FMLA, Leave of Absence, Disability, Lay-Off): generate one absence per scheduled work day
while the leave is active, and remove/refund generated absences outside the leave's dates.

Every function here is idempotent, so it is safe to run from cron, payroll close, and admin saves.
Absences in payroll-finalized weeks are never created or removed; unfinalize payroll first.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from django.db import IntegrityError, transaction
from django.db.models import Q

from attendance.models import (
    CustomUser,
    LeaveOfAbsence,
    Occurrence,
    OccurrenceType,
    PayrollPeriod,
    PTOBalanceHistory,
    TimeOffRequestStatus,
)
from attendance.services.holiday_plan_service import effective_work_hours_for_day


@dataclass
class LeaveSyncResult:
    created: int = 0
    removed: int = 0
    skipped_finalized: int = 0

    def __add__(self, other: "LeaveSyncResult") -> "LeaveSyncResult":
        return LeaveSyncResult(
            self.created + other.created,
            self.removed + other.removed,
            self.skipped_finalized + other.skipped_finalized,
        )


def _week_ending_saturday(d: date) -> date:
    return d + timedelta(days=(5 - d.weekday()) % 7)


def _finalized_week_endings(first: date, last: date) -> set[date]:
    return set(
        PayrollPeriod.objects.filter(
            is_finalized=True,
            week_ending__range=(_week_ending_saturday(first), _week_ending_saturday(last)),
        ).values_list("week_ending", flat=True)
    )


def _refund_and_delete(occurrences, reason_prefix: str) -> int:
    """Credit back PTO/personal applied for these absences, then delete them."""
    rows = list(occurrences)
    if not rows:
        return 0
    with transaction.atomic():
        u = CustomUser.objects.select_for_update().get(pk=rows[0].user_id)
        for occ in rows:
            if occ.pto_applied and (occ.pto_hours_applied or occ.personal_hours_applied):
                u.pto_balance = round(u.pto_balance + float(occ.pto_hours_applied or 0.0), 2)
                u.personal_time_balance = round(
                    max(0.0, u.personal_time_balance - float(occ.personal_hours_applied or 0.0)), 2
                )
                if occ.pto_hours_applied:
                    PTOBalanceHistory.record(
                        user=u,
                        change=float(occ.pto_hours_applied),
                        reason=f"{reason_prefix} (refund): {occ.get_subtype_display()} ({occ.date})",
                        balance_after=u.pto_balance,
                    )
            occ.delete()
        u.save()
    return len(rows)


def _remove_out_of_range(leave: LeaveOfAbsence) -> LeaveSyncResult:
    result = LeaveSyncResult()
    if leave.end_date and leave.generated_through and leave.generated_through > leave.end_date:
        leave.generated_through = leave.end_date
        LeaveOfAbsence.objects.filter(pk=leave.pk).update(generated_through=leave.end_date)
    outside = Q(date__lt=leave.start_date)
    if leave.end_date:
        outside |= Q(date__gt=leave.end_date)
    rows = list(leave.occurrences.filter(outside).order_by("date"))
    if not rows:
        return result
    finalized = _finalized_week_endings(rows[0].date, rows[-1].date)
    removable = [o for o in rows if _week_ending_saturday(o.date) not in finalized]
    result.skipped_finalized = len(rows) - len(removable)
    result.removed = _refund_and_delete(removable, "Leave dates changed")
    return result


def _create_missing(leave: LeaveOfAbsence, today: date) -> LeaveSyncResult:
    result = LeaveSyncResult()
    last = min(today, leave.end_date) if leave.end_date else today
    first = leave.start_date
    if leave.generated_through and leave.generated_through >= first:
        first = leave.generated_through + timedelta(days=1)
    if last < first:
        return result
    user = leave.user
    already_generated = set(leave.occurrences.values_list("date", flat=True))
    covered_by_time_off = set(
        Occurrence.objects.filter(
            user=user,
            date__range=(first, last),
            time_off_request__status=TimeOffRequestStatus.APPROVED,
        ).values_list("date", flat=True)
    )
    finalized = _finalized_week_endings(first, last)

    d = first
    while d <= last:
        if d not in already_generated and d not in covered_by_time_off:
            hours = effective_work_hours_for_day(user, d)
            if hours > 0:
                if _week_ending_saturday(d) in finalized:
                    result.skipped_finalized += 1
                else:
                    try:
                        with transaction.atomic():
                            Occurrence.objects.create(
                                user=user,
                                occurrence_type=OccurrenceType.PLANNED,
                                subtype=leave.subtype,
                                date=d,
                                duration_hours=round(hours, 2),
                                leave=leave,
                            )
                        result.created += 1
                    except IntegrityError:
                        pass  # generated concurrently by another sync run
        d += timedelta(days=1)
    LeaveOfAbsence.objects.filter(pk=leave.pk).update(generated_through=last)
    leave.generated_through = last
    return result


def sync_leave(leave: LeaveOfAbsence, *, today: date | None = None) -> LeaveSyncResult:
    """Bring this leave's generated absences in line with its start/end dates as of ``today``."""
    today = today or date.today()
    return _remove_out_of_range(leave) + _create_missing(leave, today)


def end_leave(leave: LeaveOfAbsence, end_date: date, ended_by=None) -> LeaveSyncResult:
    """Switch the leave off: ``end_date`` is the last day on leave."""
    leave.end_date = end_date
    leave.ended_by = ended_by
    leave.save(update_fields=["end_date", "ended_by", "updated_at"])
    return sync_leave(leave)


def sync_all_leaves(*, today: date | None = None, user=None) -> LeaveSyncResult:
    """
    Sync every leave that could still need absences: ongoing leaves and leaves that ended
    within the last 60 days (catches runs that were missed while the server was down).
    """
    today = today or date.today()
    qs = LeaveOfAbsence.objects.filter(
        Q(end_date__isnull=True) | Q(end_date__gte=today - timedelta(days=60)),
        start_date__lte=today,
    ).select_related("user")
    if user is not None:
        qs = qs.filter(user=user)
    total = LeaveSyncResult()
    for leave in qs:
        total = total + sync_leave(leave, today=today)
    return total


def delete_leave_with_absences(leave: LeaveOfAbsence) -> tuple[bool, str]:
    """
    Undo a leave entered by mistake: refund and delete its generated absences, then the leave.
    Refuses when any generated absence is in a payroll-finalized week.
    """
    rows = list(leave.occurrences.order_by("date"))
    if rows:
        finalized = _finalized_week_endings(rows[0].date, rows[-1].date)
        locked = [o for o in rows if _week_ending_saturday(o.date) in finalized]
        if locked:
            return False, (
                f"{leave} has {len(locked)} absence(s) in finalized payroll weeks. "
                "Unfinalize those weeks first, or set an end date instead of deleting."
            )
    removed = _refund_and_delete(rows, "Leave deleted")
    leave.delete()
    return True, f"Deleted {leave} and {removed} generated absence(s)."
