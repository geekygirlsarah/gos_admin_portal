import datetime

from django.contrib.auth.models import AnonymousUser, User
from django.http import HttpResponse
from django.test import RequestFactory, TestCase

from GoSAdminPortal.adapter import _find_or_provision_user_for_email
from GoSAdminPortal.middleware import LoginRequiredMiddleware


class MiddlewareAsyncTest(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.user = User.objects.create_user(username="testuser")

    def test_sync_middleware_auth(self):
        def get_response(request):
            return HttpResponse("OK")

        middleware = LoginRequiredMiddleware(get_response)
        request = self.factory.get("/programs/")
        request.user = self.user
        response = middleware(request)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b"OK")

    def test_sync_middleware_anon(self):
        def get_response(request):
            return HttpResponse("OK")

        middleware = LoginRequiredMiddleware(get_response)
        request = self.factory.get("/programs/")
        request.user = AnonymousUser()
        response = middleware(request)
        self.assertEqual(response.status_code, 302)
        self.assertIn("/accounts/login/", response["Location"])

    def test_sync_middleware_exempt(self):
        def get_response(request):
            return HttpResponse("OK")

        middleware = LoginRequiredMiddleware(get_response)
        request = self.factory.get("/accounts/login/")
        request.user = AnonymousUser()
        response = middleware(request)
        self.assertEqual(response.status_code, 200)

    async def test_async_middleware_auth(self):
        async def get_response(request):
            return HttpResponse("OK")

        middleware = LoginRequiredMiddleware(get_response)
        request = self.factory.get("/programs/")
        request.user = self.user
        response = await middleware(request)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b"OK")

    async def test_async_middleware_anon(self):
        async def get_response(request):
            return HttpResponse("OK")

        middleware = LoginRequiredMiddleware(get_response)
        request = self.factory.get("/programs/")
        request.user = AnonymousUser()
        response = await middleware(request)
        self.assertEqual(response.status_code, 302)
        self.assertIn("/accounts/login/", response["Location"])

    async def test_async_middleware_exempt(self):
        async def get_response(request):
            return HttpResponse("OK")

        middleware = LoginRequiredMiddleware(get_response)
        request = self.factory.get("/accounts/login/")
        request.user = AnonymousUser()
        response = await middleware(request)
        self.assertEqual(response.status_code, 200)


class AdapterEmailProvisioningTest(TestCase):
    """Tests for _find_or_provision_user_for_email in GoSAdminPortal/adapter.py."""

    def _make_adult(self, email, **kwargs):
        from programs.models import Adult

        return Adult.objects.create(
            first_name=kwargs.get("first_name", "Ada"),
            last_name=kwargs.get("last_name", "Lovelace"),
            personal_email=email,
            active=True,
        )

    def _make_student(self, personal_email=None, andrew_email=None):
        from programs.models import Student

        return Student.objects.create(
            legal_first_name="Grace",
            last_name="Hopper",
            date_of_birth=datetime.date(2010, 1, 1),
            personal_email=personal_email,
            andrew_email=andrew_email,
        )

    # ── existing User ────────────────────────────────────────────────────────

    def test_existing_user_email_allowed(self):
        User.objects.create_user(username="known", email="known@example.com")
        self.assertTrue(_find_or_provision_user_for_email("known@example.com"))

    def test_unknown_email_rejected(self):
        self.assertFalse(_find_or_provision_user_for_email("nobody@example.com"))

    # ── Adult provisioning ───────────────────────────────────────────────────

    def test_adult_email_allowed_no_user(self):
        """Adult with no linked User: a new User should be provisioned."""
        from programs.models import Adult

        adult = self._make_adult("parent@example.com")
        result = _find_or_provision_user_for_email("parent@example.com")
        self.assertTrue(result)
        adult.refresh_from_db()
        self.assertIsNotNone(adult.user_id)
        self.assertTrue(User.objects.filter(email="parent@example.com").exists())

    def test_adult_email_allowed_existing_user(self):
        """Adult already linked to a User: allowed without creating a new User."""
        user = User.objects.create_user(
            username="adultuser", email="adultuser@example.com"
        )
        adult = self._make_adult("adultuser@example.com", first_name="Ada")
        adult.user = user
        adult.save(update_fields=["user"])
        result = _find_or_provision_user_for_email("adultuser@example.com")
        self.assertTrue(result)
        self.assertEqual(User.objects.filter(email="adultuser@example.com").count(), 1)

    def test_adult_email_case_insensitive(self):
        self._make_adult("Parent@Example.COM")
        self.assertTrue(_find_or_provision_user_for_email("parent@example.com"))

    # ── Student provisioning ─────────────────────────────────────────────────

    def test_student_personal_email_allowed(self):
        """Student personal_email: a new User should be provisioned."""
        from programs.models import Student

        student = self._make_student(personal_email="grace@personal.com")
        result = _find_or_provision_user_for_email("grace@personal.com")
        self.assertTrue(result)
        student.refresh_from_db()
        self.assertIsNotNone(student.user_id)

    def test_student_andrew_email_allowed(self):
        """Student andrew_email: a new User should be provisioned."""
        from programs.models import Student

        student = self._make_student(andrew_email="ghopper@andrew.cmu.edu")
        result = _find_or_provision_user_for_email("ghopper@andrew.cmu.edu")
        self.assertTrue(result)
        student.refresh_from_db()
        self.assertIsNotNone(student.user_id)

    def test_student_email_case_insensitive(self):
        self._make_student(personal_email="Grace@Personal.COM")
        self.assertTrue(_find_or_provision_user_for_email("grace@personal.com"))

    def test_student_existing_user_not_duplicated(self):
        """Student already has a User: no new User created."""
        user = User.objects.create_user(username="stuuser", email="stu@personal.com")
        from programs.models import Student

        student = self._make_student(personal_email="stu@personal.com")
        student.user = user
        student.save(update_fields=["user"])
        result = _find_or_provision_user_for_email("stu@personal.com")
        self.assertTrue(result)
        self.assertEqual(User.objects.filter(email="stu@personal.com").count(), 1)

    def test_adult_andrew_email_allowed(self):
        """Adult andrew_email can also be used to log in."""
        from programs.models import Adult

        Adult.objects.create(
            first_name="Mentor",
            last_name="Smith",
            is_mentor=True,
            andrew_email="msmith@andrew.cmu.edu",
            active=True,
        )
        self.assertTrue(_find_or_provision_user_for_email("msmith@andrew.cmu.edu"))

    # ── allauth EmailAddress record ──────────────────────────────────────────

    def test_allauth_email_address_record_created(self):
        """Provisioning an adult creates an allauth EmailAddress record."""
        from allauth.account.models import EmailAddress

        self._make_adult("newparent@example.com")
        _find_or_provision_user_for_email("newparent@example.com")
        self.assertTrue(
            EmailAddress.objects.filter(email="newparent@example.com").exists()
        )


class LoginPolicyByRoleTest(TestCase):
    """TDD for role-based login identifier rules.

    Matrix to enforce:
      - Students: Andrew or personal email allowed
      - Parents: personal email only (Andrew email denied)
      - Mentors: Andrew email only (personal email denied)
      - Alumni: personal email only (Andrew email denied)
      - Lead Mentors: same as mentors (Andrew email only)
    """

    def _make_adult(self, **kwargs):
        from programs.models import Adult

        defaults = dict(
            first_name="Ada",
            last_name="Lovelace",
        )
        defaults.update(kwargs)
        return Adult.objects.create(**defaults)

    def _make_student(self, **kwargs):
        from programs.models import Student

        defaults = dict(
            legal_first_name="Grace",
            last_name="Hopper",
            date_of_birth=datetime.date(2010, 1, 1),
        )
        defaults.update(kwargs)
        return Student.objects.create(**defaults)

    # ── Mentors ─────────────────────────────────────────────────────────────

    def test_mentor_personal_email_denied(self):
        self._make_adult(is_mentor=True, personal_email="mentor.personal@example.com")
        allowed = _find_or_provision_user_for_email("mentor.personal@example.com")
        self.assertFalse(allowed)

    def test_mentor_andrew_email_allowed(self):
        self._make_adult(is_mentor=True, andrew_email="mentor1@andrew.cmu.edu")
        allowed = _find_or_provision_user_for_email("mentor1@andrew.cmu.edu")
        self.assertTrue(allowed)

    # ── Lead Mentors (same rule as mentors) ─────────────────────────────────

    def test_lead_mentor_andrew_only(self):
        # Represent lead mentor as a mentor adult; group membership is handled separately.
        self._make_adult(
            is_mentor=True,
            andrew_email="leadmentor@andrew.cmu.edu",
            personal_email="leadmentor.personal@example.com",
        )
        self.assertTrue(_find_or_provision_user_for_email("leadmentor@andrew.cmu.edu"))
        self.assertFalse(
            _find_or_provision_user_for_email("leadmentor.personal@example.com")
        )

    # ── Parents ─────────────────────────────────────────────────────────────

    def test_parent_personal_email_allowed(self):
        self._make_adult(is_parent=True, personal_email="parent@example.com")
        self.assertTrue(_find_or_provision_user_for_email("parent@example.com"))

    def test_parent_andrew_email_denied(self):
        self._make_adult(is_parent=True, andrew_email="parent1@andrew.cmu.edu")
        self.assertFalse(_find_or_provision_user_for_email("parent1@andrew.cmu.edu"))

    # ── Alumni ──────────────────────────────────────────────────────────────

    def test_alumni_personal_email_allowed(self):
        self._make_adult(is_alumni=True, personal_email="alumni@example.com")
        self.assertTrue(_find_or_provision_user_for_email("alumni@example.com"))

    def test_alumni_andrew_email_denied(self):
        self._make_adult(is_alumni=True, andrew_email="grad@andrew.cmu.edu")
        self.assertFalse(_find_or_provision_user_for_email("grad@andrew.cmu.edu"))

    # ── Students ────────────────────────────────────────────────────────────

    def test_student_allows_personal_and_andrew(self):
        self._make_student(
            personal_email="student.personal@example.com",
            andrew_email="student1@andrew.cmu.edu",
        )
        self.assertTrue(
            _find_or_provision_user_for_email("student.personal@example.com")
        )
        self.assertTrue(_find_or_provision_user_for_email("student1@andrew.cmu.edu"))
