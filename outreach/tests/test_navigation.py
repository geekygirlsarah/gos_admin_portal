from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from programs.models import Enrollment, Program, ProgramFeature, School, Student


class OutreachNavigationTest(TestCase):
    def setUp(self):
        self.school = School.objects.create(name="Test School")
        self.feature, _ = ProgramFeature.objects.get_or_create(
            key="outreach", defaults={"name": "Outreach"}
        )
        self.program = Program.objects.create(name="Test Program")
        self.program.features.add(self.feature)

        self.student_user = User.objects.create_user(
            username="student1", password="password"
        )  # nosec B106
        self.student = Student.objects.create(
            user=self.student_user,
            legal_first_name="Alice",
            last_name="Zuberg",
            school=self.school,
            graduation_year=2027,
        )
        Enrollment.objects.create(
            student=self.student, program=self.program, active=True
        )

    def test_student_nav_bar_shows_outreach_globally(self):
        self.client.login(username="student1", password="password")  # nosec B106
        # On dashboard (not outreach section)
        url = reverse("profile_dashboard")
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)

        # Outreach link should be in the nav bar
        self.assertContains(resp, '<nav class="navbar')
        self.assertContains(
            resp, f'href="/programs/{self.program.id}/outreach/">Outreach</a>'
        )

        # Should NOT see program name in the nav bar
        nav_start = resp.content.find(b'<nav class="navbar')
        nav_end = resp.content.find(b"</nav>", nav_start)
        nav_content = resp.content[nav_start:nav_end]

        self.assertNotIn(self.program.name.encode(), nav_content)

        # Should NOT see 'Students' link in the nav bar
        self.assertNotIn(b"Students</a>", nav_content)

    def test_student_nav_bar_in_outreach_section(self):
        self.client.login(username="student1", password="password")  # nosec B106
        url = reverse("outreach:event_list", args=[self.program.id])
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)

        nav_start = resp.content.find(b'<nav class="navbar')
        nav_end = resp.content.find(b"</nav>", nav_start)
        nav_content = resp.content[nav_start:nav_end]

        self.assertContains(
            resp, f'href="/programs/{self.program.id}/outreach/">Outreach</a>'
        )

        # Should NOT see program name in nav
        self.assertNotIn(self.program.name.encode(), nav_content)
