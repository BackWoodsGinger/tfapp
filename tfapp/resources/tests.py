from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse

from attendance.models import RoleChoices

from .views import _org_chart_people

User = get_user_model()


def _user(username, first, last, department="", role=RoleChoices.USER, supervisor=None):
    return User.objects.create_user(
        username=username,
        password="x",
        first_name=first,
        last_name=last,
        payroll_firstname=first,
        payroll_lastname=last,
        department=department or "",
        role=role,
        supervisor=supervisor,
    )


class ResourcesHomeOrgChartTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.ceo = _user("ceo", "Morgan", "Reyes", "Executive", RoleChoices.EXECUTIVE)
        self.mgr = _user("mgr", "Jordan", "Patel", "Production", RoleChoices.MANAGER, self.ceo)
        self.lead = _user("lead", "Riley", "Brooks", "Production", RoleChoices.SUPERVISOR, self.mgr)
        self.emp = _user("emp", "Sam", "Lee", "Production", RoleChoices.USER, self.lead)
        self.other = _user("eng", "Avery", "Kim", "Engineering", RoleChoices.MANAGER, self.ceo)
        self.inactive = _user("gone", "Old", "Hire", "Production", RoleChoices.USER, self.lead)
        self.inactive.is_active = False
        self.inactive.save(update_fields=["is_active"])

    def test_home_requires_login(self):
        response = self.client.get(reverse("resources:home"))
        self.assertEqual(response.status_code, 302)
        self.assertIn("/accounts/login", response.url)

    def test_home_includes_org_chart_payload_and_section(self):
        self.client.force_login(self.emp)
        response = self.client.get(reverse("resources:home"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="org-chart"')
        self.assertContains(response, "resources-org-chart.js")
        people = response.context["org_chart_people"]
        ids = {p["id"] for p in people}
        self.assertIn(self.emp.pk, ids)
        self.assertNotIn(self.inactive.pk, ids)
        self.assertEqual(response.context["org_chart_viewer_id"], self.emp.pk)
        self.assertEqual(response.context["org_chart_viewer_department"], "Production")
        self.assertEqual(response.context["org_chart_departments"], ["Engineering", "Executive", "Production"])
        emp = next(p for p in people if p["id"] == self.emp.pk)
        self.assertEqual(emp["supervisor_id"], self.lead.pk)
        self.assertEqual(emp["department"], "Production")
        self.assertEqual(emp["url"], reverse("resources:user_detail", args=[self.emp.public_slug]))
        self.assertEqual(emp["photo"], "")
        self.assertContains(response, 'href="#directory"')

    def test_org_chart_people_skips_missing_profiles(self):
        people = _org_chart_people([self.ceo, self.emp])
        self.assertEqual(len(people), 2)
        self.assertTrue(all(p["photo"] == "" for p in people))
