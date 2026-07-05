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
    Role-aware login policy and auto-provisioning.

    Sources checked (with role enforcement when applicable):
      - Adult.personal_email or Adult.andrew_email
        * Mentors: andrew_email only
        * Others: personal_email only
      - Student.personal_email or Student.andrew_email (both allowed)
      - Existing User/EmailAddress fallback (non-modeled accounts)

    If a match is found and allowed by any source, the login proceeds.
    If multiple sources match (e.g. both a Student and an Adult), both are linked
    to the User if possible.

    Returns True if login should proceed for this email, False otherwise.
    """
    from allauth.account.models import EmailAddress

    User = get_user_model()
    email_lower = email.strip().lower()

    # Find candidate modeled accounts
    from programs.models import Adult, Student

    # A. Adult check
    adult_personal = Adult.objects.filter(personal_email__iexact=email_lower).first()
    adult_andrew = Adult.objects.filter(andrew_email__iexact=email_lower).first()
    adult = adult_personal or adult_andrew
    adult_allowed = False
    if adult:
        is_personal = adult_personal is not None
        is_andrew = adult_andrew is not None
        if adult.is_mentor:
            adult_allowed = is_andrew
        elif (
            adult.is_parent
            or adult.is_alumni
            or (not adult.is_parent and not adult.is_alumni and not adult.is_mentor)
        ):
            adult_allowed = is_personal

    # B. Student check
    student = Student.objects.filter(personal_email__iexact=email_lower).first()
    if not student:
        student = Student.objects.filter(andrew_email__iexact=email_lower).first()
    student_allowed = student is not None

    # C. Fallback check
    fallback_allowed = User.objects.filter(
        email__iexact=email_lower
    ).exists() or EmailAddress.objects.filter(email__iexact=email_lower).exists()

    # Determine final allowance
    if not (adult_allowed or student_allowed or fallback_allowed):
        return False

    # Allowed! Now ensure User and link sources.
    # We need a User object. Find existing one from adult, student, or email.
    user = None
    if adult and adult.user_id:
        user = adult.user
    elif student and student.user_id:
        user = student.user
    else:
        user = User.objects.filter(email__iexact=email_lower).first()
        if not user:
            # Check allauth EmailAddress too
            ea = EmailAddress.objects.filter(email__iexact=email_lower).first()
            if ea:
                user = ea.user

    # If still no user, provision one
    if not user:
        first = ""
        last = ""
        if student:
            first = student.first_name or student.legal_first_name
            last = student.last_name
        elif adult:
            first = adult.first_name
            last = adult.last_name
        user = _provision_user(email_lower, first, last)

    # Link Student if matched
    if student and not student.user_id:
        student.user = user
        student.save(update_fields=["user"])

    # Link Adult if matched AND ALLOWED (we only link if the login identifier is valid for the role)
    if adult and adult_allowed and not adult.user_id:
        adult.user = user
        adult.save(update_fields=["user"])

    _ensure_email_address(user, email_lower)
    return True


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

    email_l = email.lower()
    # If already present, nothing to do
    if EmailAddress.objects.filter(user=user, email__iexact=email_l).exists():
        return
    # Only one primary per user: set primary=True only if none exists yet
    has_primary = EmailAddress.objects.filter(user=user, primary=True).exists()
    EmailAddress.objects.create(
        user=user,
        email=email_l,
        primary=not has_primary,
        verified=True,
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
        print_always = os.getenv("PRINT_LOGIN_CODE_ALWAYS", "False").lower() in (
            "1",
            "true",
            "yes",
        )
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
