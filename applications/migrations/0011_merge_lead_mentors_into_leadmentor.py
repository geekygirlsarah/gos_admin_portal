"""Merge the 'Lead Mentors' group into the 'LeadMentor' group.

Previously, two separate Django groups existed for Lead Mentors:
- "LeadMentor" (no space): used throughout programs/ for role-based access
- "Lead Mentors" (with space): created by migration 0004, used only in
  applications/ for the review_application permission

This migration:
1. Ensures the 'LeadMentor' group (created by programs signals) carries the
   review_application permission.
2. Removes the now-redundant 'Lead Mentors' group. Any users who were only in
   'Lead Mentors' are migrated to 'LeadMentor' first.
"""

from __future__ import annotations

from django.db import migrations

OLD_GROUP = "Lead Mentors"
NEW_GROUP = "LeadMentor"
REVIEW_PERM_CODENAME = "review_application"


def merge_groups(apps, schema_editor):
    Group = apps.get_model("auth", "Group")
    Permission = apps.get_model("auth", "Permission")
    ContentType = apps.get_model("contenttypes", "ContentType")

    # Find the review_application permission
    app_ct = ContentType.objects.filter(
        app_label="applications", model="application"
    ).first()
    if app_ct is None:
        return  # Nothing to migrate

    perm = Permission.objects.filter(
        content_type=app_ct, codename=REVIEW_PERM_CODENAME
    ).first()

    # Get or create the canonical LeadMentor group
    lead_mentor_group, _ = Group.objects.get_or_create(name=NEW_GROUP)

    # Grant the review_application permission to LeadMentor
    if perm is not None:
        lead_mentor_group.permissions.add(perm)

    # Migrate any users from the old 'Lead Mentors' group to 'LeadMentor',
    # then delete the old group
    old_group = Group.objects.filter(name=OLD_GROUP).first()
    if old_group is not None:
        # Move users who are in the old group but not yet in the new one
        for user in old_group.user_set.all():
            user.groups.add(lead_mentor_group)
        old_group.delete()


def reverse_merge(apps, schema_editor):
    """Recreate the old 'Lead Mentors' group for reversibility.

    We cannot restore original group membership, but we can re-create
    the group with its permission so the permission system works again.
    """
    Group = apps.get_model("auth", "Group")
    Permission = apps.get_model("auth", "Permission")
    ContentType = apps.get_model("contenttypes", "ContentType")

    app_ct = ContentType.objects.filter(
        app_label="applications", model="application"
    ).first()
    if app_ct is None:
        return

    perm = Permission.objects.filter(
        content_type=app_ct, codename=REVIEW_PERM_CODENAME
    ).first()

    old_group, _ = Group.objects.get_or_create(name=OLD_GROUP)
    if perm is not None:
        old_group.permissions.add(perm)

    # Remove the review permission from LeadMentor (restoring old state)
    lead_mentor_group = Group.objects.filter(name=NEW_GROUP).first()
    if lead_mentor_group is not None and perm is not None:
        lead_mentor_group.permissions.remove(perm)


class Migration(migrations.Migration):
    dependencies = [
        ("applications", "0010_applicationevent_application_insert_insert_and_more"),
        ("auth", "0012_alter_user_first_name_max_length"),
        ("contenttypes", "0002_remove_content_type_name"),
    ]

    operations = [
        migrations.RunPython(merge_groups, reverse_merge),
    ]
