from django.db import migrations


def seed_gos_organization(apps, schema_editor):
    """Create the single Girls of Steel organization row (Phase 1 of the
    multi-tenant effort: one hardcoded org, nullable FKs backfilled to it)."""
    Organization = apps.get_model("organizations", "Organization")
    OrganizationSettings = apps.get_model("organizations", "OrganizationSettings")
    org, _ = Organization.objects.get_or_create(
        slug="gos",
        defaults={
            "name": "Girls of Steel",
            "is_active": True,
            "settings": {},
        },
    )
    OrganizationSettings.objects.get_or_create(organization=org)


def unseed_gos_organization(apps, schema_editor):
    Organization = apps.get_model("organizations", "Organization")
    Organization.objects.filter(slug="gos").delete()


class Migration(migrations.Migration):

    dependencies = [
        ("organizations", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(seed_gos_organization, unseed_gos_organization),
    ]
