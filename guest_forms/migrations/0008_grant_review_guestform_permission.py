"""Grant the ``guest_forms.review_guestform`` permission to the LeadMentor group.

Migration 0003 attempted this but looked up the ContentType via ``objects.filter``,
which is not guaranteed to exist yet during a fresh ``migrate`` (ContentTypes are
only created by the ``post_migrate`` signal, which fires after all migrations).
On a fresh database that lookup returned nothing, so the permission was silently
never granted to Lead Mentors.

This migration uses ``get_or_create`` for the ContentType/permission so it works
regardless of ContentType timing, and is idempotent.
"""

from __future__ import annotations

from django.db import migrations

LEAD_MENTOR_GROUP = "LeadMentor"
REVIEW_PERM_CODENAME = "review_guestform"


def assign_permission(apps, schema_editor):
    Group = apps.get_model("auth", "Group")
    Permission = apps.get_model("auth", "Permission")
    ContentType = apps.get_model("contenttypes", "ContentType")

    # get_or_create guarantees the ContentType row exists even during migrate,
    # when the post_migrate signal (which normally creates ContentTypes) has not
    # run yet.
    ct, _ = ContentType.objects.get_or_create(
        app_label="guest_forms", model="guestform"
    )
    perm, _ = Permission.objects.get_or_create(
        content_type=ct,
        codename=REVIEW_PERM_CODENAME,
        defaults={"name": "Can review guest form submissions"},
    )

    lead_mentor_group, _ = Group.objects.get_or_create(name=LEAD_MENTOR_GROUP)
    lead_mentor_group.permissions.add(perm)


def reverse_assign(apps, schema_editor):
    """Remove the permission from the LeadMentor group for reversibility."""
    Group = apps.get_model("auth", "Group")
    Permission = apps.get_model("auth", "Permission")
    ContentType = apps.get_model("contenttypes", "ContentType")

    ct = ContentType.objects.filter(app_label="guest_forms", model="guestform").first()
    if ct is None:
        return

    perm = Permission.objects.filter(
        content_type=ct, codename=REVIEW_PERM_CODENAME
    ).first()

    lead_mentor_group = Group.objects.filter(name=LEAD_MENTOR_GROUP).first()
    if lead_mentor_group is not None and perm is not None:
        lead_mentor_group.permissions.remove(perm)


class Migration(migrations.Migration):
    dependencies = [
        ("guest_forms", "0007_alter_guestform_safety_guidelines_url"),
        ("auth", "0012_alter_user_first_name_max_length"),
        ("contenttypes", "0002_remove_content_type_name"),
    ]

    operations = [
        migrations.RunPython(assign_permission, reverse_assign),
    ]
