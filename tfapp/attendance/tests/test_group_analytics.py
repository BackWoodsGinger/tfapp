from datetime import date

from django.test import TestCase

from attendance.group_analytics import (
    _absence_rate_pct,
    _employment_band,
    _extrapolate_next,
    compute_group_analytics,
)
from attendance.models import CustomUser, Occurrence, OccurrenceSubtype, OccurrenceType


class GroupAnalyticsTests(TestCase):
    def test_absence_rate_pct(self):
        self.assertEqual(_absence_rate_pct(10, 100), 10.0)
        self.assertEqual(_absence_rate_pct(10, 0), 0.0)

    def test_employment_band(self):
        self.assertEqual(_employment_band(20), "part_time")
        self.assertEqual(_employment_band(35), "full_time")
        self.assertEqual(_employment_band(29.5), "between")

    def test_extrapolate_next(self):
        self.assertEqual(_extrapolate_next([2.0, 4.0, 6.0]), 8.0)

    def test_compute_group_analytics_empty(self):
        user = CustomUser.objects.create_user(username="ne1", password="x", is_exempt=False)
        result = compute_group_analytics(
            occurrences=[],
            visible_users=[user],
            start_date=date(2025, 1, 1),
            end_date=date(2025, 1, 31),
            group_by="department",
        )
        self.assertEqual(result["company"]["absence_rate_pct"], 0.0)
        self.assertEqual(len(result["by_group"]), 1)

    def test_compute_group_analytics_with_occurrence(self):
        user = CustomUser.objects.create_user(
            username="ne2", password="x", is_exempt=False, department="Ops"
        )
        occ = Occurrence.objects.create(
            user=user,
            occurrence_type=OccurrenceType.UNPLANNED,
            subtype=OccurrenceSubtype.TARDY_OUT_OF_GRACE,
            date=date(2025, 1, 10),
            duration_hours=2.0,
        )
        result = compute_group_analytics(
            occurrences=[occ],
            visible_users=[user],
            start_date=date(2025, 1, 1),
            end_date=date(2025, 1, 31),
            group_by="department",
        )
        self.assertEqual(result["company"]["tardy_hours"], 2.0)
        self.assertEqual(result["company"]["unplanned_hours"], 2.0)
