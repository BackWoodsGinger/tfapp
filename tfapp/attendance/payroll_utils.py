"""Payroll week boundaries and finalized-week checks (shared across apps)."""

from datetime import date, timedelta

from .models import PayrollPeriod


def week_ending_for_date(d: date) -> date:
    """Saturday of the payroll week containing date ``d``."""
    days_until_saturday = (5 - d.weekday()) % 7
    return d + timedelta(days=days_until_saturday)


def is_payroll_week_finalized(week_ending_date: date) -> bool:
    """True if the payroll period for this Saturday week-ending is finalized."""
    return PayrollPeriod.objects.filter(
        week_ending=week_ending_date,
        is_finalized=True,
    ).exists()


def is_payroll_week_finalized_for_calendar_date(d: date) -> bool:
    """True if the payroll week containing calendar date ``d`` is finalized."""
    return is_payroll_week_finalized(week_ending_for_date(d))
