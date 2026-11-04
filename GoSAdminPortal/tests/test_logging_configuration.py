import logging

from django.conf import settings
from django.test import SimpleTestCase


class LoggingConfigurationTests(SimpleTestCase):
    def test_django_records_are_not_emitted_again_by_the_root_logger(self):
        django_config = settings.LOGGING["loggers"]["django"]
        django_logger = logging.getLogger("django")

        self.assertEqual(django_config["handlers"], ["console"])
        self.assertFalse(django_config["propagate"])
        self.assertFalse(django_logger.propagate)
        self.assertEqual(len(django_logger.handlers), 1)

        request_config = settings.LOGGING["loggers"]["django.request"]
        security_config = settings.LOGGING["loggers"]["django.security"]
        self.assertEqual(request_config["handlers"], ["mail_admins", "console"])
        self.assertFalse(request_config["propagate"])
        self.assertEqual(security_config["handlers"], ["mail_admins", "console"])
        self.assertFalse(security_config["propagate"])

        audit_config = settings.LOGGING["loggers"]["audit"]
        expected_audit_handlers = (
            ["audit_null"]
            if settings.TESTING
            else ["audit_stdout", "audit_stderr"]
        )
        self.assertEqual(audit_config["handlers"], expected_audit_handlers)
        self.assertEqual(audit_config["propagate"], not settings.TESTING)
