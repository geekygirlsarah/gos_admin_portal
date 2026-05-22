"""Template helpers for the application wizard."""

from django import template

register = template.Library()


# Friendly labels for known field keys used in the saved step data.
# Anything not listed falls back to humanizing the snake_case key.
FIELD_LABELS = {
    "legal_first_name": "Legal first name",
    "legal_last_name": "Legal last name",
    "preferred_first_name": "Preferred first name",
    "preferred_last_name": "Preferred last name",
    "first_name": "First name",
    "last_name": "Last name",
    "middle_name": "Middle name",
    "date_of_birth": "Date of birth",
    "school_name": "School",
    "graduation_year": "Graduation year",
    "andrew_id": "Andrew ID",
    "discord_username": "Discord username",
    "phone": "Phone",
    "phone_number": "Phone number",
    "email": "Email",
    "email_address": "Email address",
    "personal_email": "Personal email",
    "andrew_email": "Andrew email",
    "race_ethnicity": "Race / ethnicity",
    "race_ethnicities": "Race / Ethnicity",
    "pronouns": "Pronouns",
    "address_line_1": "Address line 1",
    "address_line_2": "Address line 2",
    "address_street": "Street address",
    "address_city": "City",
    "address_state": "State",
    "address_zip": "ZIP code",
    "city": "City",
    "state": "State",
    "zip_code": "ZIP code",
    "address": "Address",
    "email_updates": "Receive email updates",
    "relationship": "Relationship to student",
    "medical_info": "Medical information",
    "medical_notes": "Medical notes",
    "shirt_size": "Shirt size",
    "notes": "Notes",
}


@register.filter(name="humanize_field")
def humanize_field(value):
    """Turn a snake_case field key into a friendly label.

    Examples:
        legal_first_name -> Legal first name
        andrew_id        -> Andrew ID
        email            -> Email
    """
    if value is None:
        return ""
    key = str(value)
    if key in FIELD_LABELS:
        return FIELD_LABELS[key]
    # Fallback: replace underscores with spaces and capitalize first letter.
    text = key.replace("_", " ").strip()
    if not text:
        return ""
    return text[:1].upper() + text[1:]


@register.filter(name="format_application_value")
def format_application_value(value, key):
    """Format a saved form value for display on the review page.
    Resolves FK/M2M IDs to names where appropriate.
    """
    if not value:
        return "—"

    if key == "race_ethnicities" and isinstance(value, list):
        from programs.models import RaceEthnicity

        names = RaceEthnicity.objects.filter(pk__in=value).values_list("name", flat=True)
        return ", ".join(names) or "—"

    if isinstance(value, bool):
        return "Yes" if value else "No"

    return value
