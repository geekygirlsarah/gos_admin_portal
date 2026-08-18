import django.db.models.deletion
from django.db import migrations, models


def backfill_slug(apps, schema_editor):
    """Assign the default slug to any existing MentorAgreement rows."""
    MentorAgreement = apps.get_model("programs", "MentorAgreement")
    MentorAgreement.objects.update(slug="data-access-policy")


class Migration(migrations.Migration):

    dependencies = [
        ("programs", "0100_mentor_agreement"),
    ]

    operations = [
        # 1. Add slug as nullable so we can backfill
        migrations.AddField(
            model_name="mentoragreement",
            name="slug",
            field=models.SlugField(
                help_text="URL-friendly identifier that groups versions of the same document.",
                null=True,
            ),
        ),
        # 2. Add document field (blank allowed)
        migrations.AddField(
            model_name="mentoragreement",
            name="document",
            field=models.FileField(
                blank=True,
                help_text="Uploaded document (PDF, etc.) for the agreement. Leave blank for markdown-only agreements.",
                max_length=255,
                upload_to="",
            ),
        ),
        # 3. Add updated_at
        migrations.AddField(
            model_name="mentoragreement",
            name="updated_at",
            field=models.DateTimeField(auto_now=True),
        ),
        # 4. Backfill slug for existing rows
        migrations.RunPython(backfill_slug, migrations.RunPython.noop),
        # 5. Make slug non-nullable
        migrations.AlterField(
            model_name="mentoragreement",
            name="slug",
            field=models.SlugField(
                help_text="URL-friendly identifier that groups versions of the same document.",
            ),
        ),
        # 6. Remove the global unique constraint on version and add unique_together
        migrations.AlterField(
            model_name="mentoragreement",
            name="version",
            field=models.PositiveIntegerField(),
        ),
        migrations.AlterUniqueTogether(
            name="mentoragreement",
            unique_together={("slug", "version")},
        ),
    ]
