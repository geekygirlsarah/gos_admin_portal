"""Tests for the ``find_disconnected_accounts`` management command.

The command surfaces profiles whose ``user`` account link is missing and the
floating User accounts that no longer point at any profile (the casualties of
the old portal form silently NULL-ing ``Student.user``). It is read-only by
default; ``--fix`` fills NULL links only with unambiguous email/name matches
and never overwrites an existing link or steals a user from another profile.
"""

from io import StringIO

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase

from audit.events import AuditEvent
from audit.models import AuditLog
from programs.models import Adult, Student

User = get_user_model()


class FindDisconnectedAccountsTests(TestCase):
    def _run(self, fix=False):
        out = StringIO()
        call_command("find_disconnected_accounts", fix=fix, stdout=out)
        return out.getvalue()

    def test_reports_match_read_only_and_does_not_link(self):
        student = Student.objects.create(
            legal_first_name="Alex",
            last_name="Morgan",
            personal_email="alex@example.com",
        )
        User.objects.create_user(
            username="alex@example.com",
            email="alex@example.com",
            first_name="Alex",
            last_name="Morgan",
            password="password",  # nosec B106
        )

        output = self._run()

        self.assertIn("alex@example.com", output)
        self.assertIn("would link", output.lower())
        student.refresh_from_db()
        self.assertIsNone(student.user_id)

    def test_fix_links_student_by_email(self):
        student = Student.objects.create(
            legal_first_name="Alex",
            last_name="Morgan",
            personal_email="alex@example.com",
        )
        user = User.objects.create_user(
            username="alex@example.com",
            email="alex@example.com",
            first_name="Alex",
            last_name="Morgan",
            password="password",  # nosec B106
        )

        output = self._run(fix=True)

        student.refresh_from_db()
        self.assertEqual(student.user_id, user.pk)
        self.assertIn("linked", output.lower())
        self.assertTrue(
            AuditLog.objects.filter(
                event=AuditEvent.PROFILE_LINK_CHANGED,
                resource_type="Student",
                resource_id=str(student.pk),
            ).exists()
        )

    def test_fix_links_adult_by_email(self):
        adult = Adult.objects.create(
            legal_first_name="Pat",
            last_name="Morgan",
            personal_email="pat@example.com",
        )
        user = User.objects.create_user(
            username="pat@example.com",
            email="pat@example.com",
            first_name="Pat",
            last_name="Morgan",
            password="password",  # nosec B106
        )

        self._run(fix=True)

        adult.refresh_from_db()
        self.assertEqual(adult.user_id, user.pk)

    def test_fix_falls_back_to_name_match_when_no_email(self):
        student = Student.objects.create(
            legal_first_name="Sam",
            last_name="Rivera",
            personal_email="",
        )
        user = User.objects.create_user(
            username="sam_rivera",
            email="sam_rivera@noreply.example",
            first_name="Sam",
            last_name="Rivera",
            password="password",  # nosec B106
        )

        self._run(fix=True)

        student.refresh_from_db()
        self.assertEqual(student.user_id, user.pk)

    def test_fix_never_overwrites_existing_link(self):
        existing = User.objects.create_user(
            username="existing@example.com",
            email="existing@example.com",
            first_name="Alex",
            last_name="Morgan",
            password="password",  # nosec B106
        )
        student = Student.objects.create(
            legal_first_name="Alex",
            last_name="Morgan",
            personal_email="alex@example.com",
            user=existing,
        )
        User.objects.create_user(
            username="alex@example.com",
            email="alex@example.com",
            first_name="Alex",
            last_name="Morgan",
            password="password",  # nosec B106
        )

        self._run(fix=True)

        student.refresh_from_db()
        self.assertEqual(student.user_id, existing.pk)

    def test_fix_never_steals_user_already_linked_to_another_profile(self):
        owned = User.objects.create_user(
            username="owned@example.com",
            email="owned@example.com",
            first_name="Alex",
            last_name="Morgan",
            password="password",  # nosec B106
        )
        Student.objects.create(
            legal_first_name="Alex",
            last_name="Morgan",
            user=owned,
        )
        unlinked = Student.objects.create(
            legal_first_name="Alex",
            last_name="Morgan",
        )

        self._run(fix=True)

        unlinked.refresh_from_db()
        self.assertIsNone(unlinked.user_id)

    def test_ambiguous_name_matches_are_skipped(self):
        user = User.objects.create_user(
            username="sam_lee",
            email="sam_lee@noreply.example",
            first_name="Sam",
            last_name="Lee",
            password="password",  # nosec B106
        )
        s1 = Student.objects.create(legal_first_name="Sam", last_name="Lee")
        s2 = Student.objects.create(legal_first_name="Sam", last_name="Lee")

        self._run(fix=True)

        assert s1.pk
        assert s2.pk
        # Neither ambiguous candidate may be linked to the shared account.
        self.assertIsNone(Student.objects.get(pk=s1.pk).user_id)
        self.assertIsNone(Student.objects.get(pk=s2.pk).user_id)
        # And the user must remain unattached to a profile.
        self.assertFalse(Student.objects.filter(user=user).exists())

    def test_floating_user_with_no_candidate_is_reported(self):
        User.objects.create_user(
            username="float@example.com",
            email="float@example.com",
            first_name="Nobody",
            last_name="Knows",
            password="password",  # nosec B106
        )

        output = self._run(fix=True)

        self.assertIn("float@example.com", output)

    def test_clean_database_reports_success(self):
        output = self._run()
        self.assertIn("No disconnected accounts", output)
