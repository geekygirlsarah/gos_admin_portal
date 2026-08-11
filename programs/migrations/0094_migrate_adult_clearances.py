from django.db import migrations

from programs.models import BackgroundCheckType


def _obtained_from_expiration(expiration_date):
    """Derive an obtained date from a known expiration date.

    PA clearances are valid for 5 years, so obtained = expiration - 5 years.
    Returns None when no expiration date is known.
    """
    if not expiration_date:
        return None
    try:
        return expiration_date.replace(year=expiration_date.year - 5)
    except ValueError:
        return expiration_date.replace(year=expiration_date.year - 5, day=28)


def migrate_adult_clearances(apps, schema_editor):
    """Convert the old per-field Adult clearances into BackgroundCheck rows.

    Old fields: has_paca_clearance, has_patch_clearance, has_fbi_clearance and
    the single shared pa_clearances_expiration_date.
    """
    Adult = apps.get_model("programs", "Adult")
    BackgroundCheck = apps.get_model("programs", "BackgroundCheck")

    mappings = [
        ("has_paca_clearance", BackgroundCheckType.CHILD_ABUSE),
        ("has_patch_clearance", BackgroundCheckType.STATE_POLICE),
        ("has_fbi_clearance", BackgroundCheckType.FBI),
    ]

    for adult in Adult.objects.all():
        for field_name, check_type in mappings:
            cleared = getattr(adult, field_name, False)
            if not cleared:
                continue
            BackgroundCheck.objects.create(
                adult=adult,
                check_type=check_type,
                cleared=True,
                obtained_date=_obtained_from_expiration(
                    adult.pa_clearances_expiration_date
                ),
            )


def reverse_migrate_adult_clearances(apps, schema_editor):
    """Reverse: rebuild old flags from BackgroundCheck rows."""
    Adult = apps.get_model("programs", "Adult")
    BackgroundCheck = apps.get_model("programs", "BackgroundCheck")

    mappings = [
        (BackgroundCheckType.CHILD_ABUSE, "has_paca_clearance"),
        (BackgroundCheckType.STATE_POLICE, "has_patch_clearance"),
        (BackgroundCheckType.FBI, "has_fbi_clearance"),
    ]

    for adult in Adult.objects.all():
        checks = BackgroundCheck.objects.filter(adult=adult)
        for check_type, field_name in mappings:
            check = checks.filter(check_type=check_type).first()
            setattr(adult, field_name, bool(check and check.cleared))
        expires = [c.expiration_date for c in checks if c.expiration_date and c.cleared]
        adult.pa_clearances_expiration_date = max(expires) if expires else None
        adult.save()


class Migration(migrations.Migration):

    dependencies = [
        ("programs", "0093_backgroundcheck"),
    ]

    operations = [
        migrations.RunPython(
            migrate_adult_clearances,
            reverse_code=reverse_migrate_adult_clearances,
        ),
    ]
