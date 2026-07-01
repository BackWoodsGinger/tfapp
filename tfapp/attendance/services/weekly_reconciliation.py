"""
Payroll week finalization: variance/exchange, PTO application, accrual snapshots, daily summaries, revert.
"""
from __future__ import annotations

from datetime import date, timedelta

from django.db.models import Q, Sum
from django.utils import timezone as django_tz

from attendance.models import (
    OCCURRENCE_SUBTYPES_USING_PTO_OR_PERSONAL,
    CustomUser,
    DailyAttendanceSummary,
    Occurrence,
    OccurrenceSubtype,
    OccurrenceType,
    PayrollPeriodUserSnapshot,
    TimeOffRequestStatus,
)
from attendance.schedule_utils import scheduled_duration_hours_for_day, scheduled_hours_for_range
from attendance.services.holiday_plan_service import (
    effective_scheduled_hours_for_range,
    effective_work_hours_for_day,
)
from attendance.services.attendance_engine import (
    create_tardy_occurrences_for_week,
    revert_and_delete_orphan_time_off_for_exchange_week,
)
from timeclock.models import TimeEntry


def required_week_hours_for_policy(scheduled_week_hours: float) -> float:
    """
    Weekly hours that can require PTO/personal application.
    Policy cap: once a user reaches 40 worked/covered hours, do not apply additional PTO.
    """
    return min(scheduled_week_hours, 40.0)


def unfinalize_payroll_period(period):
    """
    Revert all effects of finalizing this payroll period: PTO accrued, occurrence PTO applied,
    and delete occurrences created at finalize (variance + tardy).
    """
    # 1. Revert PTO accrued for this period (round so add/subtract cancel exactly)
    for snapshot in period.user_snapshots.select_related("user"):
        u = snapshot.user
        accrued = round(snapshot.pto_accrued_hours, 2)
        u.pto_balance = round(max(0.0, u.pto_balance - accrued), 2)
        u.save()
    period.user_snapshots.all().delete()

    # 2. Refund PTO/personal only for occurrences created at finalize (this period), then delete them
    period_occurrences = Occurrence.objects.filter(
        payroll_period=period,
        pto_applied=True,
    ).exclude(subtype=OccurrenceSubtype.HOLIDAY_PAID).select_related("user")
    # Aggregate refunds by user so we apply correct totals (same user can have variance + tardy)
    refunds = (
        period_occurrences.values("user")
        .annotate(
            pto_refund=Sum("pto_hours_applied"),
            personal_refund=Sum("personal_hours_applied"),
        )
    )
    for r in refunds:
        u = CustomUser.objects.get(pk=r["user"])
        u.pto_balance = round(u.pto_balance + (r["pto_refund"] or 0), 2)
        u.personal_time_balance = round(max(0.0, u.personal_time_balance - (r["personal_refund"] or 0)), 2)
        u.save()
    # Delete occurrences created at finalize so they can be re-created on refinalize
    Occurrence.objects.filter(payroll_period=period).delete()

    # Clear finalized daily summaries tied to this payroll period
    DailyAttendanceSummary.objects.filter(payroll_period=period).update(
        status=DailyAttendanceSummary.Status.OPEN,
        payroll_period=None,
    )


def sync_finalized_daily_summaries(users, week_start: date, week_ending: date, payroll_period):
    """
    Persist interpreted per-day state after payroll logic has created/adjusted occurrences.
    Only writes rows where there is scheduled time or reported worked time for that date.
    """
    for user in users:
        current = week_start
        while current <= week_ending:
            scheduled = effective_work_hours_for_day(user, current)
            entries_day = TimeEntry.objects.filter(user=user, date=current)
            worked = 0.0
            for e in entries_day:
                if e.clock_in and e.clock_out:
                    worked += e.payroll_credited_hours()
            if scheduled <= 0 and worked <= 0:
                current += timedelta(days=1)
                continue
            exchange_occ = (
                Occurrence.objects.filter(
                    user=user,
                    date=current,
                    is_variance_to_schedule=True,
                    subtype=OccurrenceSubtype.EXCHANGE,
                )
                .order_by("id")
                .first()
            )
            exchange_eligible = bool(exchange_occ and exchange_occ.duration_hours > 0)
            DailyAttendanceSummary.objects.update_or_create(
                user=user,
                work_date=current,
                defaults={
                    "scheduled_hours": scheduled,
                    "worked_hours": round(worked, 2),
                    "rounded_hours": round(worked, 2),
                    "lunch_deducted_hours": 0.0,
                    "tardy_minutes": 0,
                    "early_out_minutes": 0,
                    "regular_hours": round(worked, 2),
                    "overtime_hours": 0.0,
                    "exchange_eligible": exchange_eligible,
                    "status": DailyAttendanceSummary.Status.FINALIZED,
                    "payroll_period": payroll_period,
                },
            )
            current += timedelta(days=1)


def finalize_payroll_week(*, period, week_start: date, week_ending: date, finalized_by, users) -> None:
    """
    Core payroll close logic (occurrences, balances, accrual). Caller handles HTTP, overrides, holidays, CSV.
    ``users`` must be the same sorted list used elsewhere for payroll (e.g. exempt-filtered).
    """
    if period.is_finalized:
        return

    # Build total worked (time entries only) and total scheduled per user for the week
    user_total_worked = {}
    user_total_scheduled = {}
    for user in users:
        entries = TimeEntry.objects.filter(user=user, date__range=[week_start, week_ending])
        total_worked_hours = 0
        for e in entries:
            if e.clock_in and e.clock_out:
                total_worked_hours += e.payroll_credited_hours()
        user_total_worked[user.id] = total_worked_hours
        user_total_scheduled[user.id] = effective_scheduled_hours_for_range(user, week_start, week_ending)

    create_tardy_occurrences_for_week(week_start, week_ending, period=period)

    # Variance occurrences: only when user has a schedule and reported total falls short.
    approved_time_off_by_user_date = {}
    for user in users:
        approved_time_off_by_user_date[user.id] = {}
        qs = Occurrence.objects.filter(
            user=user,
            date__range=[week_start, week_ending],
            time_off_request__status=TimeOffRequestStatus.APPROVED,
        )
        for occ in qs.values("date", "duration_hours"):
            d = occ["date"]
            approved_time_off_by_user_date[user.id][d] = (
                approved_time_off_by_user_date[user.id].get(d, 0) + occ["duration_hours"]
            )

    for user in users:
        total_worked_hours = user_total_worked.get(user.id, 0)
        total_scheduled = effective_scheduled_hours_for_range(user, week_start, week_ending)
        if total_scheduled <= 0:
            continue  # No schedule: do not create any variance
        current = week_start
        while current <= week_ending:
            scheduled_day = effective_work_hours_for_day(user, current)
            if scheduled_day <= 0:
                current += timedelta(days=1)
                continue
            entries_day = TimeEntry.objects.filter(user=user, date=current)
            worked_day = 0
            for e in entries_day:
                if e.clock_in and e.clock_out:
                    worked_day += e.payroll_credited_hours()
            approved_day = approved_time_off_by_user_date.get(user.id, {}).get(current, 0)
            tardy_or_variance_hours = sum(
                Occurrence.objects.filter(
                    user=user,
                    date=current,
                )
                .filter(
                    Q(subtype__in=[OccurrenceSubtype.TARDY_IN_GRACE, OccurrenceSubtype.TARDY_OUT_OF_GRACE])
                    | Q(is_variance_to_schedule=True)
                )
                .values_list("duration_hours", flat=True)
            )
            reported_day = worked_day + approved_day + tardy_or_variance_hours
            shortfall_day = max(0, round(scheduled_day - reported_day, 2))
            if shortfall_day > 0:
                has_variance = Occurrence.objects.filter(
                    user=user,
                    date=current,
                    is_variance_to_schedule=True,
                ).exists()
                if not has_variance:
                    subtype = OccurrenceSubtype.EXCHANGE
                    Occurrence.objects.create(
                        user=user,
                        occurrence_type=OccurrenceType.UNPLANNED,
                        subtype=subtype,
                        date=current,
                        duration_hours=round(shortfall_day, 2),
                        pto_applied=False,
                        is_variance_to_schedule=True,
                        payroll_period=period,
                    )
            current += timedelta(days=1)

    # Exchange occurrences should represent only the remaining weekly shortfall
    for user in users:
        expected = user_total_scheduled.get(user.id, 0)
        if expected <= 0:
            continue
        required_week_hours = required_week_hours_for_policy(expected)
        worked = user_total_worked.get(user.id, 0)
        approved_week = sum(approved_time_off_by_user_date.get(user.id, {}).values())
        weekly_shortfall = max(0.0, round(required_week_hours - (worked + approved_week), 2))
        remaining = weekly_shortfall
        exchange_variances = list(
            Occurrence.objects.filter(
                user=user,
                date__range=[week_start, week_ending],
                is_variance_to_schedule=True,
                subtype=OccurrenceSubtype.EXCHANGE,
            ).order_by("date", "id")
        )
        for occ in exchange_variances:
            new_duration = min(occ.duration_hours, remaining)
            if round(occ.duration_hours, 2) != round(new_duration, 2):
                occ.duration_hours = round(new_duration, 2)
                occ.save(update_fields=["duration_hours"])
            remaining = max(0.0, round(remaining - new_duration, 2))

    revert_and_delete_orphan_time_off_for_exchange_week(
        users,
        week_start,
        week_ending,
    )

    # If the user met or exceeded weekly scheduled hours, convert out-of-grace tardies
    # to zero-hour Exchange rows so no PTO/personal is deducted.
    for user in users:
        expected = user_total_scheduled.get(user.id, 0)
        if expected <= 0:
            continue
        required_week_hours = required_week_hours_for_policy(expected)
        worked = user_total_worked.get(user.id, 0)
        approved_week = sum(approved_time_off_by_user_date.get(user.id, {}).values())
        if (worked + approved_week) < required_week_hours:
            continue
        tardy_out_rows = Occurrence.objects.filter(
            user=user,
            date__range=[week_start, week_ending],
            subtype=OccurrenceSubtype.TARDY_OUT_OF_GRACE,
        )
        for occ in tardy_out_rows:
            occ.subtype = OccurrenceSubtype.EXCHANGE
            occ.duration_hours = 0.0
            occ.is_variance_to_schedule = True
            occ.pto_applied = False
            occ.pto_hours_applied = 0.0
            occ.personal_hours_applied = 0.0
            occ.save(
                update_fields=[
                    "subtype",
                    "duration_hours",
                    "is_variance_to_schedule",
                    "pto_applied",
                    "pto_hours_applied",
                    "personal_hours_applied",
                ]
            )

    # Apply PTO before accrual (use current balance, not hours earned this week).
    week_occurrences = list(
        Occurrence.objects.filter(
            date__range=[week_start, week_ending],
            subtype__in=OCCURRENCE_SUBTYPES_USING_PTO_OR_PERSONAL,
            pto_applied=False,
        )
        .exclude(subtype=OccurrenceSubtype.EXCHANGE)
        .select_related("user")
        .order_by("user_id", "date")
    )
    exchange_occurrences = list(
        Occurrence.objects.filter(
            date__range=[week_start, week_ending],
            subtype=OccurrenceSubtype.EXCHANGE,
            pto_applied=False,
        ).select_related("user").order_by("user_id", "date")
    )
    for occ in exchange_occurrences:
        worked = user_total_worked.get(occ.user_id, 0)
        expected = user_total_scheduled.get(occ.user_id)
        if expected is None:
            expected = scheduled_hours_for_range(occ.user, week_start, week_ending)
            user_total_scheduled[occ.user_id] = expected
        required_week_hours = required_week_hours_for_policy(expected)
        approved_week = sum(approved_time_off_by_user_date.get(occ.user_id, {}).values())
        if (worked + approved_week) < required_week_hours and occ.duration_hours > 0:
            week_occurrences.append(occ)
    week_occurrences.sort(key=lambda o: (o.user_id, o.date))

    absence_cap_by_user: dict[int, float] = {}
    for user in users:
        worked = user_total_worked.get(user.id, 0.0)
        expected = user_total_scheduled.get(user.id, 0.0)
        required = required_week_hours_for_policy(expected)
        absence_cap_by_user[user.id] = max(0.0, round(required - worked, 2))

    for occ in week_occurrences:
        uid = occ.user_id
        cap_remaining = absence_cap_by_user.get(uid, 0.0)
        hours_to_charge = min(float(occ.duration_hours or 0.0), cap_remaining)
        if hours_to_charge < 0.001:
            occ.duration_hours = 0.0
            occ.pto_applied = True
            occ.pto_hours_applied = 0.0
            occ.personal_hours_applied = 0.0
            occ.save(
                update_fields=[
                    "duration_hours",
                    "pto_applied",
                    "pto_hours_applied",
                    "personal_hours_applied",
                ]
            )
            continue
        occ.apply_pto(max_occurrence_hours=hours_to_charge)
        charged = float(occ.pto_hours_applied or 0.0) + float(occ.personal_hours_applied or 0.0)
        absence_cap_by_user[uid] = max(0.0, round(cap_remaining - charged, 2))

    sync_finalized_daily_summaries(users, week_start, week_ending, period)

    # Accrue PTO for the week (after applying so balance used is pre-accrual).
    for user in users:
        total_worked_hours = user_total_worked.get(user.id, 0)
        if total_worked_hours and (user.years_of_service() <= 2 or user.is_part_time):
            user.refresh_from_db()
            accrued = user.accrue_pto(total_worked_hours)
            if accrued:
                PayrollPeriodUserSnapshot.objects.update_or_create(
                    period=period,
                    user=user,
                    defaults={"pto_accrued_hours": round(accrued, 2)},
                )

    period.is_finalized = True
    period.finalized_at = django_tz.now()
    period.finalized_by = finalized_by
    period.save()
