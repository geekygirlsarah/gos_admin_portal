"""Tests for the "Welcome back" / prefill banners on Steps 5 and 6."""

from __future__ import annotations

import datetime

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from applications.models import Application
from applications.services import latest_program_for_adult, latest_program_for_student
from programs.models import Adult, Enrollment, Program, Student


def _verified(**kwargs):
    defaults = dict(
        applicant_type=Application.Type.PARENT,
        email="parent@example.com",
        current_step=5,
        email_verified_at=timezone.now(),
        status=Application.Status.EMAIL_VERIFIED,
    )
    defaults.update(kwargs)
    return Application.objects.create(**defaults)


class LatestProgramHelpersTests(TestCase):
    def test_returns_none_for_none(self):
        self.assertIsNone(latest_program_for_student(None))
        self.assertIsNone(latest_program_for_adult(None))

    def test_returns_none_when_no_enrollments(self):
        s = Student.objects.create(legal_first_name="No", last_name="Enroll")
        self.assertIsNone(latest_program_for_student(s))

    def test_returns_most_recent_program_by_start_date(self):
        s = Student.objects.create(legal_first_name="A", last_name="B")
        older = Program.objects.create(
            name="Summer 2024",
            start_date=datetime.date(2024, 6, 1),
        )
        newer = Program.objects.create(
            name="Summer 2025",
            start_date=datetime.date(2025, 6, 1),
        )
        Enrollment.objects.create(student=s, program=older)
        Enrollment.objects.create(student=s, program=newer)
        self.assertEqual(str(latest_program_for_student(s)), "Summer 2025 (2025)")

    def test_latest_program_for_adult_uses_their_students(self):
        adult = Adult.objects.create(
            first_name="P",
            last_name="Q",
            personal_email="p@example.com",
            is_parent=True,
        )
        s = Student.objects.create(
            legal_first_name="K",
            last_name="Q",
            primary_contact=adult,
        )
        program = Program.objects.create(
            name="Spring 2024",
            start_date=datetime.date(2024, 3, 1),
        )
        Enrollment.objects.create(student=s, program=program)
        self.assertEqual(str(latest_program_for_adult(adult)), "Spring 2024 (2024)")


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class Step5WelcomeBackBannerTests(TestCase):
    def test_banner_shown_when_prefilling_from_existing_student(self):
        program = Program.objects.create(
            name="Summer 2024",
            start_date=datetime.date(2024, 6, 1),
        )
        student = Student.objects.create(
            legal_first_name="Ada",
            last_name="Lovelace",
            personal_email="ada@example.com",
        )
        Enrollment.objects.create(student=student, program=program)
        app = _verified(
            applicant_type=Application.Type.STUDENT,
            email="ada@example.com",
        )
        response = self.client.get(
            reverse("apply_step5", kwargs={"app_id": app.application_id})
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Welcome back")
        self.assertContains(response, "Ada")
        self.assertContains(response, "Summer 2024")

    def test_no_banner_when_no_existing_student(self):
        app = _verified(
            applicant_type=Application.Type.STUDENT,
            email="brand-new@example.com",
        )
        response = self.client.get(
            reverse("apply_step5", kwargs={"app_id": app.application_id})
        )
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Welcome back")


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class Step7WelcomeBackBannerTests(TestCase):
    def test_banner_shown_when_prefilling_from_existing_adult(self):
        program = Program.objects.create(
            name="Fall 2023",
            start_date=datetime.date(2023, 9, 1),
        )
        adult = Adult.objects.create(
            first_name="Pat",
            last_name="Parent",
            personal_email="parent@example.com",
            is_parent=True,
        )
        student = Student.objects.create(
            legal_first_name="Kid",
            last_name="Parent",
            primary_contact=adult,
        )
        Enrollment.objects.create(student=student, program=program)
        app = _verified(
            applicant_type=Application.Type.PARENT,
            email="parent@example.com",
            current_step=7,
        )
        response = self.client.get(
            reverse("apply_step7", kwargs={"app_id": app.application_id})
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Welcome back")
        self.assertContains(response, "Pat")
        self.assertContains(response, "Fall 2023")

    def test_no_banner_when_no_existing_adult(self):
        app = _verified(
            applicant_type=Application.Type.PARENT,
            email="brand-new-parent@example.com",
            current_step=7,
        )
        response = self.client.get(
            reverse("apply_step7", kwargs={"app_id": app.application_id})
        )
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Welcome back")
