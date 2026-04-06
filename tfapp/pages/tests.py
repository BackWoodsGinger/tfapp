from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse

from attendance.models import RoleChoices

from .models import HomeTickerSubmission


User = get_user_model()


class TickerSubmitViewTests(TestCase):
    def test_get_submit_requires_login(self):
        r = Client().get(reverse("ticker_submit"))
        self.assertEqual(r.status_code, 302)
        self.assertIn("/accounts/login", r.url)

    def test_get_submit_form_when_logged_in(self):
        u = User.objects.create_user(username="u2", password="x", role=RoleChoices.USER)
        c = Client()
        c.force_login(u)
        r = c.get(reverse("ticker_submit"))
        self.assertEqual(r.status_code, 200)

    def test_post_creates_pending(self):
        u = User.objects.create_user(username="u3", password="x", role=RoleChoices.USER)
        c = Client()
        c.force_login(u)
        r = c.post(
            reverse("ticker_submit"),
            {"message": "Test announcement"},
        )
        self.assertEqual(r.status_code, 302)
        sub = HomeTickerSubmission.objects.get(status=HomeTickerSubmission.Status.PENDING)
        self.assertEqual(sub.submitted_by_id, u.id)


class TickerReviewViewTests(TestCase):
    def test_review_redirects_non_exec(self):
        u = User.objects.create_user(username="u1", password="x", role=RoleChoices.USER)
        c = Client()
        c.force_login(u)
        r = c.get(reverse("ticker_review"))
        self.assertEqual(r.status_code, 302)

    def test_review_allows_exec(self):
        u = User.objects.create_user(username="exec1", password="x", role=RoleChoices.EXECUTIVE)
        c = Client()
        c.force_login(u)
        r = c.get(reverse("ticker_review"))
        self.assertEqual(r.status_code, 200)
