import django.db.models.deletion
from django.db import migrations, models

DEFAULT_PA_TYPES = [
    {
        "name": "PA State Police Criminal History (PATCH)",
        "slug_match": "state_police",
        "description": "Pennsylvania State Police criminal history check.",
        "validity_years": 5,
        "applies_to_students": True,
        "applies_to_mentors": True,
        "applies_to_lead_mentors": True,
        "applies_to_parents": False,
        "applies_to_alumni": False,
        "student_min_age": 17,
        "display_order": 1,
    },
    {
        "name": "PA Child Abuse History (Act 151)",
        "slug_match": "child_abuse",
        "description": "Pennsylvania Department of Human Services child abuse clearance.",
        "validity_years": 5,
        "applies_to_students": True,
        "applies_to_mentors": True,
        "applies_to_lead_mentors": True,
        "applies_to_parents": False,
        "applies_to_alumni": False,
        "student_min_age": 17,
        "display_order": 2,
    },
    {
        "name": "FBI Fingerprint (Act 114)",
        "slug_match": "fbi",
        "description": "Federal Bureau of Investigation fingerprint background check.",
        "validity_years": 5,
        "applies_to_students": True,
        "applies_to_mentors": True,
        "applies_to_lead_mentors": True,
        "applies_to_parents": False,
        "applies_to_alumni": False,
        "student_min_age": 17,
        "display_order": 3,
    },
]


def seed_and_migrate_background_checks(apps, schema_editor):
    Organization = apps.get_model("organizations", "Organization")
    BackgroundCheckType = apps.get_model("programs", "BackgroundCheckType")
    BackgroundCheck = apps.get_model("programs", "BackgroundCheck")

    orgs = list(Organization.objects.all())
    # Ensure default types exist for all orgs, or with organization=None
    target_orgs = orgs if orgs else [None]

    type_map = {}  # (org_id, slug_match) -> BackgroundCheckType

    for org in target_orgs:
        org_id = org.pk if org else None
        for item in DEFAULT_PA_TYPES:
            bct, _ = BackgroundCheckType.objects.get_or_create(
                organization_id=org_id,
                name=item["name"],
                defaults={
                    "description": item["description"],
                    "validity_years": item["validity_years"],
                    "applies_to_students": item["applies_to_students"],
                    "applies_to_mentors": item["applies_to_mentors"],
                    "applies_to_lead_mentors": item["applies_to_lead_mentors"],
                    "applies_to_parents": item["applies_to_parents"],
                    "applies_to_alumni": item["applies_to_alumni"],
                    "student_min_age": item["student_min_age"],
                    "display_order": item["display_order"],
                    "is_active": True,
                },
            )
            type_map[(org_id, item["slug_match"])] = bct

    # Fallback default type for any slug
    fallback_types = {
        item["slug_match"]: BackgroundCheckType.objects.filter(
            name=item["name"]
        ).first()
        for item in DEFAULT_PA_TYPES
    }

    # Populate check_type_fk on all existing BackgroundCheck rows
    for check in BackgroundCheck.objects.all():
        org_id = check.organization_id
        old_slug = check.check_type
        bct = type_map.get((org_id, old_slug)) or fallback_types.get(old_slug)
        if not bct:
            # If no exact match, grab or create the first matching by name
            bct = BackgroundCheckType.objects.filter(organization_id=org_id).first()
        if bct:
            check.check_type_fk = bct
            check.save(update_fields=["check_type_fk"])


def reverse_migrate_background_checks(apps, schema_editor):
    BackgroundCheck = apps.get_model("programs", "BackgroundCheck")
    for check in BackgroundCheck.objects.select_related("check_type_fk").all():
        if check.check_type_fk:
            name = check.check_type_fk.name.lower()
            if "state police" in name or "patch" in name:
                check.check_type = "state_police"
            elif "child abuse" in name or "151" in name:
                check.check_type = "child_abuse"
            elif "fbi" in name or "114" in name:
                check.check_type = "fbi"
            else:
                check.check_type = "state_police"
            check.save(update_fields=["check_type"])


class Migration(migrations.Migration):

    dependencies = [
        ("organizations", "0002_seed_gos_organization"),
        ("programs", "0122_alter_studentdocument_file"),
    ]

    operations = [
        migrations.CreateModel(
            name="BackgroundCheckType",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "name",
                    models.CharField(
                        help_text="Name of the background check / clearance type.",
                        max_length=200,
                    ),
                ),
                (
                    "description",
                    models.TextField(
                        blank=True,
                        help_text="Optional description or instructions for obtaining this clearance.",
                    ),
                ),
                (
                    "validity_years",
                    models.PositiveIntegerField(
                        default=5,
                        help_text="Duration in years this clearance remains valid (0 for non-expiring).",
                    ),
                ),
                (
                    "applies_to_students",
                    models.BooleanField(
                        default=True,
                        help_text="Whether this check is required for students.",
                    ),
                ),
                (
                    "applies_to_mentors",
                    models.BooleanField(
                        default=True,
                        help_text="Whether this check is required for mentors.",
                    ),
                ),
                (
                    "applies_to_lead_mentors",
                    models.BooleanField(
                        default=True,
                        help_text="Whether this check is required for lead mentors.",
                    ),
                ),
                (
                    "applies_to_parents",
                    models.BooleanField(
                        default=False,
                        help_text="Whether this check is required for parents.",
                    ),
                ),
                (
                    "applies_to_alumni",
                    models.BooleanField(
                        default=False,
                        help_text="Whether this check is required for alumni.",
                    ),
                ),
                (
                    "student_min_age",
                    models.PositiveIntegerField(
                        blank=True,
                        help_text="For students: minimum age reached by Sept 1 of current academic year to require this check (e.g. 17). Blank means all students.",
                        null=True,
                    ),
                ),
                (
                    "is_active",
                    models.BooleanField(
                        default=True,
                        help_text="Whether this background check type is actively required.",
                    ),
                ),
                (
                    "display_order",
                    models.PositiveIntegerField(
                        default=0,
                        help_text="Order in which this check is displayed in forms and lists.",
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "organization",
                    models.ForeignKey(
                        blank=True,
                        help_text="Organization that owns this background check type.",
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="background_check_types",
                        to="organizations.organization",
                    ),
                ),
            ],
            options={
                "verbose_name": "Background Check Type",
                "verbose_name_plural": "Background Check Types",
                "ordering": ["display_order", "name"],
            },
        ),
        migrations.AddField(
            model_name="backgroundcheck",
            name="check_type_fk",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="checks_temp",
                to="programs.backgroundchecktype",
            ),
        ),
        migrations.RunPython(
            seed_and_migrate_background_checks,
            reverse_code=reverse_migrate_background_checks,
        ),
        migrations.RemoveField(
            model_name="backgroundcheck",
            name="check_type",
        ),
        migrations.RenameField(
            model_name="backgroundcheck",
            old_name="check_type_fk",
            new_name="check_type",
        ),
        migrations.AlterField(
            model_name="backgroundcheck",
            name="check_type",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="checks",
                to="programs.backgroundchecktype",
            ),
        ),
        migrations.AlterModelOptions(
            name="backgroundcheck",
            options={"ordering": ["check_type__display_order", "check_type__name"]},
        ),
    ]
