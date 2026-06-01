from django.contrib.auth.models import Permission, User
from django.test import TestCase
from django.urls import reverse

from programs.models import Enrollment, Program, Student


class ViewTests(TestCase):
    def setUp(self):
        # Basic user
        self.user = User.objects.create_user(
            username="tester", password="pass12345"
        )  # nosec B106

    def test_program_list_requires_login(self):
        url = reverse("program_list")
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/accounts/login/", resp.url)

    def test_program_student_add_enrolls_student(self):
        # Grant permission and login
        perm = Permission.objects.get(codename="change_student")
        self.user.user_permissions.add(perm)
        self.client.login(username="tester", password="pass12345")  # nosec B106

        program = Program.objects.create(name="Robotics")
        student = Student.objects.create(legal_first_name="Alex", last_name="Smith")

        url = reverse("program_student_add", args=[program.pk])
        resp = self.client.post(url, {"student": student.pk})
        self.assertEqual(resp.status_code, 302)
        # Enrollment created
        self.assertTrue(
            Enrollment.objects.filter(student=student, program=program).exists()
        )
        # Redirects to program detail
        self.assertEqual(resp.url, reverse("program_detail", args=[program.pk]))

    def test_student_create_view_creates_student(self):
        # Grant permission and login
        perm = Permission.objects.get(codename="add_student")
        self.user.user_permissions.add(perm)
        # Add to LeadMentor group to satisfy DynamicWritePermissionMixin
        from django.contrib.auth.models import Group

        group, _ = Group.objects.get_or_create(name="LeadMentor")
        self.user.groups.add(group)

        self.client.login(username="tester", password="pass12345")  # nosec B106

        url = reverse("student_create")
        # StudentForm fields: legal_first_name, last_name are required in model.
        # grade_selector is optional in form but useful.
        resp = self.client.post(
            url,
            {
                "legal_first_name": "Jamie",
                "last_name": "Lee",
                "grade_selector": "9",
                "date_of_birth": "2010-01-01",
            },
        )
        # If it fails validation, it might be 200.
        if resp.status_code == 200:
            print(f"Form errors: {resp.context['form'].errors}")

        self.assertEqual(resp.status_code, 302)
        # Success URL is reverse('student_list') which is '/programs/students/'
        self.assertEqual(resp.url, reverse("student_list"))
        self.assertTrue(
            Student.objects.filter(legal_first_name="Jamie", last_name="Lee").exists()
        )

    def test_student_detail_view_renders(self):
        # Login without special permissions
        self.client.login(username="tester", password="pass12345")  # nosec B106
        student = Student.objects.create(legal_first_name="Riley", last_name="Jones")
        url = reverse("student_detail", args=[student.pk])
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        # ensure context contains student
        self.assertIn("student", resp.context)

    def test_program_update_view_saves_grade_range(self):
        # Grant permission and login
        perm = Permission.objects.get(codename="change_program")
        self.user.user_permissions.add(perm)
        # Add to LeadMentor group to satisfy role check in templates
        from django.contrib.auth.models import Group

        group, _ = Group.objects.get_or_create(name="LeadMentor")
        self.user.groups.add(group)

        self.client.login(username="tester", password="pass12345")  # nosec B106
        program = Program.objects.create(
            name="Robot Camp", start_date="2026-01-01", end_date="2026-01-31"
        )

        url = reverse("program_edit", args=[program.pk])
        data = {
            "name": "Updated Camp",
            "description": "New description",
            "start_date": "2026-01-01",
            "end_date": "2026-01-31",
            "grade_range_start": 4,
            "grade_range_end": 6,
            "cost": "$300",
            "active": True,
        }
        resp = self.client.post(url, data)
        self.assertEqual(resp.status_code, 302)

        program.refresh_from_db()
        self.assertEqual(program.grade_range_start, 4)
        self.assertEqual(program.grade_range_end, 6)

    def test_program_detail_view_displays_grade_range(self):
        # Add to LeadMentor group to satisfy can_user_read
        from django.contrib.auth.models import Group

        group, _ = Group.objects.get_or_create(name="LeadMentor")
        self.user.groups.add(group)

        self.client.login(username="tester", password="pass12345")  # nosec B106
        program = Program.objects.create(
            name="Robot Camp", grade_range_start=9, grade_range_end=12, active=True
        )
        url = reverse("program_detail", args=[program.pk])
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Grade Range:")
        self.assertContains(resp, "9th–12th Grade")
