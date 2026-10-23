from io import StringIO

from django.contrib.auth.models import Group, User
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from programs.models import Adult, EmailDeliveryEvent, Student


class EmailDeliveryUITests(TestCase):
    def setUp(self):
        self.lead_group, _ = Group.objects.get_or_create(name="LeadMentor")
        self.lead_user = User.objects.create_superuser(
            username="lead_mentor",
            email="lead@example.com",
            password="password123",  # nosec B106
        )
        self.lead_user.groups.add(self.lead_group)

        self.student = Student.objects.create(
            legal_first_name="Alice",
            last_name="Smith",
            personal_email="alice.delivery@example.com",
        )
        self.adult = Adult.objects.create(
            legal_first_name="Bob",
            last_name="Smith",
            personal_email="bob.delivery@example.com",
            is_parent=True,
        )

    def test_student_and_adult_delivery_issue_properties(self):
        self.assertFalse(self.student.has_email_delivery_issue)
        self.assertFalse(self.adult.has_email_delivery_issue)

        # Create bounce event for student
        event1 = EmailDeliveryEvent.objects.create(
            recipient_email="alice.delivery@example.com",
            event_type=EmailDeliveryEvent.EVENT_BOUNCED,
            bounce_type=EmailDeliveryEvent.BOUNCE_HARD,
            reason="550 5.1.1 User unknown",
            student=self.student,
        )

        self.assertTrue(self.student.has_email_delivery_issue)
        self.assertEqual(len(self.student.email_delivery_issues), 1)

        # Resolving the event clears the flag
        event1.resolved = True
        event1.save()
        self.assertFalse(self.student.has_email_delivery_issue)

        # Create complaint for adult
        EmailDeliveryEvent.objects.create(
            recipient_email="bob.delivery@example.com",
            event_type=EmailDeliveryEvent.EVENT_COMPLAINT,
            reason="Reported spam",
            adult=self.adult,
        )
        self.assertTrue(self.adult.has_email_delivery_issue)
        self.assertEqual(len(self.adult.email_delivery_issues), 1)

    def test_student_and_adult_detail_pages_show_warning(self):
        EmailDeliveryEvent.objects.create(
            recipient_email="alice.delivery@example.com",
            event_type=EmailDeliveryEvent.EVENT_BOUNCED,
            bounce_type=EmailDeliveryEvent.BOUNCE_HARD,
            reason="550 5.1.1 Address rejected",
            student=self.student,
        )
        EmailDeliveryEvent.objects.create(
            recipient_email="bob.delivery@example.com",
            event_type=EmailDeliveryEvent.EVENT_COMPLAINT,
            reason="Marked as spam",
            adult=self.adult,
        )

        self.client.login(username="lead_mentor", password="password123")  # nosec B106

        # Check student detail page
        resp_student = self.client.get(
            reverse("student_detail", args=[self.student.pk])
        )
        self.assertEqual(resp_student.status_code, 200)
        self.assertContains(resp_student, "Email Delivery Warning")
        self.assertContains(resp_student, "550 5.1.1 Address rejected")

        # Check adult detail page
        resp_adult = self.client.get(reverse("adult_detail", args=[self.adult.pk]))
        self.assertEqual(resp_adult.status_code, 200)
        self.assertContains(resp_adult, "Email Delivery Warning")
        self.assertContains(resp_adult, "Marked as spam")

    def test_lead_mentor_dashboard_shows_unresolved_issue_count(self):
        EmailDeliveryEvent.objects.create(
            recipient_email="test1@example.com",
            event_type=EmailDeliveryEvent.EVENT_BOUNCED,
            bounce_type=EmailDeliveryEvent.BOUNCE_HARD,
            reason="User unknown",
        )
        EmailDeliveryEvent.objects.create(
            recipient_email="test2@example.com",
            event_type=EmailDeliveryEvent.EVENT_COMPLAINT,
            reason="Spam",
        )
        EmailDeliveryEvent.objects.create(
            recipient_email="test3@example.com",
            event_type=EmailDeliveryEvent.EVENT_BOUNCED,
            bounce_type=EmailDeliveryEvent.BOUNCE_HARD,
            reason="User unknown",
            resolved=True,
        )

        self.client.login(username="lead_mentor", password="password123")  # nosec B106
        resp = self.client.get(reverse("profile_dashboard"))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context["lead_stats"]["pending_email_issues_count"], 2)
        self.assertContains(resp, "Delivery Issues")

    def test_email_delivery_issues_list_and_resolve_view(self):
        event = EmailDeliveryEvent.objects.create(
            recipient_email="resolve.me@example.com",
            event_type=EmailDeliveryEvent.EVENT_BOUNCED,
            bounce_type=EmailDeliveryEvent.BOUNCE_HARD,
            reason="Mailbox disabled",
            student=self.student,
        )

        self.client.login(username="lead_mentor", password="password123")  # nosec B106

        list_url = reverse("email_delivery_issues_list")
        resp = self.client.get(list_url)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "resolve.me@example.com")
        self.assertContains(resp, "Mailbox disabled")

        # Resolve the event
        resolve_url = reverse("email_delivery_issue_resolve", args=[event.pk])
        post_resp = self.client.post(resolve_url, follow=True)
        self.assertEqual(post_resp.status_code, 200)

        event.refresh_from_db()
        self.assertTrue(event.resolved)
        self.assertEqual(event.resolved_by, self.lead_user)
        self.assertIsNotNone(event.resolved_at)

    def test_audit_digest_reports_delivery_issues(self):
        EmailDeliveryEvent.objects.create(
            recipient_email="digest.test@example.com",
            event_type=EmailDeliveryEvent.EVENT_BOUNCED,
            bounce_type=EmailDeliveryEvent.BOUNCE_HARD,
            reason="User unknown",
        )

        out = StringIO()
        call_command("audit_digest", "--days", "1", stdout=out)
        output = out.getvalue()
        self.assertIn("EMAIL DELIVERY ISSUES", output)
        self.assertIn("digest.test@example.com", output)
