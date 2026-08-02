from __future__ import annotations

import datetime
import re

from django.contrib.auth.models import Group, Permission, User
from django.core import mail
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from applications.models import Application
from programs.models import Enrollment, Program, RaceEthnicity, School, Student


class ApplicationIntegrationFlowTests(TestCase):
    """
    High-level integration tests for the public application wizard
    and the subsequent lead mentor review/conversion flow.
    """

    def setUp(self):
        self.lead_mentor_group, _ = Group.objects.get_or_create(name="LeadMentor")
        # Ensure the review permission is attached to the LeadMentor group
        from django.contrib.contenttypes.models import ContentType

        ct = ContentType.objects.get_for_model(Application)
        perm, _ = Permission.objects.get_or_create(
            content_type=ct, codename="review_application"
        )
        self.lead_mentor_group.permissions.add(perm)

        self.reviewer = User.objects.create_user(
            username="reviewer", email="reviewer@example.com"
        )
        self.reviewer.groups.add(self.lead_mentor_group)

        # Create a program that is currently accepting applications
        today = timezone.localdate()
        self.program = Program.objects.create(
            name="Robotics 2026",
            start_date=today + datetime.timedelta(days=30),
            end_date=today + datetime.timedelta(days=90),
            applications_open=today - datetime.timedelta(days=1),
            applications_close=today + datetime.timedelta(days=15),
            active=True,
        )

        # Create necessary lookups for forms
        self.school = School.objects.create(name="Steel High School")
        self.race = RaceEthnicity.objects.create(name="Prefer not to say")

    def assertStepSuccess(self, response, app, expected_step):
        app.refresh_from_db()
        if app.current_step < expected_step:
            msg = f"Step failed to advance to {expected_step} (current: {app.current_step})."
            if "form" in response.context:
                msg += f" Form errors: {response.context['form'].errors}"
            self.fail(msg)

    def test_student_application_to_conversion_flow(self):
        """
        Story: A Student applies through the wizard, a Lead Mentor approves it,
        and then converts it to a real Student record.
        """
        # 1. Start application
        response = self.client.post(reverse("apply_start"), follow=True)
        self.assertEqual(response.status_code, 200)
        app = Application.objects.get()
        app_id = app.application_id

        # 2. Step 2: Applicant Type & Email
        response = self.client.post(
            reverse("apply_step2", args=[app_id]),
            {
                "applicant_type": Application.Type.STUDENT,
                "email": "student@example.com",
            },
            follow=True,
        )
        self.assertStepSuccess(response, app, 3)

        # Verify OTP was sent
        self.assertEqual(len(mail.outbox), 1)
        # Extract OTP from email (assuming it's in the body)
        import re

        otp_match = re.search(r"(\d{6})", mail.outbox[0].body)
        self.assertTrue(otp_match)
        otp_code = otp_match.group(1)

        # 3. Step 3: Verify Email
        response = self.client.post(
            reverse("apply_step3", args=[app_id]), {"code": otp_code}, follow=True
        )
        self.assertStepSuccess(response, app, 4)
        app.refresh_from_db()
        self.assertEqual(app.status, Application.Status.EMAIL_VERIFIED)

        # 4. Step 4: Program Select
        response = self.client.post(
            reverse("apply_step4", args=[app_id]),
            {"program": self.program.pk},
            follow=True,
        )
        self.assertStepSuccess(response, app, 5)

        # 5. Step 5: Student Info
        response = self.client.post(
            reverse("apply_step5", args=[app_id]),
            {
                "legal_first_name": "Ada",
                "last_name": "Lovelace",
                "date_of_birth": "2010-01-01",
                "address": "123 Main St",
                "city": "Pittsburgh",
                "state": "PA",
                "zip_code": "15201",
                "school_name": self.school.name,
                "grade": "10",
                "graduation_year": 2028,
                "tshirt_size": "M",
                "confirm_age": True,
                "confirm_grade": True,
            },
            follow=True,
        )
        self.assertStepSuccess(response, app, 6)

        # 6. Step 6: Experience
        response = self.client.post(
            reverse("apply_step6", args=[app_id]),
            {
                "interest_reason": "I love robots",
                "hoped_gains": "Programming skills",
            },
            follow=True,
        )
        self.assertStepSuccess(response, app, 7)

        # 7. Step 7: Primary Parent (Student Handoff)
        response = self.client.post(
            reverse("apply_step7", args=[app_id]),
            {"parent_email": "parent@example.com"},
            follow=True,
        )
        # It should redirect to 'apply_start' after handoff
        self.assertEqual(response.status_code, 200)
        app.refresh_from_db()
        self.assertEqual(app.status, Application.Status.AWAITING_PARENT)
        self.assertTrue(app.handoff_token)

        # 8. Resume as Parent
        resume_url = reverse(
            "apply_resume_link_with_token", args=[app_id, app.handoff_token]
        )
        response = self.client.get(resume_url, follow=True)
        self.assertEqual(response.status_code, 200)

        # 9. Step 7: Primary Parent (Parent providing info)
        response = self.client.post(
            reverse("apply_step7", args=[app_id]),
            {
                "first_name": "Pat",
                "last_name": "Parent",
                "relationship_to_student": "parent",
                "email": "parent@example.com",
                "address": "123 Main St",
                "city": "Pittsburgh",
                "state": "PA",
                "zip_code": "15201",
                "phone_number": "412-555-1212",
                "phone_type": "cell",
                "email_updates": True,
            },
            follow=True,
        )
        self.assertStepSuccess(response, app, 8)

        # 10. Step 8: Secondary Parent
        response = self.client.post(
            reverse("apply_step8", args=[app_id]),
            {
                "first_name": "Mary",
                "last_name": "Parent",
                "relationship_to_student": "parent",
                "phone_number": "412-555-1213",
                "phone_type": "cell",
            },
            follow=True,
        )
        self.assertStepSuccess(response, app, 9)

        # 11. Step 9: Confirm & Submit
        response = self.client.post(
            reverse("apply_step9", args=[app_id]), {"confirm": True}, follow=True
        )
        # Step 9 submit redirects to 'apply_submitted', which sets current_step to 10
        self.assertStepSuccess(response, app, 10)
        app.refresh_from_db()
        self.assertEqual(app.status, Application.Status.SUBMITTED)

        # --- Lead Mentor Review ---
        self.client.force_login(self.reviewer)

        # 12. Approve
        response = self.client.post(
            reverse("application_review_approve", args=[app_id]), follow=True
        )
        self.assertEqual(response.status_code, 200)
        app.refresh_from_db()
        self.assertEqual(app.status, Application.Status.APPROVED)

        # 13. Convert
        response = self.client.post(
            reverse("application_review_convert", args=[app_id]), follow=True
        )
        self.assertEqual(response.status_code, 200)
        app.refresh_from_db()
        self.assertEqual(app.status, Application.Status.CONVERTED)
        self.assertTrue(app.converted_student)
        self.assertTrue(
            Enrollment.objects.filter(
                student=app.converted_student, program=self.program
            ).exists()
        )

    def test_parent_application_to_conversion_flow(self):
        """
        Story: A Parent applies on behalf of their student through the wizard,
        gets approved and converted.
        """
        # 1. Start application
        response = self.client.post(reverse("apply_start"), follow=True)
        app = Application.objects.get()
        app_id = app.application_id

        # 2. Step 2: Applicant Type=Parent & Email
        response = self.client.post(
            reverse("apply_step2", args=[app_id]),
            {"applicant_type": Application.Type.PARENT, "email": "parent@example.com"},
            follow=True,
        )
        self.assertStepSuccess(response, app, 3)

        # Verify OTP
        otp_match = re.search(r"(\d{6})", mail.outbox[-1].body)
        otp_code = otp_match.group(1)

        # 3. Step 3: Verify Email
        response = self.client.post(
            reverse("apply_step3", args=[app_id]), {"code": otp_code}, follow=True
        )
        self.assertStepSuccess(response, app, 4)

        # 4. Step 4: Program Select
        response = self.client.post(
            reverse("apply_step4", args=[app_id]),
            {"program": self.program.pk},
            follow=True,
        )
        self.assertStepSuccess(response, app, 5)

        # 5. Step 5: Student Info
        response = self.client.post(
            reverse("apply_step5", args=[app_id]),
            {
                "legal_first_name": "Bob",
                "last_name": "Builder",
                "date_of_birth": "2012-05-05",
                "address": "456 Oak Rd",
                "city": "Pittsburgh",
                "state": "PA",
                "zip_code": "15202",
                "school_name": self.school.name,
                "grade": "8",
                "graduation_year": 2030,
                "confirm_age": True,
                "confirm_grade": True,
            },
            follow=True,
        )
        self.assertStepSuccess(response, app, 6)

        # 6. Step 6: Experience
        response = self.client.post(
            reverse("apply_step6", args=[app_id]),
            {"interest_reason": "Loves building things"},
            follow=True,
        )
        self.assertStepSuccess(response, app, 7)

        # 7. Step 7: Primary Parent (Should pre-fill parent email)
        response = self.client.post(
            reverse("apply_step7", args=[app_id]),
            {
                "first_name": "Pat",
                "last_name": "Parent",
                "relationship_to_student": "parent",
                "email": "parent@example.com",
                "address": "456 Oak Rd",
                "city": "Pittsburgh",
                "state": "PA",
                "zip_code": "15202",
                "phone_number": "412-555-1212",
                "phone_type": "cell",
                "email_updates": True,
            },
            follow=True,
        )
        self.assertStepSuccess(response, app, 8)

        # 8. Step 8: Secondary Parent
        response = self.client.post(
            reverse("apply_step8", args=[app_id]),
            {
                "first_name": "Mary",
                "last_name": "Parent",
                "relationship_to_student": "parent",
                "phone_number": "412-555-1213",
                "phone_type": "cell",
            },
            follow=True,
        )
        self.assertStepSuccess(response, app, 9)

        # 9. Step 9: Confirm & Submit
        response = self.client.post(
            reverse("apply_step9", args=[app_id]), {"confirm": True}, follow=True
        )
        self.assertStepSuccess(response, app, 10)

        app.refresh_from_db()
        self.assertEqual(app.status, Application.Status.SUBMITTED)

        # --- Review & Convert ---
        self.client.force_login(self.reviewer)
        self.client.post(
            reverse("application_review_approve", args=[app_id]), follow=True
        )
        self.client.post(
            reverse("application_review_convert", args=[app_id]), follow=True
        )

        app.refresh_from_db()
        self.assertEqual(app.status, Application.Status.CONVERTED)
        self.assertEqual(app.converted_student.legal_first_name, "Bob")


import re
