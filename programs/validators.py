import re

from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _


def validate_phone_number(value):
    """
    Validates that a phone number has exactly 10 digits after stripping non-digits.
    """
    if not value:
        return

    # Strip all non-digit characters
    digits = re.sub(r"\D", "", value)

    # Allow an optional leading US country code (+1 / 1-)
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]

    if len(digits) != 10:
        raise ValidationError(
            _("Phone number must be exactly 10 digits."), code="invalid_phone_number"
        )


def validate_zip_code(value):
    """
    Validates that a ZIP code has exactly 5 digits.
    """
    if not value:
        return

    # Check if the value is exactly 5 digits
    if not re.match(r"^\d{5}$", str(value)):
        raise ValidationError(
            _("ZIP code must be exactly 5 digits."), code="invalid_zip_code"
        )


def validate_andrew_id(value):
    """
    Validates that an Andrew ID starts with a letter and contains only
    lowercase letters and digits (at least 2 characters total).
    """
    if not value:
        return

    value = value.strip().lower()
    if not re.match(r"^[a-z][a-z0-9]{1,}$", value):
        raise ValidationError(
            _(
                "Andrew ID must start with a letter and contain only "
                "lowercase letters and digits (at least 2 characters)."
            ),
            code="invalid_andrew_id",
        )
