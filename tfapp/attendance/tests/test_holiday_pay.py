"""Tests for observed holidays and holiday pay bookend attendance rules."""
from datetime import date, datetime, time
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from attendance.models import (
    CustomUser,
    HolidayWeekPlanDay,
    HolidayWeekPlanTemplate,
    Occurrence,
    OccurrenceSubtype,
    OccurrenceType,
    WorkSchedule,
    ensure_holiday_occurrences_for_range,
    get_company_holidays,
    holiday_attendance_status,
    observed_company_holiday_date,
)
from attendance.services.holiday_plan_service import (
    effective_work_hours_for_day,
    get_or_create_prefilled_plan,
    prevailing_schedule_shift_hours,
)
from timeclock.models import TimeEntry


def _mon_thu_schedule(user, hours_per_day: float = 9.0):
    """Mon-Thu 5:00–15:30-style schedule (9 credited hours with lunch)."""
    for weekday in range(4):
        WorkSchedule.objects.create(
            user=user,
            day=weekday,
            start_time=time(5, 0),
            lunch_out=time(11, 0),
            lunch_in=time(11, 30),
            end_time=time(15, 30),
        )


def _full_shift_entry(user, d: date):
    tz = timezone.get_current_timezone()
    clock_in = timezone.make_aware(datetime.combine(d, time(5, 0)), tz)
    clock_out = timezone.make_aware(datetime.combine(d, time(15, 30)), tz)
    TimeEntry.objects.create(user=user, date=d, clock_in=clock_in, clock_out=clock_out)


def _complete_independence_day_2026_plan(*, four_day_holiday_pay=Decimal("9.00")):
    plan, _ = get_or_create_prefilled_plan(year=2026, holiday_key="independence_day")
    HolidayWeekPlanDay.objects.filter(
        plan=plan,
        the_date=date(2026, 7, 2),
        template=HolidayWeekPlanTemplate.FOUR_DAY,
    ).update(
        work_hours=Decimal("0.00"),
        holiday_pay_hours=four_day_holiday_pay,
    )
    plan.is_complete = True
    plan.save(update_fields=["is_complete"])
    plan.refresh_from_db()
    return plan


class TestObservedHolidays(TestCase):
    def test_saturday_observed_on_thursday(self):
        self.assertEqual(
            observed_company_holiday_date(date(2026, 7, 4)),
            date(2026, 7, 2),
        )

    def test_sunday_observed_on_monday(self):
        self.assertEqual(
            observed_company_holiday_date(date(2027, 7, 4)),
            date(2027, 7, 5),
        )

    def test_weekday_unchanged(self):
        self.assertEqual(
            observed_company_holiday_date(date(2025, 7, 4)),
            date(2025, 7, 4),
        )

    def test_independence_day_2026_observed_july_2(self):
        holidays = get_company_holidays(2026)
        self.assertIn(date(2026, 7, 2), holidays)
        self.assertEqual(holidays[date(2026, 7, 2)], "Independence Day")
        self.assertNotIn(date(2026, 7, 4), holidays)


class TestHolidayPayEligibility(TestCase):
    HOLIDAY = date(2026, 7, 2)
    LEADING = date(2026, 7, 1)
    TRAILING = date(2026, 7, 6)
    WEEK_START = date(2026, 6, 28)
    WEEK_END = date(2026, 7, 4)
    AS_OF = date(2026, 7, 7)

    def setUp(self):
        _complete_independence_day_2026_plan()

        self.ft_user = CustomUser.objects.create_user(
            username="ft_ok",
            password="test",
            payroll_lastname="OK",
            payroll_firstname="Full",
        )
        _mon_thu_schedule(self.ft_user)

        self.pt_user = CustomUser.objects.create_user(
            username="pt_user",
            password="test",
            is_part_time=True,
            payroll_lastname="Part",
            payroll_firstname="Time",
        )
        _mon_thu_schedule(self.pt_user)

    def _seed_perfect_bookends(self, user):
        _full_shift_entry(user, self.LEADING)
        _full_shift_entry(user, self.TRAILING)

    def test_no_plan_means_no_holiday_pay(self):
        from attendance.models import HolidayWeekPlan

        HolidayWeekPlan.objects.all().delete()
        self._seed_perfect_bookends(self.ft_user)
        ensure_holiday_occurrences_for_range(
            self.WEEK_START, self.WEEK_END, as_of=self.AS_OF
        )
        self.assertFalse(
            Occurrence.objects.filter(
                user=self.ft_user,
                subtype=OccurrenceSubtype.HOLIDAY_PAID,
            ).exists()
        )

    def test_eligible_full_time_receives_holiday_pay(self):
        self._seed_perfect_bookends(self.ft_user)
        ensure_holiday_occurrences_for_range(
            self.WEEK_START, self.WEEK_END, as_of=self.AS_OF
        )
        occ = Occurrence.objects.filter(
            user=self.ft_user,
            date=self.HOLIDAY,
            subtype=OccurrenceSubtype.HOLIDAY_PAID,
        ).first()
        self.assertIsNotNone(occ)
        self.assertAlmostEqual(occ.duration_hours, 9.0, places=2)

    def test_probation_employee_denied_holiday_pay(self):
        self.ft_user.hire_date = date(2026, 6, 1)
        self.ft_user.save(update_fields=["hire_date"])
        self._seed_perfect_bookends(self.ft_user)
        ensure_holiday_occurrences_for_range(
            self.WEEK_START, self.WEEK_END, as_of=self.AS_OF
        )
        self.assertFalse(
            Occurrence.objects.filter(
                user=self.ft_user,
                subtype=OccurrenceSubtype.HOLIDAY_PAID,
            ).exists()
        )
        self.assertEqual(
            holiday_attendance_status(self.ft_user, self.HOLIDAY, as_of=self.AS_OF),
            "ineligible",
        )

    def test_holiday_pay_uses_prevailing_schedule_hours(self):
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
        tz = timezone.get_current_timezone()
        for bookend in (self.LEADING, self.TRAILING):
            clock_in = timezone.make_aware(datetime.combine(bookend, time(7, 15)), tz)
            clock_out = timezone.make_aware(datetime.combine(bookend, time(15, 30)), tz)
            TimeEntry.objects.create(
                user=short_user,
                date=bookend,
                clock_in=clock_in,
                clock_out=clock_out,
                lunch_out=timezone.make_aware(datetime.combine(bookend, time(11, 0)), tz),
                lunch_in=timezone.make_aware(datetime.combine(bookend, time(11, 30)), tz),
            )
        ensure_holiday_occurrences_for_range(
            self.WEEK_START, self.WEEK_END, as_of=self.AS_OF
        )
        occ = Occurrence.objects.filter(
            user=short_user,
            date=self.HOLIDAY,
            subtype=OccurrenceSubtype.HOLIDAY_PAID,
        ).first()
        self.assertIsNotNone(occ)
        self.assertAlmostEqual(occ.duration_hours, 7.75, places=2)

    def test_part_time_never_receives_holiday_pay(self):
        self._seed_perfect_bookends(self.pt_user)
        ensure_holiday_occurrences_for_range(
            self.WEEK_START, self.WEEK_END, as_of=self.AS_OF
        )
        self.assertFalse(
            Occurrence.objects.filter(
                user=self.pt_user,
                subtype=OccurrenceSubtype.HOLIDAY_PAID,
            ).exists()
        )

    def test_part_time_existing_holiday_removed(self):
        Occurrence.objects.create(
            user=self.pt_user,
            date=self.HOLIDAY,
            occurrence_type=OccurrenceType.PLANNED,
            subtype=OccurrenceSubtype.HOLIDAY_PAID,
            duration_hours=9.0,
        )
        ensure_holiday_occurrences_for_range(
            self.WEEK_START, self.WEEK_END, as_of=self.AS_OF
        )
        self.assertFalse(
            Occurrence.objects.filter(
                user=self.pt_user,
                subtype=OccurrenceSubtype.HOLIDAY_PAID,
            ).exists()
        )

    def test_unplanned_leading_miss_denies_holiday(self):
        _full_shift_entry(self.ft_user, self.TRAILING)
        ensure_holiday_occurrences_for_range(
            self.WEEK_START, self.WEEK_END, as_of=self.AS_OF
        )
        self.assertFalse(
            Occurrence.objects.filter(
                user=self.ft_user,
                date=self.HOLIDAY,
                subtype=OccurrenceSubtype.HOLIDAY_PAID,
            ).exists()
        )

    def test_unplanned_occurrence_on_bookend_denies_holiday(self):
        _full_shift_entry(self.ft_user, self.LEADING)
        _full_shift_entry(self.ft_user, self.TRAILING)
        Occurrence.objects.create(
            user=self.ft_user,
            date=self.LEADING,
            occurrence_type=OccurrenceType.UNPLANNED,
            subtype=OccurrenceSubtype.EXCHANGE,
            duration_hours=2.0,
        )
        ensure_holiday_occurrences_for_range(
            self.WEEK_START, self.WEEK_END, as_of=self.AS_OF
        )
        self.assertFalse(
            Occurrence.objects.filter(
                user=self.ft_user,
                date=self.HOLIDAY,
                subtype=OccurrenceSubtype.HOLIDAY_PAID,
            ).exists()
        )

    def test_planned_pto_on_leading_still_eligible(self):
        _full_shift_entry(self.ft_user, self.TRAILING)
        Occurrence.objects.create(
            user=self.ft_user,
            date=self.LEADING,
            occurrence_type=OccurrenceType.PLANNED,
            subtype=OccurrenceSubtype.TIME_OFF,
            duration_hours=9.0,
        )
        self.assertEqual(
            holiday_attendance_status(self.ft_user, self.HOLIDAY, as_of=self.AS_OF),
            "eligible",
        )
        ensure_holiday_occurrences_for_range(
            self.WEEK_START, self.WEEK_END, as_of=self.AS_OF
        )
        self.assertTrue(
            Occurrence.objects.filter(
                user=self.ft_user,
                date=self.HOLIDAY,
                subtype=OccurrenceSubtype.HOLIDAY_PAID,
            ).exists()
        )

    def test_pending_until_trailing_bookend_passes(self):
        _full_shift_entry(self.ft_user, self.LEADING)
        self.assertEqual(
            holiday_attendance_status(self.ft_user, self.HOLIDAY, as_of=date(2026, 7, 3)),
            "pending",
        )
        ensure_holiday_occurrences_for_range(
            self.WEEK_START, self.WEEK_END, as_of=date(2026, 7, 3)
        )
        self.assertFalse(
            Occurrence.objects.filter(
                user=self.ft_user,
                subtype=OccurrenceSubtype.HOLIDAY_PAID,
            ).exists()
        )

    def test_partial_shift_shortfall_denies_holiday(self):
        tz = timezone.get_current_timezone()
        clock_in = timezone.make_aware(datetime.combine(self.LEADING, time(5, 0)), tz)
        clock_out = timezone.make_aware(datetime.combine(self.LEADING, time(12, 0)), tz)
        TimeEntry.objects.create(
            user=self.ft_user,
            date=self.LEADING,
            clock_in=clock_in,
            clock_out=clock_out,
        )
        _full_shift_entry(self.ft_user, self.TRAILING)
        self.assertEqual(
            holiday_attendance_status(self.ft_user, self.HOLIDAY, as_of=self.AS_OF),
            "ineligible",
        )


class TestHolidayPlanScheduleIntersection(TestCase):
    def setUp(self):
        get_or_create_prefilled_plan(year=2026, holiday_key="independence_day")

    def test_three_day_mon_wed_no_thursday_expectation(self):
        user = CustomUser.objects.create_user(username="mw", password="test")
        for weekday in (0, 1, 2):
            WorkSchedule.objects.create(
                user=user,
                day=weekday,
                start_time=time(5, 0),
                lunch_out=time(11, 0),
                lunch_in=time(11, 30),
                end_time=time(15, 30),
            )
        thursday = date(2026, 7, 2)
        self.assertAlmostEqual(
            effective_work_hours_for_day(user, thursday),
            0.0,
            places=2,
        )
        self.assertAlmostEqual(prevailing_schedule_shift_hours(user), 9.0, places=2)


class TestHolidayPlanScheduleIntersection(TestCase):
    def setUp(self):
        get_or_create_prefilled_plan(year=2026, holiday_key="independence_day")

    def test_three_day_mon_wed_no_thursday_expectation(self):
        user = CustomUser.objects.create_user(username="mw", password="test")
        for weekday in (0, 1, 2):
            WorkSchedule.objects.create(
                user=user,
                day=weekday,
                start_time=time(5, 0),
                lunch_out=time(11, 0),
                lunch_in=time(11, 30),
                end_time=time(15, 30),
            )
        thursday = date(2026, 7, 2)
        self.assertAlmostEqual(
            effective_work_hours_for_day(user, thursday),
            0.0,
            places=2,
        )
        self.assertAlmostEqual(prevailing_schedule_shift_hours(user), 9.0, places=2)
