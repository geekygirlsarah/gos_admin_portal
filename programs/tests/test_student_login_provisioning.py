"""
Regression test: Students and parents (Adults with personal_email) should be able
to log in via the OTP login-by-code flow even if no Django User account exists yet.

Previously, allauth's RequestLoginCodeForm.clean_email() called filter_users_by_email()
which only looks up existing User records. If a Student had no User yet, it returned
empty and triggered the 'unknown_account' path instead of provisioning a new User.

The fix: ProvisioningRequestLoginCodeForm calls _find_or_provision_user_for_email()
before the standard lookup, so the User is created in time for allauth to find it.
"""

from django.contrib.auth import get_user_model
from django.test import RequestFactory, TestCase, override_settings

from GoSAdminPortal.adapter import _find_or_provision_user_for_email
from programs.models import Adult, Student

User = get_user_model()

ALLAUTH_OVERRIDE = {
    "ACCOUNT_FORMS": {
        "request_login_code": "GoSAdminPortal.adapter.ProvisioningRequestLoginCodeForm"
    }
}


class StudentLoginProvisioningTests(TestCase):
    """Tests that a Student with no User account gets auto-provisioned on login."""

    def setUp(self):
        self.factory = RequestFactory()
        self.student = Student.objects.create(
            preferred_first_name="Alice",
            last_name="Smith",
            personal_email="alice@example.com",
        )

    def test_student_without_user_is_provisioned(self):
        """_find_or_provision_user_for_email creates a User for a known student email."""
        self.assertIsNone(self.student.user_id)
        result = _find_or_provision_user_for_email("alice@example.com")
        self.assertTrue(result)
        self.student.refresh_from_db()
        self.assertIsNotNone(self.student.user_id)
        self.assertTrue(User.objects.filter(email="alice@example.com").exists())

    def test_student_andrew_email_is_provisioned(self):
        """Students can also log in via their andrew email."""
        self.student.andrew_email = "alice@andrew.cmu.edu"
        self.student.save(update_fields=["andrew_email"])
        result = _find_or_provision_user_for_email("alice@andrew.cmu.edu")
        self.assertTrue(result)
        self.assertTrue(User.objects.filter(email="alice@andrew.cmu.edu").exists())

    def test_unknown_email_returns_false(self):
        """An email not associated with any Student, Adult, or User returns False."""
        result = _find_or_provision_user_for_email("nobody@example.com")
        self.assertFalse(result)

    def test_dual_user_accounts_reconcile_to_profile_user(self):
        """Regression: a student who has BOTH a personal and an andrew email, with a
        separate User account for each (the Jeannine Zhou production bug), must be able
        to log in with either email without an IntegrityError. Both emails end up on
        the single User the profile links to."""
        from allauth.account.models import EmailAddress

        # Set up Student with both emails, like in production.
        self.student.andrew_email = "alice@andrew.cmu.edu"
        self.student.personal_email = "alice@example.com"
        self.student.save(update_fields=["andrew_email", "personal_email"])

        # One User keyed by the andrew email — this is the one linked to the Student.
        andrew_user = User.objects.create_user(
            username="alice@andrew.cmu.edu", email="alice@andrew.cmu.edu"
        )
        EmailAddress.objects.create(
            user=andrew_user,
            email="alice@andrew.cmu.edu",
            verified=True,
            primary=True,
        )
        self.student.user = andrew_user
        self.student.save(update_fields=["user"])

        # A second, separate User keyed by the personal email — the source of the bug.
        personal_user = User.objects.create_user(
            username="alice@example.com", email="alice@example.com"
        )
        EmailAddress.objects.create(
            user=personal_user,
            email="alice@example.com",
            verified=True,
            primary=True,
        )

        # Logging in with the personal email must not crash and must use the
        # profile's (andrew) user, with both emails on it.
        result = _find_or_provision_user_for_email("alice@example.com")
        self.assertTrue(result)

        # The personal email's verified EmailAddress now belongs to the andrew user.
        personal_ea = EmailAddress.objects.get(email="alice@example.com")
        self.assertEqual(personal_ea.user_id, andrew_user.id)
        # Both emails live on the single profile-linked user.
        self.assertTrue(
            EmailAddress.objects.filter(
                user=andrew_user, email="alice@example.com"
            ).exists()
        )
        self.assertTrue(
            EmailAddress.objects.filter(
                user=andrew_user, email="alice@andrew.cmu.edu"
            ).exists()
        )
        self.assertEqual(
            EmailAddress.objects.filter(email="alice@example.com").count(), 1
        )
        self.assertFalse(
            EmailAddress.objects.filter(
                email="alice@example.com", user=personal_user
            ).exists()
        )

    def test_verified_email_on_other_user_does_not_crash(self):
        """Regression: an orphaned verified EmailAddress on ANOTHER user must not
        raise IntegrityError (unique_verified_email). The existing record should be
        moved to the correct user."""
        from allauth.account.models import EmailAddress

        # The correct user is the one linked to the Student.
        user = User.objects.create_user(
            username="alice-owner", email="alice@example.com"
        )
        self.student.user = user
        self.student.save(update_fields=["user"])

        # Simulate a stale/orphaned verified EmailAddress pointing at a different user.
        other_user = User.objects.create_user(
            username="alice-other", email="someoneelse@example.com"
        )
        EmailAddress.objects.create(
            user=other_user,
            email="alice@example.com",
            verified=True,
            primary=True,
        )

        result = _find_or_provision_user_for_email("alice@example.com")
        self.assertTrue(result)

        ea = EmailAddress.objects.get(email="alice@example.com")
        self.assertEqual(ea.user_id, user.id)
        self.assertEqual(
            EmailAddress.objects.filter(email="alice@example.com").count(), 1
        )
        self.assertFalse(
            EmailAddress.objects.filter(
                email="alice@example.com", user=other_user
            ).exists()
        )

    @override_settings(ACCOUNT_FORMS=ALLAUTH_OVERRIDE["ACCOUNT_FORMS"])
    def test_provisioning_form_clean_email_provisions_student(self):
        """ProvisioningRequestLoginCodeForm.clean_email provisions a User for a student."""
        from GoSAdminPortal.adapter import ProvisioningRequestLoginCodeForm

        request = self.factory.post("/accounts/login/code/request/")
        request.session = {}

        form = ProvisioningRequestLoginCodeForm(data={"email": "alice@example.com"})
        # Patch context.request used by allauth ratelimit
        from allauth.core import context as allauth_context

        with allauth_context.request_context(request):
            is_valid = form.is_valid()

        self.assertTrue(is_valid, form.errors)
        self.assertIsNotNone(form._user)
        self.assertEqual(form._user.email, "alice@example.com")

    @override_settings(ACCOUNT_FORMS=ALLAUTH_OVERRIDE["ACCOUNT_FORMS"])
    def test_provisioning_form_leaves_user_none_for_unknown_email(self):
        """ProvisioningRequestLoginCodeForm leaves _user=None for emails not in the system."""
        from GoSAdminPortal.adapter import ProvisioningRequestLoginCodeForm

        request = self.factory.post("/accounts/login/code/request/")
        request.session = {}

        form = ProvisioningRequestLoginCodeForm(data={"email": "nobody@example.com"})
        from allauth.core import context as allauth_context

        with allauth_context.request_context(request):
            is_valid = form.is_valid()

        # With PREVENT_ENUMERATION (allauth default), the form is still valid for
        # unknown emails — but _user must be None so allauth sends unknown_account mail.
        self.assertTrue(is_valid)
        self.assertIsNone(form._user)


class ParentLoginProvisioningTests(TestCase):
    """Tests that a Parent (Adult) with no User account gets auto-provisioned on login."""

    def setUp(self):
        self.parent = Adult.objects.create(
            legal_first_name="Bob",
            last_name="Smith",
            personal_email="bob@example.com",
            is_parent=True,
        )

    def test_parent_without_user_is_provisioned(self):
        """_find_or_provision_user_for_email creates a User for a known parent email."""
        self.assertIsNone(self.parent.user_id)
        result = _find_or_provision_user_for_email("bob@example.com")
        self.assertTrue(result)
        self.parent.refresh_from_db()
        self.assertIsNotNone(self.parent.user_id)
        self.assertTrue(User.objects.filter(email="bob@example.com").exists())
