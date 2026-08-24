import datetime
import logging
from decimal import Decimal
from io import BytesIO

import pghistory
from cryptography.fernet import Fernet, InvalidToken
from dateutil.relativedelta import relativedelta
from django.conf import settings
from django.core.files.base import ContentFile
from django.db import models
from django.utils import timezone
from PIL import ImageFile

from programs.constants import (
    MENTOR_ROLE_CHOICES,
    PHONE_TYPE_CHOICES,
    RELATIONSHIP_CHOICES,
    STATE_CHOICES,
    TEAM_TYPES,
    TSHIRT_SIZE_CHOICES,
)

from .validators import validate_phone_number, validate_zip_code

logger = logging.getLogger(__name__)


def get_fernet():
    key = getattr(settings, "FILE_ENCRYPTION_KEY", None)
    if not key:
        if getattr(settings, "DEBUG", False):
            # In DEBUG mode, derive a key from SECRET_KEY for local dev/test convenience.
            # This is NOT secure for production use.
            import base64

            key = base64.urlsafe_b64encode(
                settings.SECRET_KEY[:32].encode().ljust(32, b"\0")
            )
        else:
            raise RuntimeError(
                "FILE_ENCRYPTION_KEY is not configured. "
                "Set FILE_ENCRYPTION_KEY environment variable. "
                'Generate with: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"'
            )
    if isinstance(key, str):
        key = key.encode()
    return Fernet(key)


def _get_legacy_fernet():
    """Return a Fernet instance using the old SECRET_KEY-derived key.

    Before ``FILE_ENCRYPTION_KEY`` was introduced, ``get_fernet()`` fell
    back to a key derived from ``SECRET_KEY``.  Records encrypted before
    the transition can only be decrypted with that legacy key.
    """
    import base64

    key = base64.urlsafe_b64encode(settings.SECRET_KEY[:32].encode().ljust(32, b"\0"))
    return Fernet(key)


class EncryptedFileField(models.FileField):
    """
    A FileField that transparently encrypts file content on save (via pre_save)
    and decrypts on read (via EncryptedFileDescriptor). Uses Fernet symmetric
    encryption with the key from settings.FILE_ENCRYPTION_KEY.
    """

    def pre_save(self, model_instance, add):
        # Encrypt before super() so the storage backend writes ciphertext, not plaintext.
        file = getattr(model_instance, self.attname)
        if file and not file._committed and not getattr(file, "_encrypted", False):
            fernet = get_fernet()
            plaintext = file.read()
            encrypted = fernet.encrypt(plaintext)
            file.file = ContentFile(encrypted, name=file.name)
            file._encrypted = True
        return super().pre_save(model_instance, add)

    def contribute_to_class(self, cls, name, private_only=False):
        super().contribute_to_class(cls, name)
        # Patch the descriptor to decrypt when accessed
        setattr(cls, self.name, EncryptedFileDescriptor(getattr(cls, self.name)))


class EncryptedTextField(models.TextField):
    def get_prep_value(self, value):
        if value is None or value == "":
            return None
        fernet = get_fernet()
        try:
            fernet.decrypt(value.encode())
            return value  # already encrypted
        except (InvalidToken, UnicodeEncodeError):
            pass
        return fernet.encrypt(value.encode()).decode()

    def from_db_value(self, value, expression, connection):
        if value is None:
            return value
        fernet = get_fernet()
        try:
            return fernet.decrypt(value.encode()).decode()
        except (InvalidToken, UnicodeEncodeError, UnicodeDecodeError):
            # If decryption fails, return original (might be already decrypted or not encrypted)
            return value


class EncryptedCharField(models.CharField):
    def get_prep_value(self, value):
        if value is None or value == "":
            return None
        fernet = get_fernet()
        try:
            fernet.decrypt(value.encode())
            return value  # already encrypted
        except (InvalidToken, UnicodeEncodeError):
            pass
        return fernet.encrypt(value.encode()).decode()

    def from_db_value(self, value, expression, connection):
        if value is None:
            return value
        fernet = get_fernet()
        try:
            return fernet.decrypt(value.encode()).decode()
        except (InvalidToken, UnicodeEncodeError, UnicodeDecodeError):
            # If decryption fails, return original (might be already decrypted or not encrypted)
            return value


class EncryptedFileDescriptor:
    def __init__(self, original_field):
        self.original_field = original_field

    def __get__(self, instance, owner):
        if instance is None:
            return self
        file = self.original_field.__get__(instance, owner)
        if file and not hasattr(file, "_decrypted_file"):
            original_open = file.open

            def decrypted_open(mode="rb"):
                f = original_open(mode)
                if "b" not in mode:
                    return f
                try:
                    f.seek(0)
                    content = f.read()
                finally:
                    # The ciphertext is fully read; close the underlying
                    # storage handle so we don't hold file descriptors open for
                    # the lifetime of the instance. The decrypted bytes live in
                    # the returned BytesIO instead.
                    f.close()
                try:
                    fernet = get_fernet()
                    return BytesIO(fernet.decrypt(content))
                except InvalidToken:
                    # The stored bytes may be legacy plaintext; return a fresh
                    # handle positioned at the start so the caller can read the
                    # raw file.
                    f = original_open(mode)
                    f.seek(0)
                    return f

            file.open = decrypted_open
            file._decrypted_file = True
        return file

    def __set__(self, instance, value):
        self.original_field.__set__(instance, value)


# Make PIL more tolerant of malformed/truncated images (common after conversions/exports)
ImageFile.LOAD_TRUNCATED_IMAGES = True

# Register HEIC opener if available so PIL can decode .heic images
try:
    from pillow_heif import register_heif_opener  # type: ignore

    register_heif_opener()
except ImportError:
    # If pillow-heif isn't installed, we simply won't be able to open HEIC files.
    # The save() handler below will skip conversion in that case.
    pass
except Exception:
    logger.exception("Unexpected error registering HEIF opener")


class Team(models.Model):
    team_type = models.CharField(max_length=20, choices=TEAM_TYPES)
    number = models.IntegerField()
    name = models.CharField(max_length=100, blank=True, null=True)
    color = models.CharField(
        max_length=7, default="#0000ff", help_text="Hex color code (e.g. #0000ff)"
    )

    class Meta:
        unique_together = ("team_type", "number")
        ordering = ["team_type", "number"]

    def __str__(self):
        if self.name:
            return f"{self.team_type} {self.number} {self.name}"
        return f"{self.team_type} {self.number}"


class Crew(models.Model):
    name = models.CharField(max_length=100)
    program = models.ForeignKey(
        "Program", on_delete=models.CASCADE, related_name="crews"
    )
    color = models.CharField(
        max_length=7, default="#0000ff", help_text="Hex color code (e.g. #0000ff)"
    )

    class Meta:
        unique_together = ("name", "program")
        ordering = ["name"]

    def __str__(self):
        return f"{self.name} ({self.program.name})"


class SubTeam(models.Model):
    name = models.CharField(max_length=100)
    program = models.ForeignKey(
        "Program", on_delete=models.CASCADE, related_name="subteams"
    )
    color = models.CharField(
        max_length=7, default="#0000ff", help_text="Hex color code (e.g. #0000ff)"
    )

    class Meta:
        unique_together = ("name", "program")
        ordering = ["name"]

    def __str__(self):
        return f"{self.name} ({self.program.name})"


class RolePermission(models.Model):
    """
    Dynamic permission settings for Mentors and Parents.
    Lead Mentors can customize read/write access for each role to each section.
    """

    SECTION_CHOICES = [
        ("student_info", "Student - Info (General)"),
        ("identity", "Student - Identity"),
        ("contact_address", "Student - Contact & Address"),
        ("health_medical", "Student - Health & Medical"),
        ("school", "Student - School"),
        ("cmu_andrew", "Student - CMU Andrew ID"),
        ("background_checks", "Student - Background Checks"),
        ("student_documents", "Student - Signed Documents"),
        ("discord", "Student - Discord"),
        ("first_website", "Student - FIRST Website"),
        ("parents_emergency", "Student - Parents/Emergency Contacts"),
        ("other_details", "Student - Other Details"),
        ("attendance", "Student - Attendance"),
        ("adult_info", "Adult - Info"),
        ("payments", "Payments - General"),
        ("sliding_scale", "Payments - Sliding Scale"),
        ("fees", "Programs - Fees"),
        ("programs", "Programs - General"),
    ]
    ROLE_CHOICES = [
        ("Mentor", "Mentor"),
        ("Parent", "Parent"),
        ("Student", "Student"),
        ("Alumni", "Alumni"),
    ]

    role = models.CharField(max_length=20, choices=ROLE_CHOICES)
    section = models.CharField(max_length=50, choices=SECTION_CHOICES)
    can_read = models.BooleanField(default=True)
    can_write = models.BooleanField(default=False)

    class Meta:
        unique_together = ("role", "section")
        verbose_name = "Role Permission"
        verbose_name_plural = "Role Permissions"

    def __str__(self):
        return f"{self.role} - {self.get_section_display()} (R:{self.can_read}, W:{self.can_write})"


class ProgramFeature(models.Model):
    """Toggleable capability that can be enabled per Program.

    Keep keys stable. Suggested keys to start with:
      - 'discord' — show/collect Discord fields and related UI
      - 'background-checks' — show/collect background clearance fields and logic
      - 'cmu-andrew' — show/collect CMU Andrew ID related fields
    """

    key = models.SlugField(
        max_length=50,
        unique=True,
        help_text="Stable key used in code/templates (e.g., 'discord').",
    )
    name = models.CharField(max_length=100)
    description = models.TextField(blank=True)
    display_order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["display_order", "name"]

    def __str__(self):
        return self.name


class Program(models.Model):
    name = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    active = models.BooleanField(default=True)
    start_date = models.DateField(
        null=True, blank=True, db_index=True, help_text="Program start date"
    )
    end_date = models.DateField(
        null=True, blank=True, db_index=True, help_text="Program end date"
    )
    applications_open = models.DateField(
        null=True,
        blank=True,
        help_text="Date when applications open for this program. Defaults to program start date.",
    )
    applications_close = models.DateField(
        null=True,
        blank=True,
        help_text="Date when applications close for this program. Defaults to program end date.",
    )
    cost = models.CharField(
        max_length=100,
        blank=True,
        help_text="Program cost (e.g., $300 or $200-500).",
    )
    grade_range_start = models.IntegerField(
        null=True,
        blank=True,
        help_text="Starting grade for this program (0 for K).",
    )
    grade_range_end = models.IntegerField(
        null=True,
        blank=True,
        help_text="Ending grade for this program (12 for 12th grade).",
    )
    # Feature toggles
    features = models.ManyToManyField(
        ProgramFeature,
        blank=True,
        related_name="programs",
        help_text="Enable optional features (e.g., Discord, background checks, CMU Andrew ID).",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]

    @property
    def year_display(self):
        if self.start_date and self.end_date:
            if self.start_date.year == self.end_date.year:
                return str(self.start_date.year)
            return f"{self.start_date.year}-{self.end_date.year}"
        if self.start_date:
            return str(self.start_date.year)
        if self.end_date:
            return str(self.end_date.year)
        return ""

    @property
    def grade_range_display(self):
        """Return a human-readable grade range: '4th–6th Grade', 'K–2nd Grade', etc."""
        if self.grade_range_start is None or self.grade_range_end is None:
            # If only one is set, we could still show it, but usually both are set.
            # For now, if either is missing, return empty or handle individually.
            if self.grade_range_start is not None:
                from programs.utils import format_grade

                return format_grade(self.grade_range_start)
            if self.grade_range_end is not None:
                from programs.utils import format_grade

                return format_grade(self.grade_range_end)
            return ""

        from programs.utils import format_grade

        if self.grade_range_start == self.grade_range_end:
            return format_grade(self.grade_range_start)

        start = format_grade(self.grade_range_start)
        end = format_grade(self.grade_range_end)

        # format_grade(0) returns 'K'. Others return 'Nth Grade'.
        # We want '4th–6th Grade' or 'K–2nd Grade'.
        # Remove ' Grade' from the start part if it exists.
        start_label = start.replace(" Grade", "")
        return f"{start_label}–{end}"

    @property
    def name_with_dates(self):
        if self.start_date and self.end_date:
            return f"{self.name} ({self.start_date.isoformat()} - {self.end_date.isoformat()})"
        yr = self.year_display
        if yr:
            return f"{self.name} ({yr})"
        return self.name

    def __str__(self):
        yr = self.year_display
        if yr:
            return f"{self.name} ({yr})"
        return self.name

    @property
    def feature_keys(self) -> set:
        """Convenience set of enabled feature keys for quick checks in templates/views."""
        return set(self.features.values_list("key", flat=True))

    def has_feature(self, key: str) -> bool:
        return key in self.feature_keys

    @property
    def has_feature_outreach(self) -> bool:
        return self.has_feature("outreach")

    @property
    def has_feature_badges(self) -> bool:
        return self.has_feature("badges")

    @property
    def status(self) -> str:
        """Return 'Active', 'Upcoming', or 'Inactive' based on active flag and dates."""
        from django.utils import timezone

        today = timezone.now().date()
        if not self.active:
            return "Inactive"
        if self.start_date and self.start_date > today:
            return "Upcoming"
        if self.end_date and self.end_date < today:
            return "Inactive"
        return "Active"

    @property
    def is_applications_open(self) -> bool:
        """Return True if applications are currently open for this program."""
        today = timezone.now().date()
        if not self.active:
            return False
        # If open date is set, check it. Default is start_date (set in save())
        if self.applications_open and self.applications_open > today:
            return False
        # If close date is set, check it. Default is end_date (set in save())
        if self.applications_close and self.applications_close < today:
            return False
        # If the program has ended, applications are definitely closed.
        if self.end_date and self.end_date < today:
            return False
        return True

    @property
    def applications_are_invalid(self) -> bool:
        """Return True if applications are for programs where the applications closed or the program has ended."""
        # This is essentially the complement of is_applications_open, but
        # explicitly about whether an *existing* application is still valid.
        return not self.is_applications_open

    def save(self, *args, **kwargs):
        if not self.applications_open and self.start_date:
            self.applications_open = self.start_date
        if not self.applications_close and self.end_date:
            self.applications_close = self.end_date
        super().save(*args, **kwargs)


class SchoolDistrict(models.Model):
    name = models.CharField(max_length=150, unique=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class School(models.Model):
    name = models.CharField(max_length=150, unique=True)
    district = models.ForeignKey(
        SchoolDistrict,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="schools",
        verbose_name="School district",
    )
    street_address = models.CharField(max_length=255, blank=True, null=True)
    city = models.CharField(max_length=100, blank=True, null=True)
    state = models.CharField(
        max_length=50, choices=STATE_CHOICES, blank=True, null=True, default="PA"
    )
    zip_code = models.CharField(
        max_length=20, blank=True, null=True, validators=[validate_zip_code]
    )

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class RaceEthnicity(models.Model):
    """Canonical race/ethnicity options for Students (multi-select)."""

    key = models.SlugField(max_length=50, unique=True)
    name = models.CharField(max_length=100, unique=True)
    display_order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["display_order", "name"]
        verbose_name_plural = "Race/Ethnicity Options"

    def __str__(self):
        return self.name

    @classmethod
    def match_from_text(cls, text: str):
        """Best-effort mapping from a free-text race/ethnicity string to option queryset.
        Matches by keyword; supports comma/semicolon-separated lists.
        """
        if not text:
            return cls.objects.none()
        import re

        s = (text or "").lower()
        # Split on common separators to get tokens too
        parts = [p.strip() for p in re.split(r"[;,/\\]|\band\b", s) if p.strip()]
        hay = " " + s + " "
        keys = set()

        def has(substr):
            return substr in hay

        # American Indian or Alaska Native
        if (
            any(
                "american indian" in p or "alaska" in p or "native american" in p
                for p in parts
            )
            or has("american indian")
            or has("alaska")
            or has("native american")
        ):
            keys.add("american-indian-or-alaska-native")
        # Asian
        if has(" asian"):
            keys.add("asian")
        # Black or African-American
        if has("black") or has("african-american") or has("african american"):
            keys.add("black-or-african-american")
        # Hispanic or Latino
        if has("hispanic") or has("latino") or has("latina") or has("latinx"):
            keys.add("hispanic-or-latino")
        # Middle Eastern or North African
        if has("middle eastern") or has("north african") or has("mena"):
            keys.add("middle-eastern-or-north-african")
        # Native Hawaiian or Other Pacific Islander
        if has("hawaiian") or has("pacific islander"):
            keys.add("native-hawaiian-or-other-pacific-islander")
        # White
        if has(" white"):
            keys.add("white")
        # Other
        if has("other") or (not keys and s.strip()):
            # If text provided but no match, classify as other
            keys.add("other")
        return cls.objects.filter(key__in=keys)


def _student_photo_upload_to(instance, filename):
    from programs.utils.files import sanitize_upload_filename

    return f"photos/students/{sanitize_upload_filename(filename)}"


@pghistory.track()
class Student(models.Model):
    # Optional link to a User so students can self-manage later if desired
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="student_profile",
    )
    legal_first_name = models.CharField(max_length=150, verbose_name="Legal first name")
    first_name = models.CharField(
        max_length=150, blank=True, null=True, verbose_name="First name"
    )
    last_name = models.CharField(max_length=150, db_index=True)
    pronouns = models.CharField(max_length=50, blank=True, null=True)
    date_of_birth = models.DateField(
        blank=False,
        null=False,
        default=datetime.date(1900, 1, 1),
        help_text="Student's date of birth. The default value (1900-01-01) is a placeholder — please enter the actual date.",
    )
    photo = models.ImageField(
        upload_to=_student_photo_upload_to, blank=True, null=True, max_length=255
    )

    address = models.CharField(max_length=255, blank=True, null=True)
    city = models.CharField(max_length=100, blank=True, null=True)
    state = models.CharField(
        max_length=50, choices=STATE_CHOICES, blank=True, null=True, default="PA"
    )
    zip_code = models.CharField(
        max_length=20, blank=True, null=True, validators=[validate_zip_code]
    )

    phone_number = models.CharField(
        max_length=30, blank=True, null=True, validators=[validate_phone_number]
    )
    phone_type = models.CharField(
        max_length=20, choices=PHONE_TYPE_CHOICES, default="cell", blank=True, null=True
    )
    can_receive_texts = models.BooleanField(default=False)

    personal_email = models.EmailField(blank=True, null=True)
    directory_consent = models.BooleanField(
        default=True,
        verbose_name="OK to share name, address, email, and phone for student directory",
    )

    andrew_id = models.CharField(max_length=50, blank=True, null=True)
    andrew_email = models.EmailField(blank=True, null=True)
    andrew_id_expiration = models.DateField(
        blank=True,
        null=True,
        help_text="Expiration date of this Andrew ID.",
    )
    andrew_id_sponsor = models.ForeignKey(
        "Adult",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="sponsored_student_andrew_ids",
        help_text="The Adult (mentor) who sponsored this Andrew ID.",
    )

    school = models.ForeignKey(
        "School",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="students",
    )
    graduation_year = models.PositiveSmallIntegerField(
        blank=True,
        null=True,
        db_index=True,
        help_text="Expected high school graduation year",
    )

    # New multi-select of canonical options
    race_ethnicities = models.ManyToManyField(
        "RaceEthnicity",
        related_name="students",
        blank=True,
        verbose_name="Race / Ethnicity",
    )
    tshirt_size = models.CharField(
        max_length=10, choices=TSHIRT_SIZE_CHOICES, blank=True, null=True
    )

    seen_once = models.BooleanField(default=False)
    on_discord = models.BooleanField(default=False)
    discord_handle = models.CharField(max_length=100, blank=True, null=True)

    # Health & Medical
    allergies = EncryptedTextField(
        blank=True,
        null=True,
        help_text="List any food, drug, environmental, or other allergies. Include severity and typical reactions if known.",
    )
    dietary_restrictions = EncryptedTextField(
        blank=True,
        null=True,
        help_text="Dietary needs or restrictions (e.g., vegetarian, halal, no pork, no nuts).",
    )
    medical_notes = EncryptedTextField(
        blank=True,
        null=True,
        help_text="Other health information staff should know (e.g., asthma, seizures, physical limitations).",
    )

    # FIRST Website
    first_has_account = models.BooleanField(
        default=False, verbose_name="Has FIRST account"
    )
    first_attached_to_parent_account = models.BooleanField(
        default=False, verbose_name="Attached to parent account"
    )
    first_signed_cr = models.BooleanField(
        default=False, verbose_name="Signed FIRST Consent & Release (C&R)"
    )
    first_registered_teams = models.CharField(
        max_length=200,
        blank=True,
        null=True,
        verbose_name="Registered team(s)",
        help_text="Team numbers or names, comma-separated",
    )

    # Contact relationships. Primary/secondary point at an
    # AdultStudentRelationship row (the through model behind Adult.students /
    # Student.adults) so the parent side and student side can never drift.
    # The .primary_contact / .secondary_contact properties below expose the
    # linked Adult for backwards compatibility and auto-create the through row
    # on assignment (even before the Student has been saved).
    primary_contact_relationship = models.ForeignKey(
        "AdultStudentRelationship",
        on_delete=models.SET_NULL,
        related_name="students_with_primary",
        null=True,
        blank=True,
        verbose_name="Primary contact relationship",
    )
    secondary_contact_relationship = models.ForeignKey(
        "AdultStudentRelationship",
        on_delete=models.SET_NULL,
        related_name="students_with_secondary",
        null=True,
        blank=True,
        verbose_name="Secondary contact relationship",
    )

    @property
    def primary_contact(self):
        """Backwards-compatible accessor: the primary-contact Adult."""
        rel = self.primary_contact_relationship
        return rel.adult if rel is not None else None

    @primary_contact.setter
    def primary_contact(self, adult):
        self._set_contact("primary", adult)

    @property
    def primary_contact_id(self):
        rel = self.primary_contact_relationship
        return rel.adult_id if rel is not None else None

    @primary_contact_id.setter
    def primary_contact_id(self, value):
        if value in (None, ""):
            self.primary_contact = None
        else:
            self.primary_contact = Adult.objects.filter(pk=value).first()

    @property
    def secondary_contact(self):
        """Backwards-compatible accessor: the secondary-contact Adult."""
        rel = self.secondary_contact_relationship
        return rel.adult if rel is not None else None

    @secondary_contact.setter
    def secondary_contact(self, adult):
        self._set_contact("secondary", adult)

    @property
    def secondary_contact_id(self):
        rel = self.secondary_contact_relationship
        return rel.adult_id if rel is not None else None

    @secondary_contact_id.setter
    def secondary_contact_id(self, value):
        if value in (None, ""):
            self.secondary_contact = None
        else:
            self.secondary_contact = Adult.objects.filter(pk=value).first()

    def _set_contact(self, slot, adult):
        """Assign a primary/secondary contact, creating the through row."""
        if adult is None:
            setattr(self, f"{slot}_contact_relationship", None)
            self.__dict__.pop(f"_pending_{slot}_contact", None)
            return
        if self.pk is None:
            # Unsaved Student: defer creating the through row until save() so
            # there's a student PK to reference.
            self.__dict__[f"_pending_{slot}_contact"] = adult
            setattr(self, f"{slot}_contact_relationship", None)
            return
        rel, _ = AdultStudentRelationship.objects.get_or_create(
            adult=adult, student=self
        )
        setattr(self, f"{slot}_contact_relationship", rel)
        self.__dict__.pop(f"_pending_{slot}_contact", None)
        if not adult.is_parent:
            Adult.objects.filter(pk=adult.pk).update(is_parent=True)

    def _resolve_deferred_contacts(self):
        """Create through rows for contacts assigned before the first save."""
        changed = False
        for slot in ("primary", "secondary"):
            pending = self.__dict__.pop(f"_pending_{slot}_contact", None)
            if pending is None:
                continue
            rel, _ = AdultStudentRelationship.objects.get_or_create(
                adult=pending, student=self
            )
            setattr(self, f"{slot}_contact_relationship", rel)
            if not pending.is_parent:
                Adult.objects.filter(pk=pending.pk).update(is_parent=True)
            changed = True
        if changed:
            super().save(
                update_fields=[
                    "primary_contact_relationship",
                    "secondary_contact_relationship",
                ]
            )

    @property
    def parents(self):
        """Backwards-compatible alias for the M2M 'adults' relation.
        Allows templates to use student.parents.all just like older schema.
        """
        return self.adults

    @property
    def all_parents(self):
        """
        Returns a list of unique Adult objects related to this student,
        including primary, secondary, and any additional M2M adults.
        Each Adult object has an 'attached_rel' attribute representing their
        relationship to THIS student and a 'specific_rel' attribute for the
        free-text specific relationship (e.g. father, stepmom).
        """
        seen_ids = set()
        result = []

        # Helper to get relationship data from the through model
        rels = {}
        if self.pk:
            rels = {
                r.adult_id: (
                    r.relationship_to_student,
                    r.specific_relationship or "",
                )
                for r in self.adultstudentrelationship_set.all()
            }

        def add_adult(adult):
            if adult and adult.id not in seen_ids:
                rel_data = rels.get(adult.id, ("parent", ""))
                adult.attached_rel = rel_data[0]
                adult.specific_rel = rel_data[1]
                result.append(adult)
                seen_ids.add(adult.id)

        add_adult(self.primary_contact)
        add_adult(self.secondary_contact)

        if self.pk:
            for p in self.adults.all():
                add_adult(p)

        return result

    interest_reason = models.TextField(
        blank=True,
        null=True,
        verbose_name="Interest reason",
        help_text="Why are you interested in participating in this Girls of Steel program this season?",
    )
    hoped_gains = models.TextField(
        blank=True,
        null=True,
        verbose_name="Hoped gains",
        help_text="What do you hope to gain from the experience?",
    )
    prior_robotics_experience = models.TextField(
        blank=True,
        null=True,
        verbose_name="Prior robotics experience",
        help_text="What prior robotics experience do you have? (No experience is necessary to be a part of the program.)",
    )
    referral_source = models.TextField(
        blank=True,
        null=True,
        verbose_name="Referral source",
        help_text="How did you hear about Girls of Steel Robotics?",
    )

    graduated = models.BooleanField(
        default=False, db_index=True, help_text="Check if this student has graduated."
    )
    programs = models.ManyToManyField(
        "Program", through="Enrollment", related_name="students", blank=True
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["first_name", "last_name"]
        indexes = [
            models.Index(fields=["last_name", "first_name"], name="student_name_idx"),
            models.Index(
                fields=["school", "graduation_year"], name="student_school_grad_idx"
            ),
            models.Index(fields=["graduated"], name="student_graduated_idx"),
        ]

    @property
    def full_name(self):
        pref = self.first_name or self.legal_first_name
        return f"{pref} {self.last_name}".strip()

    def __str__(self):
        return self.full_name or f"Student #{self.pk}"

    def _prune_dangling_contacts(self):
        """Clear relationship pointers whose Adult is missing.

        A Student may carry a primary/secondary relationship pointer whose
        Adult has since been deleted (AdultStudentRelationship cascades with
        the Adult, which would clear the pointer via SET NULL, but keep this
        as a safety net for legacy/orphaned data).
        """
        ptrs = [
            pk
            for pk in (
                self.primary_contact_relationship_id,
                self.secondary_contact_relationship_id,
            )
            if pk
        ]
        if not ptrs:
            return
        try:
            valid = set(
                AdultStudentRelationship.objects.filter(
                    pk__in=ptrs, adult__isnull=False
                ).values_list("pk", flat=True)
            )
            if self.primary_contact_relationship_id and (
                self.primary_contact_relationship_id not in valid
            ):
                self.primary_contact_relationship_id = None
            if self.secondary_contact_relationship_id and (
                self.secondary_contact_relationship_id not in valid
            ):
                self.secondary_contact_relationship_id = None
        except Exception:
            logger.debug("Unexpected error in contact cleanup", exc_info=True)

    def clean(self):
        super().clean()
        from django.core.exceptions import ValidationError
        from django.db.models import Q

        # Students shouldn't have duplicate emails with each other or with parents.
        if self.personal_email:
            email = self.personal_email.strip().lower()
            # Check other students
            others = Student.objects.filter(
                Q(personal_email__iexact=email) | Q(andrew_email__iexact=email)
            )
            if self.pk:
                others = others.exclude(pk=self.pk)
            if others.exists():
                raise ValidationError(
                    {
                        "personal_email": "This email is already in use by another student."
                    }
                )

            # Check parents/mentors
            if Adult.objects.filter(
                Q(personal_email__iexact=email) | Q(andrew_email__iexact=email)
            ).exists():
                raise ValidationError(
                    {
                        "personal_email": "This email is already in use by a parent or mentor."
                    }
                )

        if self.andrew_email:
            email = self.andrew_email.strip().lower()
            # Check other students
            others = Student.objects.filter(
                Q(personal_email__iexact=email) | Q(andrew_email__iexact=email)
            )
            if self.pk:
                others = others.exclude(pk=self.pk)
            if others.exists():
                raise ValidationError(
                    {"andrew_email": "This email is already in use by another student."}
                )

            # Check parents/mentors
            if Adult.objects.filter(
                Q(personal_email__iexact=email) | Q(andrew_email__iexact=email)
            ).exists():
                raise ValidationError(
                    {
                        "andrew_email": "This email is already in use by a parent or mentor."
                    }
                )

    def save(self, *args, **kwargs):
        # Normalize any new photo upload to RGB JPEG in-memory (fixes EXIF orientation, handles HEIC).
        from .utils import normalize_image_field

        normalize_image_field(getattr(self, "photo", None), log_prefix="Student photo")
        self._prune_dangling_contacts()
        super().save(*args, **kwargs)
        # Create through rows for any primary/secondary contacts assigned
        # before the Student had a PK, then persist the pointers.
        self._resolve_deferred_contacts()

        # Sync Student name to User account if linked
        if self.user:
            target_first = self.first_name or self.legal_first_name
            changed = False
            if self.user.first_name != target_first:
                self.user.first_name = target_first
                changed = True
            if self.user.last_name != self.last_name:
                self.user.last_name = self.last_name
                changed = True
            target_active = not self.graduated
            if self.user.is_active != target_active:
                self.user.is_active = target_active
                changed = True
            if changed:
                self.user.save()

    def eighteenth_birthday(self):
        """Return the date this student turns 18, or None if DOB unknown."""
        dob = self.date_of_birth
        if not dob:
            return None
        try:
            return dob.replace(year=dob.year + 18)
        except ValueError:
            # Handle Feb 29 on non-leap years by using Feb 28
            return dob.replace(month=2, day=28, year=dob.year + 18)

    @property
    def age(self):
        """Return the student's current age in years, or None if DOB unknown."""
        dob = self.date_of_birth
        if not dob:
            return None
        today = datetime.date.today()
        return today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))

    @property
    def current_grade(self):
        """Return the student's current grade as an integer (0=K, 1–12).

        Returns ``None`` if ``graduation_year`` is not set, or if the student
        has already graduated (calculated grade > 12).
        Grades below 0 are clamped to 0 (Kindergarten).
        """
        if not self.graduation_year:
            return None
        from programs.utils import calculate_grade

        return calculate_grade(self.graduation_year)

    @property
    def grade_display(self):
        """Return a human-readable grade label: 'K', '1st Grade', …, '12th Grade',
        'Graduated', or ``None`` if ``graduation_year`` is not set.
        """
        if not self.graduation_year:
            return None
        from programs.utils import format_grade

        return format_grade(self.current_grade)

    def requires_background_check(self) -> bool:
        """Whether the student must hold PA background clearances.

        Per the university's rule, a student needs clearances if they will be
        17 years old on Sept 1 of the current academic year (the academic year
        is considered to start on Sept 1). Returns False if no DOB is set.
        """
        dob = self.date_of_birth
        if not dob:
            return False
        if isinstance(dob, str):
            try:
                dob = datetime.datetime.strptime(dob, "%Y-%m-%d").date()
            except ValueError:
                return False
        today = timezone.localdate()
        if (today.month, today.day) >= (9, 1):
            academic_year = today.year
        else:
            academic_year = today.year - 1
        # The oldest someone can be born and still be 17 on Sept 1 of the
        # academic year is Sept 1, 17 years prior to that academic year.
        oldest_dob = datetime.date(academic_year - 17, 9, 1)
        return dob <= oldest_dob

    def needs_background_check(self) -> bool:
        """Whether the student requires clearances AND is missing at least one.

        The requirement is always derived from date of birth; only the
        clearance records themselves are stored state. Returns False if the
        student does not require clearances.
        """
        if not self.requires_background_check():
            return False
        required_types = set(BackgroundCheckType.values)
        valid_types = {
            bc.check_type for bc in self.background_checks.all() if bc.is_valid
        }
        return not required_types.issubset(valid_types)


@pghistory.track()
class Enrollment(models.Model):
    student = models.ForeignKey(Student, on_delete=models.CASCADE)
    program = models.ForeignKey(Program, on_delete=models.CASCADE)
    team = models.ForeignKey(
        Team,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="enrollments",
    )
    crew = models.ForeignKey(
        Crew,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="enrollments",
    )
    subteam = models.ForeignKey(
        SubTeam,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="enrollments",
    )
    active = models.BooleanField(default=True)
    clearance_due = models.BooleanField(
        default=False,
        help_text="Set when the enrolled student requires PA background clearances "
        "and is missing at least one valid clearance.",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("student", "program")
        verbose_name = "Enrollment"
        verbose_name_plural = "Enrollments"

    def __str__(self):
        return f"{self.student} → {self.program}"


@pghistory.track()
class AdultStudentRelationship(models.Model):
    adult = models.ForeignKey("Adult", on_delete=models.CASCADE)
    student = models.ForeignKey(Student, on_delete=models.CASCADE)
    relationship_to_student = models.CharField(
        max_length=20, choices=RELATIONSHIP_CHOICES, default="parent"
    )
    specific_relationship = models.CharField(
        max_length=100,
        blank=True,
        null=True,
        help_text="Specific relationship, e.g. father, stepmom, foster parent, etc.",
    )

    class Meta:
        unique_together = ("adult", "student")

    def __str__(self):
        return f"{self.adult} - {self.relationship_to_student} to {self.student}"


def _adult_photo_upload_to(instance, filename):
    from programs.utils.files import sanitize_upload_filename

    return f"photos/adults/{sanitize_upload_filename(filename)}"


@pghistory.track()
class Adult(models.Model):
    # Role flags
    is_parent = models.BooleanField(
        default=False,
        db_index=True,
        help_text="Check if this adult is a parent/guardian of any student.",
    )
    is_mentor = models.BooleanField(
        default=False,
        db_index=True,
        help_text="Check if this adult serves as a mentor/volunteer.",
    )
    is_alumni = models.BooleanField(
        default=False,
        db_index=True,
        help_text="Check if this adult is a program alumni.",
    )

    # Optional link to a User; allows adults (parents/mentors) to have accounts
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="adult_profile",
    )

    # Identity
    first_name = models.CharField(max_length=150)
    preferred_first_name = models.CharField(max_length=150, blank=True, null=True)
    last_name = models.CharField(max_length=150)
    pronouns = models.CharField(max_length=50, blank=True, null=True)

    # Contact
    personal_email = models.EmailField(
        blank=True,
        null=True,
        help_text="Primary contact email (e.g. Gmail). Used for login and notifications.",
    )
    phone_number = models.CharField(
        max_length=30, blank=True, null=True, validators=[validate_phone_number]
    )
    phone_type = models.CharField(
        max_length=20, choices=PHONE_TYPE_CHOICES, default="cell", blank=True, null=True
    )
    can_receive_texts = models.BooleanField(default=False)
    address = models.CharField(max_length=255, blank=True, null=True)
    city = models.CharField(max_length=100, blank=True, null=True)
    state = models.CharField(
        max_length=50, choices=STATE_CHOICES, blank=True, null=True, default="PA"
    )
    zip_code = models.CharField(
        max_length=20, blank=True, null=True, validators=[validate_zip_code]
    )

    # Mentor-like fields
    start_year = models.PositiveSmallIntegerField(blank=True, null=True)
    role = models.CharField(
        max_length=20, choices=MENTOR_ROLE_CHOICES, default="mentor"
    )
    photo = models.ImageField(
        upload_to=_adult_photo_upload_to, blank=True, null=True, max_length=255
    )

    # Andrew ID details (mentors/CMU-affiliated staff only)
    andrew_id = models.CharField(
        max_length=50,
        blank=True,
        null=True,
        help_text="CMU Andrew ID. Assigned by lead mentors; only applies to mentors/CMU staff.",
    )
    andrew_email = models.EmailField(
        blank=True,
        null=True,
        help_text="CMU Andrew email (andrew_id@andrew.cmu.edu). Assigned by lead mentors.",
    )
    andrew_id_expiration = models.DateField(
        blank=True,
        null=True,
        help_text="Expiration date of this Andrew ID.",
    )
    andrew_id_sponsor = models.ForeignKey(
        "self",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="sponsored_andrew_ids",
        help_text="The Adult (mentor) who sponsored this Andrew ID.",
    )

    # Discord
    on_discord = models.BooleanField(default=False)
    discord_username = models.CharField(max_length=100, blank=True, null=True)

    # CMU access
    has_cmu_id_card = models.BooleanField(default=False)
    has_cmu_building_access = models.BooleanField(default=False)

    # Google access
    has_google_team_drive_access = models.BooleanField(default=False)
    has_google_mentor_drive_access = models.BooleanField(default=False)
    has_google_admin_drive_access = models.BooleanField(default=False)

    # Online platforms / memberships
    on_first_website = models.BooleanField(default=False)
    signed_first_consent_form = models.BooleanField(default=False)
    on_canvas = models.BooleanField(default=False)
    has_zoom_account = models.BooleanField(default=False)
    in_onshape_classroom = models.BooleanField(default=False)
    on_canva = models.BooleanField(default=False)
    on_google_mentor_group = models.BooleanField(default=False)
    on_google_field_crew_group = models.BooleanField(default=False)

    # Emergency contact
    emergency_contact_name = models.CharField(max_length=150, blank=True, null=True)
    emergency_contact_phone = models.CharField(
        max_length=30, blank=True, null=True, validators=[validate_phone_number]
    )

    # Status
    email_updates = models.BooleanField(
        default=False, help_text="If checked, this adult will receive email updates."
    )
    login_enabled = models.BooleanField(
        default=True,
        db_index=True,
        help_text="Uncheck to disable this person's portal login.",
    )
    mentor_active = models.BooleanField(
        default=True,
        db_index=True,
        help_text="Uncheck to mark this person as an inactive mentor.",
    )

    # Alumni information (merged from Alumni)
    student_record = models.OneToOneField(
        "Student",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="alumni_profile",
        help_text="The student record this alumni profile originated from.",
    )
    college = models.CharField(max_length=200, blank=True, null=True)
    field_of_study = models.CharField(max_length=200, blank=True, null=True)
    employer = models.CharField(max_length=200, blank=True, null=True)
    job_title = models.CharField(max_length=200, blank=True, null=True)
    ok_to_contact = models.BooleanField(
        default=True, help_text="Consents to be contacted about news/opportunities"
    )
    notes = models.TextField(blank=True, null=True)

    # Relations
    students = models.ManyToManyField(
        Student,
        related_name="adults",
        blank=True,
        through="AdultStudentRelationship",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["last_name", "first_name"]
        indexes = [
            models.Index(fields=["last_name", "first_name"], name="adult_name_idx"),
            models.Index(
                fields=["is_parent", "login_enabled"],
                name="adult_parent_login_idx",
            ),
            models.Index(
                fields=["is_mentor", "mentor_active"],
                name="adult_mentor_active_idx",
            ),
            models.Index(
                fields=["is_alumni", "login_enabled"],
                name="adult_alumni_login_idx",
            ),
        ]

    @property
    def full_name(self):
        pref = self.preferred_first_name or self.first_name
        return f"{pref} {self.last_name}".strip()

    def __str__(self):
        return self.full_name

    def all_students(self):
        """Return a list of Student objects related to this adult,
        with an 'attached_rel' attribute for each student.
        """
        if not self.pk:
            return []
        rels = {
            r.student_id: r.relationship_to_student
            for r in self.adultstudentrelationship_set.all()
        }
        students = list(self.students.all())
        for s in students:
            s.attached_rel = rels.get(s.pk, "parent")
        return students

    def requires_background_check(self) -> bool:
        """Whether the adult must hold PA background clearances.
        Currently, all mentors/volunteers require them.
        """
        return self.is_mentor

    def needs_background_check(self) -> bool:
        """Whether the adult requires clearances AND is missing at least one valid check."""
        if not self.requires_background_check():
            return False
        required_types = set(BackgroundCheckType.values)
        valid_types = {
            bc.check_type for bc in self.background_checks.all() if bc.is_valid
        }
        return not required_types.issubset(valid_types)

    def clean(self):
        super().clean()
        from django.core.exceptions import ValidationError
        from django.db.models import Q

        # Parents can have duplicate emails with each other, but not with students.
        if self.personal_email:
            email = self.personal_email.strip().lower()
            if Student.objects.filter(
                Q(personal_email__iexact=email) | Q(andrew_email__iexact=email)
            ).exists():
                raise ValidationError(
                    {"personal_email": "This email is already in use by a student."}
                )

        if self.andrew_email:
            email = self.andrew_email.strip().lower()
            if Student.objects.filter(
                Q(personal_email__iexact=email) | Q(andrew_email__iexact=email)
            ).exists():
                raise ValidationError(
                    {"andrew_email": "This email is already in use by a student."}
                )

    def save(self, *args, **kwargs):
        # Normalize newly uploaded photo: RGB JPEG, fixed orientation, in-memory (shared with Student).
        from .utils import normalize_image_field

        normalize_image_field(getattr(self, "photo", None), log_prefix="Adult photo")
        super().save(*args, **kwargs)

        # Sync Adult name to User account if linked
        if self.user:
            # Prefer preferred_first_name if set
            target_first = self.preferred_first_name or self.first_name
            changed = False
            if self.user.first_name != target_first:
                self.user.first_name = target_first
                changed = True
            if self.user.last_name != self.last_name:
                self.user.last_name = self.last_name
                changed = True
            # Login is enabled if they have login_enabled OR if they have other active roles (parent/alumni)
            target_active = self.login_enabled or self.is_parent or self.is_alumni
            if self.user.is_active != target_active:
                self.user.is_active = target_active
                changed = True
            if changed:
                self.user.save()

    def has_accepted_current_agreement(self):
        """Whether this adult has accepted all current active MentorAgreements."""
        from programs.models import MentorAgreement, MentorAgreementAcceptance

        active = MentorAgreement.get_all_active()
        if not active.exists():
            return True
        accepted_ids = MentorAgreementAcceptance.objects.filter(
            adult=self, agreement__in=active
        ).values_list("agreement_id", flat=True)
        return active.filter(id__in=accepted_ids).count() == active.count()


class Fee(models.Model):
    program = models.ForeignKey(Program, on_delete=models.CASCADE, related_name="fees")
    name = models.CharField(max_length=200)
    amount = models.DecimalField(max_digits=8, decimal_places=2)
    # Editable date for when the fee is considered received/posted
    effective_date = models.DateField(
        blank=True,
        null=True,
        help_text="Effective date the fee was posted/received (used for balance sheet sorting).",
    )
    due_date = models.DateField(
        blank=True, null=True, help_text="Optional due date for the fee."
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["program__name", "name"]
        unique_together = ("program", "name")

    def __str__(self):
        return f"{self.program.name} — {self.name}: ${self.amount}"


class Payment(models.Model):
    PAID_VIA_CHOICES = [
        ("check", "Check"),
        ("credit_card", "Credit Card"),
        ("cash", "Cash"),
        ("camp", "Camp"),
        ("other", "Other"),
    ]

    student = models.ForeignKey(
        "Student", on_delete=models.CASCADE, related_name="payments"
    )
    program = models.ForeignKey(
        "Program", on_delete=models.CASCADE, related_name="payments"
    )
    amount = models.DecimalField(max_digits=8, decimal_places=2)
    paid_on = models.DateField()
    paid_via = models.CharField(max_length=20, choices=PAID_VIA_CHOICES, default="cash")
    check_number = models.PositiveIntegerField(blank=True, null=True)
    camp_hours = models.PositiveIntegerField(blank=True, null=True)
    notes = models.TextField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-paid_on", "-created_at"]
        indexes = [
            models.Index(
                fields=["student", "program"], name="payment_student_program_idx"
            ),
            models.Index(
                fields=["program", "paid_on"], name="payment_program_date_idx"
            ),
        ]

    def __str__(self):
        via = dict(self.PAID_VIA_CHOICES).get(self.paid_via, self.paid_via)
        details = (
            f" (check #{self.check_number})"
            if (self.paid_via == "check" and self.check_number)
            else ""
        )
        return f"Payment ${self.amount} by {self.student} in {self.program.name} via {via}{details} on {self.paid_on}"

    def clean(self):
        # Ensure the student is enrolled in the payment's program
        from django.core.exceptions import ValidationError

        if (
            self.program_id
            and not Enrollment.objects.filter(
                student=self.student, program_id=self.program_id
            ).exists()
        ):
            raise ValidationError(
                "Student must be enrolled in the selected program for this payment."
            )


class SlidingScaleSettings(models.Model):
    """Singleton row holding the portal-wide sliding scale calculation constants.

    These are based on the federal poverty guidelines and can be edited by a
    Lead Mentor from the Portal Settings page.
    """

    base_amount = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=Decimal("10150.00"),
        help_text="Federal poverty guideline base amount (for household size).",
    )
    additional_member_amount = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=Decimal("5500.00"),
        help_text="Amount added per household member when computing the poverty guideline base.",
    )
    low_multiplier = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=Decimal("1.50"),
        help_text="Multiplier applied to the poverty guideline base to compute the lower income boundary.",
    )
    high_multiplier = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=Decimal("4.00"),
        help_text="Multiplier applied to the poverty guideline base to compute the upper income boundary.",
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Sliding Scale Settings"
        verbose_name_plural = "Sliding Scale Settings"

    def __str__(self):
        return "Sliding Scale Settings"

    @classmethod
    def get_solo(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj

    def compute_discount_percent(self, family_size, adjusted_gross_income):
        """Compute the suggested discount percent (0-100) for a household.

        Follows: fed_base = base_amount + family_size * additional_member_amount;
        low_boundary = fed_base * low_multiplier; high_boundary = fed_base * high_multiplier;
        percent_owed = (agi - low_boundary) / (high_boundary - low_boundary) * 100,
        clamped to [0, 100]. The discount percent is 100 - percent_owed.
        """
        if family_size is None or adjusted_gross_income is None:
            return None
        fed_base = self.base_amount + (
            Decimal(family_size) * self.additional_member_amount
        )
        low_boundary = fed_base * self.low_multiplier
        high_boundary = fed_base * self.high_multiplier
        if high_boundary == low_boundary:
            return Decimal("0.00")
        ratio = (Decimal(adjusted_gross_income) - low_boundary) / (
            high_boundary - low_boundary
        )
        ratio = max(Decimal("0"), min(Decimal("1"), ratio))
        percent_owed = ratio * Decimal("100")
        discount_percent = Decimal("100") - percent_owed
        return discount_percent.quantize(Decimal("0.01"))


class SlidingScale(models.Model):
    """An income-based discount applied to a student's fees across ALL of the
    programs they're enrolled in during the effective date range (not tied to
    a single program). A row represents one application/period; a parent
    applies (creating a "pending" row), and a Lead Mentor reviews it.
    """

    STATUS_PENDING = "pending"
    STATUS_APPROVED = "approved"
    STATUS_DECLINED = "declined"
    STATUS_CHOICES = [
        (STATUS_PENDING, "Pending Review"),
        (STATUS_APPROVED, "Approved"),
        (STATUS_DECLINED, "Declined"),
    ]

    student = models.ForeignKey(
        "Student", on_delete=models.CASCADE, related_name="sliding_scales"
    )
    percent = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        blank=True,
        null=True,
        help_text="Percent discount applied to total fees (0–100), across all of the student's programs.",
    )
    date = models.DateField(
        blank=True,
        null=True,
        help_text="Effective start date. Only fees on or after this date (and before the expiration date, if any) will be discounted.",
    )
    expiration_date = models.DateField(
        blank=True,
        null=True,
        help_text="Date the discount stops applying. Leave blank for no expiration.",
    )
    family_size = models.PositiveIntegerField(
        blank=True, null=True, verbose_name="Household size"
    )
    adjusted_gross_income = models.DecimalField(
        max_digits=10, decimal_places=2, blank=True, null=True
    )
    status = models.CharField(
        max_length=10, choices=STATUS_CHOICES, default=STATUS_APPROVED
    )
    decline_reason = models.TextField(
        blank=True,
        null=True,
        help_text="Shown to the parent when the application is declined.",
    )
    applied_by = models.ForeignKey(
        "Adult",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="sliding_scale_applications",
        help_text="Parent who submitted this application, if any.",
    )
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    reviewed_at = models.DateTimeField(blank=True, null=True)
    notes = models.TextField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(
                fields=["status", "-created_at"], name="slidingscale_status_idx"
            ),
            models.Index(
                fields=["student", "status", "-date", "-created_at"],
                name="slidingscale_active_lookup_idx",
            ),
        ]

    def __str__(self):
        return f"Sliding scale ({self.get_status_display()}) for {self.student}"

    @property
    def is_pending(self):
        return self.status == self.STATUS_PENDING

    @property
    def is_active(self):
        """Whether this approved record currently applies (not expired)."""
        if self.status != self.STATUS_APPROVED:
            return False
        today = datetime.date.today()
        if self.expiration_date and self.expiration_date < today:
            return False
        return True


def _tax_form_upload_to(instance, filename):
    from programs.utils.files import sanitize_upload_filename

    return f"tax_forms/{sanitize_upload_filename(filename)}"


class TaxForm(models.Model):
    sliding_scale = models.ForeignKey(
        SlidingScale, on_delete=models.CASCADE, related_name="tax_forms"
    )
    file = EncryptedFileField(
        upload_to=_tax_form_upload_to,
        max_length=255,
        help_text="Uploaded tax form for sliding scale verification. Will be deleted after review.",
    )
    uploaded_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Tax form for {self.sliding_scale}"


class FeeAssignment(models.Model):
    """
    Links a Fee to specific students (within the Fee's program).
    If a Fee has any assignments, it applies ONLY to those students.
    """

    fee = models.ForeignKey("Fee", on_delete=models.CASCADE, related_name="assignments")
    student = models.ForeignKey(
        "Student", on_delete=models.CASCADE, related_name="fee_assignments"
    )

    # Optional note
    notes = models.TextField(blank=True, null=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ("fee", "student")
        ordering = [
            "fee__program__name",
            "fee__name",
            "student__last_name",
            "student__first_name",
        ]

    def __str__(self):
        return f"{self.fee.name} → {self.student}"

    def clean(self):
        # Ensure the student is enrolled in the same program as the fee
        from django.core.exceptions import ValidationError

        program = self.fee.program if self.fee_id else None
        if (
            program
            and not Enrollment.objects.filter(
                student=self.student, program=program
            ).exists()
        ):
            raise ValidationError(
                "Assigned student must be enrolled in the fee’s program."
            )


def _program_document_upload_to(instance, filename):
    """Files land at MEDIA_ROOT/program_documents/<program_id>/<filename>."""
    from programs.utils.files import sanitize_upload_filename

    pid = instance.program_id or "unassigned"
    filename = sanitize_upload_filename(filename)
    return f"program_documents/{pid}/{filename}"


def _student_document_upload_to(instance, filename):
    """Files land at MEDIA_ROOT/student_documents/<student_id>/<filename>."""
    from programs.utils.files import sanitize_upload_filename

    sid = instance.student_id or "unassigned"
    filename = sanitize_upload_filename(filename)
    return f"student_documents/{sid}/{filename}"


class ProgramDocument(models.Model):
    """A document (typically a PDF) that an approved applicant needs to
    download, sign, and re-upload before becoming a full student in the
    program. Managed by lead mentors in Django admin.
    """

    program = models.ForeignKey(
        Program,
        on_delete=models.CASCADE,
        related_name="documents",
    )
    name = models.CharField(
        max_length=200,
        help_text="Short name shown to applicants (e.g. 'Photo release form').",
    )
    description = models.TextField(
        blank=True,
        default="",
        help_text="Optional longer explanation shown next to the download link.",
    )
    file = models.FileField(
        upload_to=_program_document_upload_to,
        max_length=255,
        help_text="The blank PDF (or other file) for the applicant to download and fill out.",
    )
    is_required = models.BooleanField(
        default=True,
        help_text="If checked, applicants must upload a signed copy before being marked complete.",
    )
    display_order = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(
        default=True,
        help_text="Uncheck to hide this document from applicants without deleting it.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["program", "display_order", "name"]

    def __str__(self):
        return f"{self.name} ({self.program})"


class StudentDocument(models.Model):
    """A signed document (typically a PDF) uploaded by a student (or their parent)
    that was originally part of a Program enrollment process and carried over
    from their application.
    """

    student = models.ForeignKey(
        Student,
        on_delete=models.CASCADE,
        related_name="signed_documents",
    )
    program_document = models.ForeignKey(
        ProgramDocument,
        on_delete=models.CASCADE,
        related_name="student_submissions",
    )
    file = models.FileField(
        upload_to=_student_document_upload_to,
        max_length=255,
    )
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("student", "program_document")
        ordering = ["-uploaded_at"]

    def __str__(self):
        return f"{self.program_document.name} for {self.student}"


class AddressGeocode(models.Model):
    """Cache of geocoded addresses used by the student map view.

    Keyed by a normalized address string so each unique address is looked up
    (and counted against the geocoding service's usage policy) only once.
    Students who share an address (e.g. siblings) reuse the same row.
    """

    address = models.CharField(
        max_length=512,
        unique=True,
        db_index=True,
        help_text="Normalized address string used as the cache key.",
    )
    latitude = models.FloatField(
        null=True,
        blank=True,
        help_text="Latitude of the address, or null if it could not be geocoded.",
    )
    longitude = models.FloatField(
        null=True,
        blank=True,
        help_text="Longitude of the address, or null if it could not be geocoded.",
    )
    found = models.BooleanField(
        default=False,
        help_text="True if the geocoder returned a result for this address.",
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at"]

    def __str__(self):
        if self.found and self.latitude is not None and self.longitude is not None:
            return f"{self.address} → ({self.latitude:.4f}, {self.longitude:.4f})"
        return f"{self.address} → not found"


class BackgroundCheckType(models.TextChoices):
    STATE_POLICE = "state_police", "PA State Police Criminal History (PATCH)"
    CHILD_ABUSE = "child_abuse", "PA Child Abuse History (Act 151)"
    FBI = "fbi", "FBI Fingerprint (Act 114)"


# PA clearances are valid for 5 years for both adults and students.
CLEARANCE_VALIDITY_YEARS = 5


class BackgroundCheck(models.Model):
    """A single PA background clearance held by a student or an adult.

    One row per check type per person. Both ``student`` and ``adult`` are
    nullable but exactly one must be set. The university holds the actual
    forms/reports, so we only track clearance status and (when known) the
    expiration/obtained dates. Clearances are valid for 5 years; a student
    becoming an alumni may have both a student and an adult record, so we do
    not enforce a unique-per-type constraint at the database level (the
    application logic keeps at most one row per type per person).
    """

    student = models.ForeignKey(
        Student,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="background_checks",
    )
    adult = models.ForeignKey(
        Adult,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="background_checks",
    )
    check_type = models.CharField(max_length=20, choices=BackgroundCheckType.choices)
    cleared = models.BooleanField(
        default=False,
        help_text="Whether this clearance has been obtained and passed.",
    )
    obtained_date = models.DateField(
        null=True,
        blank=True,
        help_text="When this clearance was obtained (becomes active). Expiration is calculated automatically.",
    )

    class Meta:
        ordering = ["check_type"]

    def __str__(self):
        holder = self.student or self.adult
        return f"{self.get_check_type_display()} for {holder}"

    @property
    def expiration_date(self):
        """When this clearance expires.

        PA clearances are valid for 5 years from the date obtained, so the
        expiration date is always derived from ``obtained_date``.
        """
        if not self.obtained_date:
            return None
        return self.obtained_date + relativedelta(years=CLEARANCE_VALIDITY_YEARS)

    def clean(self):
        from django.core.exceptions import ValidationError

        def holder_set(fk):
            # Works for both saved (via _id) and unsaved (via cached obj) FKs.
            if getattr(self, f"{fk}_id", None):
                return True
            try:
                obj = getattr(self, fk)
            except Exception:
                return False
            return obj is not None and bool(obj.pk)

        if holder_set("student") == holder_set("adult"):
            raise ValidationError(
                "A background check must belong to exactly one student or adult."
            )

    @property
    def is_valid(self):
        """Whether the clearance is currently valid (passed and not expired)."""
        if not self.cleared:
            return False
        expiration = self.expiration_date
        if not expiration:
            return True
        return expiration >= timezone.localdate()


def _mentor_agreement_upload_to(instance, filename):
    from programs.utils.files import sanitize_upload_filename

    slug = instance.slug or "unassigned"
    filename = sanitize_upload_filename(filename)
    return f"mentor_agreements/{slug}/{filename}"


class MentorAgreement(models.Model):
    """Versioned document that mentors must accept.

    Each unique ``slug`` groups versions of the same document.  Only one
    version per slug should be active at a time — ``save()`` automatically
    deactivates other active versions of the same slug.  Content can be
    provided as markdown *or* a uploaded document (PDF, etc.), or both.
    """

    slug = models.SlugField(
        help_text="URL-friendly identifier that groups versions of the same document.",
    )
    version = models.PositiveIntegerField()
    title = models.CharField(max_length=200)
    content = models.TextField(
        blank=True,
        help_text="Markdown content of the agreement. Leave blank for document-only agreements.",
    )
    document = models.FileField(
        upload_to=_mentor_agreement_upload_to,
        blank=True,
        max_length=255,
        help_text="Uploaded document (PDF, etc.) for the agreement. Leave blank for markdown-only agreements.",
    )
    effective_date = models.DateField()
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ("slug", "version")
        ordering = ["-version"]
        verbose_name = "Mentor Agreement"
        verbose_name_plural = "Mentor Agreements"

    def __str__(self):
        return f"{self.title} (v{self.version})"

    def save(self, *args, **kwargs):
        if self.is_active:
            MentorAgreement.objects.filter(is_active=True, slug=self.slug).exclude(
                pk=self.pk
            ).update(is_active=False)
        super().save(*args, **kwargs)

    @classmethod
    def get_active(cls, slug=None):
        """Return the current active agreement, optionally filtered by slug."""
        qs = cls.objects.filter(is_active=True)
        if slug:
            qs = qs.filter(slug=slug)
        return qs.first()

    @classmethod
    def get_all_active(cls):
        """Return all active agreements (one per slug)."""
        return cls.objects.filter(is_active=True)


class MentorAgreementAcceptance(models.Model):
    """Records that an Adult has accepted a specific MentorAgreement version."""

    adult = models.ForeignKey(
        Adult,
        on_delete=models.CASCADE,
        related_name="agreement_acceptances",
    )
    agreement = models.ForeignKey(
        MentorAgreement,
        on_delete=models.CASCADE,
        related_name="acceptances",
    )
    accepted_at = models.DateTimeField(auto_now_add=True)
    ip_address = models.GenericIPAddressField(
        null=True,
        blank=True,
        help_text="IP address of the acceptor (for audit trail).",
    )

    class Meta:
        unique_together = ("adult", "agreement")
        ordering = ["-accepted_at"]

    def __str__(self):
        return f"{self.adult} accepted {self.agreement}"

    @classmethod
    def has_accepted_for_user(cls, user):
        """Check if a user has accepted all current active agreements.

        Returns True if there are no active agreements (graceful degradation)
        or if the user's Adult profile has acceptances for every active version.
        Returns False if the user has no Adult profile.
        """
        active = MentorAgreement.get_all_active()
        if not active.exists():
            return True
        try:
            adult = user.adult_profile
        except (AttributeError, Adult.DoesNotExist):
            return False
        accepted_ids = cls.objects.filter(
            adult=adult, agreement__in=active
        ).values_list("agreement_id", flat=True)
        return active.filter(id__in=accepted_ids).count() == active.count()


def _agreement_submission_upload_to(instance, filename):
    """Files land at MEDIA_ROOT/agreement_submissions/<adult_id>/<filename>."""
    from programs.utils.files import sanitize_upload_filename

    adult_id = instance.adult_id or "unassigned"
    filename = sanitize_upload_filename(filename)
    return f"agreement_submissions/{adult_id}/{filename}"


class MentorAgreementSubmission(models.Model):
    """A signed document uploaded by an Adult in response to a
    :class:`MentorAgreement` that has an attached document (PDF).

    One row per (adult, agreement).  Re-uploading replaces the file.
    """

    adult = models.ForeignKey(
        Adult,
        on_delete=models.CASCADE,
        related_name="agreement_submissions",
    )
    agreement = models.ForeignKey(
        MentorAgreement,
        on_delete=models.CASCADE,
        related_name="submissions",
    )
    file = models.FileField(
        upload_to=_agreement_submission_upload_to,
        max_length=255,
    )
    uploaded_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ("adult", "agreement")
        ordering = ["-uploaded_at"]
        verbose_name = "Mentor Agreement Submission"
        verbose_name_plural = "Mentor Agreement Submissions"

    def __str__(self):
        return f"Signed copy for {self.agreement} by {self.adult}"
