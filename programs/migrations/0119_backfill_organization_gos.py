from django.db import migrations

ORG_SLUG = "gos"
MODELS = [
    "program",
    "student",
    "adult",
    "enrollment",
    "fee",
    "feeassignment",
    "payment",
    "slidingscale",
    "backgroundcheck",
]


def backfill_organization(apps, schema_editor):
    """Backfill every existing programs row to the GoS organization.

    The FK is nullable and scoping isn't enforced yet, but assigning the
    single org now gives Phase 2 (and the hardcoded middleware) real data
    to work with.
    """
    Organization = apps.get_model("organizations", "Organization")
    org = Organization.objects.filter(slug=ORG_SLUG).first()
    if org is None:
        return
    for model_name in MODELS:
        Model = apps.get_model("programs", model_name)
        Model.objects.filter(organization__isnull=True).update(organization_id=org.pk)


def unbackfill_organization(apps, schema_editor):
    Organization = apps.get_model("organizations", "Organization")
    org = Organization.objects.filter(slug=ORG_SLUG).first()
    if org is None:
        return
    for model_name in MODELS:
        apps.get_model("programs", model_name).objects.filter(
            organization_id=org.pk
        ).update(organization=None)


class Migration(migrations.Migration):

    dependencies = [
        ("programs", "0118_remove_adult_insert_insert_and_more"),
        ("organizations", "0002_seed_gos_organization"),
    ]

    operations = [
        migrations.RunPython(backfill_organization, unbackfill_organization),
    ]
