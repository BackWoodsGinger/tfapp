"""
Attendance orchestration: overrides, tardy generation, punch sync, past-due application.
"""
from __future__ import annotations

from datetime import date, datetime

from django.db import transaction
from django.utils import timezone as django_tz

from attendance.models import (
    OCCURRENCE_SUBTYPES_USING_PTO_OR_PERSONAL,
    CustomUser,
    Occurrence,
    OccurrenceSubtype,
    OccurrenceType,
    PTOBalanceHistory,
)
from attendance.services.time_processing import clock_in_requires_approver, get_scheduled_start_for_day
from timeclock.models import TimeEntry


def entries_requiring_clock_in_override(week_start: date, week_ending: date):
    """
    Time entries in week that require clock-in override approval but do not have it yet.
    Returns list of dicts: entry, reason, key.
    """
    out = []
    rows = (
        TimeEntry.objects.filter(
            date__range=[week_start, week_ending],
            clock_in__isnull=False,
        )
        .select_related("user")
        .order_by("date", "user__payroll_lastname", "user__payroll_firstname", "user__username")
    )
    for e in rows:
        clock_in_local = django_tz.localtime(e.clock_in)
        requires, reason = clock_in_requires_approver(e.user, clock_in_local, e.date)
        if reason == "unscheduled":
            if not e.clock_in_authorized_by_id and not e.clock_in_override_denied:
                out.append(
                    {
                        "entry": e,
                        "reason": "unscheduled",
                        "key": f"{e.id}:unscheduled",
                    }
                )
            fallback_start = e._fallback_scheduled_start_time_for_unscheduled_day()
            if fallback_start:
                fallback_local = django_tz.make_aware(
                    datetime.combine(e.date, fallback_start),
                    django_tz.get_current_timezone(),
                )
                if (
                    clock_in_local <= fallback_local
                    and not e.clock_in_early_authorized_by_id
                    and not e.clock_in_early_override_denied
                ):
                    out.append(
                        {
                            "entry": e,
                            "reason": "early",
                            "key": f"{e.id}:early",
                        }
                    )
        elif reason == "early":
            if not e.clock_in_early_authorized_by_id and not e.clock_in_early_override_denied:
                out.append(
                    {
                        "entry": e,
                        "reason": "early",
                        "key": f"{e.id}:early",
                    }
                )
    return out


def revert_and_delete_orphan_time_off_for_exchange_week(
    users,
    week_start: date,
    week_ending: date,
):
    """
    Remove legacy/orphan TIME_OFF rows when an Exchange variance exists for the same user/date.
    Keeps a single source of truth for schedule variance so PTO/personal isn't double-applied.
    """
    exchange_days = set(
        Occurrence.objects.filter(
            user__in=users,
            date__range=[week_start, week_ending],
            subtype=OccurrenceSubtype.EXCHANGE,
            is_variance_to_schedule=True,
        ).values_list("user_id", "date")
    )
    if not exchange_days:
        return

    for user_id, occ_date in exchange_days:
        orphan_rows = Occurrence.objects.filter(
            user_id=user_id,
            date=occ_date,
            subtype=OccurrenceSubtype.TIME_OFF,
            is_variance_to_schedule=False,
            time_off_request__isnull=True,
        )
        if not orphan_rows.exists():
            continue
        user = CustomUser.objects.get(pk=user_id)
        pto_refund = 0.0
        personal_refund = 0.0
        for occ in orphan_rows:
            if occ.pto_applied:
                pto_refund += float(occ.pto_hours_applied or 0.0)
                personal_refund += float(occ.personal_hours_applied or 0.0)
            occ.delete()
        if pto_refund or personal_refund:
            user.pto_balance = round(user.pto_balance + pto_refund, 2)
            user.personal_time_balance = round(
                max(0.0, user.personal_time_balance - personal_refund),
                2,
            )
            user.save(update_fields=["pto_balance", "personal_time_balance"])


def create_tardy_occurrences_for_week(week_start, week_ending, period=None):
    """
    For each time entry in the week with clock_in and a schedule that day:
    if clock_in is later than scheduled start, create TARDY_IN_GRACE (<=4 min) or
    TARDY_OUT_OF_GRACE (5+ min late, duration = rounded loss). Skip if occurrence already exists.
    If period is given, set payroll_period on created occurrences so they can be reverted on unfinalize.
    Clock-in is compared in local time (schedule is stored as local); avoid UTC vs local mismatch.
    """
    entries = TimeEntry.objects.filter(
        date__range=[week_start, week_ending],
        clock_in__isnull=False,
    ).select_related("user")
    for e in entries:
        scheduled_start = get_scheduled_start_for_day(e.user, e.date)
        if not scheduled_start:
            continue
        if not e.clock_in:
            continue
        # Compare in local time: clock_in is stored UTC when USE_TZ=True
        clock_in_local = django_tz.localtime(e.clock_in)
        clock_in_time = clock_in_local.time()
        if clock_in_time <= scheduled_start:
            continue
        delta_minutes = (clock_in_time.hour * 60 + clock_in_time.minute) - (
            scheduled_start.hour * 60 + scheduled_start.minute
        )
        if delta_minutes <= 0:
            continue
        already = Occurrence.objects.filter(
            user=e.user,
            date=e.date,
            subtype__in=[OccurrenceSubtype.TARDY_IN_GRACE, OccurrenceSubtype.TARDY_OUT_OF_GRACE],
        ).exists()
        if already:
            continue
        if delta_minutes <= 4:
            Occurrence.objects.create(
                user=e.user,
                occurrence_type=OccurrenceType.UNPLANNED,
                subtype=OccurrenceSubtype.TARDY_IN_GRACE,
                date=e.date,
                duration_hours=0,
                payroll_period=period,
            )
        else:
            loss_hours = round((delta_minutes / 60.0) * 4) / 4  # round to nearest quarter hour
            if loss_hours <= 0:
                loss_hours = 0.25
            occ = Occurrence.objects.create(
                user=e.user,
                occurrence_type=OccurrenceType.UNPLANNED,
                subtype=OccurrenceSubtype.TARDY_OUT_OF_GRACE,
                date=e.date,
                duration_hours=loss_hours,
                payroll_period=period,
            )
            occ.save()


def revert_tardy_occurrences_for_adjust_punch(user, occ_date):
    """
    Before applying an approved punch adjustment, remove tardy occurrences for that calendar day,
    refunding any PTO/personal that was applied for them (same idea as cancelling time off).
    Call inside transaction.atomic(); ``user`` must be the locked CustomUser instance (select_for_update).
    """
    qs = Occurrence.objects.filter(
        user=user,
        date=occ_date,
        subtype__in=[
            OccurrenceSubtype.TARDY_IN_GRACE,
            OccurrenceSubtype.TARDY_OUT_OF_GRACE,
        ],
    )
    if not qs.exists():
        return
    for occ in qs:
        if occ.pto_applied:
            user.pto_balance = round(user.pto_balance + occ.pto_hours_applied, 2)
            user.personal_time_balance = round(
                max(0.0, user.personal_time_balance - occ.personal_hours_applied), 2
            )
            PTOBalanceHistory.record(
                user=user,
                change=float(occ.pto_hours_applied),
                reason=f"Adjust punch: revert {occ.get_subtype_display()} ({occ.date})",
                balance_after=user.pto_balance,
            )
        occ.delete()
    user.save()


def apply_past_due_occurrences(user):
    """
    Apply PTO/personal for any occurrences that are due (date <= today) but not yet applied.
    Call from dashboard or when loading user balance so that when a future approved date passes,
    the balance is updated on next view.
    """
    today = date.today()
    past_due = Occurrence.objects.filter(
        user=user,
        date__lte=today,
        pto_applied=False,
        subtype__in=OCCURRENCE_SUBTYPES_USING_PTO_OR_PERSONAL,
    )
    for occ in past_due:
        occ.apply_pto()


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
