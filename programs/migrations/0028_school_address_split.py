from django.db import migrations, models


def copy_address_to_street(apps, schema_editor):
    School = apps.get_model("programs", "School")
    for school in School.objects.all():
        # Copy old single-line address into street_address to preserve data
        try:
            old = getattr(school, "address")
        except Exception:
            old = None
        if old and not school.street_address:
            school.street_address = old
            school.save(update_fields=["street_address"])


def noop_reverse(apps, schema_editor):
    # No-op: we won't try to merge the split fields back into a single address
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("programs", "0027_school_district_address"),
    ]

    operations = [
        migrations.AddField(
            model_name="school",
            name="street_address",
            field=models.CharField(max_length=255, blank=True, null=True),
        ),
        migrations.AddField(
            model_name="school",
            name="city",
            field=models.CharField(max_length=100, blank=True, null=True),
        ),
        migrations.AddField(
            model_name="school",
            name="state",
            field=models.CharField(max_length=50, blank=True, null=True),
        ),
        migrations.AddField(
            model_name="school",
            name="zip_code",
            field=models.CharField(max_length=20, blank=True, null=True),
        ),
        migrations.RunPython(copy_address_to_street, noop_reverse),
        migrations.RemoveField(
            model_name="school",
            name="address",
        ),
    ]
