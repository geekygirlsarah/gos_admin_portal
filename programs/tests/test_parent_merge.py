from django.contrib.auth.models import Group, User
from django.test import Client, TestCase
from django.urls import reverse

from audit.events import AuditEvent
from audit.models import AuditLog
from programs.models import (
    Adult,
    AdultStudentRelationship,
    Student,
)


class ParentMergeTest(TestCase):
    """Tests for the parent merge / consolidation feature."""

    def setUp(self):
        self.user = User.objects.create_user(
            username="admin",
            password="password",  # nosec B106
        )
        group, _ = Group.objects.get_or_create(name="LeadMentor")
        self.user.groups.add(group)
        self.client = Client()
        self.client.force_login(self.user)

    def _parent(self, first_name="Jane", last_name="Doe", **kwargs):
        defaults = {
            "first_name": first_name,
            "last_name": last_name,
            "is_parent": True,
            "login_enabled": True,
        }
        defaults.update(kwargs)
        return Adult.objects.create(**defaults)

    def _student(self, first_name="Test", last_name="Student", **kwargs):
        defaults = {
            "legal_first_name": first_name,
            "last_name": last_name,
            "graduation_year": 2026,
        }
        defaults.update(kwargs)
        return Student.objects.create(**defaults)

    def _rel(self, adult, student, relationship="parent", specific=""):
        return AdultStudentRelationship.objects.create(
            adult=adult,
            student=student,
            relationship_to_student=relationship,
            specific_relationship=specific,
        )

    # --- GET page tests ---

    def test_merge_page_lists_parents_with_keep_and_source_options(self):
        p1 = self._parent("Jane", "Doe")
        p2 = self._parent("Janet", "Doe")

        response = self.client.get(reverse("parent_merge"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'name="keep" value="%s"' % p1.pk)
        self.assertContains(response, 'name="source" value="%s"' % p2.pk)
        self.assertContains(response, "Jane")
        self.assertContains(response, "Janet")

    # --- Core merge tests ---

    def test_merge_reassigns_relationships_and_deletes_source(self):
        keep = self._parent("Jane", "Doe")
        source = self._parent("Janet", "Doe")
        student = self._student()
        self._rel(source, student)

        response = self.client.post(
            reverse("parent_merge"),
            {"keep": keep.pk, "source": source.pk},
        )

        self.assertEqual(response.status_code, 302)
        self.assertFalse(Adult.objects.filter(pk=source.pk).exists())
        self.assertTrue(
            AdultStudentRelationship.objects.filter(
                adult=keep, student=student
            ).exists()
        )
        self.assertFalse(
            AdultStudentRelationship.objects.filter(
                adult=source, student=student
            ).exists()
        )

    def test_merge_preserves_keep_fields(self):
        keep = self._parent("Jane", "Doe", personal_email="jane@example.com")
        source = self._parent("Janet", "Doe", personal_email="janet@example.com")

        self.client.post(
            reverse("parent_merge"),
            {"keep": keep.pk, "source": source.pk},
        )

        keep.refresh_from_db()
        self.assertEqual(keep.personal_email, "jane@example.com")

    def test_merge_fills_missing_keep_fields_from_source(self):
        keep = self._parent("Jane", "Doe", phone_number="555-1234")
        source = self._parent(
            "Janet", "Doe", phone_number="555-5678", city="Pittsburgh"
        )

        self.client.post(
            reverse("parent_merge"),
            {"keep": keep.pk, "source": source.pk},
        )

        keep.refresh_from_db()
        self.assertEqual(keep.phone_number, "555-1234")
        self.assertEqual(keep.city, "Pittsburgh")

    def test_cannot_merge_parent_into_itself(self):
        keep = self._parent("Jane", "Doe")

        response = self.client.post(
            reverse("parent_merge"),
            {"keep": keep.pk, "source": keep.pk},
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(Adult.objects.filter(pk=keep.pk).exists())

    def test_merge_handles_shared_student(self):
        """Both parents relate to the same student — keep's relationship is
        preserved and source's is removed."""
        keep = self._parent("Jane", "Doe")
        source = self._parent("Janet", "Doe")
        student = self._student()
        self._rel(keep, student, specific="mother")
        self._rel(source, student, specific="stepmother")

        self.client.post(
            reverse("parent_merge"),
            {"keep": keep.pk, "source": source.pk},
        )

        self.assertFalse(Adult.objects.filter(pk=source.pk).exists())
        rel = AdultStudentRelationship.objects.get(adult=keep, student=student)
        self.assertEqual(rel.specific_relationship, "mother")

    def test_merge_updates_primary_contact_relationship(self):
        keep = self._parent("Jane", "Doe")
        source = self._parent("Janet", "Doe")
        student = self._student()
        source_rel = self._rel(source, student)
        student.primary_contact_relationship = source_rel
        student.save(update_fields=["primary_contact_relationship"])

        self.client.post(
            reverse("parent_merge"),
            {"keep": keep.pk, "source": source.pk},
        )

        student.refresh_from_db()
        self.assertEqual(student.primary_contact, keep)

    def test_merge_updates_secondary_contact_relationship(self):
        keep = self._parent("Jane", "Doe")
        source = self._parent("Janet", "Doe")
        student = self._student()
        source_rel = self._rel(source, student)
        student.secondary_contact_relationship = source_rel
        student.save(update_fields=["secondary_contact_relationship"])

        self.client.post(
            reverse("parent_merge"),
            {"keep": keep.pk, "source": source.pk},
        )

        student.refresh_from_db()
        self.assertEqual(student.secondary_contact, keep)

    def test_merge_transfers_user_account(self):
        keep = self._parent("Jane", "Doe")
        source_user = User.objects.create_user(
            username="source_user",
            password="password",  # nosec B106
        )
        source = self._parent("Janet", "Doe", user=source_user)

        self.client.post(
            reverse("parent_merge"),
            {"keep": keep.pk, "source": source.pk},
        )

        keep.refresh_from_db()
        self.assertEqual(keep.user, source_user)

    def test_merge_preserves_keep_user_account(self):
        keep_user = User.objects.create_user(
            username="keep_user",
            password="password",  # nosec B106
        )
        keep = self._parent("Jane", "Doe", user=keep_user)
        source_user = User.objects.create_user(
            username="source_user",
            password="password",  # nosec B106
        )
        source = self._parent("Janet", "Doe", user=source_user)

        self.client.post(
            reverse("parent_merge"),
            {"keep": keep.pk, "source": source.pk},
        )

        keep.refresh_from_db()
        self.assertEqual(keep.user, keep_user)

    def test_merge_merges_role_flags(self):
        keep = self._parent("Jane", "Doe", is_parent=True, is_mentor=False)
        source = self._parent("Janet", "Doe", is_parent=True, is_mentor=True)

        self.client.post(
            reverse("parent_merge"),
            {"keep": keep.pk, "source": source.pk},
        )

        keep.refresh_from_db()
        self.assertTrue(keep.is_parent)
        self.assertTrue(keep.is_mentor)

    # --- Permission test ---

    def test_merge_requires_lead_mentor_permission(self):
        regular_user = User.objects.create_user(
            username="regular",
            password="password",  # nosec B106
        )
        self.client.force_login(regular_user)

        response = self.client.get(reverse("parent_merge"))
        self.assertEqual(response.status_code, 302)

    # --- Audit logging test ---

    def test_merge_creates_audit_log(self):
        keep = self._parent("Jane", "Doe")
        source = self._parent("Janet", "Doe")
        student = self._student()
        self._rel(source, student)

        self.client.post(
            reverse("parent_merge"),
            {"keep": keep.pk, "source": source.pk},
        )

        audit = AuditLog.objects.filter(
            event=AuditEvent.RECORDS_MERGED,
            resource_type="Adult",
            resource_id=str(keep.pk),
        ).first()
        self.assertIsNotNone(audit)
        self.assertIn("Janet", audit.notes)
        self.assertIn("Jane", audit.notes)
