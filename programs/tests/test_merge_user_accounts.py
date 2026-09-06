"""Tests for the ``merge_user_accounts`` management command.

Separate User accounts can accumulate for the same person (one keyed by a
personal email, one by their Andrew email), usually when students/parents were
provisioned a fresh account for a second email. The command folds a "source"
duplicate account into a surviving "target" account: it moves allauth email
addresses, groups and permissions, repoints every FK reference, fills empty
name fields, and then removes the source account.

Read-only by default; ``--execute`` actually performs the merge.
"""

from io import StringIO

from allauth.account.models import EmailAddress
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.management import call_command
from django.test import TestCase

from badges.models import Badge, StudentBadge
from programs.models import Student

User = get_user_model()


class MergeUserAccountsTests(TestCase):
    def _run(self, source, target, execute=False):
        out = StringIO()
        call_command(
            "merge_user_accounts",
            source=source.pk,
            target=target.pk,
            execute=execute,
            stdout=out,
        )
        return out.getvalue()

    def _users(self, personal="alice@example.com", andrew="alice@andrew.cmu.edu"):
        # The person has two accounts: one keyed by each email.
        andrew_user = User.objects.create_user(
            username=andrew, email=andrew, first_name="Alice", last_name="Zhou"
        )
        EmailAddress.objects.create(
            user=andrew_user, email=andrew, verified=True, primary=True
        )
        personal_user = User.objects.create_user(
            username=personal, email=personal, password="password"  # nosec B106
        )
        EmailAddress.objects.create(
            user=personal_user, email=personal, verified=True, primary=True
        )
        return personal_user, andrew_user

    # ── read-only default ────────────────────────────────────────────────

    def test_read_only_by_default(self):
        personal, andrew = self._users()
        output = self._run(personal, andrew)

        self.assertIn("dry run", output.lower())
        self.assertTrue(User.objects.filter(pk=personal.pk).exists())
        self.assertTrue(User.objects.filter(pk=andrew.pk).exists())
        self.assertTrue(
            EmailAddress.objects.filter(user=personal, email=personal).exists()
        )

    # ── EmailAddress consolidation ───────────────────────────────────────

    def test_emails_move_to_target_and_source_deleted(self):
        personal, andrew = self._users()

        self._run(personal, andrew, execute=True)

        # Both emails now live on the surviving (andrew) user; exactly one primary.
        self.assertTrue(
            EmailAddress.objects.filter(
                user=andrew, email=andrew, verified=True
            ).exists()
        )
        self.assertTrue(
            EmailAddress.objects.filter(
                user=andrew, email=personal, verified=True
            ).exists()
        )
        self.assertEqual(
            EmailAddress.objects.filter(user=andrew, primary=True).count(), 1
        )
        # The source user is gone.
        self.assertFalse(User.objects.filter(pk=personal.pk).exists())
        self.assertEqual(EmailAddress.objects.filter(email=personal).count(), 1)

    # ── Groups / permissions M2M ─────────────────────────────────────────

    def test_groups_and_permissions_move_to_target(self):
        personal, andrew = self._users()
        group = Group.objects.create(name="MentorGroup")
        personal.groups.add(group)
        from django.contrib.auth.models import Permission

        perm = Permission.objects.get(codename="add_student")
        personal.user_permissions.add(perm)

        self._run(personal, andrew, execute=True)

        andrew.refresh_from_db()
        self.assertIn(group, andrew.groups.all())
        self.assertTrue(andrew.user_permissions.filter(id=perm.id).exists())

    # ── FK references repointed ──────────────────────────────────────────

    def test_foreign_key_references_move_to_target(self):
        personal, andrew = self._users()
        student = Student.objects.create(legal_first_name="Alice", last_name="Zhou")
        badge = Badge.objects.create(name="Test Badge")
        award = StudentBadge.objects.create(
            student=student, badge=badge, awarded_by=personal
        )

        self._run(personal, andrew, execute=True)

        award.refresh_from_db()
        self.assertEqual(award.awarded_by_id, andrew.pk)

    # ── scalar fields filled from source when target is empty ────────────

    def test_missing_name_fields_filled_from_source(self):
        personal = User.objects.create_user(
            username="alice@example.com",
            email="alice@example.com",
            first_name="Alice",
            last_name="Zhou",
            password="password",  # nosec B106
        )
        andrew = User.objects.create_user(
            username="alice@andrew.cmu.edu",
            email="alice@andrew.cmu.edu",
            password="password",  # nosec B106
        )

        self._run(personal, andrew, execute=True)

        andrew.refresh_from_db()
        self.assertEqual(andrew.first_name, "Alice")
        self.assertEqual(andrew.last_name, "Zhou")

    # ── fully reconciled Jeannine-style scenario ─────────────────────────

    def test_merge_reconciles_both_emails_onto_profile_user(self):
        personal, andrew = self._users()
        student = Student.objects.create(
            legal_first_name="Alice",
            last_name="Zhou",
            personal_email=personal.username,
            andrew_email=andrew.username,
            user=andrew,
        )
        self.assertEqual(student.user_id, andrew.pk)

        self._run(personal, andrew, execute=True)

        # Source duplicate removed; both emails on the profile-linked user.
        self.assertFalse(User.objects.filter(pk=personal.pk).exists())
        self.assertTrue(
            EmailAddress.objects.filter(
                user=andrew, email=personal.username, verified=True
            ).exists()
        )
        self.assertTrue(
            EmailAddress.objects.filter(user=andrew, email=andrew.username).exists()
        )
        student.refresh_from_db()
        self.assertEqual(student.user_id, andrew.pk)
