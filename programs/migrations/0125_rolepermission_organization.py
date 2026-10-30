import django.db.models.deletion
from django.db import migrations, models

ORG_SLUG = "gos"


def backfill_role_permissions(apps, schema_editor):
    Organization = apps.get_model("organizations", "Organization")
    RolePermission = apps.get_model("programs", "RolePermission")
    org = Organization.objects.filter(slug=ORG_SLUG).first()
    if org is None:
        return
    RolePermission.objects.filter(organization__isnull=True).update(
        organization_id=org.pk
    )


def unbackfill_role_permissions(apps, schema_editor):
    Organization = apps.get_model("organizations", "Organization")
    RolePermission = apps.get_model("programs", "RolePermission")
    org = Organization.objects.filter(slug=ORG_SLUG).first()
    if org is None:
        return
    RolePermission.objects.filter(organization_id=org.pk).update(organization=None)


class Migration(migrations.Migration):

    dependencies = [
        ("organizations", "0002_seed_gos_organization"),
        ("programs", "0124_backgroundchecktype_age_criteria"),
    ]

    operations = [
        migrations.AddField(
            model_name="rolepermission",
            name="organization",
            field=models.ForeignKey(
                blank=True,
                help_text="Organization that owns this role permission (multi-tenant; null until backfilled).",
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="role_permissions",
                to="organizations.organization",
            ),
        ),
        migrations.AlterUniqueTogether(
            name="rolepermission",
            unique_together={("organization", "role", "section")},
        ),
        migrations.RunPython(
            backfill_role_permissions,
            reverse_code=unbackfill_role_permissions,
        ),
    ]
