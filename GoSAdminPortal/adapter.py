import logging
import os
import threading

from allauth.account.adapter import DefaultAccountAdapter
from django.conf import settings
from django.contrib.auth import get_user_model
from django.db.models import Q
from django.utils.crypto import get_random_string


def _find_or_provision_user_for_email(email):
    """
    Look up a User by email across all registered email sources:
      1. Django User.email (primary)
      2. allauth EmailAddress records
      3. Adult.email (parents, mentors, alumni, volunteers)
      4. Student.personal_email or Student.andrew_email

    If a matching Student or Adult is found but has no linked User, a new
    inactive-until-first-login User is created and linked, and an allauth
    EmailAddress record is added so the login-by-code flow can find it.

    Returns True if the email is allowed (a matching record was found or
    provisioned), False otherwise.
    """
    from allauth.account.models import EmailAddress

    User = get_user_model()
    email_lower = email.strip().lower()

    # 1. Already a known User email
    if User.objects.filter(email__iexact=email_lower).exists():
        return True

    # 2. Already a known allauth EmailAddress
    if EmailAddress.objects.filter(email__iexact=email_lower).exists():
        return True

    # 3. Adult personal_email or andrew_email
    from programs.models import Adult

    adult = Adult.objects.filter(
        Q(personal_email__iexact=email_lower) | Q(andrew_email__iexact=email_lower)
    ).first()
    if adult:
        if adult.user_id:
            _ensure_email_address(adult.user, email_lower)
            return True
        user = _provision_user(email_lower, adult.first_name, adult.last_name)
        adult.user = user
        adult.save(update_fields=["user"])
        _ensure_email_address(user, email_lower)
        return True

    # 4. Student personal_email or andrew_email
    from programs.models import Student

    student = Student.objects.filter(personal_email__iexact=email_lower).first()
    if not student:
        student = Student.objects.filter(andrew_email__iexact=email_lower).first()
    if student:
        if student.user_id:
            _ensure_email_address(student.user, email_lower)
            return True
        # Provision a new User for this student
        first = student.first_name or student.legal_first_name
        user = _provision_user(email_lower, first, student.last_name)
        student.user = user
        student.save(update_fields=["user"])
        _ensure_email_address(user, email_lower)
        return True

    return False


def _provision_user(email, first_name, last_name):
    """Create a new active User with the given email and name."""
    from audit.events import AuditEvent
    from audit.service import log_event

    User = get_user_model()
    user = User.objects.create_user(
        username=email,
        email=email,
        first_name=first_name or "",
        last_name=last_name or "",
    )
    log_event(
        event=AuditEvent.ACCOUNT_CREATED,
        resource=user,
        after={"email": email, "first_name": first_name, "last_name": last_name},
        notes="Auto-provisioned via AccountAdapter.",
    )
    return user


def _ensure_email_address(user, email):
    """Ensure an allauth EmailAddress record exists for this user+email."""
    from allauth.account.models import EmailAddress

    EmailAddress.objects.get_or_create(
        user=user,
        email=email.lower(),
        defaults={"primary": True, "verified": True},
    )


class AccountAdapter(DefaultAccountAdapter):
    def is_open_for_signup(self, request):
        """
        No new accounts should be allowed.
        """
        return False

    def is_email_allowed(self, email):
        """
        Allow login for any email registered to a User, Adult, or Student
        in the system. Auto-provisions a User account if needed.
        """
        return _find_or_provision_user_for_email(email)

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
        # Opt-in: always print attempted login details when explicitly enabled.
        print_always = os.getenv("PRINT_LOGIN_CODE_ALWAYS", "False")
        if template_prefix == "account/email/unknown_account":
            # If explicitly requested, emit a helpful log even for unknown accounts.
            if print_always:
                code = context.get("code")
                code_str = code if code else "(none)"
                logging.info(
                    f"PRINT_LOGIN_CODE_ALWAYS: Attempted login for {email}; template={template_prefix}; code={code_str}"
                )
            return

        # Check if we are in a safe environment to expose the code in logs
        is_staging = "staging" in os.getenv("RENDER_EXTERNAL_HOSTNAME", "")
        code = context.get("code")

        if print_always:
            logging.info(
                f"PRINT_LOGIN_CODE_ALWAYS: Login code for {email} is {code or '(none)'}; template={template_prefix}"
            )
        elif (settings.DEBUG or is_staging) and code:
            logging.info(f"DEBUG/STAGING: Login code for {email} is {code}")

        def _send():
            from django.db import close_old_connections

            try:
                super(AccountAdapter, self).send_mail(template_prefix, email, context)
            except Exception as e:
                logging.error(f"Failed to send email {template_prefix} to {email}: {e}")
            finally:
                close_old_connections()

        if (
            settings.EMAIL_BACKEND == "django.core.mail.backends.locmem.EmailBackend"
            or not getattr(settings, "EMAIL_ASYNC", True)
        ):
            _send()
        else:
            threading.Thread(target=_send, name=f"allauth-email-{email[:20]}").start()

    def format_email_subject(self, subject):
        """
        By default, allauth prepends the site name in brackets.
        We want just the subject.
        """
        return subject

    def get_from_email(self):
        """
        Include the display name if DEFAULT_FROM_NAME is set.
        """
        email = super().get_from_email()
        name = getattr(settings, "DEFAULT_FROM_NAME", None)
        if name and "<" not in email:
            return f'"{name}" <{email}>'
        return email
