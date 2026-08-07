"""Tests for wizard back-navigation and label rendering."""

import datetime
import re

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from applications.models import Application
from programs.models import Program


class WizardBackNavigationReproductionTests(TestCase):
    def setUp(self):
        self.program = Program.objects.create(
            name="Spring 2030",
            start_date=timezone.localdate() + datetime.timedelta(days=60),
            active=True,
        )

    def test_back_button_from_step3_leads_to_step2_if_verified(self):
        app = Application.objects.create(
            email="test@example.com",
            applicant_type="student",
            email_verified_at=timezone.now(),
            current_step=4,
        )
        response = self.client.get(
            reverse("apply_step4", kwargs={"app_id": app.application_id})
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response, reverse("apply_step2", kwargs={"app_id": app.application_id})
        )

    def test_accessing_step3_while_verified_redirects_forward(self):
        app = Application.objects.create(
            email="test@example.com",
            applicant_type="student",
            email_verified_at=timezone.now(),
            current_step=4,
        )
        response = self.client.get(
            reverse("apply_step3", kwargs={"app_id": app.application_id})
        )
        self.assertRedirects(
            response, reverse("apply_step4", kwargs={"app_id": app.application_id})
        )

    def test_back_button_from_step5_leads_to_step4(self):
        app = Application.objects.create(
            email="test@example.com",
            applicant_type="student",
            email_verified_at=timezone.now(),
            program=self.program,
            current_step=5,
        )
        response = self.client.get(
            reverse("apply_step5", kwargs={"app_id": app.application_id})
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response, reverse("apply_step4", kwargs={"app_id": app.application_id})
        )
        self.assertNotContains(
            response, reverse("apply_step3", kwargs={"app_id": app.application_id})
        )

    def test_back_button_from_mentor_info_leads_to_step2_if_verified(self):
        app = Application.objects.create(
            email="mentor@example.com",
            applicant_type="mentor",
            email_verified_at=timezone.now(),
            current_step=5,
        )
        response = self.client.get(
            reverse("apply_mentor_info", kwargs={"app_id": app.application_id})
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response, reverse("apply_step2", kwargs={"app_id": app.application_id})
        )
        self.assertNotContains(
            response, reverse("apply_step4", kwargs={"app_id": app.application_id})
        )


class Step2LabelReproductionTest(TestCase):
    def test_step2_labels_not_duplicated(self):
        app = Application.objects.create(email="test@example.com")
        url = reverse("apply_step2", kwargs={"app_id": app.application_id})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        student_labels = re.findall(r"<label[^>]*>\s*Student\s*</label>", content)
        self.assertEqual(len(student_labels), 1)
        parent_labels = re.findall(
            r"<label[^>]*>\s*Parent / Guardian\s*</label>", content
        )
        self.assertEqual(len(parent_labels), 1)
        mentor_labels = re.findall(
            r"<label[^>]*>\s*Mentor / Volunteer\s*</label>", content
        )
        self.assertEqual(len(mentor_labels), 1)

    def test_step4_labels_not_duplicated(self):
        today = timezone.localdate()
        Program.objects.create(
            name="Test Program",
            active=True,
            start_date=today + datetime.timedelta(days=30),
            applications_open=today - datetime.timedelta(days=1),
            applications_close=today + datetime.timedelta(days=60),
        )
        app = Application.objects.create(
            email="test@example.com",
            email_verified_at=timezone.now(),
        )
        url = reverse("apply_step4", kwargs={"app_id": app.application_id})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        program_labels = re.findall(
            r"<label[^>]*>[\s\S]*?Test Program( \(\d{4}\))?[\s\S]*?</label>",
            content,
        )
        self.assertEqual(len(program_labels), 1)
