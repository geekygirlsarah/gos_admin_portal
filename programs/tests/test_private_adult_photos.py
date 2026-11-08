from __future__ import annotations

import secrets
import shutil
import tempfile
from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse

from organizations.models import Organization, UserOrganizationMembership
from programs.models import Adult

_TMP_MEDIA = tempfile.mkdtemp(prefix="private-adult-photos-")


@override_settings(
    ALLOWED_HOSTS=[".localhost", "testserver"],
    MEDIA_ROOT=_TMP_MEDIA,
)
class PrivateAdultPhotoTests(TestCase):
    def setUp(self):
        tenant_key = secrets.token_hex(4)
        self.organization = Organization.objects.create(
            name="Adult Photo Alpha",
            slug=f"adult-photo-alpha-{tenant_key}",
            subdomain=f"adult-photo-alpha-{tenant_key}",
        )
        self.foreign_organization = Organization.objects.create(
            name="Adult Photo Beta",
            slug=f"adult-photo-beta-{tenant_key}",
            subdomain=f"adult-photo-beta-{tenant_key}",
        )
        self.adult = Adult.objects.create(
            organization=self.organization,
            legal_first_name="Avery",
            last_name="Mentor",
        )
        self.adult.photo.save(
            "avery-private-photo.jpg", ContentFile(b"adult photo bytes")
        )
        self.host = f"{self.organization.subdomain}.localhost"

    def create_member(self, username, organization, role="Parent", is_active=True):
        user = get_user_model().objects.create_user(
            username=username,
            password=secrets.token_urlsafe(32),
        )
        UserOrganizationMembership.objects.create(
            user=user,
            organization=organization,
            role=role,
            is_active=is_active,
        )
        return user

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(_TMP_MEDIA, ignore_errors=True)

    def test_direct_adult_photo_media_url_is_not_public(self):
        legacy_path = Path(_TMP_MEDIA, "photos", "adults", "legacy.jpg")
        legacy_path.parent.mkdir(parents=True, exist_ok=True)
        legacy_path.write_bytes(b"legacy adult photo bytes")

        for debug in (True, False):
            with (
                self.subTest(debug=debug),
                override_settings(DEBUG=debug),
            ):
                with self.assertNumQueries(0):
                    response = self.client.get(
                        "/media/photos/adults/legacy.jpg", HTTP_HOST=self.host
                    )
                self.assertNotEqual(response.status_code, 200)
                if response.streaming:
                    self.assertNotIn(
                        b"legacy adult photo bytes",
                        b"".join(response.streaming_content),
                    )
                else:
                    self.assertNotIn(b"legacy adult photo bytes", response.content)
                response.close()

    def test_adult_photo_endpoint_allows_active_local_members_only(self):
        member = self.create_member("adult-photo-local-member", self.organization)
        foreign_member = self.create_member(
            "adult-photo-foreign-member", self.foreign_organization
        )
        inactive_member = self.create_member(
            "adult-photo-inactive-member", self.organization, is_active=False
        )
        url = reverse("adult_photo", kwargs={"pk": self.adult.pk})

        anonymous = self.client.get(url, HTTP_HOST=self.host)
        self.assertEqual(anonymous.status_code, 302)

        self.client.force_login(member)
        allowed = self.client.get(url, HTTP_HOST=self.host)
        self.assertEqual(allowed.status_code, 200)
        self.assertEqual(b"".join(allowed.streaming_content), b"adult photo bytes")
        allowed.close()

        self.client.force_login(foreign_member)
        foreign = self.client.get(url, HTTP_HOST=self.host)
        self.assertNotEqual(foreign.status_code, 200)
        self.assertNotIn(b"adult photo bytes", foreign.content)
        foreign.close()

        self.client.force_login(inactive_member)
        inactive = self.client.get(url, HTTP_HOST=self.host)
        self.assertNotEqual(inactive.status_code, 200)
        self.assertNotIn(b"adult photo bytes", inactive.content)
        inactive.close()

    def test_platform_superuser_can_view_local_adult_photo(self):
        admin = get_user_model().objects.create_superuser(
            username="adult-photo-platform-admin",
            email="admin@example.test",
        )
        self.client.force_login(admin)

        response = self.client.get(
            reverse("adult_photo", kwargs={"pk": self.adult.pk}),
            HTTP_HOST=self.host,
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(b"".join(response.streaming_content), b"adult photo bytes")
        response.close()

    def test_adult_photo_endpoint_requires_organization_context(self):
        from programs.views.adults import AdultPhotoView

        admin = get_user_model().objects.create_superuser(
            username="contextless-adult-photo-admin",
            email="admin@example.test",
        )
        request = RequestFactory().get(
            reverse("adult_photo", kwargs={"pk": self.adult.pk})
        )
        request.user = admin
        request.organization = None

        from django.http import Http404

        with self.assertRaises(Http404):
            AdultPhotoView.as_view()(request, pk=self.adult.pk)

    def test_adult_detail_uses_protected_photo_endpoint(self):
        member = self.create_member(
            "adult-photo-page-member", self.organization, role="LeadMentor"
        )
        self.client.force_login(member)

        response = self.client.get(
            reverse("adult_detail", kwargs={"pk": self.adult.pk}),
            HTTP_HOST=self.host,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response, reverse("adult_photo", kwargs={"pk": self.adult.pk})
        )
        self.assertNotContains(response, self.adult.photo.url)
