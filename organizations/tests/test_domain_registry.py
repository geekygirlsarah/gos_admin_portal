import secrets
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Event
from types import SimpleNamespace
from unittest.mock import patch

import dns.exception
from django.apps import apps
from django.contrib.auth.models import User
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.db import (
    IntegrityError,
    OperationalError,
    close_old_connections,
    connection,
    transaction,
)
from django.test import RequestFactory, TestCase, TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from audit.models import AuditLog
from GoSAdminPortal.views import _active_organizations_with_portal_urls
from organizations import domain_recovery
from organizations.domain_forms import OrganizationDomainRequestForm
from organizations.domain_verification import verify_domain_txt
from organizations.models import (
    Organization,
    OrganizationDomain,
    UserOrganizationMembership,
    generate_domain_verification_token,
)
from organizations.platform_forms import PlatformOrganizationForm
from organizations.utils import get_organization_momentum_url, get_organization_url


@override_settings(
    MOMENTUM_ROOT_DOMAIN="momentumteam.app",
    ALLOWED_HOSTS=[
        "localhost",
        ".localhost",
        "127.0.0.1",
        "testserver",
        "momentumteam.app",
        ".momentumteam.app",
        "gos.example.org",
        "robotigers.example.org",
        "portal.girlsofsteelrobotics.org",
        "girlsofsteelrobotics.org",
        "gosadmin.com",
    ],
)
class OrganizationDomainRegistryTests(TestCase):
    def setUp(self):
        self.gos = Organization.objects.get(slug="gos")
        self.other = Organization.objects.create(
            name="RoboTigers", slug="robotigers", subdomain="robotigers"
        )

    def make_domain(self, organization, hostname, status=None):
        now = timezone.now()
        user = User.objects.create_user(username=f"operator-{hostname}")
        fields = {
            "organization": organization,
            "hostname": hostname,
            "status": status or OrganizationDomain.STATUS_PENDING_VERIFICATION,
        }
        if status in (
            OrganizationDomain.STATUS_DNS_VERIFIED,
            OrganizationDomain.STATUS_ACTIVE,
            OrganizationDomain.STATUS_DISABLED,
        ):
            fields["dns_verified_at"] = now
        if status == OrganizationDomain.STATUS_ACTIVE:
            fields.update(activated_at=now, activated_by=user)
        return OrganizationDomain.objects.create(**fields)

    def test_domain_form_normalizes_hostnames_and_rejects_reserved_values(self):
        form = OrganizationDomainRequestForm(
            data={"hostname": "Portal.Example.org."}, organization=self.other
        )
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["hostname"], "portal.example.org")

        for hostname in (
            "https://portal.example.org",
            "127.0.0.1",
            "robotigers.momentumteam.app",
            "portal.girlsofsteelrobotics.org",
        ):
            with self.subTest(hostname=hostname):
                form = OrganizationDomainRequestForm(
                    data={"hostname": hostname}, organization=self.other
                )
                self.assertFalse(form.is_valid())
                self.assertIn("hostname", form.errors)

        gos_form = OrganizationDomainRequestForm(
            data={"hostname": "portal.girlsofsteelrobotics.org"},
            organization=self.gos,
        )
        self.assertTrue(gos_form.is_valid(), gos_form.errors)

    def test_registered_gos_alias_can_be_disabled_without_legacy_fallback(self):
        domain = self.make_domain(
            self.gos, "gosadmin.com", OrganizationDomain.STATUS_ACTIVE
        )
        response = self.client.get("/accounts/login/", HTTP_HOST=domain.hostname)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.wsgi_request.organization, self.gos)

        domain.status = OrganizationDomain.STATUS_DISABLED
        domain.save(update_fields=["status", "updated_at"])
        response = self.client.get("/accounts/login/", HTTP_HOST=domain.hostname)
        self.assertEqual(response.status_code, 404)
        self.assertIsNone(response.wsgi_request.organization)

    def test_pending_gos_alias_keeps_the_existing_legacy_route(self):
        domain = self.make_domain(self.gos, "portal.girlsofsteelrobotics.org")

        response = self.client.get("/accounts/login/", HTTP_HOST=domain.hostname)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.wsgi_request.organization, self.gos)

    def test_hostname_is_unique_after_normalization_across_organizations(self):
        self.make_domain(self.gos, "shared.example.org")
        form = OrganizationDomainRequestForm(
            data={"hostname": "SHARED.example.org."}, organization=self.other
        )

        self.assertFalse(form.is_valid())
        self.assertIn("hostname", form.errors)

    def test_database_rejects_noncanonical_hostnames_even_for_bulk_writes(self):
        self.make_domain(self.gos, "shared.example.org")
        for hostname in ("SHARED.example.org", "other.example.org."):
            with self.subTest(hostname=hostname):
                with self.assertRaises(IntegrityError):
                    with transaction.atomic():
                        OrganizationDomain.objects.bulk_create(
                            [
                                OrganizationDomain(
                                    organization=self.other, hostname=hostname
                                )
                            ]
                        )

    def test_disabled_legacy_custom_domain_does_not_route(self):
        self.other.settings = {
            "custom_domain": {"enabled": False, "domain": "robotigers.example.org"}
        }
        self.other.save(update_fields=["settings"])
        cache.set("org_resolve:host:robotigers.example.org", self.other.pk, timeout=300)

        response = self.client.get(
            "/accounts/login/", HTTP_HOST="robotigers.example.org"
        )

        self.assertEqual(response.status_code, 404)
        self.assertIsNone(response.wsgi_request.organization)

    def assert_ambiguous_host_is_rejected(self, hostname, cached_org_ids):
        cache_key = f"org_resolve:host:{hostname}"
        for org_id in (*cached_org_ids, None):
            with self.subTest(hostname=hostname, cached_org_id=org_id):
                if org_id is None:
                    cache.delete(cache_key)
                else:
                    cache.set(cache_key, org_id, timeout=300)
                response = self.client.get("/accounts/login/", HTTP_HOST=hostname)
                self.assertEqual(response.status_code, 404)
                self.assertIsNone(response.wsgi_request.organization)

    def test_slug_and_subdomain_collision_fails_closed(self):
        self.other.subdomain = "robotigers-team"
        self.other.save(update_fields=["subdomain"])
        collision = Organization.objects.create(
            name="Collision Team", slug="collision-team", subdomain="robotigers"
        )

        self.assert_ambiguous_host_is_rejected(
            "robotigers.momentumteam.app", (self.other.pk, collision.pk)
        )

    def test_duplicate_legacy_custom_domains_fail_closed(self):
        for org in (self.gos, self.other):
            org.settings = {"custom_domain": "gos.example.org"}
            org.save(update_fields=["settings"])

        self.assert_ambiguous_host_is_rejected(
            "gos.example.org", (self.gos.pk, self.other.pk)
        )

    def test_legacy_custom_domain_cannot_collide_with_tenant_subdomain(self):
        self.gos.settings = {"custom_domain": "robotigers.momentumteam.app"}
        self.gos.save(update_fields=["settings"])

        self.assert_ambiguous_host_is_rejected(
            "robotigers.momentumteam.app", (self.gos.pk, self.other.pk)
        )

    def test_legacy_custom_domain_cannot_collide_with_gos_alias(self):
        self.other.settings = {"custom_domain": "gosadmin.com"}
        self.other.save(update_fields=["settings"])

        self.assert_ambiguous_host_is_rejected(
            "gosadmin.com", (self.gos.pk, self.other.pk)
        )

    def test_platform_form_rejects_cross_field_subdomain_collisions(self):
        self.other.subdomain = "robotigers-team"
        self.other.save(update_fields=["subdomain"])
        base_data = {
            "name": "New Team",
            "is_active": "on",
            "time_zone": "America/New_York",
            "theme_color": "#0056b3",
        }
        for slug, subdomain, field in (
            ("robotigers-team", "new-team", "slug"),
            ("new-team", "robotigers", "subdomain"),
        ):
            with self.subTest(field=field):
                form = PlatformOrganizationForm(
                    data={**base_data, "slug": slug, "subdomain": subdomain}
                )
                self.assertFalse(form.is_valid())
                self.assertIn(field, form.errors)

    def test_platform_form_rejects_reserved_or_invalid_new_tenant_labels(self):
        base_data = {
            "name": "New Team",
            "is_active": "on",
            "time_zone": "America/New_York",
            "theme_color": "#0056b3",
        }
        for slug, subdomain, field in (
            ("portal", "new-team", "slug"),
            ("new-team", "admin", "subdomain"),
            ("new-team", "bad_name", "subdomain"),
        ):
            with self.subTest(field=field, slug=slug, subdomain=subdomain):
                form = PlatformOrganizationForm(
                    data={**base_data, "slug": slug, "subdomain": subdomain}
                )
                self.assertFalse(form.is_valid())
                self.assertIn(field, form.errors)

    def test_marketing_directory_batches_hostname_claim_lookups(self):
        self.make_domain(
            self.other, "robotigers.example.org", OrganizationDomain.STATUS_ACTIVE
        )

        with self.assertNumQueries(2):
            organizations = _active_organizations_with_portal_urls()

        other = next(org for org in organizations if org.pk == self.other.pk)
        self.assertEqual(other.portal_url, "https://robotigers.example.org")

    def test_ambiguous_active_custom_domain_is_not_used_for_links(self):
        self.make_domain(
            self.other, "robotigers.example.org", OrganizationDomain.STATUS_ACTIVE
        )
        self.gos.settings = {"custom_domain": "robotigers.example.org"}
        self.gos.save(update_fields=["settings"])

        self.assertEqual(
            get_organization_url(self.other), "https://robotigers.momentumteam.app"
        )

    def test_ambiguous_momentum_subdomain_uses_unambiguous_slug_for_links(self):
        self.other.subdomain = "robotigers-team"
        self.other.save(update_fields=["subdomain"])
        Organization.objects.create(
            name="Collision Team", slug="robotigers-team", subdomain="collision-team"
        )

        self.assertEqual(
            get_organization_url(self.other), "https://robotigers.momentumteam.app"
        )
        self.assertEqual(
            get_organization_momentum_url(self.other),
            "https://robotigers.momentumteam.app",
        )

    def test_no_ambiguous_tenant_address_is_rendered_as_a_link(self):
        self.other.subdomain = "robotigers-team"
        self.other.save(update_fields=["subdomain"])
        Organization.objects.create(
            name="Collision Team", slug="robotigers-team", subdomain="robotigers"
        )

        self.assertEqual(get_organization_url(self.other), "")
        self.assertEqual(get_organization_momentum_url(self.other), "")
        response = self.client.get("/", HTTP_HOST="momentumteam.app")
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(
            response, 'href="https://robotigers-team.momentumteam.app"'
        )
        self.assertNotContains(response, 'href="https://robotigers.momentumteam.app"')
        self.assertContains(response, "Address unavailable")

    def test_domain_cannot_be_activated_without_dns_and_operator_confirmation(self):
        domain = OrganizationDomain(
            organization=self.other,
            hostname="robotigers.example.org",
            status=OrganizationDomain.STATUS_ACTIVE,
            dns_verified_at=timezone.now(),
        )

        with self.assertRaises(ValidationError):
            domain.save()

    def test_domain_identity_cannot_change_after_creation(self):
        domain = self.make_domain(
            self.other, "robotigers.example.org", OrganizationDomain.STATUS_ACTIVE
        )
        for field, value in (
            ("hostname", "another.example.org"),
            ("organization", self.gos),
        ):
            with self.subTest(field=field):
                domain.refresh_from_db()
                setattr(domain, field, value)
                with self.assertRaises(ValidationError):
                    domain.save()
                domain.refresh_from_db()
                self.assertEqual(domain.hostname, "robotigers.example.org")
                self.assertEqual(domain.organization, self.other)
                self.assertEqual(domain.status, OrganizationDomain.STATUS_ACTIVE)

    @patch("organizations.domain_verification.dns.resolver.resolve")
    def test_dns_txt_verification_matches_the_exact_challenge(self, resolve):
        domain = self.make_domain(self.other, "robotigers.example.org")
        resolve.return_value = [
            SimpleNamespace(
                strings=[
                    f"momentum-domain-verification={domain.verification_token}".encode()
                ]
            )
        ]

        self.assertTrue(verify_domain_txt(domain))
        resolve.assert_called_once_with(
            "_momentum-verification.robotigers.example.org", "TXT", lifetime=3.0
        )

        resolve.return_value = [
            SimpleNamespace(strings=[b"another-verification-token"])
        ]
        self.assertFalse(verify_domain_txt(domain))

    @patch("organizations.domain_verification.dns.resolver.resolve")
    def test_dns_txt_verification_ignores_non_ascii_records(self, resolve):
        domain = self.make_domain(self.other, "robotigers.example.org")
        resolve.return_value = [
            SimpleNamespace(strings=["not-the-challenge-é".encode()]),
            SimpleNamespace(strings=[domain.verification_txt_value.encode()]),
        ]

        self.assertTrue(verify_domain_txt(domain))

    @patch("organizations.domain_verification.dns.resolver.resolve")
    def test_dns_txt_lookup_failure_does_not_verify_domain(self, resolve):
        domain = self.make_domain(self.other, "robotigers.example.org")
        resolve.side_effect = dns.exception.Timeout

        self.assertFalse(verify_domain_txt(domain))

    def test_only_active_registry_domains_resolve_and_cache_is_revalidated(self):
        domain = self.make_domain(self.other, "robotigers.example.org")
        cache.set(f"org_resolve:host:{domain.hostname}", False, timeout=300)

        response = self.client.get("/accounts/login/", HTTP_HOST=domain.hostname)
        self.assertEqual(response.status_code, 404)

        domain.status = OrganizationDomain.STATUS_DNS_VERIFIED
        domain.dns_verified_at = timezone.now()
        domain.save()
        cache.set(f"org_resolve:host:{domain.hostname}", self.other.pk, timeout=300)
        response = self.client.get("/accounts/login/", HTTP_HOST=domain.hostname)
        self.assertEqual(response.status_code, 404)

        domain.status = OrganizationDomain.STATUS_ACTIVE
        domain.activated_at = timezone.now()
        domain.activated_by = User.objects.create_user(username="domain-operator")
        domain.save()
        response = self.client.get("/accounts/login/", HTTP_HOST=domain.hostname)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.wsgi_request.organization, self.other)

        cache.set(f"org_resolve:host:{domain.hostname}", self.other.pk, timeout=300)
        OrganizationDomain.objects.filter(pk=domain.pk).update(
            status=OrganizationDomain.STATUS_DISABLED
        )
        response = self.client.get("/accounts/login/", HTTP_HOST=domain.hostname)
        self.assertEqual(response.status_code, 404)
        self.assertIsNone(response.wsgi_request.organization)

    def test_only_registered_active_domain_is_used_for_organization_links(self):
        self.other.settings = {"custom_domain": "legacy.example.org"}
        self.assertEqual(
            get_organization_url(self.other), "https://robotigers.momentumteam.app"
        )

        self.make_domain(
            self.other, "robotigers.example.org", OrganizationDomain.STATUS_ACTIVE
        )
        self.assertEqual(
            get_organization_url(self.other), "https://robotigers.example.org"
        )

    def test_lead_mentor_can_request_and_verify_only_own_organization_domain(self):
        lead = User.objects.create_user(
            username="domain-lead", password=secrets.token_urlsafe()
        )
        UserOrganizationMembership.objects.create(
            user=lead,
            organization=self.gos,
            role="LeadMentor",
            is_active=True,
        )
        self.client.force_login(lead)

        response = self.client.post(
            reverse("portal_domains"),
            {"action": "request", "hostname": "gos.example.org"},
            HTTP_HOST="gos.momentumteam.app",
        )
        self.assertEqual(response.status_code, 302)
        domain = OrganizationDomain.objects.get(hostname="gos.example.org")
        self.assertEqual(domain.organization, self.gos)
        self.assertEqual(domain.status, OrganizationDomain.STATUS_PENDING_VERIFICATION)

        response = self.client.post(
            reverse("portal_domains"),
            {"action": "verify", "domain_id": domain.pk},
            HTTP_HOST="robotigers.momentumteam.app",
        )
        self.assertEqual(response.status_code, 403)
        domain.refresh_from_db()
        self.assertEqual(domain.status, OrganizationDomain.STATUS_PENDING_VERIFICATION)

    def test_non_lead_member_cannot_request_a_custom_domain(self):
        mentor = User.objects.create_user(
            username="domain-mentor", password=secrets.token_urlsafe()
        )
        UserOrganizationMembership.objects.create(
            user=mentor,
            organization=self.gos,
            role="Mentor",
            is_active=True,
        )
        self.client.force_login(mentor)

        response = self.client.post(
            reverse("portal_domains"),
            {"action": "request", "hostname": "gos.example.org"},
            HTTP_HOST="gos.momentumteam.app",
        )

        self.assertEqual(response.status_code, 302)
        self.assertFalse(
            OrganizationDomain.objects.filter(hostname="gos.example.org").exists()
        )

    @patch("organizations.domain_verification.dns.resolver.resolve")
    def test_lead_mentor_dns_check_marks_verified_but_not_active(self, resolve):
        lead = User.objects.create_user(
            username="verify-lead", password=secrets.token_urlsafe()
        )
        UserOrganizationMembership.objects.create(
            user=lead,
            organization=self.other,
            role="LeadMentor",
            is_active=True,
        )
        domain = self.make_domain(self.other, "robotigers.example.org")
        resolve.return_value = [
            SimpleNamespace(
                strings=[
                    f"momentum-domain-verification={domain.verification_token}".encode()
                ]
            )
        ]
        self.client.force_login(lead)

        response = self.client.post(
            reverse("portal_domains"),
            {"action": "verify", "domain_id": domain.pk},
            HTTP_HOST="robotigers.momentumteam.app",
        )

        self.assertEqual(response.status_code, 302)
        domain.refresh_from_db()
        self.assertEqual(domain.status, OrganizationDomain.STATUS_DNS_VERIFIED)
        self.assertIsNotNone(domain.dns_verified_at)
        self.assertIsNone(domain.activated_at)

    @patch("organizations.domain_verification.dns.resolver.resolve")
    def test_dns_recheck_does_not_restore_a_disabled_legacy_alias(self, resolve):
        lead = User.objects.create_user(
            username="disabled-domain-lead", password=secrets.token_urlsafe()
        )
        UserOrganizationMembership.objects.create(
            user=lead, organization=self.gos, role="LeadMentor", is_active=True
        )
        domain = self.make_domain(
            self.gos, "gosadmin.com", OrganizationDomain.STATUS_ACTIVE
        )
        domain.status = OrganizationDomain.STATUS_DISABLED
        domain.save(update_fields=["status", "updated_at"])
        resolve.return_value = [
            SimpleNamespace(
                strings=[
                    f"momentum-domain-verification={domain.verification_token}".encode()
                ]
            )
        ]
        self.client.force_login(lead)

        response = self.client.post(
            reverse("portal_domains"),
            {"action": "verify", "domain_id": domain.pk},
            HTTP_HOST="gos.momentumteam.app",
        )

        self.assertEqual(response.status_code, 302)
        domain.refresh_from_db()
        self.assertEqual(domain.status, OrganizationDomain.STATUS_DISABLED)
        response = self.client.get("/accounts/login/", HTTP_HOST=domain.hostname)
        self.assertEqual(response.status_code, 404)

    @patch("programs.views.portal_settings.verify_domain_txt")
    def test_dns_check_preserves_a_concurrent_operator_disable(self, verify_domain):
        lead = User.objects.create_user(
            username="concurrent-domain-lead", password=secrets.token_urlsafe()
        )
        UserOrganizationMembership.objects.create(
            user=lead, organization=self.gos, role="LeadMentor", is_active=True
        )
        domain = self.make_domain(
            self.gos, "gosadmin.com", OrganizationDomain.STATUS_DNS_VERIFIED
        )

        def disable_during_check(current):
            OrganizationDomain.objects.filter(pk=current.pk).update(
                status=OrganizationDomain.STATUS_DISABLED
            )
            return True

        verify_domain.side_effect = disable_during_check
        self.client.force_login(lead)
        response = self.client.post(
            reverse("portal_domains"),
            {"action": "verify", "domain_id": domain.pk},
            HTTP_HOST="gos.momentumteam.app",
        )

        self.assertEqual(response.status_code, 302)
        domain.refresh_from_db()
        self.assertEqual(domain.status, OrganizationDomain.STATUS_DISABLED)

    def test_non_superuser_cannot_activate_a_domain(self):
        lead = User.objects.create_user(
            username="non-super-domain-lead", password=secrets.token_urlsafe()
        )
        UserOrganizationMembership.objects.create(
            user=lead, organization=self.other, role="LeadMentor", is_active=True
        )
        domain = self.make_domain(
            self.other, "robotigers.example.org", OrganizationDomain.STATUS_DNS_VERIFIED
        )
        self.client.force_login(lead)
        url = reverse(
            "platform_admin:organization_domain_action",
            kwargs={"pk": self.other.pk, "domain_pk": domain.pk},
        )

        response = self.client.post(
            url,
            {"action": "activate", "confirm_infrastructure_ready": "on"},
            HTTP_HOST="momentumteam.app",
        )

        self.assertEqual(response.status_code, 403)
        domain.refresh_from_db()
        self.assertEqual(domain.status, OrganizationDomain.STATUS_DNS_VERIFIED)

    @patch("organizations.platform_views.verify_domain_txt", return_value=False)
    def test_activation_rechecks_dns_before_routing(self, verify_domain):
        operator = User.objects.create_superuser(username="dns-recheck-operator")
        domain = self.make_domain(
            self.other, "robotigers.example.org", OrganizationDomain.STATUS_DNS_VERIFIED
        )
        self.client.force_login(operator)
        url = reverse(
            "platform_admin:organization_domain_action",
            kwargs={"pk": self.other.pk, "domain_pk": domain.pk},
        )

        response = self.client.post(
            url,
            {"action": "activate", "confirm_infrastructure_ready": "on"},
            HTTP_HOST="momentumteam.app",
        )

        self.assertEqual(response.status_code, 302)
        domain.refresh_from_db()
        self.assertEqual(domain.status, OrganizationDomain.STATUS_DNS_VERIFIED)
        self.assertIsNone(domain.activated_at)
        verify_domain.assert_called_once()
        self.assertEqual(
            self.client.get("/accounts/login/", HTTP_HOST=domain.hostname).status_code,
            404,
        )

    @patch("organizations.platform_views.verify_domain_txt", return_value=True)
    def test_platform_activation_requires_dns_verification_and_readiness_confirmation(
        self, verify_domain
    ):
        operator = User.objects.create_superuser(
            username="domain-platform-admin", password=secrets.token_urlsafe()
        )
        domain = self.make_domain(self.other, "robotigers.example.org")
        self.client.force_login(operator)
        url = reverse(
            "platform_admin:organization_domain_action",
            kwargs={"pk": self.other.pk, "domain_pk": domain.pk},
        )

        response = self.client.post(
            url,
            {"action": "activate", "confirm_infrastructure_ready": "on"},
            HTTP_HOST="momentumteam.app",
        )
        self.assertEqual(response.status_code, 302)
        domain.refresh_from_db()
        self.assertEqual(domain.status, OrganizationDomain.STATUS_PENDING_VERIFICATION)

        domain.status = OrganizationDomain.STATUS_DNS_VERIFIED
        domain.dns_verified_at = timezone.now()
        domain.save()
        response = self.client.post(
            url, {"action": "activate"}, HTTP_HOST="momentumteam.app"
        )
        self.assertEqual(response.status_code, 302)
        domain.refresh_from_db()
        self.assertEqual(domain.status, OrganizationDomain.STATUS_DNS_VERIFIED)

        response = self.client.post(
            url,
            {"action": "activate", "confirm_infrastructure_ready": "on"},
            HTTP_HOST="momentumteam.app",
        )
        self.assertEqual(response.status_code, 302)
        domain.refresh_from_db()
        self.assertEqual(domain.status, OrganizationDomain.STATUS_ACTIVE)
        self.assertEqual(domain.activated_by, operator)
        self.assertLess(timezone.now() - domain.activated_at, timedelta(minutes=1))
        verify_domain.assert_called_once_with(domain)

        response = self.client.post(
            url, {"action": "disable"}, HTTP_HOST="momentumteam.app"
        )
        self.assertEqual(response.status_code, 302)
        domain.refresh_from_db()
        self.assertEqual(domain.status, OrganizationDomain.STATUS_DISABLED)
        response = self.client.get("/accounts/login/", HTTP_HOST=domain.hostname)
        self.assertEqual(response.status_code, 404)

    def test_platform_admin_cannot_act_on_another_organizations_domain(self):
        operator = User.objects.create_superuser(
            username="domain-scope-admin", password=secrets.token_urlsafe()
        )
        domain = self.make_domain(
            self.other, "robotigers.example.org", OrganizationDomain.STATUS_ACTIVE
        )
        self.client.force_login(operator)
        url = reverse(
            "platform_admin:organization_domain_action",
            kwargs={"pk": self.gos.pk, "domain_pk": domain.pk},
        )

        response = self.client.post(
            url, {"action": "disable"}, HTTP_HOST="momentumteam.app"
        )

        self.assertEqual(response.status_code, 302)
        domain.refresh_from_db()
        self.assertEqual(domain.status, OrganizationDomain.STATUS_ACTIVE)

    def test_operator_can_disable_domain_after_legacy_collision_appears(self):
        operator = User.objects.create_superuser(
            username="domain-conflict-admin", password=secrets.token_urlsafe()
        )
        domain = self.make_domain(
            self.other, "robotigers.example.org", OrganizationDomain.STATUS_ACTIVE
        )
        self.gos.settings = {"custom_domain": domain.hostname}
        self.gos.save(update_fields=["settings"])
        self.client.force_login(operator)
        url = reverse(
            "platform_admin:organization_domain_action",
            kwargs={"pk": self.other.pk, "domain_pk": domain.pk},
        )

        response = self.client.post(
            url, {"action": "disable"}, HTTP_HOST="momentumteam.app"
        )

        self.assertEqual(response.status_code, 302)
        domain.refresh_from_db()
        self.assertEqual(domain.status, OrganizationDomain.STATUS_DISABLED)

    @patch("organizations.platform_views.verify_domain_txt", return_value=True)
    def test_operator_cannot_activate_a_domain_with_a_conflicting_legacy_claim(
        self, verify_domain
    ):
        operator = User.objects.create_superuser(
            username="domain-conflicting-activation", password=secrets.token_urlsafe()
        )
        domain = self.make_domain(
            self.other, "robotigers.example.org", OrganizationDomain.STATUS_DNS_VERIFIED
        )
        self.gos.settings = {"custom_domain": domain.hostname}
        self.gos.save(update_fields=["settings"])
        self.client.force_login(operator)
        url = reverse(
            "platform_admin:organization_domain_action",
            kwargs={"pk": self.other.pk, "domain_pk": domain.pk},
        )

        response = self.client.post(
            url,
            {"action": "activate", "confirm_infrastructure_ready": "on"},
            HTTP_HOST="momentumteam.app",
        )

        self.assertEqual(response.status_code, 302)
        domain.refresh_from_db()
        self.assertEqual(domain.status, OrganizationDomain.STATUS_DNS_VERIFIED)
        verify_domain.assert_not_called()


@override_settings(
    MOMENTUM_ROOT_DOMAIN="momentumteam.app",
    ALLOWED_HOSTS=[
        "momentumteam.app",
        ".momentumteam.app",
        "recovery.example.org",
        "gosadmin.com",
    ],
)
class OrganizationDomainRecoveryTests(TestCase):
    def setUp(self):
        self.recovery_model = apps.get_model(
            "organizations", "OrganizationDomainRecovery"
        )
        self.original = Organization.objects.get(slug="gos")
        self.requester = Organization.objects.create(
            name="Recovery Team", slug="recovery-team", subdomain="recovery-team"
        )
        self.competitor = Organization.objects.create(
            name="Competing Team", slug="competing-team", subdomain="competing-team"
        )
        self.lead = User.objects.create_user(username="recovery-lead")
        self.other_lead = User.objects.create_user(username="competing-lead")
        for user, organization in (
            (self.lead, self.requester),
            (self.other_lead, self.competitor),
        ):
            UserOrganizationMembership.objects.create(
                user=user, organization=organization, role="LeadMentor", is_active=True
            )
        self.operator = User.objects.create_superuser(username="recovery-operator")
        self.domain = OrganizationDomain.objects.create(
            organization=self.original, hostname="recovery.example.org"
        )

    def tenant_post(self, user, organization, action, **data):
        self.client.force_login(user)
        return self.client.post(
            reverse("portal_domains"),
            {"action": action, **data},
            HTTP_HOST=f"{organization.subdomain}.momentumteam.app",
        )

    def request_recovery(self, user=None, organization=None):
        user = user or self.lead
        organization = organization or self.requester
        self.tenant_post(
            user, organization, "request_recovery", hostname=self.domain.hostname
        )
        return self.recovery_model.objects.get(
            organization=organization, domain=self.domain
        )

    def verify_recovery(self, recovery, user=None, organization=None):
        return self.tenant_post(
            user or self.lead,
            organization or self.requester,
            "verify_recovery",
            recovery_id=recovery.pk,
        )

    def approve_recovery(self, recovery, organization=None):
        self.client.force_login(self.operator)
        return self.client.post(
            reverse(
                "platform_admin:organization_domain_recovery_action",
                kwargs={
                    "pk": (organization or self.requester).pk,
                    "recovery_pk": recovery.pk,
                },
            ),
            {"action": "approve"},
            HTTP_HOST="momentumteam.app",
        )

    @patch("organizations.domain_verification.dns.resolver.resolve")
    def test_independent_proof_transfer_and_separate_activation(self, resolve):
        recovery = self.request_recovery()
        self.assertNotEqual(recovery.verification_token, self.domain.verification_token)
        resolve.return_value = [
            SimpleNamespace(strings=[recovery.verification_txt_value.encode("ascii")])
        ]
        self.verify_recovery(recovery)
        recovery.refresh_from_db()
        self.assertIsNotNone(recovery.dns_verified_at)
        self.assertTrue(
            AuditLog.objects.filter(
                actor=self.lead, notes__contains=self.domain.hostname
            ).exists()
        )
        self.domain.refresh_from_db()
        self.assertEqual(self.domain.organization, self.original)
        self.assertEqual(
            self.client.get(
                "/accounts/login/", HTTP_HOST=self.domain.hostname
            ).status_code,
            404,
        )

        self.approve_recovery(recovery)
        self.domain.refresh_from_db()
        self.assertEqual(self.domain.organization, self.requester)
        self.assertEqual(self.domain.status, OrganizationDomain.STATUS_DNS_VERIFIED)
        self.assertEqual(self.domain.verification_token, recovery.verification_token)
        self.assertIsNone(self.domain.activated_at)
        self.assertTrue(
            AuditLog.objects.filter(
                actor=self.operator, notes__contains=self.domain.hostname
            ).exists()
        )
        self.assertEqual(
            self.client.get(
                "/accounts/login/", HTTP_HOST=self.domain.hostname
            ).status_code,
            404,
        )

        response = self.client.post(
            reverse(
                "platform_admin:organization_domain_action",
                kwargs={"pk": self.requester.pk, "domain_pk": self.domain.pk},
            ),
            {"action": "activate", "confirm_infrastructure_ready": "on"},
            HTTP_HOST="momentumteam.app",
        )
        self.assertEqual(response.status_code, 302)
        self.domain.refresh_from_db()
        self.assertEqual(self.domain.status, OrganizationDomain.STATUS_ACTIVE)
        response = self.client.get("/accounts/login/", HTTP_HOST=self.domain.hostname)
        self.assertEqual(response.wsgi_request.organization, self.requester)

    @patch("organizations.domain_verification.dns.resolver.resolve")
    def test_dns_failure_or_stale_proof_cannot_transfer(self, resolve):
        recovery = self.request_recovery()
        resolve.side_effect = dns.exception.Timeout
        self.verify_recovery(recovery)
        recovery.refresh_from_db()
        self.assertIsNone(recovery.dns_verified_at)
        self.approve_recovery(recovery)
        self.domain.refresh_from_db()
        self.assertEqual(self.domain.organization, self.original)

        resolve.side_effect = None
        resolve.return_value = [
            SimpleNamespace(strings=[recovery.verification_txt_value.encode("ascii")])
        ]
        self.verify_recovery(recovery)
        recovery.refresh_from_db()
        self.assertIsNotNone(recovery.dns_verified_at)
        resolve.side_effect = dns.exception.Timeout
        self.approve_recovery(recovery)
        self.domain.refresh_from_db()
        self.assertEqual(self.domain.organization, self.original)
        self.assertEqual(
            self.domain.status, OrganizationDomain.STATUS_PENDING_VERIFICATION
        )

    @patch("organizations.domain_verification.dns.resolver.resolve")
    def test_changed_original_challenge_invalidates_pending_recovery(self, resolve):
        recovery = self.request_recovery()
        resolve.return_value = [
            SimpleNamespace(strings=[recovery.verification_txt_value.encode("ascii")])
        ]
        self.verify_recovery(recovery)
        new_challenge = generate_domain_verification_token()
        self.assertNotEqual(new_challenge, self.domain.verification_token)
        OrganizationDomain.objects.filter(pk=self.domain.pk).update(
            verification_token=new_challenge
        )
        self.approve_recovery(recovery)
        self.domain.refresh_from_db()
        self.assertEqual(self.domain.organization, self.original)
        self.assertEqual(
            self.domain.status, OrganizationDomain.STATUS_PENDING_VERIFICATION
        )

    def test_duplicate_requests_are_not_created_for_same_tenant(self):
        self.request_recovery()
        self.tenant_post(
            self.lead, self.requester, "request_recovery", hostname=self.domain.hostname
        )
        self.assertEqual(
            self.recovery_model.objects.filter(
                organization=self.requester, domain=self.domain
            ).count(),
            1,
        )

    def test_challenges_are_only_visible_to_the_requesting_organization(self):
        recovery = self.request_recovery()
        self.client.force_login(self.other_lead)
        response = self.client.get(
            "/programs/settings/?tab=portal_address",
            HTTP_HOST="competing-team.momentumteam.app",
        )
        self.assertNotContains(response, recovery.verification_txt_value)
        self.client.force_login(self.lead)
        response = self.client.get(
            "/programs/settings/?tab=portal_address",
            HTTP_HOST="recovery-team.momentumteam.app",
        )
        self.assertContains(response, recovery.verification_txt_value)

    def test_legacy_alias_cannot_be_recovered_by_another_tenant(self):
        alias = OrganizationDomain.objects.create(
            organization=self.original, hostname="gosadmin.com"
        )
        self.tenant_post(
            self.lead, self.requester, "request_recovery", hostname=alias.hostname
        )
        self.assertFalse(self.recovery_model.objects.filter(domain=alias).exists())
        self.client.logout()
        response = self.client.get("/accounts/login/", HTTP_HOST=alias.hostname)
        self.assertEqual(response.wsgi_request.organization, self.original)

    @patch("organizations.domain_verification.dns.resolver.resolve")
    def test_only_root_platform_superuser_can_approve(self, resolve):
        recovery = self.request_recovery()
        resolve.return_value = [
            SimpleNamespace(strings=[recovery.verification_txt_value.encode("ascii")])
        ]
        self.verify_recovery(recovery)
        url = reverse(
            "platform_admin:organization_domain_recovery_action",
            kwargs={"pk": self.requester.pk, "recovery_pk": recovery.pk},
        )
        self.client.force_login(self.lead)
        response = self.client.post(
            url, {"action": "approve"}, HTTP_HOST="momentumteam.app"
        )
        self.assertEqual(response.status_code, 403)
        self.client.force_login(self.operator)
        response = self.client.post(
            url, {"action": "approve"}, HTTP_HOST="recovery-team.momentumteam.app"
        )
        # The project's 404 handler redirects to home for rejected tenant-host access.
        self.assertRedirects(response, reverse("home"), fetch_redirect_response=False)
        self.domain.refresh_from_db()
        self.assertEqual(self.domain.organization, self.original)
        self.approve_recovery(recovery, self.competitor)
        self.domain.refresh_from_db()
        self.assertEqual(self.domain.organization, self.original)

    @patch("organizations.domain_verification.dns.resolver.resolve")
    def test_competing_claims_cannot_reuse_proof_after_transfer(self, resolve):
        first = self.request_recovery()
        second = self.request_recovery(self.other_lead, self.competitor)
        self.assertNotEqual(first.verification_token, second.verification_token)
        for recovery, user, organization in (
            (first, self.lead, self.requester),
            (second, self.other_lead, self.competitor),
        ):
            resolve.return_value = [
                SimpleNamespace(
                    strings=[recovery.verification_txt_value.encode("ascii")]
                )
            ]
            self.verify_recovery(recovery, user, organization)
        resolve.return_value = [
            SimpleNamespace(strings=[first.verification_txt_value.encode("ascii")])
        ]
        self.approve_recovery(first)
        resolve.return_value = [
            SimpleNamespace(strings=[second.verification_txt_value.encode("ascii")])
        ]
        self.approve_recovery(second, self.competitor)
        self.domain.refresh_from_db()
        self.assertEqual(self.domain.organization, self.requester)

    @patch("organizations.domain_verification.dns.resolver.resolve")
    def test_verified_active_and_disabled_reservations_cannot_be_taken(self, resolve):
        recovery = self.request_recovery()
        resolve.return_value = [
            SimpleNamespace(strings=[recovery.verification_txt_value.encode("ascii")])
        ]
        self.verify_recovery(recovery)
        for status in (
            OrganizationDomain.STATUS_DNS_VERIFIED,
            OrganizationDomain.STATUS_ACTIVE,
            OrganizationDomain.STATUS_DISABLED,
        ):
            with self.subTest(status=status):
                OrganizationDomain.objects.filter(pk=self.domain.pk).update(
                    status=status,
                    dns_verified_at=timezone.now(),
                    activated_at=timezone.now(),
                    activated_by=self.operator,
                )
                self.approve_recovery(recovery)
                self.domain.refresh_from_db()
                self.assertEqual(self.domain.organization, self.original)
                self.assertEqual(self.domain.status, status)

    @patch("organizations.domain_verification.dns.resolver.resolve")
    def test_revoked_claim_and_cross_tenant_actions_cannot_transfer(self, resolve):
        recovery = self.request_recovery()
        resolve.return_value = [
            SimpleNamespace(strings=[recovery.verification_txt_value.encode("ascii")])
        ]
        response = self.verify_recovery(recovery, self.other_lead, self.competitor)
        self.assertRedirects(response, reverse("home"), fetch_redirect_response=False)
        recovery.refresh_from_db()
        self.assertIsNone(recovery.dns_verified_at)
        self.verify_recovery(recovery)
        self.tenant_post(
            self.lead, self.requester, "revoke_recovery", recovery_id=recovery.pk
        )
        self.approve_recovery(recovery)
        self.domain.refresh_from_db()
        self.assertEqual(self.domain.organization, self.original)

    @patch("organizations.domain_verification.dns.resolver.resolve")
    def test_new_legacy_claim_blocks_recovery(self, resolve):
        recovery = self.request_recovery()
        resolve.return_value = [
            SimpleNamespace(strings=[recovery.verification_txt_value.encode("ascii")])
        ]
        self.verify_recovery(recovery)
        self.original.settings = {"custom_domain": self.domain.hostname}
        self.original.save(update_fields=["settings"])
        self.approve_recovery(recovery)
        self.domain.refresh_from_db()
        self.assertEqual(self.domain.organization, self.original)

    def test_duplicate_pending_request_is_rejected_and_revocation_renews_token(self):
        recovery = self.request_recovery()
        self.tenant_post(
            self.lead, self.requester, "request_recovery", hostname=self.domain.hostname
        )
        self.assertEqual(
            self.recovery_model.objects.filter(domain=self.domain).count(), 1
        )
        self.tenant_post(
            self.lead, self.requester, "revoke_recovery", recovery_id=recovery.pk
        )
        self.tenant_post(
            self.lead, self.requester, "request_recovery", hostname=self.domain.hostname
        )
        replacement = self.recovery_model.objects.get(
            domain=self.domain, status=self.recovery_model.STATUS_PENDING
        )
        self.assertNotEqual(recovery.verification_token, replacement.verification_token)
        self.assertEqual(
            self.recovery_model.objects.filter(domain=self.domain).count(), 2
        )

    def test_disabled_or_secondary_legacy_setting_blocks_recovery(self):
        for settings in (
            {"custom_domain": {"enabled": False, "domain": self.domain.hostname}},
            {"custom_domain": "elsewhere.example.org", "domain": self.domain.hostname},
        ):
            with self.subTest(settings=settings):
                self.original.settings = settings
                self.original.save(update_fields=["settings"])
                self.tenant_post(
                    self.lead,
                    self.requester,
                    "request_recovery",
                    hostname=self.domain.hostname,
                )
                self.assertFalse(
                    self.recovery_model.objects.filter(domain=self.domain).exists()
                )

    @patch("organizations.domain_verification.dns.resolver.resolve")
    def test_changed_original_challenge_invalidates_recovery(self, resolve):
        recovery = self.request_recovery()
        resolve.return_value = [
            SimpleNamespace(strings=[recovery.verification_txt_value.encode("ascii")])
        ]
        self.verify_recovery(recovery)
        new_challenge = generate_domain_verification_token()
        self.assertNotEqual(new_challenge, self.domain.verification_token)
        OrganizationDomain.objects.filter(pk=self.domain.pk).update(
            verification_token=new_challenge
        )
        self.approve_recovery(recovery)
        self.domain.refresh_from_db()
        recovery.refresh_from_db()
        self.assertEqual(self.domain.organization, self.original)
        self.assertEqual(recovery.status, self.recovery_model.STATUS_PENDING)

    @patch("organizations.domain_recovery.log_event", return_value=None)
    def test_audit_failure_rolls_back_request(self, log_event):
        self.tenant_post(
            self.lead, self.requester, "request_recovery", hostname=self.domain.hostname
        )
        self.assertFalse(
            self.recovery_model.objects.filter(domain=self.domain).exists()
        )
        log_event.assert_called_once()

    @patch("organizations.domain_verification.dns.resolver.resolve")
    def test_audit_failure_rolls_back_approval(self, resolve):
        recovery = self.request_recovery()
        resolve.return_value = [
            SimpleNamespace(strings=[recovery.verification_txt_value.encode("ascii")])
        ]
        self.verify_recovery(recovery)
        with patch("organizations.domain_recovery.log_event", return_value=None):
            self.approve_recovery(recovery)
        self.domain.refresh_from_db()
        recovery.refresh_from_db()
        self.assertEqual(self.domain.organization, self.original)
        self.assertEqual(
            self.domain.status, OrganizationDomain.STATUS_PENDING_VERIFICATION
        )
        self.assertEqual(recovery.status, self.recovery_model.STATUS_PENDING)

    def test_malformed_recovery_ids_are_rejected(self):
        for value in ("", "not-an-id", "9" * 100):
            with self.subTest(value=value):
                response = self.tenant_post(
                    self.lead, self.requester, "verify_recovery", recovery_id=value
                )
                self.assertRedirects(
                    response, reverse("home"), fetch_redirect_response=False
                )

    def test_approval_rejects_backends_without_safe_legacy_claim_locking(self):
        with patch.object(domain_recovery.connection, "vendor", "unsupported"):
            with self.assertRaises(domain_recovery.RecoveryError):
                domain_recovery._lock_legacy_claims()

    def test_postgresql_locks_organization_table_for_legacy_claims(self):
        with patch("organizations.domain_recovery.connection") as database:
            database.vendor = "postgresql"
            database.ops.quote_name.return_value = '"organizations_organization"'
            domain_recovery._lock_legacy_claims()
            database.cursor.return_value.__enter__.return_value.execute.assert_called_once_with(
                'LOCK TABLE "organizations_organization" IN SHARE MODE'
            )


class OrganizationDomainRecoveryConcurrencyTests(TransactionTestCase):
    def test_competing_approvals_never_transfer_the_same_reservation_twice(self):
        original = Organization.objects.create(
            name="Original", slug="race-original", subdomain="race-original"
        )
        first = Organization.objects.create(
            name="First", slug="race-first", subdomain="race-first"
        )
        second = Organization.objects.create(
            name="Second", slug="race-second", subdomain="race-second"
        )
        user = User.objects.create_superuser(
            username="race-operator", password=secrets.token_urlsafe()
        )
        domain = OrganizationDomain.objects.create(
            organization=original, hostname="race.example.org"
        )
        request = RequestFactory().post("/")
        request.user = user
        claims = [
            domain_recovery.request_recovery(request, org, domain.hostname)
            for org in (first, second)
        ]
        for claim in claims:
            claim.dns_verified_at = timezone.now()
            claim.save(update_fields=["dns_verified_at"])

        first_in_dns = Event()
        release_first = Event()
        second_started = Event()

        def verify(claim):
            if claim.pk == claims[0].pk:
                first_in_dns.set()
                if not release_first.wait(10):
                    raise AssertionError("Timed out waiting for the competing approval")
            return True

        def approve(org_id, claim_id, started=None):
            close_old_connections()
            try:
                org = Organization.objects.get(pk=org_id)
                actor = User.objects.get(pk=user.pk)
                thread_request = RequestFactory().post("/")
                thread_request.user = actor
                if started:
                    started.set()
                domain_recovery.approve_recovery(thread_request, org, claim_id)
                return "approved"
            except domain_recovery.RecoveryError:
                return "rejected"
            except OperationalError:
                if connection.vendor != "sqlite":
                    raise
                return "database locked"
            finally:
                close_old_connections()

        with patch(
            "organizations.domain_recovery.verify_domain_txt", side_effect=verify
        ):
            with ThreadPoolExecutor(max_workers=2) as executor:
                one = executor.submit(approve, first.pk, claims[0].pk)
                self.assertTrue(first_in_dns.wait(10))
                two = executor.submit(approve, second.pk, claims[1].pk, second_started)
                try:
                    self.assertTrue(second_started.wait(10))
                finally:
                    release_first.set()
                results = [one.result(timeout=10), two.result(timeout=10)]

        self.assertEqual(results.count("approved"), 1, results)
        domain.refresh_from_db()
        self.assertEqual(domain.status, OrganizationDomain.STATUS_DNS_VERIFIED)
        self.assertIsNone(domain.activated_at)
        self.assertEqual(domain.recoveries.filter(status="approved").count(), 1)
