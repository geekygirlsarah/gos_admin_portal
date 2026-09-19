from django.db import migrations

ORG_SLUG = "gos"
MODELS = [
    "kioskconfig",
    "kioskdevice",
    "rfidcard",
    "attendanceevent",
    "attendancesession",
    "digitalsignoutconfig",
    "studentpresence",
    "digitalsignout",
]


def backfill_organization(apps, schema_editor):
    Organization = apps.get_model("organizations", "Organization")
    org = Organization.objects.filter(slug=ORG_SLUG).first()
    if org is None:
        return
    for model_name in MODELS:
        Model = apps.get_model("attendance", model_name)
        Model.objects.filter(organization__isnull=True).update(organization_id=org.pk)


def unbackfill_organization(apps, schema_editor):
    Organization = apps.get_model("organizations", "Organization")
    org = Organization.objects.filter(slug=ORG_SLUG).first()
    if org is None:
        return
    for model_name in MODELS:
        apps.get_model("attendance", model_name).objects.filter(
            organization_id=org.pk
        ).update(organization=None)


class Migration(migrations.Migration):

    dependencies = [
        ("attendance", "0011_attendanceevent_organization_and_more"),
        ("organizations", "0002_seed_gos_organization"),
    ]

    operations = [
        migrations.RunPython(backfill_organization, unbackfill_organization),
    ]
