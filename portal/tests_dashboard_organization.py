import datetime
import uuid

from cryptography.fernet import Fernet
from django.contrib.auth.models import Group, User
from django.http import Http404
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from applications.models import Application
from badges.models import Badge, StudentBadge
from orders.models import Order, OrderItem
from organizations.models import Organization, UserOrganizationMembership
from portal.views import DashboardView, MyProfileView
from programs.models import (
    Adult,
    AdultStudentRelationship,
    EmailDeliveryEvent,
    Enrollment,
    Program,
    ProgramFeature,
    Student,
    TrustedOutgoingEmailRecipient,
)
from programs.utils.tosend_webhooks import _email_fingerprint

_FILE_ENCRYPTION_KEY = Fernet.generate_key().decode()


@override_settings(FILE_ENCRYPTION_KEY=_FILE_ENCRYPTION_KEY)
class DashboardOrganizationTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.get(slug="gos")
        self.other_org = Organization.objects.create(name="Other", slug="other")
        self.user = User.objects.create_user(username="dashboard-user")
        UserOrganizationMembership.objects.create(
            user=self.user, organization=self.org, role="Staff"
        )
        today = timezone.localdate()
        self.program = Program.objects.create(
            organization=self.org,
            name="Local Program",
            start_date=today - datetime.timedelta(days=1),
            end_date=today + datetime.timedelta(days=1),
        )
        self.other_program = Program.objects.create(
            organization=self.other_org,
            name="Foreign Program",
            start_date=today - datetime.timedelta(days=1),
            end_date=today + datetime.timedelta(days=1),
        )

    def dashboard(self):
        self.client.force_login(self.user)
        return self.client.get(reverse("profile_dashboard"))

    def student(self, organization, name, user=None):
        return Student.objects.create(
            organization=organization,
            legal_first_name=name,
            last_name="Example",
            user=user,
        )

    def adult(self, organization, **kwargs):
        return Adult.objects.create(
            organization=organization,
            user=self.user,
            legal_first_name="Adult",
            last_name="Example",
            **kwargs,
        )

    def test_pending_applications_use_only_local_profiles_and_applications(self):
        Student.objects.create(
            user=self.user,
            organization=self.org,
            legal_first_name="Student",
            last_name="Example",
            personal_email="student@example.com",
        )
        self.adult(
            self.other_org, personal_email="other-parent@example.com", is_parent=True
        )
        local = Application.objects.create(
            organization=self.org,
            program=self.program,
            email="student@example.com",
            status=Application.Status.DRAFT,
        )
        foreign = Application.objects.create(
            organization=self.other_org,
            program=self.other_program,
            email="student@example.com",
            status=Application.Status.DRAFT,
        )
        foreign_profile_match = Application.objects.create(
            organization=self.org,
            program=self.program,
            email="other-parent@example.com",
            status=Application.Status.DRAFT,
        )
        mismatched_program = Application.objects.create(
            organization=self.org,
            program=self.other_program,
            email="student@example.com",
            status=Application.Status.DRAFT,
        )

        response = self.dashboard()
        pending = response.context["pending_applications"]
        self.assertIn(local, pending)
        self.assertNotIn(foreign, pending)
        self.assertNotIn(foreign_profile_match, pending)
        self.assertNotIn(mismatched_program, pending)

    def test_lead_counts_only_local_pending_orders_and_email_issues(self):
        group, _ = Group.objects.get_or_create(name="LeadMentor")
        self.user.groups.add(group)
        UserOrganizationMembership.objects.create(
            user=self.user, organization=self.org, role="LeadMentor"
        )
        OrderItem.objects.create(organization=self.org, item_name="Local pending")
        OrderItem.objects.create(
            organization=self.other_org, item_name="Foreign pending"
        )
        order = Order.objects.create(organization=self.org)
        OrderItem.objects.create(
            organization=self.org, item_name="Grouped", order=order
        )
        for org, event_type, resolved in [
            (self.org, EmailDeliveryEvent.EVENT_BOUNCED, False),
            (self.org, EmailDeliveryEvent.EVENT_COMPLAINT, False),
            (self.org, EmailDeliveryEvent.EVENT_BOUNCED, True),
            (self.org, EmailDeliveryEvent.EVENT_DELIVERED, False),
            (self.other_org, EmailDeliveryEvent.EVENT_COMPLAINT, False),
        ]:
            recipient_email = "example@example.com"
            message_id = f"dashboard-message-{uuid.uuid4().hex}"
            recipient_fingerprint = _email_fingerprint(recipient_email)
            mapping = TrustedOutgoingEmailRecipient.objects.create(
                organization=org,
                provider_message_id=message_id,
                recipient_email=recipient_email,
                recipient_fingerprint=recipient_fingerprint,
            )
            EmailDeliveryEvent.objects.create(
                organization=org,
                recipient_email=recipient_email,
                recipient_fingerprint=recipient_fingerprint,
                message_id=message_id,
                trusted_mapping=mapping,
                event_type=event_type,
                resolved=resolved,
                is_verified=True,
            )

        response = self.dashboard()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["lead_stats"]["pending_orders_count"], 1)
        self.assertEqual(
            response.context["lead_stats"]["pending_email_issues_count"], 2
        )

    def test_foreign_profiles_are_not_displayed_or_redirected(self):
        self.student(self.other_org, "ForeignStudent", self.user)
        self.adult(self.other_org, is_parent=True, is_alumni=True)
        response = self.dashboard()
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.context["student"])
        self.assertIsNone(response.context["adult"])
        self.assertNotContains(response, "ForeignStudent")
        self.assertRedirects(
            self.client.get(reverse("my_profile")), reverse("profile_dashboard")
        )

    def test_profile_redirect_prefers_local_student_then_local_adult(self):
        student = self.student(self.org, "LocalStudent", self.user)
        adult = self.adult(self.org, is_parent=True)
        self.client.force_login(self.user)
        response = self.client.get(reverse("my_profile"))
        self.assertEqual(response.url, reverse("student_detail", args=[student.pk]))
        student.organization = self.other_org
        student.save()
        response = self.client.get(reverse("my_profile"))
        self.assertEqual(response.url, reverse("adult_detail", args=[adult.pk]))

    def test_student_enrollments_require_local_enrollment_and_program(self):
        student = self.student(self.org, "LocalStudent", self.user)
        local = Enrollment.objects.create(
            organization=self.org, student=student, program=self.program
        )
        Enrollment.objects.create(
            organization=self.org, student=student, program=self.other_program
        )
        foreign_owned_program = Program.objects.create(
            organization=self.org, name="Foreign Enrollment Program"
        )
        Enrollment.objects.create(
            organization=self.other_org, student=student, program=foreign_owned_program
        )
        response = self.dashboard()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(list(response.context["student_enrollments"]), [local])
        self.assertContains(response, "Local Program")
        self.assertNotContains(response, "Foreign Program")
        self.assertNotContains(response, "Foreign Enrollment Program")

    def test_parent_relationships_and_enrollments_are_tenant_scoped(self):
        parent = self.adult(self.org, is_parent=True)
        local = self.student(self.org, "LocalChild")
        foreign = self.student(self.other_org, "ForeignChild")
        wrong_relation = self.student(self.org, "WrongRelationship")
        for organization, student in [
            (self.org, local),
            (self.org, foreign),
            (self.other_org, wrong_relation),
        ]:
            AdultStudentRelationship.objects.create(
                organization=organization, adult=parent, student=student
            )
        enrollment = Enrollment.objects.create(
            organization=self.org, student=local, program=self.program
        )
        Enrollment.objects.create(
            organization=self.org, student=local, program=self.other_program
        )
        response = self.dashboard()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [row["student"] for row in response.context["parent_data"]], [local]
        )
        self.assertEqual(
            [
                row["enrollment"]
                for row in response.context["parent_data"][0]["program_rows"]
            ],
            [enrollment],
        )
        self.assertContains(response, "LocalChild")
        self.assertNotContains(response, "ForeignChild")
        self.assertNotContains(response, "WrongRelationship")
        self.assertNotContains(response, "Foreign Program")

    def test_alumni_history_requires_local_student_and_enrollment(self):
        student = self.student(self.org, "FormerLocal")
        alumni = self.adult(self.org, is_alumni=True, student_record=student)
        local = Enrollment.objects.create(
            organization=self.org, student=student, program=self.program
        )
        Enrollment.objects.create(
            organization=self.org, student=student, program=self.other_program
        )
        response = self.dashboard()
        self.assertEqual(list(response.context["enrollments"]), [local])
        self.assertNotContains(response, "Foreign Program")

        foreign = self.student(self.other_org, "FormerForeign")
        alumni.student_record = foreign
        alumni.save()
        Enrollment.objects.create(
            organization=self.org, student=foreign, program=self.program
        )
        response = self.dashboard()
        self.assertIsNone(response.context["student_record"])
        self.assertEqual(response.context["enrollments"], [])
        self.assertNotContains(response, "FormerForeign")

    def test_badges_require_local_enrollment_award_and_badge(self):
        student = self.student(self.org, "LocalStudent", self.user)
        feature = ProgramFeature.objects.create(
            organization=self.other_org, key="badges", name="Badges"
        )
        self.other_program.features.add(feature)
        Enrollment.objects.create(
            organization=self.org, student=student, program=self.other_program
        )
        response = self.dashboard()
        self.assertFalse(response.context["has_badges"])

        local_feature, _ = ProgramFeature.objects.get_or_create(
            organization=self.org, key="badges", defaults={"name": "Badges"}
        )
        self.program.features.add(local_feature)
        Enrollment.objects.create(
            organization=self.org, student=student, program=self.program
        )
        local_badge = Badge.objects.create(organization=self.org, name="Local Badge")
        foreign_badge = Badge.objects.create(
            organization=self.other_org, name="Foreign Badge"
        )
        award = StudentBadge.objects.create(
            organization=self.org, student=student, badge=local_badge
        )
        StudentBadge.objects.create(
            organization=self.other_org, student=student, badge=local_badge
        )
        StudentBadge.objects.create(
            organization=self.org, student=student, badge=foreign_badge
        )
        response = self.dashboard()
        self.assertTrue(response.context["has_badges"])
        self.assertEqual(list(response.context["badges_earned"]), [award])
        self.assertEqual(response.context["navbar_badge_count"], 1)

    def test_parent_badges_exclude_foreign_children_and_awards(self):
        parent = self.adult(self.org, is_parent=True)
        child = self.student(self.org, "LocalChild")
        foreign = self.student(self.other_org, "ForeignChild")
        for student in (child, foreign):
            AdultStudentRelationship.objects.create(
                organization=self.org, adult=parent, student=student
            )
        feature, _ = ProgramFeature.objects.get_or_create(
            organization=self.org, key="badges", defaults={"name": "Badges"}
        )
        self.program.features.add(feature)
        Enrollment.objects.create(
            organization=self.org, student=child, program=self.program
        )
        local_badge = Badge.objects.create(organization=self.org, name="Local Badge")
        foreign_badge = Badge.objects.create(
            organization=self.other_org, name="Foreign Badge"
        )
        award = StudentBadge.objects.create(
            organization=self.org, student=child, badge=local_badge
        )
        StudentBadge.objects.create(
            organization=self.org, student=child, badge=foreign_badge
        )
        StudentBadge.objects.create(
            organization=self.other_org, student=child, badge=local_badge
        )
        StudentBadge.objects.create(
            organization=self.other_org, student=foreign, badge=foreign_badge
        )

        response = self.dashboard()
        self.assertTrue(response.context["has_badges"])
        self.assertEqual(response.context["badges_by_student"], {child: [award]})
        self.assertEqual(response.context["parent_data"][0]["badges"], [award])
        self.assertEqual(response.context["navbar_badge_count"], 1)

    def test_missing_organization_fails_closed(self):
        request = RequestFactory().get("/")
        request.user = self.user
        for view in (DashboardView, MyProfileView):
            for missing_attribute in (True, False):
                if not missing_attribute:
                    request.organization = None
                with self.subTest(
                    view=view.__name__, missing_attribute=missing_attribute
                ):
                    with self.assertRaises(Http404):
                        view.as_view()(request)
                if not missing_attribute:
                    del request.organization
