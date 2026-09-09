import datetime

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from applications.models import Application
from programs.models import Program, ProgramDocument

User = get_user_model()


def _ensure_reviewer():
    group, _ = Group.objects.get_or_create(name="LeadMentor")
    from django.contrib.contenttypes.models import ContentType

    ct, _ = ContentType.objects.get_or_create(
        app_label="applications", model="application"
    )
    perm, _ = Permission.objects.get_or_create(
        content_type=ct,
        codename="review_application",
        defaults={"name": "Can review applications"},
    )
    group.permissions.add(perm)

    user = User.objects.create_user(
        username="lead_reviewer",
        email="lead_reviewer@example.com",
        password="password123",  # nosec B106
    )
    user.groups.add(group)
    return user


class ApplicationReviewUIImprovementsTests(TestCase):
    def setUp(self):
        self.reviewer = _ensure_reviewer()
        self.client.force_login(self.reviewer)
        self.program = Program.objects.create(
            name="Girls of Steel FRC 2026-2027",
            active=True,
            start_date=datetime.date(2026, 9, 1),
            end_date=datetime.date(2027, 5, 31),
        )

    def test_review_list_kpi_summary_cards(self):
        # 1 Submitted
        Application.objects.create(
            program=self.program,
            email="app1@example.com",
            applicant_type=Application.Type.STUDENT,
            status=Application.Status.SUBMITTED,
            submitted_at=timezone.now(),
            data={
                "step5-student": {"legal_first_name": "Ada", "last_name": "Lovelace"}
            },
        )
        # 1 Approved
        Application.objects.create(
            program=self.program,
            email="app2@example.com",
            applicant_type=Application.Type.STUDENT,
            status=Application.Status.APPROVED,
            submitted_at=timezone.now(),
            data={
                "step5-student": {"legal_first_name": "Grace", "last_name": "Hopper"}
            },
        )
        # 1 Approved Signed
        Application.objects.create(
            program=self.program,
            email="app3@example.com",
            applicant_type=Application.Type.STUDENT,
            status=Application.Status.APPROVED_SIGNED,
            submitted_at=timezone.now(),
            data={
                "step5-student": {
                    "legal_first_name": "Katherine",
                    "last_name": "Johnson",
                }
            },
        )
        # 2 Converted
        Application.objects.create(
            program=self.program,
            email="app4@example.com",
            applicant_type=Application.Type.STUDENT,
            status=Application.Status.CONVERTED,
            submitted_at=timezone.now(),
            data={
                "step5-student": {
                    "legal_first_name": "Margaret",
                    "last_name": "Hamilton",
                }
            },
        )
        Application.objects.create(
            program=self.program,
            email="app5@example.com",
            applicant_type=Application.Type.MENTOR,
            status=Application.Status.CONVERTED,
            submitted_at=timezone.now(),
            data={"mentor_info": {"legal_first_name": "Mary", "last_name": "Jackson"}},
        )

        resp = self.client.get(reverse("application_review_list"))
        self.assertEqual(resp.status_code, 200)

        # Check KPI summary cards
        self.assertContains(resp, "Pending Review")
        self.assertContains(resp, "Awaiting Signatures")
        self.assertContains(resp, "Ready to Convert")
        self.assertContains(resp, "Converted")

        # Context counts
        self.assertEqual(resp.context["kpi_pending_review"], 1)
        self.assertEqual(resp.context["kpi_awaiting_signatures"], 1)
        self.assertEqual(resp.context["kpi_ready_to_convert"], 1)
        self.assertEqual(resp.context["kpi_converted"], 2)

    def test_review_list_toolbar_and_actions(self):
        resp = self.client.get(reverse("application_review_list"))
        self.assertEqual(resp.status_code, 200)

        # Action links
        self.assertContains(resp, reverse("application_review_messaging"))
        self.assertContains(resp, reverse("application_cleanup_stale"))
        self.assertContains(resp, "Message Applicants")
        self.assertContains(resp, "Clean Up Stale")

        # Filters
        self.assertContains(resp, "status-filter")
        self.assertContains(resp, "program-filter")
        self.assertContains(resp, "open-only")

    def test_review_detail_progress_stepper_student(self):
        app = Application.objects.create(
            program=self.program,
            email="ada@example.com",
            applicant_type=Application.Type.STUDENT,
            status=Application.Status.SUBMITTED,
            submitted_at=timezone.now(),
            data={
                "step5-student": {
                    "legal_first_name": "Ada",
                    "last_name": "Lovelace",
                    "email": "ada@example.com",
                },
                "step6-experience": {
                    "interests": "Robotics, Programming",
                },
                "step7-primaryparent": {
                    "legal_first_name": "Ann",
                    "last_name": "Byron",
                    "email": "ann@example.com",
                },
            },
        )

        resp = self.client.get(
            reverse("application_review_detail", kwargs={"app_id": app.application_id})
        )
        self.assertEqual(resp.status_code, 200)

        # Stepper elements
        self.assertContains(resp, "1. Submitted")
        self.assertContains(resp, "2. Approved")
        self.assertContains(resp, "3. Signed Docs")
        self.assertContains(resp, "4. Converted")

        # Overview & Hero
        self.assertContains(resp, "Ada Lovelace")
        self.assertContains(resp, "Student Application")
        self.assertContains(resp, app.application_id)
        self.assertContains(resp, "Girls of Steel FRC 2026-2027")

        # Thematic section names
        self.assertContains(resp, "Student Information")
        self.assertContains(resp, "Robotics &amp; Experience")
        self.assertContains(resp, "Primary Parent / Guardian")
        # Should not show raw step keys
        self.assertNotContains(resp, ">step5-student<")
        self.assertNotContains(resp, ">step6-experience<")
        self.assertNotContains(resp, ">step7-primaryparent<")

    def test_review_detail_progress_stepper_mentor(self):
        app = Application.objects.create(
            program=self.program,
            email="grace@example.com",
            applicant_type=Application.Type.MENTOR,
            status=Application.Status.APPROVED,
            submitted_at=timezone.now(),
            data={
                "mentor_info": {
                    "legal_first_name": "Grace",
                    "last_name": "Hopper",
                    "email": "grace@example.com",
                },
            },
        )

        resp = self.client.get(
            reverse("application_review_detail", kwargs={"app_id": app.application_id})
        )
        self.assertEqual(resp.status_code, 200)

        self.assertContains(resp, "Grace Hopper")
        self.assertContains(resp, "Mentor Application")
        self.assertContains(resp, "Clearances / Docs")
        self.assertContains(resp, "Converted to Mentor")
        self.assertContains(resp, "Mentor Information")
        self.assertNotContains(resp, ">mentor_info<")

    def test_review_detail_declined_state(self):
        app = Application.objects.create(
            program=self.program,
            email="declined@example.com",
            applicant_type=Application.Type.STUDENT,
            status=Application.Status.DECLINED,
            decline_reason="Ineligible grade level for this program.",
            submitted_at=timezone.now(),
            data={
                "step5-student": {
                    "legal_first_name": "Test",
                    "last_name": "Declined",
                }
            },
        )

        resp = self.client.get(
            reverse("application_review_detail", kwargs={"app_id": app.application_id})
        )
        self.assertEqual(resp.status_code, 200)

        self.assertContains(resp, "Application Declined")
        self.assertContains(resp, "Ineligible grade level for this program.")

    def test_review_detail_action_center_and_communications(self):
        app = Application.objects.create(
            program=self.program,
            email="app_actions@example.com",
            applicant_type=Application.Type.STUDENT,
            status=Application.Status.SUBMITTED,
            submitted_at=timezone.now(),
            data={
                "step5-student": {
                    "legal_first_name": "Action",
                    "last_name": "Test",
                }
            },
        )

        resp = self.client.get(
            reverse("application_review_detail", kwargs={"app_id": app.application_id})
        )
        self.assertEqual(resp.status_code, 200)

        # Action buttons
        self.assertContains(
            resp, reverse("application_review_approve", args=[app.application_id])
        )
        self.assertContains(
            resp, reverse("application_review_decline", args=[app.application_id])
        )
        self.assertContains(
            resp, reverse("application_review_edit", args=[app.application_id])
        )
        self.assertContains(
            resp, reverse("application_review_delete", args=[app.application_id])
        )

        # Communications resend options
        self.assertContains(
            resp, reverse("application_review_resend_email", args=[app.application_id])
        )
