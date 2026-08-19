import json
import logging

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import RequestFactory, TestCase
from django.urls import reverse

from applications.models import Application
from audit.events import AuditEvent
from audit.logging_handlers import AuditStderrHandler, AuditStdoutHandler
from audit.models import AuditLog
from audit.service import log_event
from programs.models import (
    Adult,
    AdultStudentRelationship,
    Enrollment,
    Program,
    Student,
)

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


class AuditLoggingPrivacyTest(TestCase):
    def test_audit_failure_does_not_log_sensitive_values(self):
        # Simulate an exception during save that includes sensitive-looking content
        from unittest.mock import patch

        from audit.events import AuditEvent
        from audit.service import log_event

        sensitive_msg = "ValueError: password=secret123 should not appear"

        with patch("audit.models.AuditLog.save", side_effect=Exception(sensitive_msg)):
            # Use a real model instance as the resource to avoid unrelated AttributeError
            from django.contrib.auth import get_user_model

            User = get_user_model()
            dummy = User.objects.create_user(username="dummy", email="d@e.co")

            # Capture logs from the 'audit' logger
            with self.assertLogs("audit", level="ERROR") as cm:
                result = log_event(event=AuditEvent.PASSWORD_RESET, resource=dummy)

        output = "\n".join(cm.output)
        # Ensure function swallowed the error and returned None
        self.assertIsNone(result)
        # Ensure the sensitive content is not present in logs
        self.assertNotIn("password=secret123", output)
        # Ensure no traceback leaked
        self.assertNotIn("Traceback (most recent call last)", output)
        # Event should be logged as its enum name label only, not as raw object
        self.assertIn("event=PASSWORD_RESET", output)
        self.assertNotRegex(output, r"<AuditEvent\\.PASSWORD_RESET>")


class AuditLogConsoleSuppressionTest(TestCase):
    """
    While running unit tests, the 'audit' logger must not echo records to the
    console (stdout/stderr). Development-mode console output is intentional, but
    it pollutes the output of `python manage.py test`.
    """

    def test_audit_logger_does_not_reach_console_during_tests(self):
        logger = logging.getLogger("audit")

        # No stdout/stderr handlers wired up during test runs.
        handler_types = [type(h) for h in logger.handlers]
        self.assertNotIn(AuditStdoutHandler, handler_types)
        self.assertNotIn(AuditStderrHandler, handler_types)

        # Records must not bubble up to the root console handler.
        self.assertFalse(logger.propagate)

        # INFO (success) / WARNING (failure) records are suppressed outright.
        self.assertGreaterEqual(logger.level, logging.WARNING)


class GuardianRemovedSignalTest(TestCase):
    """Tests that deleting an AdultStudentRelationship emits GUARDIAN_REMOVED."""

    def setUp(self):
        self.student = Student.objects.create(
            legal_first_name="Jane", last_name="Doe", graduation_year=2026
        )
        self.adult = Adult.objects.create(
            first_name="John", last_name="Doe", is_parent=True
        )
        self.relationship = AdultStudentRelationship.objects.create(
            adult=self.adult,
            student=self.student,
            relationship_to_student="parent",
        )

    def test_delete_relationship_emits_guardian_removed(self):
        rel_pk = self.relationship.pk
        self.relationship.delete()

        log = AuditLog.objects.filter(
            event=AuditEvent.GUARDIAN_REMOVED,
            resource_type="AdultStudentRelationship",
            resource_id=str(rel_pk),
        ).first()
        self.assertIsNotNone(log, "GUARDIAN_REMOVED should be emitted on delete")
        self.assertIn("John Doe", log.notes)
        self.assertIn("Jane Doe", log.notes)

    def test_queryset_delete_emits_guardian_removed(self):
        rel_pk = self.relationship.pk
        AdultStudentRelationship.objects.filter(pk=rel_pk).delete()

        log = AuditLog.objects.filter(
            event=AuditEvent.GUARDIAN_REMOVED,
            resource_type="AdultStudentRelationship",
            resource_id=str(rel_pk),
        ).first()
        self.assertIsNotNone(log, "GUARDIAN_REMOVED should be emitted on queryset delete")

    def test_m2m_set_triggers_guardian_removed(self):
        """Simulates what StudentForm.save() does: adults.set(selected)."""
        student2 = Student.objects.create(
            legal_first_name="Jim", last_name="Doe", graduation_year=2027
        )
        adult2 = Adult.objects.create(
            first_name="Jane", last_name="Smith", is_parent=True
        )
        # student2 is related to BOTH adults initially
        AdultStudentRelationship.objects.create(
            adult=adult2, student=student2, relationship_to_student="parent"
        )
        rel = AdultStudentRelationship.objects.create(
            adult=self.adult, student=student2, relationship_to_student="parent"
        )
        rel_pk = rel.pk

        # Now call adults.set() which should remove the first adult
        student2.adults.set([adult2])

        log = AuditLog.objects.filter(
            event=AuditEvent.GUARDIAN_REMOVED,
            resource_type="AdultStudentRelationship",
            resource_id=str(rel_pk),
        ).first()
        self.assertIsNotNone(log, "GUARDIAN_REMOVED should fire for m2m set removals")


class SensitiveDataViewProgramContextTest(TestCase):
    """Tests that SENSITIVE_DATA_VIEW notes include program scope info."""

    def setUp(self):
        self.mentor_user = User.objects.create_user(
            username="mentorctx",
            email="mentorctx@example.com",
            password="password123",  # nosec B106
            is_staff=True,
        )
        lm_group, _ = Group.objects.get_or_create(name="LeadMentor")
        self.mentor_user.groups.add(lm_group)
        self.mentor_adult = Adult.objects.create(
            user=self.mentor_user,
            first_name="Mentor",
            last_name="Context",
            is_mentor=True,
        )

        self.program_a = Program.objects.create(
            name="Robotics", start_date="2024-01-01", end_date="2024-12-31"
        )
        self.program_b = Program.objects.create(
            name="CyberPatriot", start_date="2024-01-01", end_date="2024-12-31"
        )

        self.student = Student.objects.create(
            legal_first_name="Jane", last_name="Ctx", graduation_year=2026
        )
        Enrollment.objects.create(student=self.student, program=self.program_a)
        Enrollment.objects.create(student=self.student, program=self.program_b)

    def test_notes_include_student_programs(self):
        self.client.force_login(self.mentor_user)
        url = reverse("student_detail", kwargs={"pk": self.student.pk})
        self.client.get(url)

        log = AuditLog.objects.filter(
            event=AuditEvent.SENSITIVE_DATA_VIEW,
            actor=self.mentor_user,
            resource_type="Student",
        ).first()
        self.assertIsNotNone(log)
        self.assertIn("Robotics", log.notes)
        self.assertIn("CyberPatriot", log.notes)

    def test_notes_include_scope_label(self):
        self.client.force_login(self.mentor_user)
        url = reverse("student_detail", kwargs={"pk": self.student.pk})
        self.client.get(url)

        log = AuditLog.objects.filter(
            event=AuditEvent.SENSITIVE_DATA_VIEW,
            actor=self.mentor_user,
        ).first()
        self.assertIsNotNone(log)
        self.assertIn("SCOPE:", log.notes)


class AuditDigestCommandTest(TestCase):
    """Tests for the audit_digest management command."""

    def setUp(self):
        self.user = User.objects.create_user(
            username="digestuser", email="digest@example.com"
        )

    def test_command_runs_without_error(self):
        from io import StringIO

        from django.core.management import call_command

        out = StringIO()
        call_command("audit_digest", "--days", "7", stdout=out)
        output = out.getvalue()
        self.assertIn("Audit Digest", output)

    def test_command_detects_login_failures(self):
        from io import StringIO

        from django.core.management import call_command

        # Create some failed logins
        for i in range(5):
            AuditLog.objects.create(
                event=AuditEvent.LOGIN_FAILED,
                resource_type="User",
                resource_id="0",
                resource_repr=f"Non-existent user: bad{i}@example.com",
                ip_address="10.0.0.1",
                outcome=AuditLog.FAILURE,
            )

        out = StringIO()
        call_command("audit_digest", "--days", "7", "--threshold", "3", stdout=out)
        output = out.getvalue()
        self.assertIn("10.0.0.1", output)

    def test_command_detects_privilege_changes(self):
        from io import StringIO

        from django.core.management import call_command

        AuditLog.objects.create(
            event=AuditEvent.ROLE_CHANGED,
            resource_type="User",
            resource_id=str(self.user.pk),
            resource_repr=str(self.user),
            before={"is_staff": False},
            after={"is_staff": True},
            actor=self.user,
        )

        out = StringIO()
        call_command("audit_digest", "--days", "7", stdout=out)
        output = out.getvalue()
        self.assertIn("ROLE_CHANGED", output)

    def test_command_detects_guardian_removals(self):
        from io import StringIO

        from django.core.management import call_command

        AuditLog.objects.create(
            event=AuditEvent.GUARDIAN_REMOVED,
            resource_type="AdultStudentRelationship",
            resource_id="999",
            resource_repr="John Doe - parent to Jane Doe",
            notes="Guardian John Doe removed from student Jane Doe.",
        )

        out = StringIO()
        call_command("audit_digest", "--days", "7", stdout=out)
        output = out.getvalue()
        self.assertIn("GUARDIAN REMOVALS", output)
        self.assertIn("John Doe - parent to Jane Doe", output)


class AuditUtilsTest(TestCase):
    """Tests for audit/utils.py unified query helpers."""

    def setUp(self):
        self.user = User.objects.create_user(
            username="timelineuser", email="timeline@example.com"
        )

    def test_get_actor_timeline(self):
        from audit.utils import get_actor_timeline

        AuditLog.objects.create(
            event=AuditEvent.USER_LOGIN,
            resource_type="User",
            resource_id=str(self.user.pk),
            resource_repr=str(self.user),
            actor=self.user,
        )
        AuditLog.objects.create(
            event=AuditEvent.USER_LOGOUT,
            resource_type="User",
            resource_id=str(self.user.pk),
            resource_repr=str(self.user),
            actor=self.user,
        )

        timeline = get_actor_timeline(self.user, hours=1)
        self.assertEqual(timeline.count(), 2)

    def test_detect_session_anomalies_no_data(self):
        from audit.utils import detect_session_anomalies

        anomalies = detect_session_anomalies(hours=24)
        self.assertEqual(anomalies.count(), 0)

    def test_detect_session_anomalies_finds_mismatched_ips(self):
        from audit.utils import detect_session_anomalies

        AuditLog.objects.create(
            event=AuditEvent.USER_LOGIN,
            resource_type="User",
            resource_id=str(self.user.pk),
            resource_repr=str(self.user),
            actor=self.user,
            session_id="sess_abc123",
            ip_address="192.168.1.1",
        )
        AuditLog.objects.create(
            event=AuditEvent.USER_LOGIN,
            resource_type="User",
            resource_id=str(self.user.pk),
            resource_repr=str(self.user),
            actor=self.user,
            session_id="sess_abc123",
            ip_address="10.0.0.1",
        )

        anomalies = detect_session_anomalies(hours=1)
        self.assertGreater(anomalies.count(), 0)
