"""Audit logging for User ↔ Student/Adult profile link changes.

The log_event entry is emitted from a pre_save signal whenever Student.user_id
or Adult.user_id changes, regardless of which code path triggered the save.
"""

from django.contrib.auth import get_user_model
from django.test import TestCase

from audit.events import AuditEvent
from audit.models import AuditLog
from programs.models import Adult, Student

User = get_user_model()


class StudentUserLinkAuditTests(TestCase):
    def test_linking_student_to_user_creates_audit_row(self):
        user = User.objects.create_user(username="link1", password="x")  # nosec B106
        student = Student.objects.create(legal_first_name="Alex", last_name="Morgan")
        student.user = user
        student.save(update_fields=["user"])

        rows = AuditLog.objects.filter(
            event=AuditEvent.PROFILE_LINK_CHANGED,
            resource_type="Student",
            resource_id=str(student.pk),
        )
        self.assertEqual(rows.count(), 1)
        row = rows.first()
        self.assertEqual(row.before, {"user_id": None})
        self.assertEqual(row.after, {"user_id": user.pk})

    def test_unlinking_student_creates_audit_row(self):
        user = User.objects.create_user(username="unlink1", password="x")  # nosec B106
        student = Student.objects.create(
            legal_first_name="Bob", last_name="Smith", user=user
        )
        student.user = None
        student.save(update_fields=["user"])

        rows = AuditLog.objects.filter(
            event=AuditEvent.PROFILE_LINK_CHANGED,
            resource_type="Student",
            resource_id=str(student.pk),
        )
        self.assertEqual(rows.count(), 1)
        row = rows.first()
        self.assertEqual(row.before, {"user_id": user.pk})
        self.assertEqual(row.after, {"user_id": None})

    def test_changing_student_user_logs_both_before_and_after(self):
        user1 = User.objects.create_user(username="old", password="x")  # nosec B106
        user2 = User.objects.create_user(username="new", password="x")  # nosec B106
        student = Student.objects.create(
            legal_first_name="C", last_name="D", user=user1
        )
        student.user = user2
        student.save(update_fields=["user"])

        row = (
            AuditLog.objects.filter(
                event=AuditEvent.PROFILE_LINK_CHANGED,
                resource_type="Student",
                resource_id=str(student.pk),
            )
            .order_by("id")
            .last()
        )
        self.assertEqual(row.before, {"user_id": user1.pk})
        self.assertEqual(row.after, {"user_id": user2.pk})

    def test_no_audit_row_when_user_link_unchanged(self):
        user = User.objects.create_user(username="same", password="x")  # nosec B106
        student = Student.objects.create(legal_first_name="E", last_name="F", user=user)
        # Save without changing user
        student.legal_first_name = "E2"
        student.save(update_fields=["legal_first_name"])
        self.assertEqual(
            AuditLog.objects.filter(
                event=AuditEvent.PROFILE_LINK_CHANGED,
                resource_type="Student",
                resource_id=str(student.pk),
            ).count(),
            0,
        )


class AdultUserLinkAuditTests(TestCase):
    def test_linking_adult_to_user_creates_audit_row(self):
        user = User.objects.create_user(
            username="adultlink", password="x"
        )  # nosec B106
        adult = Adult.objects.create(
            legal_first_name="M", last_name="N", is_parent=True
        )
        adult.user = user
        adult.save(update_fields=["user"])

        rows = AuditLog.objects.filter(
            event=AuditEvent.PROFILE_LINK_CHANGED,
            resource_type="Adult",
            resource_id=str(adult.pk),
        )
        self.assertEqual(rows.count(), 1)
        row = rows.first()
        self.assertEqual(row.before, {"user_id": None})
        self.assertEqual(row.after, {"user_id": user.pk})

    def test_unlinking_adult_creates_audit_row(self):
        user = User.objects.create_user(
            username="adultunlink", password="x"
        )  # nosec B106
        adult = Adult.objects.create(
            legal_first_name="O", last_name="P", is_parent=True, user=user
        )
        adult.user = None
        adult.save(update_fields=["user"])

        rows = AuditLog.objects.filter(
            event=AuditEvent.PROFILE_LINK_CHANGED,
            resource_type="Adult",
            resource_id=str(adult.pk),
        )
        self.assertEqual(rows.count(), 1)
        row = rows.first()
        self.assertEqual(row.before, {"user_id": user.pk})
        self.assertEqual(row.after, {"user_id": None})
