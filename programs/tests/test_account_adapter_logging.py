import os
from unittest import mock

from django.test import TestCase, override_settings

from GoSAdminPortal.adapter import AccountAdapter


class AccountAdapterLoggingTests(TestCase):
    @override_settings(DEBUG=False)
    def test_print_login_code_always_logs_for_unknown_account(self):
        adapter = AccountAdapter()
        template = "account/email/unknown_account"
        email = "unknown@example.com"
        context = {}

        with mock.patch.dict(os.environ, {"PRINT_LOGIN_CODE_ALWAYS": "1"}, clear=False):
            with self.assertLogs(level="INFO") as cm:
                adapter.send_mail(template, email, context)

        joined = "\n".join(cm.output)
        self.assertIn("PRINT_LOGIN_CODE_ALWAYS", joined)
        self.assertIn(email, joined)
        self.assertIn("(none)", joined)

    @override_settings(DEBUG=False)
    def test_print_login_code_always_logs_with_code_any_template(self):
        adapter = AccountAdapter()
        template = "account/email/login_code"
        email = "student@example.com"
        context = {"code": "654321"}

        with mock.patch.dict(os.environ, {"PRINT_LOGIN_CODE_ALWAYS": "true"}, clear=False):
            with self.assertLogs(level="INFO") as cm:
                adapter.send_mail(template, email, context)

        joined = "\n".join(cm.output)
        self.assertIn("PRINT_LOGIN_CODE_ALWAYS", joined)
        self.assertIn(email, joined)
        self.assertIn("654321", joined)
