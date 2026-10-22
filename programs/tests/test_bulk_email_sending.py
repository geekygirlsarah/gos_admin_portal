from unittest import mock

from django.contrib.auth.models import Group, User
from django.core import mail
from django.test import TestCase
from django.urls import reverse

from programs.models import (
    Adult,
    AdultStudentRelationship,
    Enrollment,
    Program,
    Student,
)


class BulkEmailSendingTests(TestCase):
    def setUp(self):
        mail.outbox = []
        self.lead_group, _ = Group.objects.get_or_create(name="LeadMentor")
        self.lead_user = User.objects.create_superuser(
            username="lead_mentor",
            email="lead@example.com",
            password="password123",  # nosec B106
        )

        self.program = Program.objects.create(name="FRC 2026-2027", active=True)

        self.student1 = Student.objects.create(
            legal_first_name="Alice",
            last_name="Smith",
            personal_email="alice@example.com",
        )
        self.student2 = Student.objects.create(
            legal_first_name="Bob",
            last_name="Jones",
            personal_email="bob@example.com",
        )

        Enrollment.objects.create(
            student=self.student1, program=self.program, active=True
        )
        Enrollment.objects.create(
            student=self.student2, program=self.program, active=True
        )

        self.parent = Adult.objects.create(
            legal_first_name="Carol",
            last_name="Smith",
            personal_email="carol@example.com",
            is_parent=True,
            email_updates=True,
            login_enabled=True,
        )
        AdultStudentRelationship.objects.create(
            adult=self.parent,
            student=self.student1,
            relationship_to_student="parent",
        )

        self.client.login(username="lead_mentor", password="password123")  # nosec B106

    @mock.patch("programs.views.programs.send_messages_in_background")
    def test_program_email_creates_individual_recipient_messages(self, mock_send_bg):
        post_data = {
            "program": self.program.pk,
            "recipient_groups": ["students", "parents"],
            "subject": "Important Team Notice",
            "body": "<p>Hello everyone!</p>",
            "from_account": "DEFAULT",
        }

        response = self.client.post(
            reverse("program_messaging"), data=post_data, follow=True
        )
        self.assertEqual(response.status_code, 200)

        # 2 students + 1 parent = 3 recipients
        self.assertEqual(mock_send_bg.call_count, 1)
        messages_sent = mock_send_bg.call_args[0][0]
        self.assertEqual(len(messages_sent), 3)

        recipient_tos = [msg.to for msg in messages_sent]
        # Verify each email has individual address in 'to' and no 'bcc' bundling
        self.assertIn(["alice@example.com"], recipient_tos)
        self.assertIn(["bob@example.com"], recipient_tos)
        self.assertIn(["carol@example.com"], recipient_tos)

        for msg in messages_sent:
            self.assertEqual(msg.bcc, [])
            self.assertEqual(msg.subject, "Important Team Notice")

    @mock.patch("programs.views.programs.send_messages_in_background")
    def test_program_email_test_recipient_only(self, mock_send_bg):
        post_data = {
            "program": self.program.pk,
            "recipient_groups": ["students"],
            "test_email": "mentor_test@example.com",
            "subject": "Test Announcement",
            "body": "<p>Testing only</p>",
            "from_account": "DEFAULT",
        }

        response = self.client.post(
            reverse("program_messaging"), data=post_data, follow=True
        )
        self.assertEqual(response.status_code, 200)

        self.assertEqual(mock_send_bg.call_count, 1)
        messages_sent = mock_send_bg.call_args[0][0]
        self.assertEqual(len(messages_sent), 1)
        self.assertEqual(messages_sent[0].to, ["mentor_test@example.com"])

    @mock.patch("programs.views.finances.send_messages_in_background")
    def test_program_email_balances_batch_dispatch(self, mock_send_bg):
        self.student1.primary_contact = self.parent
        self.student1.save()

        url = reverse("program_dues_email", args=[self.program.pk])
        post_data = {
            "program": self.program.pk,
            "recipient_filter": "all",
            "subject": "Tuition Balance Notice",
            "body": "<p>Please check your dues</p>",
            "from_account": "DEFAULT",
        }

        response = self.client.post(url, data=post_data, follow=True)
        self.assertEqual(response.status_code, 200)

        self.assertEqual(mock_send_bg.call_count, 1)
        messages_sent = mock_send_bg.call_args[0][0]
        self.assertGreaterEqual(len(messages_sent), 1)
        recipient_tos = [msg.to for msg in messages_sent]
        self.assertIn(["carol@example.com"], recipient_tos)

    @mock.patch("applications.views.review.send_messages_in_background")
    def test_application_email_batch_dispatch(self, mock_send_bg):
        from applications.models import Application

        Application.objects.create(
            program=self.program,
            status=Application.Status.SUBMITTED,
            email="applicant@example.com",
            data={
                "step5-student": {
                    "legal_first_name": "Test",
                    "last_name": "Applicant",
                    "email": "applicant@example.com",
                },
                "step7-primaryparent": {
                    "first_name": "Parent",
                    "last_name": "Applicant",
                    "email": "app_parent@example.com",
                },
            },
        )

        url = reverse("application_review_messaging")
        post_data = {
            "program": str(self.program.pk),
            "statuses": [Application.Status.SUBMITTED],
            "subject": "Application Received",
            "body": "<p>Thank you for applying</p>",
            "from_account": "DEFAULT",
        }

        response = self.client.post(url, data=post_data, follow=True)
        self.assertEqual(response.status_code, 200)

        self.assertEqual(mock_send_bg.call_count, 1)
        messages_sent = mock_send_bg.call_args[0][0]
        self.assertEqual(len(messages_sent), 2)
        recipient_tos = [msg.to for msg in messages_sent]
        self.assertIn(["applicant@example.com"], recipient_tos)
        self.assertIn(["app_parent@example.com"], recipient_tos)
