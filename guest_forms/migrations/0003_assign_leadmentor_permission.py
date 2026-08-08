"""Assign the ``guest_forms.review_guestform`` permission to the LeadMentor group."""

from __future__ import annotations

from django.db import migrations

LEAD_MENTOR_GROUP = "LeadMentor"
REVIEW_PERM_CODENAME = "review_guestform"


def assign_permission(apps, schema_editor):
    Group = apps.get_model("auth", "Group")
    Permission = apps.get_model("auth", "Permission")
    ContentType = apps.get_model("contenttypes", "ContentType")

    # Find the review_guestform permission
    ct = ContentType.objects.filter(app_label="guest_forms", model="guestform").first()
    if ct is None:
        return  # Nothing to migrate

    perm = Permission.objects.filter(
        content_type=ct, codename=REVIEW_PERM_CODENAME
    ).first()

    # Get or create the canonical LeadMentor group
    lead_mentor_group, _ = Group.objects.get_or_create(name=LEAD_MENTOR_GROUP)

    # Grant the review_guestform permission to LeadMentor
    if perm is not None:
        lead_mentor_group.permissions.add(perm)


def reverse_assign(apps, schema_editor):
    """Remove the permission from LeadMentor group for reversibility."""
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
        ("guest_forms", "0002_alter_guestform_options"),
        ("auth", "0012_alter_user_first_name_max_length"),
        ("contenttypes", "0002_remove_content_type_name"),
    ]

    operations = [
        migrations.RunPython(assign_permission, reverse_assign),
    ]
