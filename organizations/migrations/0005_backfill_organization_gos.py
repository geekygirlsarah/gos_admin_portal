"""Backfill all newly-scoped models to the Girls of Steel organization.

Existing records have ``organization=NULL`` and are visible to all tenants
via ``for_organization(org)`` (which includes ``organization__isnull=True``).
This migration assigns them to the seeded GoS org so a second org does not
see GoS data through unscoped rows.
"""

from django.db import migrations


def backfill_to_gos(apps, schema_editor):
    """Assign all existing rows of newly org-scoped models to the GoS org."""
    Organization = apps.get_model("organizations", "Organization")
    gos = Organization.objects.filter(slug="gos").first()
    if not gos:
        return

    # Orders
    for model_name in ["Vendor", "ShippingCarrier", "Order", "OrderItem"]:
        Model = apps.get_model("orders", model_name)
        Model.objects.filter(organization__isnull=True).update(organization=gos)

    # Travel
    for model_name in [
        "TravelEvent",
        "TravelDeparture",
        "TravelCostOption",
        "TravelSignup",
        "TravelMentorSignup",
        "TravelParentChaperoneSignup",
    ]:
        Model = apps.get_model("travel", model_name)
        Model.objects.filter(organization__isnull=True).update(organization=gos)

    # Badges
    for model_name in ["Badge", "StudentBadge"]:
        Model = apps.get_model("badges", model_name)
        Model.objects.filter(organization__isnull=True).update(organization=gos)

    # Calendar feeds
    for model_name in [
        "CalendarFeed",
        "CalendarFeedACL",
        "CalendarEvent",
        "EventException",
    ]:
        Model = apps.get_model("calendar_feeds", model_name)
        Model.objects.filter(organization__isnull=True).update(organization=gos)

    # Guest forms
    for model_name in ["GuestForm", "GuestFormSubmission"]:
        Model = apps.get_model("guest_forms", model_name)
        Model.objects.filter(organization__isnull=True).update(organization=gos)

    # Programs
    for model_name in [
        "MentorAgreement",
        "MentorAgreementAcceptance",
        "MentorAgreementSubmission",
        "Team",
        "Crew",
        "SubTeam",
        "SchoolDistrict",
        "School",
        "RaceEthnicity",
        "ProgramFeature",
        "AdultStudentRelationship",
        "SlidingScaleSettings",
        "TaxForm",
        "EmailDeliveryEvent",
    ]:
        Model = apps.get_model("programs", model_name)
        Model.objects.filter(organization__isnull=True).update(organization=gos)


def unbackfill(apps, schema_editor):
    """Clear organization backfill (reverse). No-op in practice."""
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("organizations", "0004_convert_lead_mentors_to_memberships"),
        ("orders", "0009_order_organization_orderitem_organization_and_more"),
        ("travel", "0004_travelcostoption_organization_and_more"),
        ("badges", "0002_alter_badge_unique_together_and_more"),
        ("calendar_feeds", "0007_alter_calendarfeedacl_unique_together_and_more"),
        ("guest_forms", "0011_guestform_organization_and_more"),
        ("programs", "0128_remove_adultstudentrelationship_insert_insert_and_more"),
    ]

    operations = [
        migrations.RunPython(backfill_to_gos, unbackfill),
    ]
