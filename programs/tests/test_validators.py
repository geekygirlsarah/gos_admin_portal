"""Tests for programs.validators."""

from django.core.exceptions import ValidationError
from django.test import SimpleTestCase

from programs.validators import validate_phone_number


class ValidatePhoneNumberTests(SimpleTestCase):
    def test_plain_10_digits_is_valid(self):
        validate_phone_number("4125551234")

    def test_formatted_10_digits_is_valid(self):
        validate_phone_number("(412) 555-1234")

    def test_leading_country_code_with_plus_is_valid(self):
        """Regression: guests typing +1 before their 10-digit number were rejected."""
        validate_phone_number("+14125551234")

    def test_leading_country_code_without_plus_is_valid(self):
        validate_phone_number("1 412 555 1234")

    def test_too_few_digits_is_invalid(self):
        with self.assertRaises(ValidationError):
            validate_phone_number("412555123")

    def test_too_many_digits_is_invalid(self):
        with self.assertRaises(ValidationError):
            validate_phone_number("41255512345")
