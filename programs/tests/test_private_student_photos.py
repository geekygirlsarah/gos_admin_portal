from __future__ import annotations

import secrets
import shutil
import tempfile
from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.test import TestCase, override_settings
from django.urls import reverse

from organizations.models import Organization, UserOrganizationMembership
from programs.models import Student

_TMP_MEDIA = tempfile.mkdtemp(prefix="private-student-photos-")


@override_settings(
    ALLOWED_HOSTS=[".localhost", "testserver"],
    MEDIA_ROOT=_TMP_MEDIA,
)
class PrivateStudentPhotoTests(TestCase):
    def setUp(self):
        tenant_key = secrets.token_hex(4)
        self.organization = Organization.objects.create(
            name="Photo Alpha",
            slug=f"photo-alpha-{tenant_key}",
            subdomain=f"photo-alpha-{tenant_key}",
        )
        self.foreign_organization = Organization.objects.create(
            name="Photo Beta",
            slug=f"photo-beta-{tenant_key}",
            subdomain=f"photo-beta-{tenant_key}",
        )
        self.student = Student.objects.create(
            organization=self.organization,
            legal_first_name="Avery",
            last_name="Applicant",
        )
        self.student.photo.save(
            "avery-private-photo.jpg", ContentFile(b"student photo bytes")
        )
        self.host = f"{self.organization.subdomain}.localhost"

    def create_lead_user(self, username, email):
        user = get_user_model().objects.create_user(
            username=username,
            email=email,
            password=secrets.token_urlsafe(32),
        )
        UserOrganizationMembership.objects.create(
            user=user,
            organization=self.organization,
            role="LeadMentor",
            is_active=True,
        )
        return user

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(_TMP_MEDIA, ignore_errors=True)

    def test_student_photo_is_stored_in_org_scoped_random_path(self):
        path = self.student.photo.name
        self.assertTrue(
            path.startswith(f"private/student_photos/{self.organization.pk}/")
        )
        self.assertNotIn("avery-private-photo", path)

    def test_direct_media_photo_url_is_not_public(self):
        legacy_path = Path(_TMP_MEDIA, "photos", "students", "legacy.jpg")
        legacy_path.parent.mkdir(parents=True, exist_ok=True)
        legacy_path.write_bytes(b"student photo bytes")
        for path in (self.student.photo.url, "/media/photos/students/legacy.jpg"):
            for debug in (True, False):
                with (
                    self.subTest(path=path, debug=debug),
                    override_settings(DEBUG=debug),
                ):
                    with self.assertNumQueries(0):
                        response = self.client.get(path, HTTP_HOST=self.host)
                    self.assertNotEqual(response.status_code, 200)
                    if response.streaming:
                        self.assertNotIn(
                            b"student photo bytes", b"".join(response.streaming_content)
                        )
                    else:
                        self.assertNotIn(b"student photo bytes", response.content)

    def test_photo_endpoint_allows_local_reviewer_and_denies_anonymous_or_foreign_host(
        self,
    ):
        user = self.create_lead_user("photo-reviewer", "reviewer@example.test")
        url = reverse("student_photo", kwargs={"pk": self.student.pk})

        anonymous = self.client.get(url, HTTP_HOST=self.host)
        self.assertEqual(anonymous.status_code, 302)

        self.client.force_login(user)
        allowed = self.client.get(url, HTTP_HOST=self.host)
        self.assertEqual(allowed.status_code, 200)
        self.assertEqual(b"".join(allowed.streaming_content), b"student photo bytes")

        foreign = self.client.get(
            url, HTTP_HOST=f"{self.foreign_organization.subdomain}.localhost"
        )
        self.assertNotEqual(foreign.status_code, 200)
        if foreign.streaming:
            self.assertNotIn(
                b"student photo bytes", b"".join(foreign.streaming_content)
            )
        else:
            self.assertNotIn(b"student photo bytes", foreign.content)

        nonmember = get_user_model().objects.create_user(
            username="photo-nonmember",
            email="nonmember@example.test",
            password=secrets.token_urlsafe(32),
        )
        self.client.force_login(nonmember)
        denied = self.client.get(url, HTTP_HOST=self.host)
        self.assertNotEqual(denied.status_code, 200)

    def test_student_detail_uses_protected_photo_endpoint(self):
        user = self.create_lead_user("photo-page-reviewer", "reviewer2@example.test")
        self.client.force_login(user)
        response = self.client.get(
            reverse("student_detail", kwargs={"pk": self.student.pk}),
            HTTP_HOST=self.host,
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            reverse("student_photo", kwargs={"pk": self.student.pk}),
        )
        self.assertNotContains(response, self.student.photo.url)

    def test_legacy_photo_is_served_only_through_authorized_endpoint(self):
        legacy_path = Path(_TMP_MEDIA, "photos", "students", "legacy.jpg")
        legacy_path.parent.mkdir(parents=True, exist_ok=True)
        legacy_path.write_bytes(b"legacy student photo")
        legacy_student = Student.objects.create(
            organization=self.organization,
            legal_first_name="Legacy",
            last_name="Student",
            photo="photos/students/legacy.jpg",
        )
        user = self.create_lead_user("legacy-photo-reviewer", "legacy@example.test")
        self.client.force_login(user)
        response = self.client.get(
            reverse("student_photo", kwargs={"pk": legacy_student.pk}),
            HTTP_HOST=self.host,
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(b"".join(response.streaming_content), b"legacy student photo")

    def test_non_sensitive_media_remains_public_and_database_free(self):
        public_path = Path(_TMP_MEDIA, "public", "sample.txt")
        public_path.parent.mkdir(parents=True, exist_ok=True)
        public_path.write_bytes(b"public media")
        with self.assertNumQueries(0):
            response = self.client.get("/media/public/sample.txt", HTTP_HOST=self.host)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(b"".join(response.streaming_content), b"public media")
