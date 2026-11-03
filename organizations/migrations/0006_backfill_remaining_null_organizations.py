"""Assign every remaining unscoped (NULL organization) row to the GoS org.

``for_organization`` is now strict: rows with ``organization=NULL`` are no
longer visible to any tenant. Migration 0005 and the per-app backfills covered
the rows that existed at the time; this sweeps every nullable organization FK
(including orders, vendors, and order items created since) so nothing is left
orphaned. Idempotent, and a no-op if the GoS org has not been seeded.
"""

from django.db import migrations


def backfill_remaining(apps, schema_editor):
    Organization = apps.get_model("organizations", "Organization")
    gos = Organization.objects.filter(slug="gos").first()
    if gos is None:
        return

    for model in apps.get_models():
        if model._meta.app_label == "organizations":
            continue
        fields = {f.name: f for f in model._meta.fields}
        field = fields.get("organization")
        if field is None or not field.null or field.related_model is not Organization:
            continue
        model._base_manager.filter(organization__isnull=True).update(organization=gos)


class Migration(migrations.Migration):

    dependencies = [
        ("organizations", "0005_backfill_organization_gos"),
        ("programs", "0128_remove_adultstudentrelationship_insert_insert_and_more"),
        ("attendance", "0013_alter_digitalsignout_signature"),
        ("applications", "0017_backfill_organization_gos"),
        ("guest_forms", "0011_guestform_organization_and_more"),
        ("outreach", "0015_alter_outreachmentorsignup_options"),
        ("orders", "0009_order_organization_orderitem_organization_and_more"),
        ("travel", "0004_travelcostoption_organization_and_more"),
        ("badges", "0002_alter_badge_unique_together_and_more"),
        ("calendar_feeds", "0007_alter_calendarfeedacl_unique_together_and_more"),
    ]

    operations = [
        migrations.RunPython(backfill_remaining, migrations.RunPython.noop),
    ]
