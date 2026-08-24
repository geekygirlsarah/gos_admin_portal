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

    def test_empty_string_is_valid(self):
        validate_phone_number("")

    def test_none_is_valid(self):
        validate_phone_number(None)

    def test_country_code_11_digits_starting_with_1_is_valid(self):
        validate_phone_number("14125551234")

    def test_country_code_with_dashes_is_valid(self):
        validate_phone_number("1-412-555-1234")

    def test_plus_one_with_formatting_is_valid(self):
        validate_phone_number("+1 (412) 555-1234")

    def test_12_digits_with_leading_1_is_invalid(self):
        """11-digit country code + extra digit = too many."""
        with self.assertRaises(ValidationError):
            validate_phone_number("141255512345")

    def test_all_special_chars_is_invalid(self):
        """Non-digit chars that strip to empty string = 0 digits."""
        with self.assertRaises(ValidationError):
            validate_phone_number("()- ")

    def test_too_few_digits_is_invalid(self):
        with self.assertRaises(ValidationError):
            validate_phone_number("412555123")

    def test_too_many_digits_is_invalid(self):
        with self.assertRaises(ValidationError):
            validate_phone_number("41255512345")

    def test_eleven_digits_not_starting_with_1_is_invalid(self):
        """11 digits but leading digit is not '1' — not a valid country code."""
        with self.assertRaises(ValidationError):
            validate_phone_number("21255512345")

    def test_nine_digits_is_invalid(self):
        with self.assertRaises(ValidationError):
            validate_phone_number("41255512")

    def test_thirteen_digits_is_invalid(self):
        with self.assertRaises(ValidationError):
            validate_phone_number("4125551234567")
