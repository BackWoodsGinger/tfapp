"""Observed holidays, holiday pay suggestions, and manual approval at payroll close."""
from datetime import date, datetime, time
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from attendance.models import (
    CustomUser,
    HolidayWeekPlan,
    HolidayWeekPlanDay,
    HolidayWeekPlanTemplate,
    Occurrence,
    OccurrenceSubtype,
    OccurrenceType,
    PayrollPeriod,
    WorkSchedule,
    get_company_holidays,
    holiday_attendance_status,
    observed_company_holiday_date,
)
from attendance.services.holiday_plan_service import (
    apply_approved_holiday_pay,
    build_holiday_pay_suggestions,
    effective_work_hours_for_day,
    get_or_create_prefilled_plan,
    prevailing_schedule_shift_hours,
)
from attendance.services.weekly_reconciliation import finalize_payroll_week
from timeclock.models import TimeEntry

HOLIDAY = date(2026, 7, 2)
OBSERVED_FRIDAY = date(2026, 7, 3)
LEADING = date(2026, 7, 1)
TRAILING = date(2026, 7, 6)
WEEK_START = date(2026, 6, 28)
WEEK_END = date(2026, 7, 4)
AS_OF = date(2026, 7, 7)


def _four_day_schedule(user):
    """Mon-Thu 5:00–15:30 with a half-hour lunch: 10 paid hours per day, no Friday."""
    for weekday in range(4):
        WorkSchedule.objects.create(
            user=user,
            day=weekday,
            start_time=time(5, 0),
            lunch_out=time(11, 0),
            lunch_in=time(11, 30),
            end_time=time(15, 30),
        )


def _five_day_schedule(user):
    """Mon-Thu 9 paid hours plus a short 4-hour Friday."""
    for weekday in range(4):
        WorkSchedule.objects.create(
            user=user,
            day=weekday,
            start_time=time(5, 0),
            lunch_out=time(11, 0),
            lunch_in=time(11, 30),
            end_time=time(14, 30),
        )
    WorkSchedule.objects.create(user=user, day=4, start_time=time(5, 0), end_time=time(9, 0))


def _entry(user, d: date, start: time, end: time, *, lunch=None):
    tz = timezone.get_current_timezone()

    def aware(t):
        return timezone.make_aware(datetime.combine(d, t), tz)

    return TimeEntry.objects.create(
        user=user,
        date=d,
        clock_in=aware(start),
        clock_out=aware(end),
        lunch_out=aware(lunch[0]) if lunch else None,
        lunch_in=aware(lunch[1]) if lunch else None,
    )


def _full_shift_entry(user, d: date):
    _entry(user, d, time(5, 0), time(15, 30))


def _complete_independence_day_2026_plan():
    """
    Independence Day 2026 falls on Saturday. 4-day employees take Thursday off; 5-day employees
    work a short Thursday and take Friday off.
    """
    plan, _ = get_or_create_prefilled_plan(year=2026, holiday_key="independence_day")
    updates = {
        (HOLIDAY, HolidayWeekPlanTemplate.FOUR_DAY): (Decimal("0.00"), Decimal("10.00")),
        (HOLIDAY, HolidayWeekPlanTemplate.FIVE_DAY): (Decimal("4.00"), Decimal("0.00")),
        (OBSERVED_FRIDAY, HolidayWeekPlanTemplate.FIVE_DAY): (Decimal("0.00"), Decimal("9.00")),
    }
    for (the_date, template), (work_hours, holiday_pay_hours) in updates.items():
        HolidayWeekPlanDay.objects.filter(plan=plan, the_date=the_date, template=template).update(
            work_hours=work_hours,
            holiday_pay_hours=holiday_pay_hours,
        )
    plan.is_complete = True
    plan.save(update_fields=["is_complete"])
    plan.refresh_from_db()
    return plan


def _row_for(rows, user, the_date=HOLIDAY):
    for row in rows:
        if row["user_id"] == user.id and row["date"] == the_date:
            return row
    return None


class TestObservedHolidays(TestCase):
    def test_saturday_observed_on_thursday(self):
        self.assertEqual(observed_company_holiday_date(date(2026, 7, 4)), HOLIDAY)

    def test_sunday_observed_on_monday(self):
        self.assertEqual(observed_company_holiday_date(date(2027, 7, 4)), date(2027, 7, 5))

    def test_weekday_unchanged(self):
        self.assertEqual(observed_company_holiday_date(date(2025, 7, 4)), date(2025, 7, 4))

    def test_independence_day_2026_observed_july_2(self):
        holidays = get_company_holidays(2026)
        self.assertIn(HOLIDAY, holidays)
        self.assertEqual(holidays[HOLIDAY], "Independence Day")
        self.assertNotIn(date(2026, 7, 4), holidays)


class TestHolidayPaySuggestions(TestCase):
    def setUp(self):
        _complete_independence_day_2026_plan()

        self.ft_user = CustomUser.objects.create_user(
            username="ft_ok",
            password="test",
            hire_date=date(2020, 1, 1),
            payroll_lastname="OK",
            payroll_firstname="Full",
        )
        _four_day_schedule(self.ft_user)

        self.pt_user = CustomUser.objects.create_user(
            username="pt_user",
            password="test",
            hire_date=date(2020, 1, 1),
            is_part_time=True,
            payroll_lastname="Part",
            payroll_firstname="Time",
        )
        _four_day_schedule(self.pt_user)

    def _seed_perfect_bookends(self, user):
        _full_shift_entry(user, LEADING)
        _full_shift_entry(user, TRAILING)

    def _suggestions(self, as_of=AS_OF):
        return build_holiday_pay_suggestions(WEEK_START, WEEK_END, as_of=as_of)

    def test_no_plan_means_no_suggestions(self):
        HolidayWeekPlan.objects.all().delete()
        self._seed_perfect_bookends(self.ft_user)
        self.assertEqual(self._suggestions(), [])

    def test_building_suggestions_creates_no_occurrences(self):
        self._seed_perfect_bookends(self.ft_user)
        self._suggestions()
        self.assertFalse(Occurrence.objects.filter(subtype=OccurrenceSubtype.HOLIDAY_PAID).exists())

    def test_eligible_full_time_defaults_to_approve(self):
        self._seed_perfect_bookends(self.ft_user)
        row = _row_for(self._suggestions(), self.ft_user)
        self.assertIsNotNone(row)
        self.assertTrue(row["eligible"])
        self.assertTrue(row["default_approve"])
        self.assertEqual(row["bookend_status"], "eligible")
        self.assertAlmostEqual(row["suggested_hours"], 10.0, places=2)

    def test_suggested_hours_follow_prevailing_schedule(self):
        short_user = CustomUser.objects.create_user(
            username="short_shift",
            password="test",
            hire_date=date(2020, 1, 1),
            payroll_lastname="Short",
            payroll_firstname="Shift",
        )
        for weekday in range(4):
            WorkSchedule.objects.create(
                user=short_user,
                day=weekday,
                start_time=time(7, 15),
                lunch_out=time(11, 0),
                lunch_in=time(11, 30),
                end_time=time(15, 30),
            )
        for bookend in (LEADING, TRAILING):
            _entry(short_user, bookend, time(7, 15), time(15, 30), lunch=(time(11, 0), time(11, 30)))

        row = _row_for(self._suggestions(), short_user)
        self.assertTrue(row["default_approve"])
        self.assertAlmostEqual(row["suggested_hours"], 7.75, places=2)

    def test_probation_employee_defaults_to_deny(self):
        self.ft_user.hire_date = date(2026, 6, 1)
        self.ft_user.save(update_fields=["hire_date"])
        self._seed_perfect_bookends(self.ft_user)

        row = _row_for(self._suggestions(), self.ft_user)
        self.assertFalse(row["eligible"])
        self.assertIn("Under 90 days", row["ineligible_reasons"])
        self.assertFalse(row["default_approve"])
        self.assertEqual(holiday_attendance_status(self.ft_user, HOLIDAY, as_of=AS_OF), "ineligible")

    def test_part_time_defaults_to_deny(self):
        self._seed_perfect_bookends(self.pt_user)
        row = _row_for(self._suggestions(), self.pt_user)
        self.assertFalse(row["eligible"])
        self.assertIn("Part-time", row["ineligible_reasons"])
        self.assertFalse(row["default_approve"])

    def test_unplanned_leading_miss_defaults_to_deny(self):
        _full_shift_entry(self.ft_user, TRAILING)
        row = _row_for(self._suggestions(), self.ft_user)
        self.assertTrue(row["bookend_miss"])
        self.assertFalse(row["default_approve"])
        self.assertEqual(row["leading_status"], "ineligible")

    def test_unplanned_occurrence_on_bookend_defaults_to_deny(self):
        self._seed_perfect_bookends(self.ft_user)
        Occurrence.objects.create(
            user=self.ft_user,
            date=LEADING,
            occurrence_type=OccurrenceType.UNPLANNED,
            subtype=OccurrenceSubtype.EXCHANGE,
            duration_hours=2.0,
        )
        row = _row_for(self._suggestions(), self.ft_user)
        self.assertTrue(row["bookend_miss"])
        self.assertFalse(row["default_approve"])

    def test_planned_pto_on_leading_still_defaults_to_approve(self):
        _full_shift_entry(self.ft_user, TRAILING)
        Occurrence.objects.create(
            user=self.ft_user,
            date=LEADING,
            occurrence_type=OccurrenceType.PLANNED,
            subtype=OccurrenceSubtype.TIME_OFF,
            duration_hours=10.0,
        )
        row = _row_for(self._suggestions(), self.ft_user)
        self.assertTrue(row["default_approve"])

    def test_pending_trailing_bookend_defaults_to_deny(self):
        _full_shift_entry(self.ft_user, LEADING)
        row = _row_for(self._suggestions(as_of=date(2026, 7, 3)), self.ft_user)
        self.assertTrue(row["bookend_pending"])
        self.assertFalse(row["default_approve"])

    def test_trailing_bookend_in_next_payroll_week_is_flagged(self):
        self._seed_perfect_bookends(self.ft_user)
        row = _row_for(self._suggestions(), self.ft_user)
        self.assertEqual(row["trailing_date"], TRAILING)
        self.assertTrue(row["trailing_outside_week"])

    def test_partial_shift_shortfall_denies_holiday(self):
        _entry(self.ft_user, LEADING, time(5, 0), time(12, 0))
        _full_shift_entry(self.ft_user, TRAILING)
        self.assertEqual(holiday_attendance_status(self.ft_user, HOLIDAY, as_of=AS_OF), "ineligible")

    def test_existing_holiday_pay_defaults_to_approve_at_its_own_hours(self):
        Occurrence.objects.create(
            user=self.pt_user,
            date=HOLIDAY,
            occurrence_type=OccurrenceType.PLANNED,
            subtype=OccurrenceSubtype.HOLIDAY_PAID,
            duration_hours=8.0,
        )
        row = _row_for(self._suggestions(), self.pt_user)
        self.assertFalse(row["eligible"])
        self.assertTrue(row["default_approve"])
        self.assertAlmostEqual(row["suggested_hours"], 8.0, places=2)

    def test_five_day_employee_is_suggested_on_friday(self):
        five_day = CustomUser.objects.create_user(
            username="five_day",
            password="test",
            hire_date=date(2020, 1, 1),
            payroll_lastname="Five",
            payroll_firstname="Day",
        )
        _five_day_schedule(five_day)
        rows = self._suggestions()
        self.assertIsNone(_row_for(rows, five_day, HOLIDAY))
        self.assertIsNotNone(_row_for(rows, five_day, OBSERVED_FRIDAY))


class TestApplyApprovedHolidayPay(TestCase):
    def setUp(self):
        _complete_independence_day_2026_plan()
        self.user = CustomUser.objects.create_user(
            username="approver_target",
            password="test",
            hire_date=date(2020, 1, 1),
        )
        _four_day_schedule(self.user)

    def _holiday_occurrences(self):
        return Occurrence.objects.filter(user=self.user, subtype=OccurrenceSubtype.HOLIDAY_PAID)

    def test_approval_creates_occurrence(self):
        apply_approved_holiday_pay(
            [{"user_id": self.user.id, "date": HOLIDAY, "approve": True, "hours": 10.0}]
        )
        occ = self._holiday_occurrences().get()
        self.assertEqual(occ.date, HOLIDAY)
        self.assertEqual(occ.occurrence_type, OccurrenceType.PLANNED)
        self.assertAlmostEqual(occ.duration_hours, 10.0, places=2)

    def test_denial_removes_existing_occurrence(self):
        Occurrence.objects.create(
            user=self.user,
            date=HOLIDAY,
            occurrence_type=OccurrenceType.PLANNED,
            subtype=OccurrenceSubtype.HOLIDAY_PAID,
            duration_hours=10.0,
        )
        apply_approved_holiday_pay(
            [{"user_id": self.user.id, "date": HOLIDAY, "approve": False, "hours": 10.0}]
        )
        self.assertFalse(self._holiday_occurrences().exists())

    def test_edited_hours_replace_existing_amount(self):
        apply_approved_holiday_pay(
            [{"user_id": self.user.id, "date": HOLIDAY, "approve": True, "hours": 10.0}]
        )
        apply_approved_holiday_pay(
            [{"user_id": self.user.id, "date": HOLIDAY, "approve": True, "hours": 6.5}]
        )
        occ = self._holiday_occurrences().get()
        self.assertAlmostEqual(occ.duration_hours, 6.5, places=2)

    def test_approval_of_zero_hours_is_treated_as_denial(self):
        apply_approved_holiday_pay(
            [{"user_id": self.user.id, "date": HOLIDAY, "approve": True, "hours": 0}]
        )
        self.assertFalse(self._holiday_occurrences().exists())

    def test_days_not_reviewed_are_left_alone(self):
        other_date = date(2026, 5, 25)
        Occurrence.objects.create(
            user=self.user,
            date=other_date,
            occurrence_type=OccurrenceType.PLANNED,
            subtype=OccurrenceSubtype.HOLIDAY_PAID,
            duration_hours=10.0,
        )
        apply_approved_holiday_pay(
            [{"user_id": self.user.id, "date": HOLIDAY, "approve": False, "hours": 10.0}]
        )
        self.assertTrue(self._holiday_occurrences().filter(date=other_date).exists())


class TestHolidayWeekPlanScheduleExpectations(TestCase):
    """The plan's work hours, not holiday pay, are what attendance is measured against."""

    def setUp(self):
        _complete_independence_day_2026_plan()
        self.five_day = CustomUser.objects.create_user(
            username="five_day_thu",
            password="test",
            hire_date=date(2020, 1, 1),
        )
        _five_day_schedule(self.five_day)

    def _work_short_holiday_week(self):
        for workday in (date(2026, 6, 29), date(2026, 6, 30), date(2026, 7, 1)):
            _entry(self.five_day, workday, time(5, 0), time(14, 30), lunch=(time(11, 0), time(11, 30)))
        _entry(self.five_day, HOLIDAY, time(5, 0), time(9, 0))

    def _finalize_holiday_week(self):
        period, _ = PayrollPeriod.objects.get_or_create(
            week_ending=WEEK_END, defaults={"is_finalized": False}
        )
        finalize_payroll_week(
            period=period,
            week_start=WEEK_START,
            week_ending=WEEK_END,
            finalized_by=self.five_day,
            users=[self.five_day],
        )

    def test_plan_shortens_thursday_expectation(self):
        self.assertAlmostEqual(effective_work_hours_for_day(self.five_day, HOLIDAY), 4.0, places=2)
        self.assertAlmostEqual(effective_work_hours_for_day(self.five_day, OBSERVED_FRIDAY), 0.0, places=2)
        self.assertAlmostEqual(effective_work_hours_for_day(self.five_day, LEADING), 9.0, places=2)

    def test_short_thursday_creates_no_variance(self):
        self._work_short_holiday_week()
        self._finalize_holiday_week()
        self.assertFalse(
            Occurrence.objects.filter(
                user=self.five_day,
                date__range=[WEEK_START, WEEK_END],
                subtype=OccurrenceSubtype.EXCHANGE,
            ).exists()
        )

    def test_short_thursday_without_plan_is_penalized(self):
        HolidayWeekPlan.objects.all().delete()
        self._work_short_holiday_week()
        self._finalize_holiday_week()
        variance = Occurrence.objects.filter(
            user=self.five_day,
            date=HOLIDAY,
            subtype=OccurrenceSubtype.EXCHANGE,
        ).first()
        self.assertIsNotNone(variance)
        self.assertAlmostEqual(variance.duration_hours, 5.0, places=2)

    def test_three_day_employee_gets_no_thursday_expectation(self):
        user = CustomUser.objects.create_user(
            username="mon_wed",
            password="test",
            hire_date=date(2020, 1, 1),
        )
        for weekday in (0, 1, 2):
            WorkSchedule.objects.create(
                user=user,
                day=weekday,
                start_time=time(5, 0),
                lunch_out=time(11, 0),
                lunch_in=time(11, 30),
                end_time=time(15, 30),
            )
        self.assertAlmostEqual(effective_work_hours_for_day(user, HOLIDAY), 0.0, places=2)
        self.assertAlmostEqual(prevailing_schedule_shift_hours(user), 10.0, places=2)
