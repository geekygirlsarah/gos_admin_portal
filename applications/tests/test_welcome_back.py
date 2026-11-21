"""Tests for the "Welcome back" / prefill banners on Steps 5 and 6."""

from __future__ import annotations

import datetime

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from applications.models import Application
from applications.services import latest_program_for_adult, latest_program_for_student
from applications.tests.access_helpers import verify_application_browser
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
            legal_first_name="P",
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
        verify_application_browser(self.client, app)
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
        verify_application_browser(self.client, app)
        response = self.client.get(
            reverse("apply_step5", kwargs={"app_id": app.application_id})
        )
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Welcome back")


class Step7WelcomeBackBannerTests(TestCase):
    def test_banner_shown_when_prefilling_from_existing_adult(self):
        program = Program.objects.create(
            name="Fall 2023",
            start_date=datetime.date(2023, 9, 1),
        )
        adult = Adult.objects.create(
            legal_first_name="Pat",
            last_name="Parent",
            personal_email="parent@example.com",
            is_parent=True,
        )
        student = Student.objects.create(
            preferred_first_name="Kid",
            last_name="Parent",
            primary_contact=adult,
        )
        Enrollment.objects.create(student=student, program=program)
        app = _verified(
            applicant_type=Application.Type.PARENT,
            email="parent@example.com",
            current_step=7,
        )
        verify_application_browser(self.client, app)
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
        verify_application_browser(self.client, app)
        response = self.client.get(
            reverse("apply_step7", kwargs={"app_id": app.application_id})
        )
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Welcome back")

    def test_step7_prefills_primary_contact_when_two_adults_share_email(self):
        """
        A shared email is not enough to identify which Adult is applying, so
        step 7 must not prefill either person or offer a relationship-derived swap.
        """
        shared_email = "shared@example.com"

        # Mary is the primary contact for an existing student.
        mary = Adult.objects.create(
            legal_first_name="Mary",
            last_name="Smith",
            personal_email=shared_email,
            is_parent=True,
        )
        student = Student.objects.create(
            legal_first_name="Ada",
            last_name="Smith",
            primary_contact=mary,
        )
        program = Program.objects.create(
            name="Summer 2025",
            start_date=datetime.date(2025, 6, 1),
        )
        Enrollment.objects.create(student=student, program=program)

        # John shares the same email but is only a secondary contact.
        john = Adult.objects.create(
            legal_first_name="John",
            last_name="Smith",
            personal_email=shared_email,
            is_parent=True,
        )
        student.secondary_contact = john
        student.save()

        app = _verified(
            applicant_type=Application.Type.PARENT,
            email=shared_email,
            current_step=7,
        )
        verify_application_browser(self.client, app)
        response = self.client.get(
            reverse("apply_step7", kwargs={"app_id": app.application_id})
        )
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.context["welcome_back"])
        self.assertFalse(response.context["has_secondary"])
        self.assertNotContains(response, "Welcome back")
        self.assertNotContains(response, "Swap primary")
        form = response.context["form"]
        self.assertNotIn("Mary", form.initial.values())
        self.assertNotIn("John", form.initial.values())
        self.assertNotEqual(form["legal_first_name"].value(), "Mary")
        self.assertNotEqual(form["legal_first_name"].value(), "John")

    def test_ambiguous_email_does_not_replace_saved_primary_contact_fields(self):
        first_adult = Adult.objects.create(
            legal_first_name="First",
            last_name="Guardian",
            personal_email="saved-shared@example.com",
            is_parent=True,
        )
        Adult.objects.create(
            legal_first_name="Second",
            last_name="Guardian",
            personal_email="saved-shared@example.com",
            is_parent=True,
        )
        Student.objects.create(
            legal_first_name="Child",
            last_name="Guardian",
            primary_contact=first_adult,
        )
        saved_parent = {
            "legal_first_name": "Manually Saved",
            "last_name": "Applicant",
            "email": "saved-shared@example.com",
        }
        app = _verified(
            applicant_type=Application.Type.PARENT,
            email="saved-shared@example.com",
            current_step=7,
            data={"step7-primaryparent": saved_parent},
        )
        verify_application_browser(self.client, app)

        response = self.client.get(
            reverse("apply_step7", kwargs={"app_id": app.application_id})
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'value="Manually Saved"')
        app.refresh_from_db()
        self.assertEqual(app.data["step7-primaryparent"], saved_parent)
