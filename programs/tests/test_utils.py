import string

from django.test import TestCase

from programs.utils import generate_otp


class UtilsTests(TestCase):
    def test_generate_otp_default_length(self):
        otp = generate_otp()
        self.assertEqual(len(otp), 6)
        self.assertTrue(all(c in string.digits for c in otp))

    def test_generate_otp_custom_length(self):
        otp = generate_otp(length=8)
        self.assertEqual(len(otp), 8)
        self.assertTrue(all(c in string.digits for c in otp))

    def test_generate_otp_is_random(self):
        # Very basic check that it's not returning the same thing every time
        otp1 = generate_otp()
        otp2 = generate_otp()
        self.assertNotEqual(otp1, otp2)
