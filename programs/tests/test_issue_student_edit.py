"""
Reproduction tests for:
1. Editing a Student record via the edit view must update the existing record,
   not create a new one.
2. Application conversion must match an existing Student by case-insensitive
   name + DOB before creating a new record (prevents duplicates when the same
   student re-applies with a differently-cased last name, e.g. "SMITH" vs "Smith").
"""

import datetime

from django.contrib.auth.models import Group, Permission, User
from django.test import TestCase
from django.urls import reverse

from applications.models import Application
from applications.services import convert_application_to_student
from programs.models import Program, Student


class StudentEditUpdatesExistingRecordTest(TestCase):
    """Editing a Student via StudentUpdateView must update the existing record,
    not create a new one."""

    def setUp(self):
        self.user = User.objects.create_user(
            username="staff", password="pass12345"  # nosec B106
        )
        perm = Permission.objects.get(codename="change_student")
        self.user.user_permissions.add(perm)
        group, _ = Group.objects.get_or_create(name="LeadMentor")
        self.user.groups.add(group)
        self.client.login(username="staff", password="pass12345")  # nosec B106

        self.student = Student.objects.create(
            legal_first_name="Jane",
            last_name="SMITH",
            date_of_birth=datetime.date(2008, 5, 15),
        )

    def test_edit_updates_existing_student_not_creates_new(self):
        """POSTing to student_edit should update the existing record, not create a new one."""
        url = reverse("student_edit", args=[self.student.pk])
        data = {
            "legal_first_name": "Jane",
            "last_name": "Smith",  # changed from all-caps to sentence case
            "date_of_birth": "2008-05-15",
        }
        resp = self.client.post(url, data)

        # Should redirect to student_detail (success), not re-render the form (200)
        self.assertEqual(
            resp.status_code,
            302,
            msg=f"Expected redirect, got 200. Form errors: {resp.context['form'].errors if resp.status_code == 200 else 'N/A'}",
        )
        self.assertRedirects(resp, reverse("student_detail", args=[self.student.pk]))

        # Only one Student record should exist
        self.assertEqual(
            Student.objects.count(),
            1,
            msg=f"Expected 1 student, got {Student.objects.count()}. "
            f"Students: {list(Student.objects.values('pk', 'legal_first_name', 'last_name'))}",
        )

        # The existing record should be updated
        self.student.refresh_from_db()
        self.assertEqual(self.student.last_name, "Smith")

    def test_edit_all_caps_last_name_updates_in_place(self):
        """Editing a student with an all-caps last name should update the same record."""
        original_pk = self.student.pk
        url = reverse("student_edit", args=[self.student.pk])
        data = {
            "legal_first_name": "Jane",
            "last_name": "SMITH",  # keeping all-caps
            "date_of_birth": "2008-05-15",
        }
        resp = self.client.post(url, data)
        self.assertEqual(resp.status_code, 302)

        # PK must not change
        self.assertEqual(Student.objects.count(), 1)
        self.assertEqual(Student.objects.first().pk, original_pk)


class ApplicationConversionDeduplicatesStudentTest(TestCase):
    """Application conversion must not create a duplicate Student when an
    existing record matches by case-insensitive name + date of birth."""

    def setUp(self):
        self.program = Program.objects.create(name="Robotics 2026")
        self.existing_student = Student.objects.create(
            legal_first_name="Jane",
            last_name="SMITH",
            date_of_birth=datetime.date(2008, 5, 15),
        )

    def _make_application(self, last_name):
        """Helper: build a minimal APPROVED_SIGNED application."""
        app = Application.objects.create(
            email="jane.smith.applicant@example.com",
            program=self.program,
            applicant_type=Application.Type.STUDENT,
            status=Application.Status.APPROVED_SIGNED,
            data={
                "step5-student": {
                    "legal_first_name": "Jane",
                    "last_name": last_name,
                    "date_of_birth": "2008-05-15",
                    "personal_email": "different@example.com",  # different email — no email match
                },
            },
        )
        return app

    def test_conversion_matches_existing_student_case_insensitive(self):
        """Converting an application with 'Smith' should find the existing 'SMITH' student."""
        app = self._make_application("Smith")
        result = convert_application_to_student(app)

        # Must reuse the existing student, not create a new one
        self.assertEqual(Student.objects.count(), 1)
        self.assertEqual(result.pk, self.existing_student.pk)

    def test_conversion_does_not_create_duplicate_for_all_caps_name(self):
        """Converting an application with 'SMITH' should find the existing 'SMITH' student."""
        app = self._make_application("SMITH")
        result = convert_application_to_student(app)

        self.assertEqual(Student.objects.count(), 1)
        self.assertEqual(result.pk, self.existing_student.pk)
