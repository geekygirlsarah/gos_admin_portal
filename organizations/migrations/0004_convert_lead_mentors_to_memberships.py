from django.conf import settings
from django.db import migrations, models


def convert_lead_mentors(apps, schema_editor):
    """Ensure all current superusers, staff users, and LeadMentor group members
    have active UserOrganizationMembership records with role='LeadMentor' for the GoS org.
    """
    Organization = apps.get_model("organizations", "Organization")
    UserOrganizationMembership = apps.get_model(
        "organizations", "UserOrganizationMembership"
    )
    User = apps.get_model(*settings.AUTH_USER_MODEL.split("."))
    Group = apps.get_model("auth", "Group")

    gos_org = Organization.objects.filter(slug="gos").first()
    if not gos_org:
        gos_org = Organization.objects.first()
    if not gos_org:
        return

    lead_group = Group.objects.filter(name="LeadMentor").first()
    lead_group_user_ids = set()
    if lead_group:
        lead_group_user_ids = set(lead_group.user_set.values_list("id", flat=True))

    lead_users = User.objects.filter(
        models.Q(is_superuser=True)
        | models.Q(is_staff=True)
        | models.Q(id__in=lead_group_user_ids)
    ).distinct()

    for user in lead_users:
        UserOrganizationMembership.objects.get_or_create(
            user=user,
            organization=gos_org,
            role="LeadMentor",
            defaults={"is_active": True},
        )


def reverse_convert_lead_mentors(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("organizations", "0003_organizationinvitation_userorganizationmembership"),
        ("auth", "__latest__"),
    ]

    operations = [
        migrations.RunPython(convert_lead_mentors, reverse_convert_lead_mentors),
    ]
