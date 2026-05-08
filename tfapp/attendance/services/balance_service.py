"""
Centralized PTO / personal balance application for occurrences.
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import ROUND_DOWN, Decimal

from django.db import transaction
from django.db.models import Q, Sum


QUARTER_HOUR = Decimal("0.25")


def floor_hours_to_quarter_increment(hours: Decimal) -> Decimal:
    """
    Floor hours to payroll quarter-hour increments (0.25).
    PTO balance may accrue in hundredths (e.g. 1.33 from 40/30 accrual), but only
    whole quarter-hours may be applied toward an absence; the fractional balance remains.
    """
    if hours <= 0:
        return Decimal("0")
    quarters = (hours / QUARTER_HOUR).to_integral_value(rounding=ROUND_DOWN)
    return quarters * QUARTER_HOUR


def probation_grace_hours_used_before(occ, anchor: date, probation_end: date) -> Decimal:
    """Probation grace bank hours already allocated to earlier applied occurrences (same user, same window)."""
    from attendance.models import Occurrence

    total = (
        Occurrence.objects.filter(
            user_id=occ.user_id,
            date__gte=anchor,
            date__lt=probation_end,
            pto_applied=True,
        )
        .filter(Q(date__lt=occ.date) | Q(date=occ.date, pk__lt=occ.pk))
        .aggregate(s=Sum("probation_grace_hours_applied"))
    )["s"]
    return Decimal(str(total or 0)).quantize(Decimal("0.01"))


def apply_occurrence_pto(occ, max_pto_to_apply=None):
    """
    Deduct from PTO (then personal) for this occurrence. Only if occurrence date has passed.
    Same behavior as Occurrence.apply_pto (delegated from model).

    Returns the number of PTO hours deducted.
    """
    from attendance.models import (
        PROBATION_GRACE_ELIGIBLE_SUBTYPES,
        PROBATION_GRACE_HOURS_CAP,
        CustomUser,
        OccurrenceSubtype,
        PTOBalanceHistory,
    )

    # ``Occurrence.save()`` already calls ``apply_pto`` once; callers that also call
    # ``apply_pto`` after ``create``/``get_or_create`` must not deduct twice.
    if occ.pto_applied:
        return float(occ.pto_hours_applied or 0.0)

    if occ.date > date.today():
        return 0.0

    # Subtypes that do NOT affect balances (company-paid or fully unpaid/ excused)
    if occ.subtype in [
        OccurrenceSubtype.LAYOFF,
        OccurrenceSubtype.DISCIPLINE,
        OccurrenceSubtype.WORK_COMP,
        OccurrenceSubtype.DISABILITY,
        OccurrenceSubtype.TARDY_IN_GRACE,
        OccurrenceSubtype.BEREAVEMENT_UNPAID,
        OccurrenceSubtype.JURY_DUTY_UNPAID,
        OccurrenceSubtype.WEATHER_UNPAID,
        OccurrenceSubtype.HOLIDAY_PAID,
    ]:
        return 0.0

    # Subtypes that affect PTO and possibly personal time
    if occ.subtype not in [
        OccurrenceSubtype.TIME_OFF,
        OccurrenceSubtype.TARDY_OUT_OF_GRACE,
        OccurrenceSubtype.EXCHANGE,
        OccurrenceSubtype.FMLA,
        OccurrenceSubtype.LEAVE_OF_ABSENCE,
        OccurrenceSubtype.WEATHER_PAID,
        OccurrenceSubtype.BEREAVEMENT_PAID,
        OccurrenceSubtype.JURY_DUTY_PAID,
        OccurrenceSubtype.GRACE_TIME,
    ]:
        return 0.0

    used = Decimal(str(occ.duration_hours))
    with transaction.atomic():
        u = CustomUser.objects.select_for_update().get(pk=occ.user_id)
        pto_bal = Decimal(str(u.pto_balance)).quantize(Decimal("0.01"))
        personal_bal = Decimal(str(u.personal_time_balance)).quantize(Decimal("0.01"))

        # For FMLA and Leave of Absence: use PTO when available, but do NOT
        # convert any remaining hours into personal/unpaid time. Remaining
        # hours are treated as leave for tracking only.
        if occ.subtype in [OccurrenceSubtype.FMLA, OccurrenceSubtype.LEAVE_OF_ABSENCE]:
            pto_usable = floor_hours_to_quarter_increment(pto_bal)
            pto_deducted = min(used, pto_usable)
            if max_pto_to_apply is not None:
                cap = floor_hours_to_quarter_increment(Decimal(str(max_pto_to_apply)))
                pto_deducted = min(pto_deducted, cap)
            new_pto = max(Decimal("0"), pto_bal - pto_deducted)
            u.pto_balance = float(new_pto.quantize(Decimal("0.01")))
            u.save()
            if pto_deducted > 0:
                PTOBalanceHistory.record(
                    user=u,
                    change=float(-pto_deducted.quantize(Decimal("0.01"))),
                    reason=f"Occurrence apply_pto: {occ.get_subtype_display()} ({occ.date})",
                    balance_after=u.pto_balance,
                )
            occ.pto_hours_applied = float(pto_deducted.quantize(Decimal("0.01")))
            occ.personal_hours_applied = 0.0
            occ.pto_applied = True
            occ.save()
            return float(pto_deducted.quantize(Decimal("0.01")))

        anchor = u.employment_anchor_date()
        probation_end = anchor + timedelta(days=90) if anchor else None
        uses_probation_grace = (
            anchor
            and probation_end
            and u.is_date_in_probation_period(occ.date)
            and (
                occ.subtype in PROBATION_GRACE_ELIGIBLE_SUBTYPES
                or occ.subtype == OccurrenceSubtype.GRACE_TIME
            )
        )
        if uses_probation_grace:
            grace_used_prior = probation_grace_hours_used_before(occ, anchor, probation_end)
            grace_remaining = max(Decimal("0"), PROBATION_GRACE_HOURS_CAP - grace_used_prior)
            grace_portion = min(used, grace_remaining)
            personal_portion = used - grace_portion
            new_personal = personal_bal + personal_portion
            u.personal_time_balance = float(new_personal.quantize(Decimal("0.01")))
            u.save()
            if personal_portion > 0:
                PTOBalanceHistory.record(
                    user=u,
                    change=float(personal_portion.quantize(Decimal("0.01"))),
                    reason=f"Personal time (probation): {occ.get_subtype_display()} ({occ.date})",
                    balance_after=u.personal_time_balance,
                    balance_type=PTOBalanceHistory.BALANCE_TYPE_PERSONAL,
                )
            occ.pto_hours_applied = 0.0
            occ.personal_hours_applied = float(personal_portion.quantize(Decimal("0.01")))
            occ.probation_grace_hours_applied = float(grace_portion.quantize(Decimal("0.01")))
            occ.pto_applied = True
            if (
                grace_portion == used
                and used > 0
                and personal_portion == 0
                and occ.subtype != OccurrenceSubtype.GRACE_TIME
            ):
                occ.subtype = OccurrenceSubtype.GRACE_TIME
            occ.save()
            return 0.0

        # Default behavior: PTO first (quarter-hour increments from balance only), then
        # remaining hours to personal time.
        pto_usable = floor_hours_to_quarter_increment(pto_bal)
        pto_deducted = min(used, pto_usable)
        if max_pto_to_apply is not None:
            cap = floor_hours_to_quarter_increment(Decimal(str(max_pto_to_apply)))
            pto_deducted = min(pto_deducted, cap)
        personal_deducted = used - pto_deducted
        new_pto = max(Decimal("0"), pto_bal - pto_deducted)
        new_personal = personal_bal + personal_deducted
        u.pto_balance = float(new_pto.quantize(Decimal("0.01")))
        u.personal_time_balance = float(new_personal.quantize(Decimal("0.01")))
        u.save()
        PTOBalanceHistory.record(
            user=u,
            change=float(-pto_deducted.quantize(Decimal("0.01"))),
            reason=f"Occurrence apply_pto: {occ.get_subtype_display()} ({occ.date})",
            balance_after=u.pto_balance,
        )
        if personal_deducted > 0:
            PTOBalanceHistory.record(
                user=u,
                change=float(personal_deducted.quantize(Decimal("0.01"))),
                reason=f"Personal time: {occ.get_subtype_display()} ({occ.date})",
                balance_after=u.personal_time_balance,
                balance_type=PTOBalanceHistory.BALANCE_TYPE_PERSONAL,
            )
        occ.pto_hours_applied = float(pto_deducted.quantize(Decimal("0.01")))
        occ.personal_hours_applied = float(personal_deducted.quantize(Decimal("0.01")))
        occ.pto_applied = True
        occ.save()
    return float(pto_deducted.quantize(Decimal("0.01")))
