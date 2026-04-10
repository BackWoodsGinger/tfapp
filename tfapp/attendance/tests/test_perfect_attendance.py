"""Perfect Attendance: new-hire month window and disqualifying absence subtypes."""
from datetime import date

from django.test import TestCase

from attendance.models import (
    CustomUser,
    Occurrence,
    OccurrenceSubtype,
    OccurrenceType,
    first_full_month_start_after_hire,
    user_eligible_for_perfect_attendance_new_hire_month,
)
from attendance.views import _perfect_attendance_with_hours


class TestPerfectAttendanceNewHireMonth(TestCase):
    def test_first_full_month_after_june_hire_is_july(self):
        self.assertEqual(
            first_full_month_start_after_hire(date(2025, 6, 15)),
            date(2025, 7, 1),
        )

    def test_first_full_month_after_december_hire_is_january_next_year(self):
        self.assertEqual(
            first_full_month_start_after_hire(date(2025, 12, 1)),
            date(2026, 1, 1),
        )

    def test_before_first_full_month_ineligible(self):
        anchor = date(2025, 6, 10)
        self.assertFalse(
            user_eligible_for_perfect_attendance_new_hire_month(anchor, date(2025, 6, 1))
        )

    def test_from_first_full_month_onward_eligible(self):
        anchor = date(2025, 6, 10)
        self.assertTrue(
            user_eligible_for_perfect_attendance_new_hire_month(anchor, date(2025, 7, 1))
        )
        self.assertTrue(
            user_eligible_for_perfect_attendance_new_hire_month(anchor, date(2026, 1, 1))
        )

    def test_no_anchor_skips_new_hire_rule(self):
        self.assertTrue(user_eligible_for_perfect_attendance_new_hire_month(None, date(2025, 1, 1)))


class TestPerfectAttendanceFilters(TestCase):
    def setUp(self):
        self.period_first = date(2025, 7, 1)
        self.period_end = date(2025, 7, 15)

    def test_new_hire_excluded_before_first_full_month(self):
        u = CustomUser.objects.create_user(
            username="nh1",
            password="x",
            hire_date=date(2025, 6, 20),
            is_exempt=False,
        )
        qs = CustomUser.objects.filter(pk=u.pk)
        rows = _perfect_attendance_with_hours(qs, date(2025, 6, 1), date(2025, 6, 30))
        self.assertEqual(len(rows), 0)

    def test_new_hire_included_from_first_full_month_if_clean(self):
        u = CustomUser.objects.create_user(
            username="nh2",
            password="x",
            hire_date=date(2025, 6, 20),
            is_exempt=False,
        )
        qs = CustomUser.objects.filter(pk=u.pk)
        rows = _perfect_attendance_with_hours(qs, self.period_first, self.period_end)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["user"].pk, u.pk)

    def test_disqualifying_subtype_excludes_user(self):
        u = CustomUser.objects.create_user(
            username="dq",
            password="x",
            hire_date=date(2020, 1, 1),
            is_exempt=False,
        )
        Occurrence.objects.create(
            user=u,
            occurrence_type=OccurrenceType.PLANNED,
            subtype=OccurrenceSubtype.FMLA,
            date=self.period_first,
            duration_hours=4.0,
        )
        qs = CustomUser.objects.filter(pk=u.pk)
        rows = _perfect_attendance_with_hours(qs, self.period_first, self.period_end)
        self.assertEqual(len(rows), 0)

    def test_unplanned_absence_still_excludes(self):
        u = CustomUser.objects.create_user(
            username="unp",
            password="x",
            hire_date=date(2020, 1, 1),
            is_exempt=False,
        )
        Occurrence.objects.create(
            user=u,
            occurrence_type=OccurrenceType.UNPLANNED,
            subtype=OccurrenceSubtype.TIME_OFF,
            date=self.period_first,
            duration_hours=2.0,
        )
        qs = CustomUser.objects.filter(pk=u.pk)
        rows = _perfect_attendance_with_hours(qs, self.period_first, self.period_end)
        self.assertEqual(len(rows), 0)

    def test_time_off_planned_does_not_disqualify_if_not_unplanned(self):
        u = CustomUser.objects.create_user(
            username="pto",
            password="x",
            hire_date=date(2020, 1, 1),
            is_exempt=False,
        )
        Occurrence.objects.create(
            user=u,
            occurrence_type=OccurrenceType.PLANNED,
            subtype=OccurrenceSubtype.TIME_OFF,
            date=self.period_first,
            duration_hours=8.0,
        )
        qs = CustomUser.objects.filter(pk=u.pk)
        rows = _perfect_attendance_with_hours(qs, self.period_first, self.period_end)
        self.assertEqual(len(rows), 1)
