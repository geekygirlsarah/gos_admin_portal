import logging
import os

from allauth.account.adapter import DefaultAccountAdapter
from django.conf import settings
from django.contrib.auth import get_user_model
from django.utils.crypto import get_random_string


class AccountAdapter(DefaultAccountAdapter):
    def is_open_for_signup(self, request):
        """
        No new accounts should be allowed.
        """
        return False

    def is_email_allowed(self, email):
        """
        Only allow emails that already exist in the system.
        """
        User = get_user_model()
        return User.objects.filter(email__iexact=email).exists()

    def generate_login_code(self) -> str:
        """
        Generates a 6-digit login code.
        """
        return get_random_string(length=6, allowed_chars="0123456789")

    def send_mail(self, template_prefix, email, context):
        """
        Suppresses the 'unknown_account' email to avoid SMTPRecipientsRefused
        or other issues with invalid/non-existent emails.
        Also handles SMTP failures gracefully in staging/debug by logging the code.
        """
        logging.debug(
            f"DEBUG: send_mail called with template_prefix={template_prefix}, email={email}"
        )
        if template_prefix == "account/email/unknown_account":
            return

        try:
            super().send_mail(template_prefix, email, context)
        except Exception as e:
            logging.error(f"Failed to send email {template_prefix} to {email}: {e}")

            # Check if we are in a safe environment to expose the code in logs
            is_staging = "staging" in os.getenv("RENDER_EXTERNAL_HOSTNAME", "")
            if settings.DEBUG or is_staging:
                code = context.get("code")
                if code:
                    logging.info(f"STAGING/DEBUG FALLBACK: Login code for {email} is {code}")
                # We return instead of re-raising so the user is not greeted with a 500 error.
                # They can check the logs to get their code.
                return

            # In production, we still want to know it failed
            raise

    def format_email_subject(self, subject):
        """
        By default, allauth prepends the site name in brackets.
        We want just the subject.
        """
        return subject
