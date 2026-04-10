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


class TestProbationGracePolicy(TestCase):
    """First 90 days: up to 30h grace bank (no PTO); excess to personal. Then PTO/personal as usual."""

    def setUp(self):
        self.past_date = date.today() - timedelta(days=1)
        self.hire = date.today() - timedelta(days=30)

    def test_probation_full_grace_no_balance_hit_relabels_grace_time(self):
        user = CustomUser.objects.create_user(
            username="newhire",
            password="x",
            hire_date=self.hire,
            pto_balance=40.0,
            personal_time_balance=0.0,
        )
        occ = Occurrence.objects.create(
            user=user,
            occurrence_type=OccurrenceType.PLANNED,
            subtype=OccurrenceSubtype.TIME_OFF,
            date=self.past_date,
            duration_hours=8.0,
        )
        user.refresh_from_db()
        occ.refresh_from_db()
        self.assertEqual(occ.subtype, OccurrenceSubtype.GRACE_TIME)
        self.assertAlmostEqual(user.pto_balance, 40.0, places=2)
        self.assertAlmostEqual(user.personal_time_balance, 0.0, places=2)
        self.assertAlmostEqual(occ.probation_grace_hours_applied, 8.0, places=2)
        self.assertEqual(occ.pto_hours_applied, 0.0)
        self.assertEqual(occ.personal_hours_applied, 0.0)

    def test_probation_after_30h_bank_excess_goes_personal_only(self):
        user = CustomUser.objects.create_user(
            username="newhire2",
            password="x",
            hire_date=self.hire,
            pto_balance=40.0,
            personal_time_balance=0.0,
        )
        Occurrence.objects.create(
            user=user,
            occurrence_type=OccurrenceType.PLANNED,
            subtype=OccurrenceSubtype.TIME_OFF,
            date=self.past_date - timedelta(days=5),
            duration_hours=25.0,
        )
        occ2 = Occurrence.objects.create(
            user=user,
            occurrence_type=OccurrenceType.PLANNED,
            subtype=OccurrenceSubtype.TIME_OFF,
            date=self.past_date,
            duration_hours=8.0,
        )
        user.refresh_from_db()
        occ2.refresh_from_db()
        self.assertAlmostEqual(occ2.probation_grace_hours_applied, 5.0, places=2)
        self.assertAlmostEqual(occ2.personal_hours_applied, 3.0, places=2)
        self.assertAlmostEqual(user.personal_time_balance, 3.0, places=2)
        self.assertAlmostEqual(user.pto_balance, 40.0, places=2)

    def test_after_probation_standard_pto_then_personal(self):
        hire = date.today() - timedelta(days=120)
        user = CustomUser.objects.create_user(
            username="tenured",
            password="x",
            hire_date=hire,
            pto_balance=1.33,
            personal_time_balance=0.0,
        )
        occ = Occurrence.objects.create(
            user=user,
            occurrence_type=OccurrenceType.PLANNED,
            subtype=OccurrenceSubtype.TIME_OFF,
            date=self.past_date,
            duration_hours=10.0,
        )
        user.refresh_from_db()
        self.assertAlmostEqual(user.pto_balance, 0.08, places=2)
        self.assertAlmostEqual(user.personal_time_balance, 8.75, places=2)
        self.assertEqual(occ.probation_grace_hours_applied, 0.0)

    def test_fmla_during_probation_uses_pto_branch_not_grace_bank(self):
        user = CustomUser.objects.create_user(
            username="fmlau",
            password="x",
            hire_date=self.hire,
            pto_balance=10.0,
            personal_time_balance=0.0,
        )
        Occurrence.objects.create(
            user=user,
            occurrence_type=OccurrenceType.PLANNED,
            subtype=OccurrenceSubtype.FMLA,
            date=self.past_date,
            duration_hours=8.0,
        )
        user.refresh_from_db()
        self.assertAlmostEqual(user.pto_balance, 2.0, places=2)
        self.assertAlmostEqual(user.personal_time_balance, 0.0, places=2)
