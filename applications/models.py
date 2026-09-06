"""Models for the public application wizard.

This app supersedes the previous lightweight ``StudentApplication`` model
in ``programs``. It supports a multi-step, resumable wizard for students,
parents and prospective mentors.
"""

from __future__ import annotations

import enum
import secrets

import pghistory
from django.contrib.auth.hashers import check_password, make_password
from django.db import models
from django.utils import timezone

from programs.constants import (
    APP_ID_ALPHABET,
    APP_ID_LENGTH,
    OTP_LENGTH,
    OTP_TTL_SECONDS,
)


def generate_application_id() -> str:
    """Return a random 8-character application id from APP_ID_ALPHABET."""
    return "".join(secrets.choice(APP_ID_ALPHABET) for _ in range(APP_ID_LENGTH))


# --- Legacy step-key migration ----------------------------------------------
#
# The wizard originally stored each step under ``step5`` / ``step6`` /
# ``step7`` / ``step8`` / ``step6_handoff`` and later renamed those keys to
# ``step5-student`` / ``step6-experience`` / ``step7-primaryparent`` /
# ``step8-secondaryparent`` / ``step7_handoff`` (without a data migration).
# Applications created before the rename therefore hold their data under the
# legacy keys, and any code that only reads the current names would silently
# lose that data. These helpers merge the legacy keys into their current names
# wherever we load, display, or convert an application's data.


LEGACY_STEP_KEY_MAP = {
    "step5": "step5-student",
    "step6": "step6-experience",
    "step7": "step7-primaryparent",
    "step8": "step8-secondaryparent",
    "step6_handoff": "step7_handoff",
}


def _is_step_value_empty(value) -> bool:
    """Whether a captured value can be treated as "no real data".

    Matches the heuristic the review edit form uses for "touched" fields:
    ``None``, empty strings, empty collections and ``False`` all count as empty
    (so legacy data that only holds default checkbox values never wins a merge).
    """
    if value in (None, "", False):
        return True
    if isinstance(value, (list, tuple, set, dict)):
        return not value
    if isinstance(value, str):
        return not value.strip()
    return False


def _step_data_score(step: dict) -> tuple:
    """A completeness score for one step's data dict.

    Returns ``(filled values, total keys)``. ``filled`` counts values that are
    not empty, ``total`` breaks ties by favouring the dict that knows about
    more fields. Used to pick the richer of two dicts for the same step.
    """
    step = step or {}
    filled = sum(1 for v in step.values() if not _is_step_value_empty(v))
    return filled, len(step)


def normalize_step_keys(data) -> dict:
    """Merge any legacy step keys in ``data`` into their current names.

    When the same step exists under both a legacy and a current key (which
    happens when the pre-fix edit page re-saved a legacy application under the
    current names), the more complete dict is kept and the other dropped;
    ties go to the current key. All non-step keys are left untouched.
    """
    normalized = dict(data or {})
    for legacy_key, current_key in LEGACY_STEP_KEY_MAP.items():
        legacy_step = normalized.pop(legacy_key, None)
        if legacy_step is None:
            continue
        if not isinstance(legacy_step, dict):
            legacy_step = {}
        current_step = normalized.get(current_key)
        if isinstance(current_step, dict):
            if _step_data_score(legacy_step) > _step_data_score(current_step):
                normalized[current_key] = legacy_step
        else:
            normalized[current_key] = legacy_step
    return normalized


# --- OTP helpers ------------------------------------------------------------


def generate_otp_code() -> str:
    """Return a 6-digit numeric OTP as a zero-padded string."""
    return f"{secrets.randbelow(10 ** OTP_LENGTH):0{OTP_LENGTH}d}"


class OtpVerifyResult(enum.Enum):
    """Outcome of an OTP verification attempt.

    Distinct results let the caller show accurate messages instead of
    collapsing every failure into a generic "didn't match or expired".
    """

    SUCCESS = "success"
    NO_CODE = "no_code"
    EXPIRED = "expired"
    INVALID = "invalid"
    TOO_MANY_ATTEMPTS = "too_many_attempts"


# --- SiteSettings -----------------------------------------------------------


class SiteSettings(models.Model):
    """Singleton model for portal-wide configurable text.

    For now this only holds the welcome message shown on Step 1 of the
    application wizard, but it is intended to grow.
    """

    DEFAULT_WELCOME = (
        "Welcome to the Girls of Steel application system! "
        "Use this wizard to apply for one of our upcoming programs or start the process "
        "of becoming a mentor."
        "If you started an application earlier, you can resume it below "
        "with your application ID."
    )

    welcome_message = models.TextField(
        default=DEFAULT_WELCOME,
        help_text=("Message shown on the first page of the public application wizard."),
    )

    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Site settings"
        verbose_name_plural = "Site settings"

    def __str__(self) -> str:  # pragma: no cover - trivial
        return "Site settings"

    def save(self, *args, **kwargs):
        # Enforce singleton: always pk=1
        self.pk = 1
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):  # pragma: no cover - protected
        # Don't allow deletion through the singleton.
        return None

    @classmethod
    def load(cls) -> "SiteSettings":
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj


# --- Application ------------------------------------------------------------


@pghistory.track()
class Application(models.Model):
    """A single in-progress or completed application to a Program."""

    class Type(models.TextChoices):
        STUDENT = "student", "Student"
        PARENT = "parent", "Parent / Guardian"
        MENTOR = "mentor", "Mentor / Volunteer"

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"  # in progress, email not yet verified
        EMAIL_VERIFIED = "email_verified", "Email verified"
        AWAITING_PARENT = "awaiting_parent", "Awaiting parent"
        SUBMITTED = "submitted", "Submitted"
        APPROVED = "approved", "App Approved"
        APPROVED_SIGNED = "approved_signed", "Approved + Signed"
        CONVERTED = "converted", "Converted to Student"
        DECLINED = "declined", "Declined"

    application_id = models.CharField(
        max_length=APP_ID_LENGTH,
        unique=True,
        db_index=True,
        editable=False,
        help_text="Public 8-character code used to resume the application.",
    )

    applicant_type = models.CharField(
        max_length=16,
        choices=Type.choices,
        blank=True,
        help_text="Who is filling out this application.",
    )

    email = models.EmailField(
        blank=True,
        help_text="Primary email used to verify and resume the application.",
    )
    email_verified_at = models.DateTimeField(blank=True, null=True)

    program = models.ForeignKey(
        "programs.Program",
        on_delete=models.PROTECT,
        related_name="applications",
        blank=True,
        null=True,
    )

    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.DRAFT,
        db_index=True,
    )

    # Step the user is currently on (1-9). Used to redirect them back into
    # the wizard at the right point on resume.
    current_step = models.PositiveSmallIntegerField(default=1)

    # Free-form bag of partial form data captured per step. Each step writes
    # its own sub-key (e.g. {"step5": {...}, "step6": {...}}). Keeping this
    # loose lets us iterate on individual steps without schema churn.
    data = models.JSONField(default=dict, blank=True)

    # OTP for email verification: hashed code + expiry.
    otp_hash = models.CharField(max_length=128, blank=True, default="")
    otp_expires_at = models.DateTimeField(blank=True, null=True)
    otp_attempts = models.PositiveSmallIntegerField(default=0)

    # Secret token for parent handoff. When a student hands off to a parent,
    # this token is included in the email and must be present to resume.
    handoff_token = models.CharField(
        max_length=64,
        blank=True,
        default="",
        help_text="Secret token required for parent handoff access.",
    )

    # Lead-mentor review fields
    decline_reason = models.TextField(blank=True, default="")
    reviewed_at = models.DateTimeField(blank=True, null=True)
    reviewed_by = models.ForeignKey(
        "auth.User",
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="reviewed_applications",
    )

    # Set when a lead mentor converts an approved application into a real
    # Student record + Enrollment in the program.
    converted_student = models.ForeignKey(
        "programs.Student",
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="source_applications",
    )
    converted_at = models.DateTimeField(blank=True, null=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    submitted_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(
                fields=["program", "status"], name="application_program_status_idx"
            ),
            models.Index(fields=["email"], name="application_email_idx"),
            models.Index(
                fields=["status", "submitted_at"], name="application_status_sub_idx"
            ),
            models.Index(fields=["created_at"], name="application_created_at_idx"),
        ]
        permissions = [
            (
                "review_application",
                "Can review applications (approve / decline / edit / delete)",
            ),
        ]

    def __str__(self) -> str:  # pragma: no cover - trivial
        who = self.email or "(no email)"
        return f"Application {self.application_id} [{self.applicant_type or '?'}] {who}"

    # -- Application ID -----------------------------------------------------

    def save(self, *args, **kwargs):
        if not self.application_id:
            self.application_id = self._generate_unique_application_id()
        super().save(*args, **kwargs)

    @classmethod
    def _generate_unique_application_id(cls, max_attempts: int = 16) -> str:
        for _ in range(max_attempts):
            candidate = generate_application_id()
            if not cls.objects.filter(application_id=candidate).exists():
                return candidate
        # Astronomically unlikely with 31**8 ≈ 8.5e11 keyspace, but be defensive.
        raise RuntimeError(
            "Unable to allocate a unique application_id after "
            f"{max_attempts} attempts."
        )

    # -- Data normalization --------------------------------------------------

    def normalize_step_data(self, save: bool = False) -> dict:
        """Migrate legacy step keys in ``self.data`` to their current names.

        Applications started before the ``step5`` → ``step5-student`` key
        rename hold their data under the legacy keys; review / edit / convert
        code only understands the current names. This method merges the legacy
        keys (``normalize_step_keys``) into ``self.data`` and, with
        ``save=True``, persists the result so a stale legacy copy can never
        reappear next load. Returns the normalized ``data`` dict.
        """
        normalized = normalize_step_keys(self.data)
        if normalized == self.data:
            return self.data
        self.data = normalized
        if save:
            self.save(update_fields=["data", "updated_at"])
        return self.data

    # -- OTP ----------------------------------------------------------------

    def issue_otp(self) -> str:
        """Generate, hash and store a fresh OTP. Returns the plain code.

        The plain code is *only* returned here so the caller can email it;
        it is never persisted in plaintext.
        """
        code = generate_otp_code()
        self.otp_hash = make_password(code)
        self.otp_expires_at = timezone.now() + timezone.timedelta(
            seconds=OTP_TTL_SECONDS
        )
        self.otp_attempts = 0
        self.save(
            update_fields=["otp_hash", "otp_expires_at", "otp_attempts", "updated_at"]
        )
        return code

    def verify_otp(self, code: str) -> OtpVerifyResult:
        """Check ``code`` against the stored hash.

        Returns an :class:`OtpVerifyResult` describing the outcome. On success
        the OTP is cleared and the email is marked as verified.
        """
        if not self.otp_hash or not self.otp_expires_at:
            return OtpVerifyResult.NO_CODE
        if timezone.now() > self.otp_expires_at:
            return OtpVerifyResult.EXPIRED
        # Cap brute-force attempts.
        self.otp_attempts = (self.otp_attempts or 0) + 1
        if self.otp_attempts > 10:
            self.save(update_fields=["otp_attempts", "updated_at"])
            return OtpVerifyResult.TOO_MANY_ATTEMPTS
        if not check_password((code or "").strip(), self.otp_hash):
            self.save(update_fields=["otp_attempts", "updated_at"])
            return OtpVerifyResult.INVALID
        self.otp_hash = ""
        self.otp_expires_at = None
        self.otp_attempts = 0
        self.email_verified_at = timezone.now()
        if self.status == self.Status.DRAFT:
            self.status = self.Status.EMAIL_VERIFIED
        self.save(
            update_fields=[
                "otp_hash",
                "otp_expires_at",
                "otp_attempts",
                "email_verified_at",
                "status",
                "updated_at",
            ]
        )
        return OtpVerifyResult.SUCCESS

    def issue_handoff_token(self) -> str:
        """Generate and store a fresh handoff token. Returns it."""
        token = secrets.token_urlsafe(32)
        self.handoff_token = token
        self.save(update_fields=["handoff_token", "updated_at"])
        return token

    @property
    def email_is_verified(self) -> bool:
        return self.email_verified_at is not None

    @property
    def student_name(self) -> str:
        """Friendly name of the student from step 5 data."""
        data = normalize_step_keys(self.data)
        step5 = data.get("step5-student") or {}
        # Support legacy "first_name" key (was the preferred name) and new keys
        first = (
            step5.get("preferred_first_name")
            or step5.get("first_name")
            or step5.get("legal_first_name")
            or ""
        ).strip()
        last = (step5.get("last_name") or "").strip()
        if first and last:
            return f"{first} {last}"
        return first or last or ""

    @property
    def primary_parent_name(self) -> str:
        """Friendly name of the primary parent from step 7 data."""
        data = normalize_step_keys(self.data)
        step6 = data.get("step7-primaryparent") or {}
        # Support legacy "first_name" key (was the legal name for parents) and new keys
        first = (step6.get("legal_first_name") or step6.get("first_name") or "").strip()
        last = (step6.get("last_name") or "").strip()
        if first and last:
            return f"{first} {last}"
        return first or last or ""

    @property
    def secondary_parent_name(self) -> str:
        """Friendly name of the secondary parent from step 8 data (if not skipped)."""
        data = normalize_step_keys(self.data)
        step7 = data.get("step8-secondaryparent") or {}
        if step7.get("_skipped"):
            return ""
        # Support legacy "first_name" key (was the legal name for parents) and new keys
        first = (step7.get("legal_first_name") or step7.get("first_name") or "").strip()
        last = (step7.get("last_name") or "").strip()
        if first and last:
            return f"{first} {last}"
        return first or last or ""

    @property
    def mentor_name(self) -> str:
        """Friendly name of the mentor from mentor_info data."""
        data = self.data or {}
        minfo = data.get("mentor_info") or {}
        # Support legacy "first_name" key (was the preferred name) and new keys
        first = (
            minfo.get("preferred_first_name")
            or minfo.get("first_name")
            or minfo.get("legal_first_name")
            or ""
        ).strip()
        last = (minfo.get("last_name") or "").strip()
        if first and last:
            return f"{first} {last}"
        return first or last or ""

    @property
    def is_mentor(self) -> bool:
        """``True`` if this application is for a mentor / volunteer."""
        return self.applicant_type == self.Type.MENTOR

    @property
    def applicant_name(self) -> str:
        """Friendly name of the main person applying (student or mentor)."""
        if self.is_mentor:
            return self.mentor_name
        return self.student_name

    @property
    def status_label(self) -> str:
        """Status label for display, adjusted for converted mentors."""
        if self.status == self.Status.CONVERTED and self.is_mentor:
            return "Converted to Mentor"
        return self.get_status_display()


# --- Step 9: post-approval signed-document uploads --------------------------


def _application_doc_upload_to(instance, filename):
    """Files land at MEDIA_ROOT/application_documents/<application_id>/<filename>."""
    from programs.utils.files import sanitize_upload_filename

    aid = (
        instance.application.application_id if instance.application_id else "unassigned"
    )
    filename = sanitize_upload_filename(filename)
    return f"application_documents/{aid}/{filename}"


class ApplicationDocumentSubmission(models.Model):
    """A signed (or completed) document uploaded by an approved applicant
    in response to a :class:`programs.ProgramDocument`.

    One row per (application, program document). Re-uploading replaces the
    file on the existing row.
    """

    application = models.ForeignKey(
        Application,
        on_delete=models.CASCADE,
        related_name="document_submissions",
    )
    document = models.ForeignKey(
        "programs.ProgramDocument",
        on_delete=models.CASCADE,
        related_name="submissions",
    )
    file = models.FileField(upload_to=_application_doc_upload_to, max_length=255)
    uploaded_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ("application", "document")
        ordering = ["document__display_order", "document__name"]

    def __str__(self) -> str:  # pragma: no cover - trivial
        return f"Submission for {self.document} on {self.application.application_id}"
