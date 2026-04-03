"""Tests for approval notification emails (group lead / supervisor routing)."""
from datetime import date

from django.core import mail
from django.test import TestCase, override_settings

from attendance import approval_emails
from attendance.models import (
    AdjustPunchField,
    AdjustPunchRequest,
    CustomUser,
    OccurrenceSubtype,
    TimeOffRequest,
    WorkThroughLunchRequest,
)
from timeclock.models import TimeEntry


@override_settings(
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    DEFAULT_FROM_EMAIL="app@example.com",
    SITE_BASE_URL="https://app.example.com",
)
class ApprovalEmailRoutingTests(TestCase):
    def test_recipient_prefers_group_lead_when_email_set(self):
        lead = CustomUser.objects.create_user(
            username="lead", password="x", email="lead@example.com"
        )
        emp = CustomUser.objects.create_user(username="emp", password="x", email="e@e.com")
        emp.group_lead = lead
        emp.save()
        self.assertEqual(approval_emails.recipient_for_employee(emp), lead)

    def test_recipient_falls_back_to_supervisor_when_no_group_lead(self):
        sup = CustomUser.objects.create_user(
            username="sup", password="x", email="sup@example.com"
        )
        emp = CustomUser.objects.create_user(username="emp", password="x", email="e@e.com")
        emp.supervisor = sup
        emp.save()
        self.assertEqual(approval_emails.recipient_for_employee(emp), sup)

    def test_recipient_falls_back_when_group_lead_has_no_email(self):
        lead = CustomUser.objects.create_user(username="lead", password="x", email="")
        sup = CustomUser.objects.create_user(
            username="sup", password="x", email="sup@example.com"
        )
        emp = CustomUser.objects.create_user(username="emp", password="x", email="e@e.com")
        emp.group_lead = lead
        emp.supervisor = sup
        emp.save()
        self.assertEqual(approval_emails.recipient_for_employee(emp), sup)


@override_settings(
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    DEFAULT_FROM_EMAIL="app@example.com",
    SITE_BASE_URL="https://app.example.com",
)
class ApprovalEmailSendTests(TestCase):
    def setUp(self):
        self.lead = CustomUser.objects.create_user(
            username="lead", password="x", email="lead@example.com"
        )
        self.emp = CustomUser.objects.create_user(
            username="emp", password="x", email="emp@example.com", group_lead=self.lead
        )

    def test_time_off_submitted_email(self):
        tor = TimeOffRequest.objects.create(
            user=self.emp,
            start_date=date(2026, 6, 1),
            end_date=date(2026, 6, 1),
            subtype=OccurrenceSubtype.TIME_OFF,
        )
        approval_emails.notify_time_off_submitted(tor)
        self.assertEqual(len(mail.outbox), 1)
        msg = mail.outbox[0]
        self.assertEqual(msg.to, ["lead@example.com"])
        self.assertIn("Time off", msg.subject)
        self.assertIn("app.example.com", msg.body)

    def test_time_off_cancelled_email(self):
        tor = TimeOffRequest.objects.create(
            user=self.emp,
            start_date=date(2026, 6, 1),
            end_date=date(2026, 6, 1),
            subtype=OccurrenceSubtype.TIME_OFF,
        )
        approval_emails.notify_time_off_cancelled(tor, was_approved=False)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("cancelled", mail.outbox[0].subject.lower())

    def test_work_through_lunch_submitted(self):
        wtl = WorkThroughLunchRequest.objects.create(
            user=self.emp,
            work_date=date(2026, 6, 2),
        )
        approval_emails.notify_work_through_lunch_submitted(wtl)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("lunch", mail.outbox[0].subject.lower())

    def test_adjust_punch_submitted(self):
        entry = TimeEntry.objects.create(user=self.emp, date=date(2026, 6, 3))
        from django.utils import timezone

        t = timezone.now()
        apr = AdjustPunchRequest.objects.create(
            user=self.emp,
            time_entry=entry,
            punch_field=AdjustPunchField.CLOCK_IN,
            previous_at=t,
            requested_at=t,
        )
        approval_emails.notify_adjust_punch_submitted(apr)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("edit", mail.outbox[0].subject.lower())
