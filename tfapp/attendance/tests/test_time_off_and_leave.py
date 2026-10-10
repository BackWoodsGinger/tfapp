"""Time off request shapes (partial / full / multiple days) and admin-managed ongoing leave."""
from datetime import date, time, timedelta

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse

from attendance.forms import TimeOffRequestForm
from attendance.models import (
    CustomUser,
    LeaveOfAbsence,
    Occurrence,
    OccurrenceSubtype,
    OccurrenceType,
    PayrollPeriod,
    TimeOffRequest,
    TimeOffRequestKind,
    TimeOffRequestStatus,
)
from attendance.schedule_utils import scheduled_hours_between
from attendance.services.leave_service import delete_leave_with_absences, end_leave, sync_leave
from attendance.services.weekly_reconciliation import finalize_payroll_week

DAY_SHIFT = {"start": "07:00", "end": "15:30", "lunch_out": "11:00", "lunch_in": "11:30"}
WEEKDAY_SCHEDULE = {
    "monday": DAY_SHIFT,
    "tuesday": DAY_SHIFT,
    "wednesday": DAY_SHIFT,
    "thursday": DAY_SHIFT,
    "friday": {"start": "06:30", "end": "11:00"},
}
NIGHT_SCHEDULE = {
    "monday": {"start": "15:30", "end": "02:00", "lunch_out": "19:00", "lunch_in": "19:30"},
}

# A future Monday, so approvals never draw PTO and tests do not depend on today's date.
MONDAY = date(2030, 6, 3)


def _make_user(username="emp", schedule=None, **extra):
    return CustomUser.objects.create_user(
        username=username,
        password="x",
        weekly_schedule=WEEKDAY_SCHEDULE if schedule is None else schedule,
        **extra,
    )


def _form(user, **data):
    data.setdefault("subtype", OccurrenceSubtype.TIME_OFF)
    return TimeOffRequestForm(data, request_user=user)


def _submit(form, user):
    assert form.is_valid(), form.errors
    tor = form.save(commit=False)
    tor.user = user
    tor.save()
    form.save_days(tor)
    return tor


class ScheduledHoursBetweenTests(TestCase):
    def test_day_shift_excludes_lunch_overlap(self):
        user = _make_user()
        self.assertEqual(MONDAY.weekday(), 0)
        self.assertEqual(scheduled_hours_between(user, MONDAY, time(7, 0), time(11, 0)), 4.0)
        self.assertEqual(scheduled_hours_between(user, MONDAY, time(10, 0), time(13, 0)), 2.5)
        self.assertEqual(scheduled_hours_between(user, MONDAY, time(7, 0), time(15, 30)), 8.0)
        self.assertEqual(scheduled_hours_between(user, MONDAY, time(13, 15), time(15, 30)), 2.25)

    def test_unscheduled_day_is_zero(self):
        user = _make_user()
        saturday = MONDAY + timedelta(days=5)
        self.assertEqual(scheduled_hours_between(user, saturday, time(7, 0), time(11, 0)), 0.0)

    def test_overnight_shift_rolls_past_midnight(self):
        user = _make_user(schedule=NIGHT_SCHEDULE)
        self.assertEqual(scheduled_hours_between(user, MONDAY, time(15, 30), time(2, 0)), 10.0)
        self.assertEqual(scheduled_hours_between(user, MONDAY, time(22, 0), time(2, 0)), 4.0)
        self.assertEqual(scheduled_hours_between(user, MONDAY, time(18, 0), time(20, 0)), 1.5)
        self.assertEqual(scheduled_hours_between(user, MONDAY, time(0, 30), time(2, 0)), 1.5)


class TimeOffRequestFormTests(TestCase):
    def setUp(self):
        self.user = _make_user()

    def test_partial_day_stores_times_and_hours(self):
        form = _form(
            self.user,
            request_kind=TimeOffRequestKind.PARTIAL_DAY,
            partial_date=MONDAY.isoformat(),
            partial_start="10:00",
            partial_end="13:00",
        )
        tor = _submit(form, self.user)
        self.assertTrue(tor.partial_day)
        self.assertEqual(tor.partial_hours, 2.5)
        self.assertEqual((tor.start_date, tor.end_date), (MONDAY, MONDAY))
        day = tor.days.get()
        self.assertEqual((day.start_time, day.end_time, day.hours), (time(10, 0), time(13, 0), 2.5))
        self.assertEqual(tor.compute_requested_hours(), 2.5)

    def test_partial_day_outside_shift_is_rejected(self):
        form = _form(
            self.user,
            request_kind=TimeOffRequestKind.PARTIAL_DAY,
            partial_date=MONDAY.isoformat(),
            partial_start="06:00",
            partial_end="09:00",
        )
        self.assertFalse(form.is_valid())
        self.assertIn("within your scheduled shift", str(form.non_field_errors()))

    def test_partial_day_requires_times(self):
        form = _form(
            self.user,
            request_kind=TimeOffRequestKind.PARTIAL_DAY,
            partial_date=MONDAY.isoformat(),
        )
        self.assertFalse(form.is_valid())
        self.assertIn("partial_start", form.errors)
        self.assertIn("partial_end", form.errors)

    def test_full_day_uses_scheduled_hours(self):
        friday = MONDAY + timedelta(days=4)
        tor = _submit(
            _form(self.user, request_kind=TimeOffRequestKind.FULL_DAY, full_date=friday.isoformat()),
            self.user,
        )
        self.assertFalse(tor.partial_day)
        day = tor.days.get()
        self.assertEqual((day.date, day.start_time, day.hours), (friday, None, 4.5))

    def test_full_day_on_unscheduled_day_is_rejected(self):
        saturday = MONDAY + timedelta(days=5)
        form = _form(self.user, request_kind=TimeOffRequestKind.FULL_DAY, full_date=saturday.isoformat())
        self.assertFalse(form.is_valid())
        self.assertIn("full_date", form.errors)

    def test_multiple_days_with_skipped_and_partial_day(self):
        days = [MONDAY + timedelta(days=i) for i in range(5)]
        data = {
            "request_kind": TimeOffRequestKind.MULTIPLE_DAYS,
            "multi_start": days[0].isoformat(),
            "multi_end": days[4].isoformat(),
            "days_rendered": "1",
        }
        for d in days:
            data[f"day_{d.isoformat()}_start"] = "06:30" if d.weekday() == 4 else "07:00"
            data[f"day_{d.isoformat()}_end"] = "11:00" if d.weekday() == 4 else "15:30"
            if d.weekday() != 2:
                data[f"day_{d.isoformat()}_include"] = "on"
        data[f"day_{days[3].isoformat()}_end"] = "11:00"

        tor = _submit(_form(self.user, **data), self.user)
        self.assertEqual(
            [(r.date, r.hours) for r in tor.days.all()],
            [(days[0], 8.0), (days[1], 8.0), (days[3], 4.0), (days[4], 4.5)],
        )
        self.assertEqual(tor.compute_requested_hours(), 24.5)

        tor.approve(_make_user("boss"))
        occ = Occurrence.objects.filter(time_off_request=tor).order_by("date")
        self.assertEqual([(o.date, o.duration_hours) for o in occ], [(r.date, r.hours) for r in tor.days.all()])

    def test_multiple_days_without_script_takes_every_scheduled_day(self):
        sunday = MONDAY - timedelta(days=1)
        saturday = MONDAY + timedelta(days=5)
        tor = _submit(
            _form(
                self.user,
                request_kind=TimeOffRequestKind.MULTIPLE_DAYS,
                multi_start=sunday.isoformat(),
                multi_end=saturday.isoformat(),
            ),
            self.user,
        )
        self.assertEqual(tor.days.count(), 5)
        self.assertEqual((tor.start_date, tor.end_date), (MONDAY, MONDAY + timedelta(days=4)))
        self.assertEqual(tor.compute_requested_hours(), 36.5)

    def test_multiple_days_may_span_payroll_weeks(self):
        thursday = MONDAY + timedelta(days=3)
        next_tuesday = MONDAY + timedelta(days=8)
        tor = _submit(
            _form(
                self.user,
                request_kind=TimeOffRequestKind.MULTIPLE_DAYS,
                multi_start=thursday.isoformat(),
                multi_end=next_tuesday.isoformat(),
            ),
            self.user,
        )
        self.assertEqual(tor.compute_requested_hours(), 8 + 4.5 + 8 + 8)

    def test_weekly_cap_counts_existing_requests_per_week(self):
        six_day = _make_user("sixday", schedule={**WEEKDAY_SCHEDULE, "saturday": DAY_SHIFT})
        TimeOffRequest.objects.create(
            user=six_day,
            start_date=MONDAY,
            end_date=MONDAY + timedelta(days=3),
            status=TimeOffRequestStatus.APPROVED,
        )
        friday = MONDAY + timedelta(days=4)
        saturday = MONDAY + timedelta(days=5)
        form = _form(six_day, request_kind=TimeOffRequestKind.FULL_DAY, full_date=friday.isoformat())
        self.assertTrue(form.is_valid(), form.errors)

        form = _form(
            six_day,
            request_kind=TimeOffRequestKind.MULTIPLE_DAYS,
            multi_start=friday.isoformat(),
            multi_end=saturday.isoformat(),
        )
        self.assertFalse(form.is_valid())
        self.assertIn("44.50 hours, over the 40-hour weekly limit", str(form.non_field_errors()))

        next_monday = MONDAY + timedelta(days=7)
        form = _form(
            six_day,
            request_kind=TimeOffRequestKind.MULTIPLE_DAYS,
            multi_start=saturday.isoformat(),
            multi_end=next_monday.isoformat(),
        )
        self.assertTrue(form.is_valid(), form.errors)

    def test_request_kind_is_required(self):
        form = _form(self.user, full_date=MONDAY.isoformat())
        self.assertFalse(form.is_valid())
        self.assertIn("request_kind", form.errors)


class RequestTimeOffViewTests(TestCase):
    def test_post_creates_request_with_days(self):
        user = _make_user()
        self.client.force_login(user)
        response = self.client.post(
            reverse("attendance:request_time_off"),
            {
                "request_kind": TimeOffRequestKind.PARTIAL_DAY,
                "partial_date": MONDAY.isoformat(),
                "partial_start": "13:00",
                "partial_end": "15:30",
                "subtype": OccurrenceSubtype.TIME_OFF,
                "comments": "Appointment",
            },
        )
        self.assertEqual(response.status_code, 302)
        tor = TimeOffRequest.objects.get(user=user)
        self.assertEqual(tor.request_kind, TimeOffRequestKind.PARTIAL_DAY)
        self.assertEqual(tor.comments, "Appointment")
        self.assertEqual(tor.days.get().hours, 2.5)

    def test_get_renders_kind_choice_and_schedule(self):
        user = _make_user()
        self.client.force_login(user)
        response = self.client.get(reverse("attendance:request_time_off"))
        self.assertContains(response, 'name="request_kind"')
        self.assertContains(response, 'id="weekly-schedule"')
        self.assertContains(response, "Multiple days")


class LeaveOfAbsenceTests(TestCase):
    def setUp(self):
        self.user = _make_user(pto_balance=100.0)
        self.today = date.today()
        # Monday two weeks ago, so every generated day is in the past and PTO applies.
        self.start = self.today - timedelta(days=self.today.weekday() + 14)

    def _scheduled_days(self, first, last):
        out, d = [], first
        while d <= last:
            if d.weekday() < 5:
                out.append(d)
            d += timedelta(days=1)
        return out

    def test_sync_creates_one_planned_absence_per_scheduled_day(self):
        leave = LeaveOfAbsence.objects.create(
            user=self.user, subtype=OccurrenceSubtype.FMLA, start_date=self.start
        )
        result = sync_leave(leave, today=self.today)
        expected = self._scheduled_days(self.start, self.today)
        self.assertEqual(result.created, len(expected))
        occ = list(leave.occurrences.order_by("date"))
        self.assertEqual([o.date for o in occ], expected)
        self.assertTrue(all(o.subtype == OccurrenceSubtype.FMLA for o in occ))
        self.assertTrue(all(o.occurrence_type == OccurrenceType.PLANNED for o in occ))
        self.assertEqual(occ[0].duration_hours, 8.0)

        again = sync_leave(leave, today=self.today)
        self.assertEqual(again.created, 0)
        self.assertEqual(leave.occurrences.count(), len(expected))

    def test_fmla_draws_pto_and_disability_does_not(self):
        fmla = LeaveOfAbsence.objects.create(
            user=self.user, subtype=OccurrenceSubtype.FMLA, start_date=self.start, end_date=self.start
        )
        sync_leave(fmla, today=self.today)
        self.user.refresh_from_db()
        self.assertEqual(self.user.pto_balance, 92.0)

        other = _make_user("other", pto_balance=50.0)
        disability = LeaveOfAbsence.objects.create(
            user=other, subtype=OccurrenceSubtype.DISABILITY, start_date=self.start
        )
        sync_leave(disability, today=self.today)
        other.refresh_from_db()
        self.assertEqual(other.pto_balance, 50.0)
        self.assertTrue(disability.occurrences.exists())

    def test_layoff_creates_daily_absences_without_drawing_balances(self):
        layoff = LeaveOfAbsence(user=self.user, subtype=OccurrenceSubtype.LAYOFF, start_date=self.start)
        layoff.full_clean()
        layoff.save()
        sync_leave(layoff, today=self.today)
        self.user.refresh_from_db()
        self.assertEqual(self.user.pto_balance, 100.0)
        self.assertEqual(
            set(layoff.occurrences.values_list("subtype", flat=True)), {OccurrenceSubtype.LAYOFF}
        )
        self.assertEqual(layoff.occurrences.count(), len(self._scheduled_days(self.start, self.today)))

    def test_end_leave_removes_and_refunds_later_days(self):
        leave = LeaveOfAbsence.objects.create(
            user=self.user, subtype=OccurrenceSubtype.LEAVE_OF_ABSENCE, start_date=self.start
        )
        sync_leave(leave, today=self.today)
        last_day = self.start + timedelta(days=1)
        result = end_leave(leave, last_day)
        self.assertEqual(list(leave.occurrences.values_list("date", flat=True).order_by("date")),
                         [self.start, last_day])
        self.assertGreater(result.removed, 0)
        self.user.refresh_from_db()
        self.assertEqual(self.user.pto_balance, 84.0)
        self.assertFalse(leave.is_active)

    def test_reopened_leave_regenerates_days_after_new_end(self):
        leave = LeaveOfAbsence.objects.create(
            user=self.user, subtype=OccurrenceSubtype.DISABILITY, start_date=self.start
        )
        sync_leave(leave, today=self.today)
        end_leave(leave, self.start)
        leave.end_date = None
        leave.save()
        sync_leave(leave, today=self.today)
        self.assertEqual(leave.occurrences.count(), len(self._scheduled_days(self.start, self.today)))

    def test_hand_deleted_day_is_not_recreated(self):
        leave = LeaveOfAbsence.objects.create(
            user=self.user, subtype=OccurrenceSubtype.DISABILITY, start_date=self.start
        )
        sync_leave(leave, today=self.today)
        leave.occurrences.get(date=self.start).delete()
        sync_leave(leave, today=self.today)
        self.assertFalse(leave.occurrences.filter(date=self.start).exists())

    def test_finalized_payroll_week_is_left_alone(self):
        week_ending = self.start + timedelta(days=5)
        PayrollPeriod.objects.create(week_ending=week_ending, is_finalized=True)
        leave = LeaveOfAbsence.objects.create(
            user=self.user, subtype=OccurrenceSubtype.DISABILITY, start_date=self.start
        )
        result = sync_leave(leave, today=self.today)
        self.assertEqual(result.skipped_finalized, 5)
        self.assertFalse(leave.occurrences.filter(date__lte=week_ending).exists())

    def test_days_with_approved_time_off_are_skipped(self):
        tor = TimeOffRequest.objects.create(
            user=self.user, start_date=self.start, end_date=self.start, status=TimeOffRequestStatus.PENDING
        )
        tor.approve(_make_user("boss"))
        leave = LeaveOfAbsence.objects.create(
            user=self.user, subtype=OccurrenceSubtype.DISABILITY, start_date=self.start
        )
        sync_leave(leave, today=self.today)
        self.assertFalse(leave.occurrences.filter(date=self.start).exists())
        self.assertTrue(leave.occurrences.filter(date=self.start + timedelta(days=1)).exists())

    def test_overlapping_leave_is_rejected(self):
        LeaveOfAbsence.objects.create(user=self.user, subtype=OccurrenceSubtype.FMLA, start_date=self.start)
        second = LeaveOfAbsence(
            user=self.user, subtype=OccurrenceSubtype.DISABILITY, start_date=self.start + timedelta(days=3)
        )
        with self.assertRaises(ValidationError):
            second.full_clean()

    def test_delete_leave_refunds_generated_absences(self):
        leave = LeaveOfAbsence.objects.create(
            user=self.user, subtype=OccurrenceSubtype.FMLA, start_date=self.start, end_date=self.start
        )
        sync_leave(leave, today=self.today)
        ok, _msg = delete_leave_with_absences(leave)
        self.assertTrue(ok)
        self.assertFalse(Occurrence.objects.filter(user=self.user).exists())
        self.user.refresh_from_db()
        self.assertEqual(self.user.pto_balance, 100.0)

    def test_admin_add_backfills_and_switch_off_action_ends_leave(self):
        admin = CustomUser.objects.create_superuser("admin", "a@example.com", "x")
        self.client.force_login(admin)
        response = self.client.post(
            reverse("admin:attendance_leaveofabsence_add"),
            {
                "user": self.user.pk,
                "subtype": OccurrenceSubtype.DISABILITY,
                "start_date": self.start.isoformat(),
                "notes": "",
                "occurrences-TOTAL_FORMS": "0",
                "occurrences-INITIAL_FORMS": "0",
            },
        )
        self.assertEqual(response.status_code, 302)
        leave = LeaveOfAbsence.objects.get(user=self.user)
        self.assertEqual(leave.created_by, admin)
        self.assertEqual(leave.occurrences.count(), len(self._scheduled_days(self.start, self.today)))

        response = self.client.post(
            reverse("admin:attendance_leaveofabsence_changelist"),
            {"action": "end_leave_returning_today", "_selected_action": [leave.pk]},
        )
        self.assertEqual(response.status_code, 302)
        leave.refresh_from_db()
        self.assertEqual(leave.end_date, self.today - timedelta(days=1))
        self.assertEqual(leave.ended_by, admin)
        self.assertFalse(leave.occurrences.filter(date__gte=self.today).exists())

    def test_admin_end_date_edit_removes_later_days(self):
        admin = CustomUser.objects.create_superuser("admin", "a@example.com", "x")
        self.client.force_login(admin)
        leave = LeaveOfAbsence.objects.create(
            user=self.user, subtype=OccurrenceSubtype.DISABILITY, start_date=self.start
        )
        sync_leave(leave, today=self.today)
        response = self.client.post(
            reverse("admin:attendance_leaveofabsence_change", args=[leave.pk]),
            {
                "start_date": self.start.isoformat(),
                "end_date": self.start.isoformat(),
                "notes": "Returned early",
                "occurrences-TOTAL_FORMS": "0",
                "occurrences-INITIAL_FORMS": "0",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(list(leave.occurrences.values_list("date", flat=True)), [self.start])

    def test_payroll_close_generates_leave_days_and_adds_no_exchange(self):
        week_start = self.start - timedelta(days=1)
        week_ending = week_start + timedelta(days=6)
        LeaveOfAbsence.objects.create(
            user=self.user, subtype=OccurrenceSubtype.DISABILITY, start_date=self.start
        )
        period = PayrollPeriod.objects.create(week_ending=week_ending)
        finalize_payroll_week(
            period=period,
            week_start=week_start,
            week_ending=week_ending,
            finalized_by=None,
            users=[self.user],
        )
        week = Occurrence.objects.filter(user=self.user, date__range=(week_start, week_ending))
        self.assertEqual(week.filter(subtype=OccurrenceSubtype.DISABILITY).count(), 5)
        self.assertFalse(week.filter(subtype=OccurrenceSubtype.EXCHANGE).exists())
