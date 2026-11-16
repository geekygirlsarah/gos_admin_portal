from django.db import migrations

LEGACY_ORGANIZATION_SETTINGS = {
    "clearance_types": [
        "PA State Police Criminal Record Check (PATCH)",
        "PA Child Abuse History Clearance (PACA)",
        "FBI Fingerprinting / Affidavit",
    ],
    "portal_title": "Girls of Steel Portal",
    "footer_tagline": "is an outreach program of Carnegie Mellon University.",
    "org_account_enabled": True,
    "org_account_name": "Andrew ID",
    "org_email_domain": "andrew.cmu.edu",
    "force_mentor_org_email": True,
}

LEGACY_EMAIL_SETTINGS = {
    "organization_name": "Girls of Steel Robotics",
    "sender_display_name": "Girls of Steel",
    "contact_email": "leads@girlsofsteelrobotics.org",
    "logo_url": "/static/img/gos_logo.png",
    "address_line1": "Carnegie Mellon University",
    "address_line2": "5000 Forbes Ave, Pittsburgh, PA 15213-3890",
    "tax_id": "25-0969449",
    "website": "cmu.edu",
}


def preserve_legacy_gos_defaults(apps, schema_editor):
    """Materialize old implicit GoS values; reverse is a no-op to preserve edits."""
    database = schema_editor.connection.alias
    Organization = apps.get_model("organizations", "Organization")
    OrganizationSettings = apps.get_model("organizations", "OrganizationSettings")

    organization = Organization.objects.using(database).filter(slug="gos").first()
    if organization is None:
        return

    raw_organization_settings = organization.settings
    organization_settings = (
        dict(raw_organization_settings)
        if isinstance(raw_organization_settings, dict)
        else {}
    )
    for key, value in LEGACY_ORGANIZATION_SETTINGS.items():
        organization_settings.setdefault(key, value)
    if organization_settings != raw_organization_settings:
        organization.settings = organization_settings
        organization.save(using=database, update_fields=["settings"])

    email_settings_row, _ = OrganizationSettings.objects.using(database).get_or_create(
        organization_id=organization.pk,
        defaults={"data": {}},
    )
    raw_data = email_settings_row.data
    data = dict(raw_data) if isinstance(raw_data, dict) else {}
    raw_email_settings = data.get("email_settings")
    email_settings = (
        dict(raw_email_settings) if isinstance(raw_email_settings, dict) else {}
    )
    for key, value in LEGACY_EMAIL_SETTINGS.items():
        email_settings.setdefault(key, value)
    data["email_settings"] = email_settings
    if data != raw_data:
        email_settings_row.data = data
        email_settings_row.save(using=database, update_fields=["data"])


class Migration(migrations.Migration):
    dependencies = [
        (
            "organizations",
            "0011_supportimpersonationhandoff_platform_confirmed_at_and_more",
        )
    ]

    operations = [
        migrations.RunPython(
            preserve_legacy_gos_defaults,
            reverse_code=migrations.RunPython.noop,
        ),
    ]
