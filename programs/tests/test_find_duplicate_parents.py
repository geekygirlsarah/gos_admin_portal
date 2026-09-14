"""Tests for the ``find_duplicate_parents`` management command.

The command surfaces ``Adult`` records flagged ``is_parent=True`` that look
like the same person. Old application-wizard bugs created duplicate parent
records during conversion (matching by email failed on a name variant and a
brand-new Adult was created, or a name-less parent was stored as
``(unknown)``). The command is read-only by default; ``--fix`` merges only the
unambiguous groups (same email + same name, or same name + same phone when
there is no email) into a single surviving record, reusing the exact same
merge logic as the portal's "Merge Parents" page.
"""

from io import StringIO

from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import TestCase

from audit.events import AuditEvent
from audit.models import AuditLog
from programs.models import Adult, AdultStudentRelationship, Student


def _parent(first="Jane", last="Doe", **kwargs):
    defaults = {
        "legal_first_name": first,
        "last_name": last,
        "is_parent": True,
        "login_enabled": True,
    }
    defaults.update(kwargs)
    return Adult.objects.create(**defaults)


def _student(first="Test", last="Student"):
    return Student.objects.create(
        legal_first_name=first,
        last_name=last,
        graduation_year=2027,
    )


def _link(adult, student):
    return AdultStudentRelationship.objects.create(adult=adult, student=student)


class FindDuplicateParentsTests(TestCase):
    def _run(self, fix=False):
        out = StringIO()
        call_command("find_duplicate_parents", fix=fix, stdout=out)
        return out.getvalue()

    # --- detection -----------------------------------------------------------

    def test_same_email_and_name_reported(self):
        _parent("Jane", "Doe", personal_email="jane@example.com")
        _parent("Jane", "Doe", personal_email="JANE@example.com")

        output = self._run()

        self.assertIn("jane@example.com", output)
        self.assertIn("#", output)

    def test_shared_email_with_different_names_is_not_a_duplicate(self):
        _parent("Jane", "Doe", personal_email="family@example.com")
        _parent("John", "Doe", personal_email="family@example.com")

        output = self._run()

        self.assertIn("No duplicate parents found", output)

    def test_different_last_names_are_not_duplicates(self):
        _parent("Jane", "Doe", personal_email="jane@example.com")
        _parent("Jane", "Smith", personal_email="jane@example.com")

        output = self._run()

        self.assertIn("No duplicate parents found", output)

    def test_same_name_and_phone_without_email_reported(self):
        _parent("Jane", "Doe", phone_number="(412) 555-1234")
        _parent("Jane", "Doe", phone_number="4125551234")

        output = self._run()

        self.assertIn("4125551234", output)

    def test_report_includes_relationship_and_specific_type(self):
        parent = _parent("Jane", "Doe", personal_email="jane@example.com")
        _parent("Jane", "Doe", personal_email="jane@example.com")
        student = _student()
        AdultStudentRelationship.objects.create(
            adult=parent,
            student=student,
            relationship_to_student="parent",
            specific_relationship="father",
        )

        output = self._run()

        self.assertIn("parent/father of Student", output)
        self.assertIn(f"Student #{student.pk}", output)

    def test_anonymous_placeholder_parent_reported_as_review_only(self):
        _parent("Jane", "Doe", personal_email="jane@example.com")
        _parent("(unknown)", "(unknown)", personal_email="jane@example.com")

        output = self._run()

        self.assertIn("(unknown)", output)
        self.assertIn("review", output.lower())

    def test_similar_first_name_with_shared_email_reported_as_review_only(self):
        _parent("Matthew", "Doe", personal_email="matt@example.com")
        _parent("Matt", "Doe", personal_email="matt@example.com")

        output = self._run()

        self.assertIn("Matt", output)
        self.assertIn("review", output.lower())

    def test_non_parent_adults_with_same_email_are_ignored(self):
        _parent("Jane", "Doe", is_parent=False, personal_email="jane@example.com")
        _parent("Jane", "Doe", is_parent=False, personal_email="jane@example.com")

        output = self._run()

        self.assertIn("No duplicate parents found", output)

    def test_clean_database_reports_success(self):
        output = self._run()
        self.assertIn("No duplicate parents found", output)

    # --- read-only by default ------------------------------------------------

    def test_report_only_never_merges(self):
        _parent("Jane", "Doe", personal_email="jane@example.com")
        source = _parent("Jane", "Doe", personal_email="jane@example.com")
        student = _student()
        _link(source, student)

        self._run()

        self.assertEqual(
            Adult.objects.filter(personal_email="jane@example.com").count(), 2
        )
        self.assertTrue(Adult.objects.filter(pk=source.pk).exists())

    # --- --fix ----------------------------------------------------------------

    def test_fix_merges_same_email_and_name_group(self):
        first = _parent("Jane", "Doe", personal_email="jane@example.com")
        second = _parent("Jane", "Doe", personal_email="JANE@example.com")
        student = _student()
        _link(second, student)

        output = self._run(fix=True)

        remaining = Adult.objects.filter(personal_email__iexact="jane@example.com")
        self.assertEqual(remaining.count(), 1)
        survivor = remaining.get()
        self.assertIn(survivor.pk, (first.pk, second.pk))
        self.assertTrue(
            AdultStudentRelationship.objects.filter(
                adult=survivor, student=student
            ).exists()
        )
        self.assertGreaterEqual(output.lower().count("merged"), 1)

    def test_fix_merges_no_email_phone_group(self):
        first = _parent("Jane", "Doe", phone_number="555-1234")
        second = _parent("Jane", "Doe", phone_number="5551234")
        student = _student()
        _link(second, student)

        self._run(fix=True)

        remaining = Adult.objects.filter(phone_number__in=["555-1234", "5551234"])
        self.assertEqual(remaining.count(), 1)
        survivor = remaining.get()
        self.assertIn(survivor.pk, (first.pk, second.pk))
        self.assertTrue(
            AdultStudentRelationship.objects.filter(
                adult=survivor, student=student
            ).exists()
        )

    def test_fix_preserves_both_students_when_row_relationship_exists(self):
        """Both duplicate rows may already be linked to different students
        (e.g. siblings) — the merge must keep both students linked."""
        keep = _parent("Jane", "Doe", personal_email="jane@example.com")
        source = _parent("Jane", "Doe", personal_email="jane@example.com")
        student_a = _student("Alex")
        student_b = _student("Bailey")
        _link(keep, student_a)
        _link(source, student_b)

        self._run(fix=True)

        self.assertFalse(Adult.objects.filter(pk=source.pk).exists())
        self.assertTrue(
            AdultStudentRelationship.objects.filter(
                adult=keep, student=student_a
            ).exists()
        )
        self.assertTrue(
            AdultStudentRelationship.objects.filter(
                adult=keep, student=student_b
            ).exists()
        )

    def test_fix_keeps_most_complete_record(self):
        sparse = _parent(
            "Jane", "Doe", personal_email="jane@example.com", phone_number="555-1234"
        )
        complete = _parent(
            "Jane",
            "Doe",
            personal_email="JANE@example.com",
            phone_number="555-9999",
            address="1 Main St",
            city="Pittsburgh",
        )

        self._run(fix=True)

        self.assertFalse(Adult.objects.filter(pk=sparse.pk).exists())
        complete.refresh_from_db()
        self.assertEqual(complete.phone_number, "555-9999")
        self.assertEqual(complete.address, "1 Main St")

    def test_fix_merges_role_flags(self):
        keep = _parent(
            "Jane",
            "Doe",
            personal_email="jane@example.com",
            is_parent=True,
            is_mentor=False,
        )
        _parent(
            "Jane",
            "Doe",
            personal_email="jane@example.com",
            is_parent=True,
            is_mentor=True,
        )

        self._run(fix=True)

        keep.refresh_from_db()
        self.assertTrue(keep.is_mentor)

    def test_fix_keeps_record_with_user_account(self):
        """A record linked to a login is more complete; the merge must keep
        that login regardless of which row is the survivor."""
        source_user = User.objects.create_user(
            username="source_user", password="password"  # nosec B106
        )
        _parent("Jane", "Doe", personal_email="jane@example.com")
        _parent("Jane", "Doe", personal_email="jane@example.com", user=source_user)

        self._run(fix=True)

        remaining = Adult.objects.filter(personal_email="jane@example.com")
        self.assertEqual(remaining.count(), 1)
        self.assertEqual(remaining.get().user, source_user)

    def test_fix_skips_group_when_multiple_members_have_login_accounts(self):
        """Two different people accidentally sharing an email AND a name (e.g.
        father and son) both have their own logins — never auto-merge those."""
        user_a = User.objects.create_user(
            username="jane_a", password="password"
        )  # nosec B106
        user_b = User.objects.create_user(
            username="jane_b", password="password"
        )  # nosec B106
        a = _parent("Jane", "Doe", personal_email="jane@example.com", user=user_a)
        b = _parent("Jane", "Doe", personal_email="jane@example.com", user=user_b)

        output = self._run(fix=True)

        self.assertTrue(Adult.objects.filter(pk=a.pk).exists())
        self.assertTrue(Adult.objects.filter(pk=b.pk).exists())
        self.assertIn("skipped", output.lower())

    def test_fix_never_touches_review_only_candidates(self):
        _parent("Jane", "Doe", personal_email="jane@example.com")
        anon = _parent("(unknown)", "(unknown)", personal_email="jane@example.com")
        jenny = _parent("Jenny", "Doe", personal_email="jane@example.com")

        self._run(fix=True)

        self.assertTrue(Adult.objects.filter(pk=anon.pk).exists())
        self.assertTrue(Adult.objects.filter(pk=jenny.pk).exists())

    def test_fix_logs_audit_event(self):
        keep = _parent("Jane", "Doe", personal_email="jane@example.com")
        source = _parent("Jane", "Doe", personal_email="jane@example.com")

        self._run(fix=True)

        audit = AuditLog.objects.filter(
            event=AuditEvent.RECORDS_MERGED,
            resource_type="Adult",
            resource_id=str(keep.pk),
        ).first()
        self.assertIsNotNone(audit)
        self.assertIn(str(source.pk), audit.notes)
