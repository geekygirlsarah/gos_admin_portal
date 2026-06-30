import json

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import RequestFactory, TestCase
from django.urls import reverse

from applications.models import Application
from audit.events import AuditEvent
from audit.models import AuditLog
from audit.service import log_event
from programs.models import Adult, Enrollment, Program, Student

User = get_user_model()


class AuditLogTest(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        # Admin user
        self.admin_user = User.objects.create_superuser(
            username="admin", email="admin@example.com", password="password"
        )  # nosec B106
        # Lead mentor user
        self.lead_user = User.objects.create_user(
            username="lead",
            email="lead@example.com",
            password="password",
            is_staff=True,
        )  # nosec B106
        self.lead_group, _ = Group.objects.get_or_create(name="LeadMentor")
        self.lead_user.groups.add(self.lead_group)

        self.program = Program.objects.create(
            name="Test Program", start_date="2024-01-01", end_date="2024-12-31"
        )

    def test_log_event_manual(self):
        log_event(
            event=AuditEvent.ACCOUNT_CREATED,
            resource=self.lead_user,
            notes="Manual log test",
        )
        log = AuditLog.objects.filter(event=AuditEvent.ACCOUNT_CREATED).first()
        self.assertIsNotNone(log)
        self.assertEqual(log.notes, "Manual log test")
        self.assertEqual(log.resource_repr, str(self.lead_user))

    def test_account_deactivation_signal(self):
        user = User.objects.create_user(username="testuser", email="test@example.com")
        user.is_active = False
        user.save()

        log = AuditLog.objects.filter(
            event=AuditEvent.ACCOUNT_DEACTIVATED, resource_id=str(user.pk)
        ).first()
        self.assertIsNotNone(log)
        self.assertEqual(log.after["is_active"], False)

    def test_role_change_signal(self):
        user = User.objects.create_user(username="staffuser", email="staff@example.com")
        user.is_staff = True
        user.save()

        log = AuditLog.objects.filter(
            event=AuditEvent.ROLE_CHANGED, resource_id=str(user.pk)
        ).first()
        self.assertIsNotNone(log)
        self.assertEqual(log.after["is_staff"], True)

    def test_admission_decision_logging(self):
        app = Application.objects.create(
            email="applicant@example.com",
            program=self.program,
            status=Application.Status.SUBMITTED,
            data={"step5-student": {"legal_first_name": "Applicant"}},
        )

        self.client.force_login(self.admin_user)
        url = reverse(
            "application_review_approve", kwargs={"app_id": app.application_id}
        )
        self.client.post(url)

        log = AuditLog.objects.filter(
            event=AuditEvent.ADMISSION_DECISION, resource_id=str(app.pk)
        ).first()
        self.assertIsNotNone(log)
        self.assertEqual(log.after["status"], Application.Status.APPROVED)

    def test_audit_admin_access(self):
        url = "/admin/audit/auditlog/"

        # Regular lead mentor should have access
        self.client.force_login(self.lead_user)
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

        # Random user (even if staff) should NOT unless lead mentor
        other_user = User.objects.create_user(
            username="other", email="other@example.com", is_staff=True
        )
        self.client.force_login(other_user)
        response = self.client.get(url)
        # Since it's admin, if they are staff but not permitted, they might get redirected or 403.
        # AuditLogAdmin has has_view_permission which returns _is_lead_mentor.
        self.assertEqual(response.status_code, 403)

    def test_audit_export_csv(self):
        log_event(
            event=AuditEvent.ACCOUNT_CREATED,
            resource=self.lead_user,
            notes="Export test",
        )

        self.client.force_login(self.admin_user)
        url = "/admin/audit/auditlog/export/"
        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "text/csv")
        content = response.content.decode()
        self.assertIn("Export test", content)
        self.assertIn("ACCOUNT_CREATED", content)


class AuthenticationAuditLogTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="testuser", email="testuser@example.com", password="password123"
        )  # nosec B106
        self.mentor = User.objects.create_user(
            username="mentor",
            email="mentor@example.com",
            password="password123",
            is_staff=True,
        )  # nosec B106
        lm_group, _ = Group.objects.get_or_create(name="LeadMentor")
        self.mentor.groups.add(lm_group)

        Adult.objects.create(
            user=self.mentor, first_name="Mentor", last_name="User", is_mentor=True
        )

        self.student = Student.objects.create(
            legal_first_name="Jane", last_name="Doe", graduation_year=2025
        )
        self.parent = Adult.objects.create(first_name="John", last_name="Doe")

    def test_login_logging(self):
        self.client.login(username="testuser", password="password123")  # nosec B106
        log = AuditLog.objects.filter(
            event=AuditEvent.USER_LOGIN, actor=self.user
        ).first()
        self.assertIsNotNone(log, "Login should be logged")
        self.assertEqual(log.outcome, AuditLog.SUCCESS)

    def test_logout_logging(self):
        self.client.login(username="testuser", password="password123")  # nosec B106
        self.client.logout()
        log = AuditLog.objects.filter(
            event=AuditEvent.USER_LOGOUT, actor=self.user
        ).first()
        self.assertIsNotNone(log, "Logout should be logged")

    def test_login_failure_logging(self):
        self.client.login(username="testuser", password="wrongpassword")  # nosec B106
        log = AuditLog.objects.filter(event=AuditEvent.LOGIN_FAILED).first()
        self.assertIsNotNone(log, "Failed login should be logged")
        self.assertEqual(log.outcome, AuditLog.FAILURE)

    def test_mentor_view_student_detail(self):
        self.client.force_login(self.mentor)
        url = reverse("student_detail", kwargs={"pk": self.student.pk})
        self.client.get(url, HTTP_REFERER="http://testserver/")
        log = AuditLog.objects.filter(
            event=AuditEvent.SENSITIVE_DATA_VIEW,
            actor=self.mentor,
            resource_type="Student",
            resource_id=str(self.student.pk),
        ).first()
        self.assertIsNotNone(log, "Mentor viewing student detail should be logged")

    def test_mentor_view_parent_edit(self):
        from django.contrib.auth.models import Permission

        change_adult_perm = Permission.objects.get(
            codename="change_adult", content_type__app_label="programs"
        )
        self.mentor.user_permissions.add(change_adult_perm)

        self.client.force_login(self.mentor)
        url = reverse("adult_edit", kwargs={"pk": self.parent.pk})
        self.client.get(url, HTTP_REFERER="http://testserver/")
        log = AuditLog.objects.filter(
            event=AuditEvent.SENSITIVE_DATA_VIEW,
            actor=self.mentor,
            resource_type="Adult",
            resource_id=str(self.parent.pk),
        ).first()
        self.assertIsNotNone(log, "Mentor viewing parent data should be logged")

    def test_non_mentor_view_no_log(self):
        regular_user = User.objects.create_user(
            username="regular", email="reg@example.com"
        )
        self.client.force_login(regular_user)
        url = reverse("student_detail", kwargs={"pk": self.student.pk})
        self.client.get(url)
        log = AuditLog.objects.filter(
            event=AuditEvent.SENSITIVE_DATA_VIEW, actor=regular_user
        ).first()
        self.assertIsNone(
            log,
            "Regular user viewing data should not be logged under SENSITIVE_DATA_VIEW",
        )
