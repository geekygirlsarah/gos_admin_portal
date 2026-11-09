"""Tests for the ``find_disconnected_accounts`` management command.

The command surfaces profiles whose ``user`` account link is missing and the
floating User accounts that no longer point at any profile (the casualties of
the old portal form silently NULL-ing ``Student.user``). Reports are read-only;
email and name matches are suggestions only. ``--fix`` requires an operator to
confirm one specific account/profile pair.
"""

from io import StringIO

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db.models.signals import post_init
from django.test import TestCase

from audit.events import AuditEvent
from audit.models import AuditLog
from GoSAdminPortal.test_runner import _assign_default_test_org
from organizations.models import Organization, UserOrganizationMembership
from programs.models import Adult, Student

User = get_user_model()


class FindDisconnectedAccountsTests(TestCase):
    def setUp(self):
        self.disconnected_models = []
        for model in (Adult, Student):
            if post_init.disconnect(
                receiver=_assign_default_test_org,
                sender=model,
                dispatch_uid=f"default-test-org-{model._meta.label}",
            ):
                self.disconnected_models.append(model)
        self.addCleanup(self._restore_default_organization_hook)
        self.alpha = Organization.objects.create(
            name="Recovery Alpha", slug="recovery-alpha", subdomain="recovery-alpha"
        )
        self.beta = Organization.objects.create(
            name="Recovery Beta", slug="recovery-beta", subdomain="recovery-beta"
        )

    def _restore_default_organization_hook(self):
        for model in self.disconnected_models:
            post_init.connect(
                receiver=_assign_default_test_org,
                sender=model,
                dispatch_uid=f"default-test-org-{model._meta.label}",
            )

    def _run(self, fix=False, **options):
        out = StringIO()
        call_command("find_disconnected_accounts", fix=fix, stdout=out, **options)
        return out.getvalue()

    def _confirmed_options(self, profile_type, profile, user):
        return {
            "organization_id": profile.organization_id,
            "profile_type": profile_type,
            "profile_id": profile.pk,
            "user_id": user.pk,
            "confirm_link": True,
        }

    def test_reports_match_read_only_and_does_not_link(self):
        student = Student.objects.create(
            organization=self.alpha,
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

        output = self._run()

        self.assertIn("alex@example.com", output)
        self.assertIn("suggestion only", output.lower())
        student.refresh_from_db()
        self.assertIsNone(student.user_id)

    def test_fix_without_complete_confirmed_pair_links_nothing(self):
        student = Student.objects.create(
            organization=self.alpha,
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

        with self.assertRaises(CommandError):
            self._run(fix=True)

        student.refresh_from_db()
        self.assertIsNone(student.user_id)
        self.assertFalse(
            AuditLog.objects.filter(event=AuditEvent.PROFILE_LINK_CHANGED).exists()
        )

    def test_fix_requires_explicit_affirmative_confirmation(self):
        student = Student.objects.create(
            organization=self.alpha,
            legal_first_name="Alex",
            last_name="Morgan",
        )
        user = User.objects.create_user(username="alex@example.com")
        options = self._confirmed_options("student", student, user)
        options.pop("confirm_link")

        with self.assertRaises(CommandError):
            self._run(fix=True, **options)

        student.refresh_from_db()
        self.assertIsNone(student.user_id)

    def test_fix_links_explicit_student_pair_and_retains_audit_and_membership(self):
        student = Student.objects.create(
            organization=self.alpha,
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

        output = self._run(
            fix=True, **self._confirmed_options("student", student, user)
        )

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
        membership = UserOrganizationMembership.objects.get(
            user=user, organization=self.alpha, role="Student"
        )
        self.assertTrue(membership.is_active)

    def test_fix_preserves_existing_inactive_membership(self):
        student = Student.objects.create(
            organization=self.alpha,
            legal_first_name="Alex",
            last_name="Morgan",
        )
        user = User.objects.create_user(username="alex@example.com")
        membership = UserOrganizationMembership.objects.create(
            user=user,
            organization=self.alpha,
            role="Student",
            is_active=False,
        )

        self._run(fix=True, **self._confirmed_options("student", student, user))

        membership.refresh_from_db()
        self.assertFalse(membership.is_active)

    def test_fix_links_explicit_adult_pair(self):
        adult = Adult.objects.create(
            organization=self.alpha,
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

        self._run(fix=True, **self._confirmed_options("adult", adult, user))

        adult.refresh_from_db()
        self.assertEqual(adult.user_id, user.pk)

    def test_explicit_pair_can_be_confirmed_without_a_matching_email(self):
        student = Student.objects.create(
            organization=self.alpha,
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

        self._run(fix=True, **self._confirmed_options("student", student, user))

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
            organization=self.alpha,
            legal_first_name="Alex",
            last_name="Morgan",
            personal_email="alex@example.com",
            user=existing,
        )
        user = User.objects.create_user(
            username="alex@example.com",
            email="alex@example.com",
            first_name="Alex",
            last_name="Morgan",
            password="password",  # nosec B106
        )

        options = self._confirmed_options("student", student, user)
        with self.assertRaises(CommandError):
            self._run(fix=True, **options)

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
            organization=self.alpha,
            legal_first_name="Alex",
            last_name="Morgan",
            user=owned,
        )
        unlinked = Student.objects.create(
            organization=self.alpha,
            legal_first_name="Alex",
            last_name="Morgan",
        )

        with self.assertRaises(CommandError):
            self._run(fix=True, **self._confirmed_options("student", unlinked, owned))

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
        s1 = Student.objects.create(
            organization=self.alpha, legal_first_name="Sam", last_name="Lee"
        )
        s2 = Student.objects.create(
            organization=self.alpha, legal_first_name="Sam", last_name="Lee"
        )

        output = self._run()

        self.assertIsNotNone(s1.pk)
        self.assertIsNotNone(s2.pk)
        # Neither ambiguous candidate may be linked to the shared account.
        self.assertIsNone(Student.objects.get(pk=s1.pk).user_id)
        self.assertIsNone(Student.objects.get(pk=s2.pk).user_id)
        # And the user must remain unattached to a profile.
        self.assertFalse(Student.objects.filter(user=user).exists())
        self.assertIn("ambiguous", output.lower())

    def test_foreign_organization_selector_is_refused(self):
        student = Student.objects.create(
            organization=self.beta,
            legal_first_name="Alex",
            last_name="Beta",
        )
        user = User.objects.create_user(username="alex@example.com")
        options = self._confirmed_options("student", student, user)
        options["organization_id"] = self.alpha.pk

        with self.assertRaises(CommandError):
            self._run(fix=True, **options)

        student.refresh_from_db()
        self.assertIsNone(student.user_id)

    def test_fix_refuses_profile_already_linked_to_another_user(self):
        existing = User.objects.create_user(username="existing@example.com")
        student = Student.objects.create(
            organization=self.alpha,
            legal_first_name="Alex",
            last_name="Morgan",
            user=existing,
        )
        user = User.objects.create_user(username="replacement@example.com")

        with self.assertRaises(CommandError):
            self._run(fix=True, **self._confirmed_options("student", student, user))

        student.refresh_from_db()
        self.assertEqual(student.user_id, existing.pk)

    def test_fix_refuses_user_linked_to_other_profile_type(self):
        user = User.objects.create_user(username="linked-adult@example.com")
        adult = Adult.objects.create(
            organization=self.alpha,
            legal_first_name="Pat",
            last_name="Morgan",
            user=user,
        )
        student = Student.objects.create(
            organization=self.alpha,
            legal_first_name="Pat",
            last_name="Morgan",
        )

        with self.assertRaises(CommandError):
            self._run(fix=True, **self._confirmed_options("student", student, user))

        adult.refresh_from_db()
        student.refresh_from_db()
        self.assertEqual(adult.user_id, user.pk)
        self.assertIsNone(student.user_id)

    def test_fix_refuses_staff_and_superuser_accounts(self):
        for username, flags in (
            ("staff-account", {"is_staff": True}),
            ("superuser-account", {"is_superuser": True}),
        ):
            with self.subTest(username=username):
                student = Student.objects.create(
                    organization=self.alpha,
                    legal_first_name="Protected",
                    last_name=username,
                )
                user = User.objects.create_user(username=username, **flags)

                with self.assertRaises(CommandError):
                    self._run(
                        fix=True,
                        **self._confirmed_options("student", student, user),
                    )

                student.refresh_from_db()
                self.assertIsNone(student.user_id)

    def test_fix_refuses_stale_profile_or_user_ids(self):
        student = Student.objects.create(
            organization=self.alpha,
            legal_first_name="Alex",
            last_name="Morgan",
        )
        user = User.objects.create_user(username="alex@example.com")
        options = self._confirmed_options("student", student, user)
        options["profile_id"] = student.pk + 100000

        with self.assertRaises(CommandError):
            self._run(fix=True, **options)

        options = self._confirmed_options("student", student, user)
        options["user_id"] = user.pk + 100000
        with self.assertRaises(CommandError):
            self._run(fix=True, **options)

        student.refresh_from_db()
        self.assertIsNone(student.user_id)

    def test_floating_user_with_no_candidate_is_reported(self):
        User.objects.create_user(
            username="float@example.com",
            email="float@example.com",
            first_name="Nobody",
            last_name="Knows",
            password="password",  # nosec B106
        )

        output = self._run()

        self.assertIn("float@example.com", output)

    def test_clean_database_reports_success(self):
        output = self._run()
        self.assertIn("No disconnected accounts", output)
