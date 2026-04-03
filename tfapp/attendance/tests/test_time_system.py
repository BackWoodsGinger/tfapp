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
