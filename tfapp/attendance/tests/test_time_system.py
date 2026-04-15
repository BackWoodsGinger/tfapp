"""
Automated tests for the time/clock system: TimeEntry model, punch flow, guards, and rounding.
"""
from datetime import date, datetime, time, timedelta, timezone as dt_timezone

from django.db import IntegrityError
from django.test import TestCase, Client
from django.urls import reverse
from django.utils import timezone

from attendance.models import (
    CustomUser,
    Occurrence,
    OccurrenceSubtype,
    PayrollPeriod,
    PayrollPeriodUserSnapshot,
    RoleChoices,
    TimeOffRequestStatus,
    WorkSchedule,
    WorkThroughLunchRequest,
)
from timeclock.models import TimeEntry


class TestTimeEntryModel(TestCase):
    """TimeEntry model: unique constraint, is_incomplete, total_worked_time, missing_punch flag."""

    def setUp(self):
        self.user = CustomUser.objects.create_user(
            username="clockuser",
            password="testpass",
            timeclock_login="1000",
            timeclock_pin="1234",
        )

    def test_one_entry_per_user_per_day(self):
        """Unique constraint enforces at most one TimeEntry per (user, date)."""
        today = timezone.now().date()
        TimeEntry.objects.create(user=self.user, date=today)
        with self.assertRaises(IntegrityError):
            TimeEntry.objects.create(user=self.user, date=today)

    def test_is_incomplete_empty(self):
        """Entry with no punches is not incomplete (nothing recorded)."""
        entry = TimeEntry(user=self.user, date=timezone.now().date())
        entry.save()
        self.assertFalse(entry.is_incomplete())

    def test_is_incomplete_partial(self):
        """Entry with only some punches is incomplete."""
        now = timezone.now()
        entry = TimeEntry.objects.create(user=self.user, date=now.date(), clock_in=now)
        self.assertTrue(entry.is_incomplete())
        entry.clock_out = now + timedelta(hours=8)
        entry.save()
        self.assertTrue(entry.is_incomplete())
        entry.lunch_out = now + timedelta(hours=4)
        entry.lunch_in = entry.lunch_out + timedelta(minutes=30)
        entry.save()
        self.assertFalse(entry.is_incomplete())

    def test_total_worked_time_no_clock_out(self):
        """total_worked_time returns 0 when clock_out is missing."""
        now = timezone.now()
        entry = TimeEntry.objects.create(
            user=self.user, date=now.date(), clock_in=now
        )
        self.assertEqual(entry.total_worked_time(), 0.0)

    def test_total_worked_time_full_day_with_lunch(self):
        """total_worked_time subtracts 30 min lunch and returns decimal-rounded hours."""
        tz = timezone.get_current_timezone()
        base = timezone.make_aware(
            timezone.datetime(2025, 3, 5, 8, 0, 0), tz
        )
        entry = TimeEntry.objects.create(
            user=self.user,
            date=base.date(),
            clock_in=base,
            lunch_out=base + timedelta(hours=4),
            lunch_in=base + timedelta(hours=4, minutes=30),
            clock_out=base + timedelta(hours=9),
        )
        # 9h - 0.5h lunch = 8.5h
        self.assertEqual(entry.total_worked_time(), 8.5)

    def test_total_worked_time_uses_decimal_rounding(self):
        """total_worked_time is rounded to 2 decimals to avoid float drift."""
        tz = timezone.get_current_timezone()
        base = timezone.make_aware(
            timezone.datetime(2025, 3, 5, 8, 0, 0), tz
        )
        # Slightly odd duration so float would give 7.0000000001 etc.
        entry = TimeEntry.objects.create(
            user=self.user,
            date=base.date(),
            clock_in=base,
            lunch_out=base + timedelta(hours=3, minutes=15),
            lunch_in=base + timedelta(hours=3, minutes=45),
            clock_out=base + timedelta(hours=7, minutes=7, seconds=12),
        )
        result = entry.total_worked_time()
        self.assertIsInstance(result, float)
        self.assertEqual(round(result, 2), result)
        # 7h7m12s - 30m lunch = 6h37m12s = 6.62h
        self.assertAlmostEqual(result, 6.62, places=2)

    def test_reported_worked_hours_floors_to_quarter_hour(self):
        """reported_worked_hours floors actual hours to prior 0.25 increment."""
        tz = timezone.get_current_timezone()
        base = timezone.make_aware(
            timezone.datetime(2025, 3, 5, 5, 23, 0), tz
        )
        # 5:23 -> 15:32 with 11:30-12:00 lunch => 9h39m actual = 9.65
        entry = TimeEntry.objects.create(
            user=self.user,
            date=base.date(),
            clock_in=base,
            lunch_out=timezone.make_aware(timezone.datetime(2025, 3, 5, 11, 30, 0), tz),
            lunch_in=timezone.make_aware(timezone.datetime(2025, 3, 5, 12, 0, 0), tz),
            clock_out=timezone.make_aware(timezone.datetime(2025, 3, 5, 15, 32, 0), tz),
        )
        self.assertAlmostEqual(entry.actual_worked_hours(), 9.65, places=2)
        self.assertAlmostEqual(entry.reported_worked_hours(), 9.5, places=2)

    def test_save_clears_missing_punch_flagged_when_completed(self):
        """Completing an entry clears missing_punch_flagged."""
        now = timezone.now()
        entry = TimeEntry.objects.create(
            user=self.user,
            date=now.date(),
            clock_in=now,
            missing_punch_flagged=True,
            missing_punch_flagged_at=now,
        )
        entry.clock_out = now + timedelta(hours=8)
        entry.lunch_out = now + timedelta(hours=4)
        entry.lunch_in = now + timedelta(hours=4, minutes=30)
        entry.save()
        entry.refresh_from_db()
        self.assertFalse(entry.missing_punch_flagged)
        self.assertIsNone(entry.missing_punch_flagged_at)


class TestTimeClockPunchView(TestCase):
    """Timeclock punch view: login, punch flow, duplicate guards."""

    def setUp(self):
        self.client = Client()
        self.user = CustomUser.objects.create_user(
            username="punchuser",
            password="testpass",
            timeclock_login="2000",
            timeclock_pin="5678",
        )
        self.punch_url = reverse("timeclock:timeclock_home")

    def test_get_shows_form(self):
        """GET timeclock home returns 200."""
        response = self.client.get(self.punch_url)
        self.assertEqual(response.status_code, 200)

    def test_invalid_login_rejected(self):
        """Invalid timeclock login/pin shows error and does not create entry."""
        response = self.client.post(
            self.punch_url,
            {"login": "9999", "pin": "0000", "action": "clock_in"},
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(TimeEntry.objects.filter(user=self.user).count(), 0)
        messages = list(response.context["messages"])
        self.assertTrue(any("Invalid" in str(m) for m in messages))

    def test_clock_in_creates_entry_and_succeeds(self):
        """Valid clock_in creates TimeEntry and shows success."""
        response = self.client.post(
            self.punch_url,
            {
                "login": self.user.timeclock_login,
                "pin": self.user.timeclock_pin,
                "action": "clock_in",
            },
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        entry = TimeEntry.objects.filter(user=self.user).first()
        self.assertIsNotNone(entry)
        self.assertIsNotNone(entry.clock_in)
        self.assertIsNone(entry.clock_out)
        messages = list(response.context["messages"])
        self.assertTrue(any("recorded" in str(m).lower() for m in messages))

    def test_duplicate_clock_in_guarded(self):
        """Second clock_in for same day is rejected with warning."""
        self.client.post(
            self.punch_url,
            {
                "login": self.user.timeclock_login,
                "pin": self.user.timeclock_pin,
                "action": "clock_in",
            },
            follow=True,
        )
        response = self.client.post(
            self.punch_url,
            {
                "login": self.user.timeclock_login,
                "pin": self.user.timeclock_pin,
                "action": "clock_in",
            },
            follow=True,
        )
        messages = list(response.context["messages"])
        self.assertTrue(
            any("already clocked in" in str(m).lower() for m in messages)
        )
        # Still only one entry with one clock_in
        entry = TimeEntry.objects.get(user=self.user, date=timezone.now().date())
        self.assertIsNotNone(entry.clock_in)

    def test_full_punch_sequence(self):
        """Clock in -> lunch out -> lunch in -> clock out all succeed."""
        for action in ["clock_in", "lunch_out", "lunch_in", "clock_out"]:
            response = self.client.post(
                self.punch_url,
                {
                    "login": self.user.timeclock_login,
                    "pin": self.user.timeclock_pin,
                    "action": action,
                },
                follow=True,
            )
            self.assertEqual(response.status_code, 200, msg=action)
        entry = TimeEntry.objects.get(user=self.user, date=timezone.now().date())
        self.assertIsNotNone(entry.clock_in)
        self.assertIsNotNone(entry.lunch_out)
        self.assertIsNotNone(entry.lunch_in)
        self.assertIsNotNone(entry.clock_out)
        self.assertFalse(entry.is_incomplete())


class TestTimeEntryRounding(TestCase):
    """round_to_quarter and total_worked_time rounding behavior."""

    def setUp(self):
        self.user = CustomUser.objects.create_user(
            username="rounduser", password="testpass"
        )

    def test_round_to_quarter_ceiling(self):
        """Round up to next 15-minute boundary."""
        entry = TimeEntry(user=self.user, date=date(2025, 3, 5))
        tz = timezone.get_current_timezone()
        # 8:07 -> 8:15
        dt = timezone.make_aware(
            timezone.datetime(2025, 3, 5, 8, 7, 0), tz
        )
        rounded = entry.round_to_quarter(dt)
        self.assertEqual(rounded.hour, 8)
        self.assertEqual(rounded.minute, 15)
        # 8:15 -> 8:15
        dt2 = timezone.make_aware(
            timezone.datetime(2025, 3, 5, 8, 15, 0), tz
        )
        self.assertEqual(entry.round_to_quarter(dt2).minute, 15)


class TestTardyOccurrence(TestCase):
    """Tardy rules: in-grace vs out-of-grace occurrence creation."""

    def setUp(self):
        self.user = CustomUser.objects.create_user(
            username="tardyuser", password="testpass"
        )
        # Monday 8:00–17:00, lunch 12:00–12:30
        WorkSchedule.objects.create(
            user=self.user,
            day=0,  # Monday
            start_time=time(8, 0),
            lunch_out=time(12, 0),
            lunch_in=time(12, 30),
            end_time=time(17, 0),
        )

    def test_tardy_in_grace_creates_occurrence(self):
        """Clock in 2 min late creates Tardy In Grace with 0 hours."""
        tz = timezone.get_current_timezone()
        # Monday 2025-03-03, clock in at 8:02
        clock_in = timezone.make_aware(
            timezone.datetime(2025, 3, 3, 8, 2, 0), tz
        )
        entry = TimeEntry.objects.create(
            user=self.user, date=clock_in.date(), clock_in=clock_in
        )
        entry.check_tardy()
        occ = Occurrence.objects.filter(
            user=self.user, date=clock_in.date(), subtype=OccurrenceSubtype.TARDY_IN_GRACE
        ).first()
        self.assertIsNotNone(occ)
        self.assertEqual(occ.duration_hours, 0.0)

    def test_early_clock_in_does_not_create_tardy_occurrence(self):
        """Clock in before schedule start should not create tardy occurrence."""
        tz = timezone.get_current_timezone()
        clock_in = timezone.make_aware(
            timezone.datetime(2025, 3, 3, 7, 53, 0), tz
        )
        entry = TimeEntry.objects.create(
            user=self.user, date=clock_in.date(), clock_in=clock_in
        )
        entry.check_tardy()
        self.assertFalse(
            Occurrence.objects.filter(
                user=self.user,
                date=clock_in.date(),
                subtype__in=[
                    OccurrenceSubtype.TARDY_IN_GRACE,
                    OccurrenceSubtype.TARDY_OUT_OF_GRACE,
                ],
            ).exists()
        )

    def test_out_of_grace_rounds_from_scheduled_start(self):
        """20 minutes late rounds to 30 minutes (0.5h) out-of-grace."""
        tz = timezone.get_current_timezone()
        clock_in = timezone.make_aware(
            timezone.datetime(2025, 3, 3, 8, 20, 0), tz
        )
        entry = TimeEntry.objects.create(
            user=self.user, date=clock_in.date(), clock_in=clock_in
        )
        entry.check_tardy()
        occ = Occurrence.objects.filter(
            user=self.user,
            date=clock_in.date(),
            subtype=OccurrenceSubtype.TARDY_OUT_OF_GRACE,
        ).first()
        self.assertIsNotNone(occ)
        self.assertAlmostEqual(occ.duration_hours, 0.5, places=2)

    def test_utc_stored_clock_in_in_grace_not_misread_as_hours_late(self):
        """UTC-stored 8:01 local punch remains in-grace (no out-of-grace hours)."""
        local_tz = timezone.get_current_timezone()
        local_clock_in = timezone.make_aware(
            timezone.datetime(2025, 3, 3, 8, 1, 0), local_tz
        )
        utc_clock_in = local_clock_in.astimezone(dt_timezone.utc)
        entry = TimeEntry.objects.create(
            user=self.user, date=local_clock_in.date(), clock_in=utc_clock_in
        )
        entry.check_tardy()
        self.assertFalse(
            Occurrence.objects.filter(
                user=self.user,
                date=local_clock_in.date(),
                subtype=OccurrenceSubtype.TARDY_OUT_OF_GRACE,
            ).exists()
        )
        in_grace = Occurrence.objects.filter(
            user=self.user,
            date=local_clock_in.date(),
            subtype=OccurrenceSubtype.TARDY_IN_GRACE,
        ).first()
        self.assertIsNotNone(in_grace)
        self.assertEqual(in_grace.duration_hours, 0.0)

    def test_reported_hours_allow_late_stay_to_recover_tardy(self):
        """8:08 in (tardy) and 17:47 out reports as 9.00 with quarter-hour rules."""
        tz = timezone.get_current_timezone()
        entry = TimeEntry.objects.create(
            user=self.user,
            date=date(2025, 3, 3),
            clock_in=timezone.make_aware(timezone.datetime(2025, 3, 3, 8, 8, 0), tz),
            lunch_out=timezone.make_aware(timezone.datetime(2025, 3, 3, 12, 0, 0), tz),
            lunch_in=timezone.make_aware(timezone.datetime(2025, 3, 3, 12, 30, 0), tz),
            clock_out=timezone.make_aware(timezone.datetime(2025, 3, 3, 17, 47, 0), tz),
        )
        self.assertAlmostEqual(entry.actual_worked_hours(), 9.15, places=2)
        self.assertAlmostEqual(entry.reported_worked_hours(), 9.0, places=2)


class TestScheduledLunchAutoFill(TestCase):
    """When lunch punches are missing, save() applies scheduled lunch_in/out within the shift."""

    def setUp(self):
        self.user = CustomUser.objects.create_user(
            username="lunchfill",
            password="testpass",
        )
        WorkSchedule.objects.create(
            user=self.user,
            day=0,
            start_time=time(5, 0),
            lunch_out=time(11, 0),
            lunch_in=time(11, 30),
            end_time=time(15, 30),
        )

    def test_save_applies_scheduled_lunch_when_both_punches_missing(self):
        tz = timezone.get_current_timezone()
        d = date(2025, 3, 3)
        ci = timezone.make_aware(datetime(2025, 3, 3, 5, 0, 0), tz)
        co = timezone.make_aware(datetime(2025, 3, 3, 15, 30, 0), tz)
        entry = TimeEntry.objects.create(
            user=self.user,
            date=d,
            clock_in=ci,
            clock_out=co,
        )
        entry.refresh_from_db()
        self.assertIsNotNone(entry.lunch_out)
        self.assertIsNotNone(entry.lunch_in)
        lo = timezone.localtime(entry.lunch_out)
        li = timezone.localtime(entry.lunch_in)
        self.assertEqual(lo.hour, 11)
        self.assertEqual(lo.minute, 0)
        self.assertEqual(li.hour, 11)
        self.assertEqual(li.minute, 30)
        self.assertAlmostEqual(entry.actual_worked_hours(), 10.0, places=2)

    def test_work_through_lunch_approved_no_deduction_and_no_auto_fill(self):
        tz = timezone.get_current_timezone()
        d = date(2025, 3, 3)
        WorkThroughLunchRequest.objects.create(
            user=self.user,
            work_date=d,
            status=TimeOffRequestStatus.APPROVED,
        )
        ci = timezone.make_aware(datetime(2025, 3, 3, 5, 0, 0), tz)
        co = timezone.make_aware(datetime(2025, 3, 3, 15, 30, 0), tz)
        entry = TimeEntry.objects.create(
            user=self.user,
            date=d,
            clock_in=ci,
            clock_out=co,
        )
        entry.refresh_from_db()
        self.assertIsNone(entry.lunch_out)
        self.assertIsNone(entry.lunch_in)
        self.assertAlmostEqual(entry.actual_worked_hours(), 10.5, places=2)
        self.assertFalse(entry.is_incomplete())


class TestScheduleHalfDay(TestCase):
    """Half-day and no-lunch schedules (omit lunch_out/lunch_in in JSON or leave blank on WorkSchedule)."""

    def test_weekly_json_friday_no_lunch_hours(self):
        from attendance.schedule_utils import scheduled_duration_hours_for_day

        user = CustomUser.objects.create_user(username="halfuser_json", password="x")
        user.weekly_schedule = {
            "monday": {
                "start": "06:30",
                "end": "16:00",
                "lunch_out": "12:00",
                "lunch_in": "12:30",
            },
            "friday": {"start": "06:30", "end": "11:00"},
        }
        user.save()
        fri = date(2025, 3, 7)
        self.assertAlmostEqual(scheduled_duration_hours_for_day(user, fri), 4.5, places=2)

    def test_workschedule_null_lunch_hours(self):
        from attendance.schedule_utils import scheduled_duration_hours_for_day

        user = CustomUser.objects.create_user(username="halfuser_ws", password="x")
        WorkSchedule.objects.create(
            user=user,
            day=4,
            start_time=time(6, 30),
            lunch_out=None,
            lunch_in=None,
            end_time=time(11, 0),
        )
        fri = date(2025, 3, 7)
        self.assertAlmostEqual(scheduled_duration_hours_for_day(user, fri), 4.5, places=2)

    def test_clock_in_out_only_complete_when_no_scheduled_lunch(self):
        tz = timezone.get_current_timezone()
        user = CustomUser.objects.create_user(username="halfuser_ci", password="x")
        user.weekly_schedule = {"friday": {"start": "06:30", "end": "11:00"}}
        user.save()
        fri = date(2025, 3, 7)
        ci = timezone.make_aware(datetime(2025, 3, 7, 6, 30, 0), tz)
        co = timezone.make_aware(datetime(2025, 3, 7, 11, 0, 0), tz)
        entry = TimeEntry.objects.create(user=user, date=fri, clock_in=ci, clock_out=co)
        self.assertFalse(entry.is_incomplete())


class TestReportsIncompleteAlerts(TestCase):
    """Reports should only show missing-punch alerts after day completion."""

    def setUp(self):
        self.client = Client()
        self.executive = CustomUser.objects.create_user(
            username="exec1",
            password="testpass",
            role=RoleChoices.EXECUTIVE,
            department="Ops",
        )
        self.employee = CustomUser.objects.create_user(
            username="employee1",
            password="testpass",
            department="Ops",
        )

    def test_reports_excludes_today_incomplete_entry_from_alerts(self):
        """An active same-day shift should not appear in reports alerts."""
        now = timezone.now()
        TimeEntry.objects.create(
            user=self.employee,
            date=now.date(),
            clock_in=now,
        )
        self.client.force_login(self.executive)
        response = self.client.get(reverse("attendance:payroll"))
        self.assertEqual(response.status_code, 200)
        alerts = response.context.get("alerts", [])
        self.assertEqual(len(alerts), 0)


class TestPayrollCloseLunchValidation(TestCase):
    """Payroll close should honor schedule-aware and approved lunch rules."""

    def setUp(self):
        self.client = Client()
        self.admin = CustomUser.objects.create_user(
            username="payrolladmin",
            password="testpass",
            is_staff=True,
            role=RoleChoices.EXECUTIVE,
        )
        self.client.force_login(self.admin)
        self.close_url = reverse("attendance:close_payroll")

    def test_close_payroll_allows_approved_work_through_lunch_without_lunch_punches(self):
        tz = timezone.get_current_timezone()
        user = CustomUser.objects.create_user(username="wtlclose", password="x")
        WorkSchedule.objects.create(
            user=user,
            day=0,  # Monday
            start_time=time(5, 0),
            lunch_out=time(11, 0),
            lunch_in=time(11, 30),
            end_time=time(15, 30),
        )
        d = date(2025, 3, 3)  # Monday
        WorkThroughLunchRequest.objects.create(
            user=user,
            work_date=d,
            status=TimeOffRequestStatus.APPROVED,
        )
        TimeEntry.objects.create(
            user=user,
            date=d,
            clock_in=timezone.make_aware(datetime(2025, 3, 3, 5, 0, 0), tz),
            clock_out=timezone.make_aware(datetime(2025, 3, 3, 15, 30, 0), tz),
        )

        response = self.client.post(self.close_url, {"week_ending": "2025-03-08"})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(PayrollPeriod.objects.get(week_ending=date(2025, 3, 8)).is_finalized)

    def test_close_payroll_allows_shift_ending_before_scheduled_lunch(self):
        tz = timezone.get_current_timezone()
        user = CustomUser.objects.create_user(username="leftbeforelunch", password="x")
        WorkSchedule.objects.create(
            user=user,
            day=0,  # Monday
            start_time=time(5, 0),
            lunch_out=time(11, 0),
            lunch_in=time(11, 30),
            end_time=time(15, 30),
        )
        d = date(2025, 3, 3)  # Monday
        entry = TimeEntry.objects.create(
            user=user,
            date=d,
            clock_in=timezone.make_aware(datetime(2025, 3, 3, 5, 0, 0), tz),
            clock_out=timezone.make_aware(datetime(2025, 3, 3, 9, 0, 0), tz),
        )

        self.assertFalse(entry.is_incomplete())
        self.assertAlmostEqual(entry.actual_worked_hours(), 4.0, places=2)
        response = self.client.post(self.close_url, {"week_ending": "2025-03-08"})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(PayrollPeriod.objects.get(week_ending=date(2025, 3, 8)).is_finalized)

    def test_close_payroll_allows_unscheduled_overtime_day_without_lunch_punches(self):
        tz = timezone.get_current_timezone()
        user = CustomUser.objects.create_user(username="unscheduledot", password="x")
        d = date(2025, 3, 7)  # Friday, no schedule configured
        entry = TimeEntry.objects.create(
            user=user,
            date=d,
            clock_in=timezone.make_aware(datetime(2025, 3, 7, 7, 0, 0), tz),
            clock_out=timezone.make_aware(datetime(2025, 3, 7, 12, 0, 0), tz),
        )

        self.assertFalse(entry.is_incomplete())
        response = self.client.post(self.close_url, {"week_ending": "2025-03-08"})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(PayrollPeriod.objects.get(week_ending=date(2025, 3, 8)).is_finalized)


class TestPayrollCloseAccrualAndExchange(TestCase):
    """Payroll close should accrue on overtime and preserve exchange subtype behavior."""

    def setUp(self):
        self.client = Client()
        self.admin = CustomUser.objects.create_user(
            username="payrolladmin2",
            password="testpass",
            is_staff=True,
            role=RoleChoices.EXECUTIVE,
        )
        self.client.force_login(self.admin)
        self.close_url = reverse("attendance:close_payroll")
        self.tz = timezone.get_current_timezone()

    def _entry(self, user, d, in_h, in_m, out_h, out_m):
        return TimeEntry.objects.create(
            user=user,
            date=d,
            clock_in=timezone.make_aware(datetime(d.year, d.month, d.day, in_h, in_m, 0), self.tz),
            clock_out=timezone.make_aware(datetime(d.year, d.month, d.day, out_h, out_m, 0), self.tz),
        )

    def test_accrual_includes_overtime_hours(self):
        user = CustomUser.objects.create_user(
            username="accrualot",
            password="x",
            service_date=date.today() - timedelta(days=365),  # in 0-2yr bucket
            pto_balance=0.0,
        )
        # Scheduled Mon-Thu 10h paid (5:00-15:30 with 30m lunch)
        for day in [0, 1, 2, 3]:
            WorkSchedule.objects.create(
                user=user,
                day=day,
                start_time=time(5, 0),
                lunch_out=time(11, 0),
                lunch_in=time(11, 30),
                end_time=time(15, 30),
            )
        # Friday overtime day (unscheduled): 6h
        week_dates = [
            date(2025, 3, 3),
            date(2025, 3, 4),
            date(2025, 3, 5),
            date(2025, 3, 6),
            date(2025, 3, 7),
        ]
        for d in week_dates[:4]:
            self._entry(user, d, 5, 0, 15, 30)  # 10h reported each day
        self._entry(user, week_dates[4], 7, 0, 13, 0)  # 6h overtime day

        response = self.client.post(self.close_url, {"week_ending": "2025-03-08"})
        self.assertEqual(response.status_code, 200)
        user.refresh_from_db()
        self.assertAlmostEqual(user.pto_balance, 1.53, places=2)  # 46 / 30 = 1.53
        period = PayrollPeriod.objects.get(week_ending=date(2025, 3, 8))
        snapshot = PayrollPeriodUserSnapshot.objects.get(period=period, user=user)
        self.assertAlmostEqual(snapshot.pto_accrued_hours, 1.53, places=2)

    def test_exchange_without_pto_when_weekly_hours_met(self):
        user = CustomUser.objects.create_user(
            username="exchangefull",
            password="x",
            pto_balance=5.0,
        )
        # Mon-Wed 10h schedule
        for day in [0, 1, 2]:
            WorkSchedule.objects.create(
                user=user,
                day=day,
                start_time=time(5, 0),
                lunch_out=time(11, 0),
                lunch_in=time(11, 30),
                end_time=time(15, 30),
            )
        # Work Monday/Wednesday and make up missed Tuesday on Thursday.
        self._entry(user, date(2025, 3, 3), 5, 0, 15, 30)  # Mon
        self._entry(user, date(2025, 3, 5), 5, 0, 15, 30)  # Wed
        self._entry(user, date(2025, 3, 6), 5, 0, 15, 30)  # Thu make-up

        response = self.client.post(self.close_url, {"week_ending": "2025-03-08"})
        self.assertEqual(response.status_code, 200)
        occ = Occurrence.objects.get(
            user=user,
            date=date(2025, 3, 4),  # missed scheduled Tuesday
            is_variance_to_schedule=True,
        )
        self.assertEqual(occ.subtype, OccurrenceSubtype.EXCHANGE)
        self.assertFalse(occ.pto_applied)
        self.assertEqual(occ.pto_hours_applied, 0.0)
        self.assertEqual(occ.personal_hours_applied, 0.0)

    def test_exchange_applies_pto_when_weekly_hours_short(self):
        user = CustomUser.objects.create_user(
            username="exchangepartial",
            password="x",
            pto_balance=4.0,
            personal_time_balance=0.0,
        )
        # Mon-Wed 10h schedule
        for day in [0, 1, 2]:
            WorkSchedule.objects.create(
                user=user,
                day=day,
                start_time=time(5, 0),
                lunch_out=time(11, 0),
                lunch_in=time(11, 30),
                end_time=time(15, 30),
            )
        # Miss Tuesday; only make up 6h Thursday => weekly shortfall remains 4h
        self._entry(user, date(2025, 3, 3), 5, 0, 15, 30)  # Mon 10h
        self._entry(user, date(2025, 3, 5), 5, 0, 15, 30)  # Wed 10h
        self._entry(user, date(2025, 3, 6), 7, 0, 13, 0)   # Thu 6h make-up

        response = self.client.post(self.close_url, {"week_ending": "2025-03-08"})
        self.assertEqual(response.status_code, 200)
        occ = Occurrence.objects.get(
            user=user,
            date=date(2025, 3, 4),  # missed scheduled Tuesday
            is_variance_to_schedule=True,
        )
        self.assertEqual(occ.subtype, OccurrenceSubtype.EXCHANGE)
        self.assertTrue(occ.pto_applied)
        self.assertAlmostEqual(occ.pto_hours_applied, 4.0, places=2)
        self.assertAlmostEqual(occ.personal_hours_applied, 0.0, places=2)

    def test_tardy_out_of_grace_converts_to_zero_exchange_when_weekly_hours_met(self):
        user = CustomUser.objects.create_user(
            username="tardyexchange",
            password="x",
            pto_balance=5.0,
        )
        # Mon-Thu scheduled 10h paid shifts
        for day in [0, 1, 2, 3]:
            WorkSchedule.objects.create(
                user=user,
                day=day,
                start_time=time(5, 0),
                lunch_out=time(11, 0),
                lunch_in=time(11, 30),
                end_time=time(15, 30),
            )
        # Monday one hour late (9h worked), Tue/Wed/Thu normal (10h each), plus Friday 10h unscheduled make-up.
        self._entry(user, date(2025, 3, 3), 6, 0, 15, 30)  # Mon => tardy out of grace expected
        self._entry(user, date(2025, 3, 4), 5, 0, 15, 30)  # Tue
        self._entry(user, date(2025, 3, 5), 5, 0, 15, 30)  # Wed
        self._entry(user, date(2025, 3, 6), 5, 0, 15, 30)  # Thu
        self._entry(user, date(2025, 3, 7), 5, 0, 15, 30)  # Fri unscheduled make-up

        response = self.client.post(self.close_url, {"week_ending": "2025-03-08"})
        self.assertEqual(response.status_code, 200)
        occ = Occurrence.objects.get(user=user, date=date(2025, 3, 3), is_variance_to_schedule=True)
        self.assertEqual(occ.subtype, OccurrenceSubtype.EXCHANGE)
        self.assertAlmostEqual(occ.duration_hours, 0.0, places=2)
        self.assertFalse(occ.pto_applied)
        self.assertAlmostEqual(occ.pto_hours_applied, 0.0, places=2)


class TestReportedHoursOverridesAndLunchRounding(TestCase):
    """Reported-hours policy for early starts, overrides, and long lunches."""

    def setUp(self):
        self.tz = timezone.get_current_timezone()
        self.user = CustomUser.objects.create_user(username="reportedhours", password="x")
        for day in [0, 1, 2, 3]:
            WorkSchedule.objects.create(
                user=self.user,
                day=day,
                start_time=time(5, 0),
                lunch_out=time(11, 0),
                lunch_in=time(11, 30),
                end_time=time(15, 30),
            )
        self.approver = CustomUser.objects.create_user(username="manager1", password="x")

    def _dt(self, y, m, d, hh, mm):
        return timezone.make_aware(datetime(y, m, d, hh, mm, 0), self.tz)

    def test_scheduled_early_clock_in_not_credited_without_override(self):
        entry = TimeEntry.objects.create(
            user=self.user,
            date=date(2025, 3, 3),  # Monday
            clock_in=self._dt(2025, 3, 3, 4, 45),
            lunch_out=self._dt(2025, 3, 3, 11, 0),
            lunch_in=self._dt(2025, 3, 3, 11, 30),
            clock_out=self._dt(2025, 3, 3, 15, 30),
        )
        self.assertAlmostEqual(entry.reported_worked_hours(), 10.0, places=2)

    def test_scheduled_early_clock_in_credited_with_override(self):
        entry = TimeEntry.objects.create(
            user=self.user,
            date=date(2025, 3, 3),  # Monday
            clock_in=self._dt(2025, 3, 3, 4, 45),
            lunch_out=self._dt(2025, 3, 3, 11, 0),
            lunch_in=self._dt(2025, 3, 3, 11, 30),
            clock_out=self._dt(2025, 3, 3, 15, 30),
            clock_in_authorized_by=self.approver,
        )
        self.assertAlmostEqual(entry.reported_worked_hours(), 10.25, places=2)

    def test_unscheduled_early_clock_in_not_credited_without_override(self):
        # Friday is unscheduled; fallback schedule start from nearby days is 5:00.
        entry = TimeEntry.objects.create(
            user=self.user,
            date=date(2025, 3, 7),  # Friday
            clock_in=self._dt(2025, 3, 7, 4, 45),
            lunch_out=self._dt(2025, 3, 7, 11, 0),
            lunch_in=self._dt(2025, 3, 7, 11, 30),
            clock_out=self._dt(2025, 3, 7, 15, 30),
        )
        self.assertAlmostEqual(entry.reported_worked_hours(), 10.0, places=2)

    def test_long_lunch_result_is_floored_to_quarter_hour(self):
        entry = TimeEntry.objects.create(
            user=self.user,
            date=date(2025, 3, 3),  # Monday
            clock_in=self._dt(2025, 3, 3, 4, 45),
            lunch_out=self._dt(2025, 3, 3, 9, 25),
            lunch_in=self._dt(2025, 3, 3, 12, 26),
            clock_out=self._dt(2025, 3, 3, 15, 30),
        )
        # Actual keeps true punches: (10h45m span - 3h1m lunch) = 7h44m => 7.73
        self.assertAlmostEqual(entry.actual_worked_hours(), 7.73, places=2)
        # Reported uses schedule start (no early override), floors out, and rounds lunch edges:
        # lunch_out 9:25 -> 9:15, lunch_in 12:26 -> 12:30 => 3h15m deduction.
        self.assertAlmostEqual(entry.reported_worked_hours(), 7.25, places=2)
