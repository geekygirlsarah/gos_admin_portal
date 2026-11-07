from django.contrib.auth.models import User
from django.db.models import ProtectedError
from django.test import TestCase
from django.utils import timezone

from organizations.models import (
    Organization,
    OrganizationDomain,
    OrganizationDomainRecovery,
)


class OrganizationDomainDeletionTests(TestCase):
    def test_domain_reservations_protect_organizations_in_every_state(self):
        operator = User.objects.create_user(username="domain-operator")
        for status, _ in OrganizationDomain.STATUS_CHOICES:
            with self.subTest(status=status):
                organization = Organization.objects.create(
                    name=f"Team {status}",
                    slug=status,
                    subdomain=status.replace("_", "-"),
                )
                fields = {
                    "organization": organization,
                    "hostname": f"{status.replace('_', '-')}.example.org",
                    "status": status,
                }
                if status in (
                    OrganizationDomain.STATUS_DNS_VERIFIED,
                    OrganizationDomain.STATUS_ACTIVE,
                ):
                    fields["dns_verified_at"] = timezone.now()
                if status == OrganizationDomain.STATUS_ACTIVE:
                    fields.update(activated_at=timezone.now(), activated_by=operator)
                domain = OrganizationDomain.objects.create(**fields)

                with self.assertRaises(ProtectedError) as error:
                    organization.delete()

                self.assertIn(domain, error.exception.protected_objects)
                self.assertTrue(
                    Organization.objects.filter(pk=organization.pk).exists()
                )
                self.assertTrue(
                    OrganizationDomain.objects.filter(pk=domain.pk).exists()
                )

    def test_recovery_history_protects_original_owner_new_owner_and_domain(self):
        original = Organization.objects.create(
            name="Original Team", slug="original-team", subdomain="original-team"
        )
        new_owner = Organization.objects.create(
            name="New Team", slug="new-team", subdomain="new-team"
        )
        domain = OrganizationDomain.objects.create(
            organization=new_owner, hostname="recovered.example.org"
        )
        recovery = OrganizationDomainRecovery.objects.create(
            organization=new_owner,
            original_organization=original,
            domain=domain,
            hostname=domain.hostname,
            original_token_digest="a" * 64,
            status=OrganizationDomainRecovery.STATUS_APPROVED,
        )

        for record in (original, new_owner, domain):
            with self.subTest(record=record):
                with self.assertRaises(ProtectedError) as error:
                    record.delete()
                self.assertIn(recovery, error.exception.protected_objects)

        recovery.refresh_from_db()
        self.assertEqual(recovery.original_organization, original)
        self.assertEqual(recovery.organization, new_owner)
        self.assertEqual(recovery.domain, domain)
