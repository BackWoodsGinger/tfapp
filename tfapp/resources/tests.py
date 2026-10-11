from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse

from attendance.models import RoleChoices

from .org_chart import build_forest, flatten_forest, parent_id, visible_ids
from .views import _org_chart_people

User = get_user_model()


def _user(username, first, last, department="", role=RoleChoices.USER, supervisor=None, group_lead=None, team_lead=None):
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
        group_lead=group_lead,
        team_lead=team_lead,
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
        self.assertEqual(emp["role_key"], RoleChoices.USER)
        self.assertIn("group_lead_id", emp)
        self.assertIn("team_lead_id", emp)
        self.assertEqual(emp["department"], "Production")
        self.assertEqual(emp["url"], reverse("resources:user_detail", args=[self.emp.public_slug]))
        self.assertEqual(emp["photo"], "")
        self.assertContains(response, 'href="#directory"')
        self.assertContains(response, reverse("resources:org_chart_pdf"))
        self.assertContains(response, "Export PDF")

    def test_org_chart_people_skips_missing_profiles(self):
        people = _org_chart_people([self.ceo, self.emp])
        self.assertEqual(len(people), 2)
        self.assertTrue(all(p["photo"] == "" for p in people))


class OrgChartRelationshipTests(TestCase):
    def setUp(self):
        self.exec = _user("ceo", "Morgan", "Reyes", "Executive", RoleChoices.EXECUTIVE)
        self.sup = _user("sup", "Jordan", "Patel", "Production", RoleChoices.SUPERVISOR, self.exec)
        self.gl = _user(
            "gl", "Riley", "Brooks", "Production", RoleChoices.GROUP_LEAD, supervisor=self.sup
        )
        self.tl = _user(
            "tl",
            "Taylor",
            "Diaz",
            "Production",
            RoleChoices.TEAM_LEAD,
            supervisor=self.sup,
            group_lead=self.gl,
        )
        self.emp = _user(
            "emp",
            "Sam",
            "Lee",
            "Production",
            RoleChoices.USER,
            supervisor=self.sup,
            group_lead=self.gl,
            team_lead=self.tl,
        )
        self.eng = _user("eng", "Avery", "Kim", "Engineering", RoleChoices.MANAGER, self.exec)
        self.people = _org_chart_people(
            [self.exec, self.sup, self.gl, self.tl, self.emp, self.eng]
        )
        self.ids = {p["id"] for p in self.people}

    def test_parent_prefers_team_lead_then_group_lead_then_supervisor(self):
        by_name = {p["id"]: p for p in self.people}
        self.assertEqual(parent_id(by_name[self.emp.pk], self.ids), self.tl.pk)
        self.assertEqual(parent_id(by_name[self.tl.pk], self.ids), self.gl.pk)
        self.assertEqual(parent_id(by_name[self.gl.pk], self.ids), self.sup.pk)
        self.assertEqual(parent_id(by_name[self.sup.pk], self.ids), self.exec.pk)
        self.assertIsNone(parent_id(by_name[self.exec.pk], self.ids))

    def test_production_filter_includes_executive_ancestor_not_other_departments(self):
        shown = visible_ids(self.people, "Production")
        self.assertIn(self.exec.pk, shown)
        self.assertIn(self.emp.pk, shown)
        self.assertNotIn(self.eng.pk, shown)

    def test_executive_filter_does_not_pull_other_department_reports(self):
        shown = visible_ids(self.people, "Executive")
        self.assertEqual(shown, {self.exec.pk})

    def test_flatten_nests_team_under_group_under_supervisor(self):
        roots, children = build_forest(self.people, "")
        names = [row["name"] for row in flatten_forest(roots, children)]
        self.assertEqual(
            names,
            [
                "Reyes, Morgan",
                "Kim, Avery",
                "Patel, Jordan",
                "Brooks, Riley",
                "Diaz, Taylor",
                "Lee, Sam",
            ],
        )


class OrgChartPdfTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.exec = _user("ceo", "Morgan", "Reyes", "Executive", RoleChoices.EXECUTIVE)
        self.emp = _user("emp", "Sam", "Lee", "Production", RoleChoices.USER, supervisor=self.exec)

    def test_pdf_requires_login(self):
        response = self.client.get(reverse("resources:org_chart_pdf"))
        self.assertEqual(response.status_code, 302)
        self.assertIn("/accounts/login", response.url)

    def test_pdf_exports_filtered_department(self):
        self.client.force_login(self.emp)
        response = self.client.get(reverse("resources:org_chart_pdf"), {"dept": "Production"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertIn("org_chart_production.pdf", response["Content-Disposition"])
        self.assertTrue(response.content.startswith(b"%PDF"))
