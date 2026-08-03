"""Holiday week plans: admin-configured schedule expectations for 4-day and 5-day employees."""

from __future__ import annotations

import re
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

from django.db import transaction

from attendance.payroll_utils import is_payroll_week_finalized, week_ending_for_date
from attendance.services.time_processing import scheduled_duration_hours_for_day

FOUR_DAY_DEFAULT_WORK_HOURS = {
    0: Decimal("10.00"),
    1: Decimal("10.00"),
    2: Decimal("10.00"),
    3: Decimal("10.00"),
    4: Decimal("0.00"),
    5: Decimal("0.00"),
    6: Decimal("0.00"),
}
FIVE_DAY_DEFAULT_WORK_HOURS = {
    0: Decimal("9.00"),
    1: Decimal("9.00"),
    2: Decimal("9.00"),
    3: Decimal("9.00"),
    4: Decimal("4.00"),
    5: Decimal("0.00"),
    6: Decimal("0.00"),
}


def holiday_key_from_name(name: str) -> str:
    normalized = name.lower().replace("'", "")
    return re.sub(r"[^a-z0-9]+", "_", normalized).strip("_")


def payroll_week_bounds_for_actual_holiday(actual_holiday_date: date) -> tuple[date, date]:
    """Return (week_start Sunday, week_ending Saturday) for the payroll week containing the holiday."""
    week_ending = week_ending_for_date(actual_holiday_date)
    week_start = week_ending - timedelta(days=6)
    return week_start, week_ending


def user_holiday_schedule_template(user) -> str:
    """Friday scheduled hours > 0 => five_day; otherwise four_day."""
    from attendance.models import HolidayWeekPlanTemplate

    ref_friday = date(2020, 1, 10)
    if scheduled_duration_hours_for_day(user, ref_friday) > 0:
        return HolidayWeekPlanTemplate.FIVE_DAY
    return HolidayWeekPlanTemplate.FOUR_DAY


_REFERENCE_WEEK_MONDAY = date(2020, 1, 6)


def prevailing_schedule_shift_hours(user) -> float:
    """Typical paid hours per work day (max scheduled length across the week)."""
    best = 0.0
    for offset in range(7):
        d = _REFERENCE_WEEK_MONDAY + timedelta(days=offset)
        hours = scheduled_duration_hours_for_day(user, d)
        if hours > best:
            best = hours
    return round(best, 2)


def holiday_pay_ineligibility_reasons(user, holiday_date: date) -> list[str]:
    """Short labels explaining why a user cannot receive holiday pay; empty list means eligible."""
    reasons = []
    if not user.is_active:
        reasons.append("Inactive")
    if user.is_exempt:
        reasons.append("Exempt")
    if user.is_part_time:
        reasons.append("Part-time")
    if user.is_date_in_probation_period(holiday_date):
        reasons.append("Under 90 days")
    return reasons


def user_eligible_for_holiday_pay(user, holiday_date: date) -> bool:
    """Full-time, non-exempt, active, and past the 90-day probation window on the holiday date."""
    return not holiday_pay_ineligibility_reasons(user, holiday_date)


def plan_marks_paid_holiday_for_user_on_date(user, the_date: date, *, plan=None) -> bool:
    plan = plan or get_complete_plan_covering_date(the_date)
    if not plan:
        return False
    template = user_holiday_schedule_template(user)
    return plan_holiday_pay_hours(plan, the_date=the_date, template=template) > 0


def list_company_holidays_for_year(year: int) -> list[dict]:
    """Holidays for a calendar year with payroll week bounds (actual calendar date, not observed)."""
    from attendance.models import _actual_company_holidays

    rows = []
    for actual, name in sorted(_actual_company_holidays(year).items()):
        week_start, week_ending = payroll_week_bounds_for_actual_holiday(actual)
        rows.append(
            {
                "key": holiday_key_from_name(name),
                "name": name,
                "actual_date": actual,
                "year": actual.year,
                "week_start": week_start,
                "week_ending": week_ending,
            }
        )
    return rows


def holidays_in_payroll_week(week_start: date, week_ending: date) -> list[dict]:
    """Company holidays whose actual calendar date falls in this payroll week."""
    result = []
    for year in range(week_start.year - 1, week_ending.year + 2):
        for row in list_company_holidays_for_year(year):
            if week_start <= row["actual_date"] <= week_ending:
                result.append(row)
    return result


def get_plan_for_holiday(*, year: int, holiday_key: str):
    from attendance.models import HolidayWeekPlan

    return HolidayWeekPlan.objects.filter(year=year, holiday_key=holiday_key).first()


def get_complete_plan_covering_date(the_date: date):
    from attendance.models import HolidayWeekPlan

    return (
        HolidayWeekPlan.objects.filter(
            is_complete=True,
            week_start__lte=the_date,
            week_ending__gte=the_date,
        )
        .prefetch_related("days")
        .first()
    )


def get_complete_plans_overlapping_range(start_date: date, end_date: date):
    from attendance.models import HolidayWeekPlan

    return list(
        HolidayWeekPlan.objects.filter(
            is_complete=True,
            week_start__lte=end_date,
            week_ending__gte=start_date,
        ).prefetch_related("days")
    )


def _plan_day_row(plan, *, the_date: date, template: str):
    for day in plan.days.all():
        if day.the_date == the_date and day.template == template:
            return day
    return None


def plan_work_hours(plan, *, the_date: date, template: str) -> float | None:
    row = _plan_day_row(plan, the_date=the_date, template=template)
    if not row:
        return None
    return float(row.work_hours)


def plan_holiday_pay_hours(plan, *, the_date: date, template: str) -> float:
    row = _plan_day_row(plan, the_date=the_date, template=template)
    if not row:
        return 0.0
    return float(row.holiday_pay_hours)


def effective_work_hours_for_day(user, the_date: date) -> float:
    normal = scheduled_duration_hours_for_day(user, the_date)
    plan = get_complete_plan_covering_date(the_date)
    if not plan:
        return normal
    template = user_holiday_schedule_template(user)
    plan_hours = plan_work_hours(plan, the_date=the_date, template=template)
    if plan_hours is None:
        return normal
    if plan_hours <= 0 or normal <= 0:
        return 0.0
    return min(plan_hours, normal)


def effective_scheduled_hours_for_range(user, week_start: date, week_ending: date) -> float:
    total = 0.0
    current = week_start
    while current <= week_ending:
        total += effective_work_hours_for_day(user, current)
        current += timedelta(days=1)
    return total


TEMPLATE_LABELS = {"four_day": "4-day", "five_day": "5-day"}


def holiday_pay_review_key(user_id: int, the_date: date) -> str:
    """Form field suffix identifying one holiday pay review row."""
    return f"{user_id}_{the_date.isoformat()}"


def _holiday_pay_suggestion_row(user, the_date: date, plan, *, as_of: date, review_end: date, existing) -> dict:
    from attendance.models import _bookend_day_attendance_status, _scheduled_bookend_days_for_holiday

    reasons = holiday_pay_ineligibility_reasons(user, the_date)
    eligible = not reasons

    leading_date, trailing_date = _scheduled_bookend_days_for_holiday(user, the_date)
    leading_status = _bookend_day_attendance_status(user, leading_date, as_of=as_of) if leading_date else None
    trailing_status = _bookend_day_attendance_status(user, trailing_date, as_of=as_of) if trailing_date else None

    statuses = [s for s in (leading_status, trailing_status) if s]
    if "ineligible" in statuses:
        bookend_status = "ineligible"
    elif "pending" in statuses:
        bookend_status = "pending"
    else:
        bookend_status = "eligible"

    occurrence = existing.get((user.id, the_date))
    suggested_hours = round(float(occurrence.duration_hours), 2) if occurrence else prevailing_schedule_shift_hours(user)
    template = user_holiday_schedule_template(user)

    return {
        "key": holiday_pay_review_key(user.id, the_date),
        "user": user,
        "user_id": user.id,
        "sort_key": (
            (user.payroll_lastname or user.last_name or user.username or "").strip().lower(),
            (user.payroll_firstname or user.first_name or "").strip().lower(),
        ),
        "date": the_date,
        "holiday_name": plan.name,
        "template": template,
        "template_label": TEMPLATE_LABELS.get(template, template),
        "suggested_hours": suggested_hours,
        "eligible": eligible,
        "ineligible_reasons": reasons,
        "leading_date": leading_date,
        "leading_status": leading_status,
        "trailing_date": trailing_date,
        "trailing_status": trailing_status,
        "trailing_outside_week": bool(trailing_date and trailing_date > review_end),
        "bookend_status": bookend_status if eligible else None,
        "bookend_miss": eligible and bookend_status == "ineligible",
        "bookend_pending": eligible and bookend_status == "pending",
        "existing_occurrence": occurrence,
        "default_approve": bool(occurrence) or (eligible and bookend_status == "eligible"),
    }


def build_holiday_pay_suggestions(start_date: date, end_date: date, *, as_of: date | None = None) -> list[dict]:
    """
    Holiday pay rows for admin review at payroll close.

    One row per candidate employee per date that a complete holiday week plan marks as paid for
    that employee's template. Each row carries suggested hours, eligibility, and bookend
    attendance so the admin can approve or deny. Nothing is written to the database here.
    """
    from attendance.models import CustomUser, Occurrence, OccurrenceSubtype

    if start_date > end_date:
        return []

    plans = get_complete_plans_overlapping_range(start_date, end_date)
    if not plans:
        return []

    as_of = as_of or date.today()
    users = list(CustomUser.objects.filter(is_active=True, is_exempt=False))
    existing = {
        (occ.user_id, occ.date): occ
        for occ in Occurrence.objects.filter(
            date__range=[start_date, end_date],
            subtype=OccurrenceSubtype.HOLIDAY_PAID,
        )
    }

    rows = []
    for plan in plans:
        current = max(plan.week_start, start_date)
        plan_end = min(plan.week_ending, end_date)
        while current <= plan_end:
            for user in users:
                if not plan_marks_paid_holiday_for_user_on_date(user, current, plan=plan):
                    continue
                rows.append(
                    _holiday_pay_suggestion_row(
                        user, current, plan, as_of=as_of, review_end=end_date, existing=existing
                    )
                )
            current += timedelta(days=1)

    rows.sort(key=lambda row: (row["date"], row["sort_key"]))
    return rows


@transaction.atomic
def apply_approved_holiday_pay(decisions) -> dict:
    """
    Write the admin's holiday pay choices from payroll close.

    ``decisions`` is an iterable of ``{"user_id", "date", "approve", "hours"}``. Only those exact
    employee/date pairs are touched, so manual corrections elsewhere are never overwritten.
    """
    from attendance.models import Occurrence, OccurrenceSubtype, OccurrenceType

    counts = {"approved": 0, "denied": 0}
    for decision in decisions:
        user_id = decision["user_id"]
        the_date = decision["date"]
        hours = round(float(decision.get("hours") or 0.0), 2)
        existing = Occurrence.objects.filter(
            user_id=user_id,
            date=the_date,
            subtype=OccurrenceSubtype.HOLIDAY_PAID,
        )

        if not decision.get("approve") or hours <= 0:
            if existing.exists():
                existing.delete()
            counts["denied"] += 1
            continue

        occurrence = existing.first()
        if occurrence is None:
            Occurrence.objects.create(
                user_id=user_id,
                date=the_date,
                subtype=OccurrenceSubtype.HOLIDAY_PAID,
                occurrence_type=OccurrenceType.PLANNED,
                duration_hours=hours,
            )
        elif float(occurrence.duration_hours) != hours:
            occurrence.duration_hours = hours
            occurrence.save(update_fields=["duration_hours"])
        counts["approved"] += 1

    return counts


def is_plan_editable(plan) -> bool:
    return not is_payroll_week_finalized(plan.week_ending)


def validate_plan_rows(plan) -> tuple[bool, list[str]]:
    from attendance.models import HolidayWeekPlanTemplate

    errors: list[str] = []
    current = plan.week_start
    while current <= plan.week_ending:
        for template, label in HolidayWeekPlanTemplate.choices:
            row = plan.days.filter(the_date=current, template=template).first()
            if not row:
                errors.append(f"Missing {label} row for {current.strftime('%a %m/%d/%Y')}.")
        current += timedelta(days=1)
    return len(errors) == 0, errors


def refresh_plan_completeness(plan) -> bool:
    complete, _ = validate_plan_rows(plan)
    if plan.is_complete != complete:
        plan.is_complete = complete
        plan.save(update_fields=["is_complete", "updated_at"])
    return complete


@transaction.atomic
def get_or_create_prefilled_plan(*, year: int, holiday_key: str):
    from attendance.models import HolidayWeekPlan, HolidayWeekPlanDay, HolidayWeekPlanTemplate

    existing = get_plan_for_holiday(year=year, holiday_key=holiday_key)
    if existing:
        return existing, False

    holiday_row = None
    for row in list_company_holidays_for_year(year):
        if row["key"] == holiday_key:
            holiday_row = row
            break
    if not holiday_row:
        return None, False

    plan = HolidayWeekPlan.objects.create(
        year=year,
        holiday_key=holiday_key,
        name=holiday_row["name"],
        actual_holiday_date=holiday_row["actual_date"],
        week_start=holiday_row["week_start"],
        week_ending=holiday_row["week_ending"],
        is_complete=False,
    )

    current = plan.week_start
    while current <= plan.week_ending:
        weekday = current.weekday()
        for template, defaults in (
            (HolidayWeekPlanTemplate.FOUR_DAY, FOUR_DAY_DEFAULT_WORK_HOURS),
            (HolidayWeekPlanTemplate.FIVE_DAY, FIVE_DAY_DEFAULT_WORK_HOURS),
        ):
            HolidayWeekPlanDay.objects.create(
                plan=plan,
                the_date=current,
                template=template,
                work_hours=defaults[weekday],
                holiday_pay_hours=Decimal("0.00"),
            )
        current += timedelta(days=1)

    refresh_plan_completeness(plan)
    plan.refresh_from_db()
    return plan, True


def missing_holiday_plans_for_payroll_week(week_start: date, week_ending: date) -> list[dict]:
    """Holidays in this payroll week that lack a complete plan."""
    missing = []
    for holiday in holidays_in_payroll_week(week_start, week_ending):
        plan = get_plan_for_holiday(year=holiday["year"], holiday_key=holiday["key"])
        if not plan or not plan.is_complete:
            missing.append(
                {
                    **holiday,
                    "plan": plan,
                    "status": "missing" if not plan else "incomplete",
                }
            )
    return missing


def parse_plan_hours(value) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value)).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError):
        return None


@transaction.atomic
def save_plan_from_post(plan, *, posted_rows: dict, updated_by) -> tuple[bool, list[str]]:
    """
    ``posted_rows``: {(iso_date, template): {"work": str, "holiday_pay": str}}
  """
    from attendance.models import HolidayWeekPlanDay

    errors: list[str] = []
    for (iso_date, template), values in posted_rows.items():
        try:
            the_date = date.fromisoformat(iso_date)
        except (TypeError, ValueError):
            errors.append(f"Invalid date: {iso_date}")
            continue
        if not (plan.week_start <= the_date <= plan.week_ending):
            errors.append(f"Date {iso_date} is outside the plan week.")
            continue
        work = parse_plan_hours(values.get("work"))
        holiday_pay = parse_plan_hours(values.get("holiday_pay"))
        if work is None or holiday_pay is None:
            errors.append(f"Invalid hours for {iso_date} ({template}).")
            continue
        if work < 0 or holiday_pay < 0:
            errors.append(f"Hours cannot be negative for {iso_date} ({template}).")
            continue
        HolidayWeekPlanDay.objects.update_or_create(
            plan=plan,
            the_date=the_date,
            template=template,
            defaults={
                "work_hours": work,
                "holiday_pay_hours": holiday_pay,
            },
        )

    if errors:
        return False, errors

    plan.updated_by = updated_by
    plan.save(update_fields=["updated_by", "updated_at"])
    refresh_plan_completeness(plan)
    return True, []
