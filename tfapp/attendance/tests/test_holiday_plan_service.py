"""Tests for holiday week plan service."""
from datetime import date, time

from django.contrib.auth import get_user_model
from django.test import TestCase

from attendance.models import HolidayWeekPlanTemplate, PayrollPeriod, WorkSchedule
from attendance.services.holiday_plan_service import (
    get_or_create_prefilled_plan,
    holidays_in_payroll_week,
    is_plan_editable,
    missing_holiday_plans_for_payroll_week,
    user_holiday_schedule_template,
)


class TestHolidayPlanService(TestCase):
    def test_prefilled_plan_is_complete(self):
        plan, created = get_or_create_prefilled_plan(year=2026, holiday_key="independence_day")
        self.assertTrue(created)
        self.assertTrue(plan.is_complete)
        self.assertEqual(plan.days.count(), 14)

    def test_independence_day_week_bounds(self):
        plan, _ = get_or_create_prefilled_plan(year=2026, holiday_key="independence_day")
        self.assertEqual(plan.week_start, date(2026, 6, 28))
        self.assertEqual(plan.week_ending, date(2026, 7, 4))
        self.assertEqual(plan.actual_holiday_date, date(2026, 7, 4))

    def test_missing_plan_detected_for_payroll_week(self):
        week_start = date(2026, 6, 28)
        week_ending = date(2026, 7, 4)
        missing = missing_holiday_plans_for_payroll_week(week_start, week_ending)
        self.assertEqual(len(missing), 1)
        self.assertEqual(missing[0]["key"], "independence_day")

        get_or_create_prefilled_plan(year=2026, holiday_key="independence_day")
        missing = missing_holiday_plans_for_payroll_week(week_start, week_ending)
        self.assertEqual(len(missing), 0)

    def test_plan_locked_when_payroll_finalized(self):
        plan, _ = get_or_create_prefilled_plan(year=2026, holiday_key="independence_day")
        PayrollPeriod.objects.create(week_ending=plan.week_ending, is_finalized=True)
        self.assertFalse(is_plan_editable(plan))

    def test_user_template_assignment(self):
        User = get_user_model()
        four_day = User.objects.create_user(username="four", password="x")
        five_day = User.objects.create_user(username="five", password="x")
        for weekday in range(4):
            WorkSchedule.objects.create(
                user=four_day,
                day=weekday,
                start_time=time(5, 0),
                lunch_out=time(11, 0),
                lunch_in=time(11, 30),
                end_time=time(15, 30),
            )
        for weekday in range(5):
            WorkSchedule.objects.create(
                user=five_day,
                day=weekday,
                start_time=time(5, 0),
                lunch_out=time(11, 0),
                lunch_in=time(11, 30),
                end_time=time(15, 30),
            )
        self.assertEqual(user_holiday_schedule_template(four_day), HolidayWeekPlanTemplate.FOUR_DAY)
        self.assertEqual(user_holiday_schedule_template(five_day), HolidayWeekPlanTemplate.FIVE_DAY)

    def test_holidays_in_payroll_week(self):
        rows = holidays_in_payroll_week(date(2026, 6, 28), date(2026, 7, 4))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["name"], "Independence Day")
