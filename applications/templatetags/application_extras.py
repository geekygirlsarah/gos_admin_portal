"""Template helpers for the application wizard."""

from django import template

from programs.utils import (
    calculate_grade,
    format_grade,
)

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


# Defines the order in which fields should be displayed on the review page.
# Fields not in this list will appear at the end in their original order.
PREFERRED_FIELD_ORDER = [
    "legal_first_name",
    "first_name",
    "last_name",
    "pronouns",
    "date_of_birth",
    "email",
    "personal_email",
    "andrew_email",
    "phone",
    "phone_number",
    "cell_phone",
    "cell_phone_number",
    "home_phone",
    "address",
    "address_line_1",
    "address_line_2",
    "address_street",
    "address_city",
    "address_state",
    "address_zip",
    "city",
    "state",
    "zip_code",
    "school_name",
    "graduation_year",
    "race_ethnicities",
    "tshirt_size",
    "shirt_size",
    "discord_handle",
    "discord_username",
    "andrew_id",
    "employer",
]


@register.filter(name="order_step_fields")
def order_step_fields(step_data):
    """Return a list of (field, value) pairs ordered by PREFERRED_FIELD_ORDER."""
    if not isinstance(step_data, dict):
        return []

    # Identify fields that are in our preferred list and exist in step_data
    ordered_items = []
    seen_fields = set()

    for field in PREFERRED_FIELD_ORDER:
        if field in step_data:
            ordered_items.append((field, step_data[field]))
            seen_fields.add(field)

    # Add any remaining fields that were not in the preferred list
    for field, value in step_data.items():
        if field not in seen_fields:
            ordered_items.append((field, value))

    return ordered_items


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
    if isinstance(value, bool):
        return "Yes" if value else "No"

    if not value:
        return "—"

    if key == "race_ethnicities" and isinstance(value, list):
        from programs.models import RaceEthnicity

        names = RaceEthnicity.objects.filter(pk__in=value).values_list(
            "name", flat=True
        )
        return ", ".join(names) or "—"

    if isinstance(value, bool):
        return "Yes" if value else "No"

    return value


@register.filter(name="get_grade")
def get_grade(step5_data, application=None):
    grade = step5_data.get("grade")
    if grade is not None and grade != "":
        return format_grade(grade)

    grad_year = step5_data.get("graduation_year")
    if grad_year:
        try:
            ref_date = (
                application.program.start_date
                if application and application.program
                else None
            )
            grade = calculate_grade(int(grad_year), ref_date)
            return format_grade(grade)
        except (ValueError, TypeError):
            pass
    return "—"
