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
    plan = get_complete_plan_covering_date(the_date)
    if plan:
        template = user_holiday_schedule_template(user)
        hours = plan_work_hours(plan, the_date=the_date, template=template)
        if hours is not None:
            return hours
    return scheduled_duration_hours_for_day(user, the_date)


def holiday_pay_hours_for_user_on_date(user, the_date: date, *, plan=None) -> float:
    plan = plan or get_complete_plan_covering_date(the_date)
    if not plan:
        return 0.0
    template = user_holiday_schedule_template(user)
    return plan_holiday_pay_hours(plan, the_date=the_date, template=template)


def effective_scheduled_hours_for_range(user, week_start: date, week_ending: date) -> float:
    total = 0.0
    current = week_start
    while current <= week_ending:
        total += effective_work_hours_for_day(user, current)
        current += timedelta(days=1)
    return total


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
