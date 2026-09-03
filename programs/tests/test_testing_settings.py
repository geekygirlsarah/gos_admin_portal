from django.conf import settings
from django.contrib.auth.hashers import get_hasher
from django.test import SimpleTestCase


class TestingSettingsTestCase(SimpleTestCase):
    def test_password_hasher_is_md5_during_testing(self):
        """Verify that testing uses the fast MD5PasswordHasher to accelerate tests."""
        self.assertTrue(getattr(settings, "TESTING", False))
        self.assertTrue(hasattr(settings, "PASSWORD_HASHERS"))
        self.assertEqual(
            settings.PASSWORD_HASHERS[0],
            "django.contrib.auth.hashers.MD5PasswordHasher",
        )
        hasher = get_hasher()
        self.assertEqual(hasher.algorithm, "md5")
