"""PTO/personal split: only quarter-hour increments may be drawn from PTO balance."""
from datetime import date, timedelta

from django.test import TestCase

from attendance.models import (
    CustomUser,
    Occurrence,
    OccurrenceSubtype,
    OccurrenceType,
    floor_hours_to_quarter_increment,
)
from decimal import Decimal


class TestFloorQuarterHours(TestCase):
    def test_floor_examples(self):
        self.assertEqual(floor_hours_to_quarter_increment(Decimal("1.33")), Decimal("1.25"))
        self.assertEqual(floor_hours_to_quarter_increment(Decimal("1.25")), Decimal("1.25"))
        self.assertEqual(floor_hours_to_quarter_increment(Decimal("0.24")), Decimal("0"))
        self.assertEqual(floor_hours_to_quarter_increment(Decimal("0.25")), Decimal("0.25"))


class TestApplyPtoQuarterHours(TestCase):
    def setUp(self):
        self.user = CustomUser.objects.create_user(
            username="ptouser",
            password="testpass",
            pto_balance=1.33,
            personal_time_balance=0.0,
        )
        self.past_date = date.today() - timedelta(days=7)

    def test_pto_balance_fractional_only_quarter_hours_applied_rest_personal(self):
        occ = Occurrence.objects.create(
            user=self.user,
            occurrence_type=OccurrenceType.PLANNED,
            subtype=OccurrenceSubtype.TIME_OFF,
            date=self.past_date,
            duration_hours=10.0,
        )
        self.user.refresh_from_db()
        self.assertAlmostEqual(self.user.pto_balance, 0.08, places=2)
        self.assertAlmostEqual(self.user.personal_time_balance, 8.75, places=2)
        self.assertAlmostEqual(occ.pto_hours_applied, 1.25, places=2)
        self.assertAlmostEqual(occ.personal_hours_applied, 8.75, places=2)

    def test_fmla_path_uses_quarter_hour_pto_from_balance(self):
        self.user.pto_balance = 1.33
        self.user.save()
        occ = Occurrence.objects.create(
            user=self.user,
            occurrence_type=OccurrenceType.PLANNED,
            subtype=OccurrenceSubtype.FMLA,
            date=self.past_date,
            duration_hours=10.0,
        )
        self.user.refresh_from_db()
        self.assertAlmostEqual(self.user.pto_balance, 0.08, places=2)
        self.assertAlmostEqual(occ.pto_hours_applied, 1.25, places=2)
        self.assertEqual(occ.personal_hours_applied, 0.0)
