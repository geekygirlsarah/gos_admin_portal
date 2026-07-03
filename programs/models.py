import base64
import datetime
import logging
from io import BytesIO

import pghistory
from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from django.core.files.base import ContentFile
from django.db import models
from PIL import ImageFile

from programs.constants import (
    MENTOR_ROLE_CHOICES,
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
        # Fallback to a key derived from SECRET_KEY
        key = base64.urlsafe_b64encode(
            settings.SECRET_KEY[:32].encode().ljust(32, b"\0")
        )
    if isinstance(key, str):
        key = key.encode()
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
        if value is None:
            return value
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
        if value is None:
            return value
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
                if "b" in mode:
                    content = f.read()
                    try:
                        fernet = get_fernet()
                        decrypted_content = fernet.decrypt(content)
                        return BytesIO(decrypted_content)
                    except InvalidToken:
                        # If decryption fails, return original (might be already decrypted or not encrypted)
                        f.seek(0)
                        return f
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

    def save(self, *args, **kwargs):
        if not self.applications_open and self.start_date:
            self.applications_open = self.start_date
        if not self.applications_close and self.end_date:
            self.applications_close = self.end_date
        super().save(*args, **kwargs)


class School(models.Model):
    name = models.CharField(max_length=150, unique=True)
    district = models.CharField(
        max_length=150, blank=True, null=True, verbose_name="School district"
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
    # Background clearances (per-student, separate from mentor clearances)
    has_passed_clearances = models.BooleanField(
        default=False,
        help_text="Check if the student has completed and passed required background clearances.",
    )
    clearances_expiration_date = models.DateField(
        blank=True,
        null=True,
        help_text="Expiration date for the student's background clearances (if applicable).",
    )
    photo = models.ImageField(upload_to="photos/students/", blank=True, null=True)

    address = models.CharField(max_length=255, blank=True, null=True)
    city = models.CharField(max_length=100, blank=True, null=True)
    state = models.CharField(
        max_length=50, choices=STATE_CHOICES, blank=True, null=True, default="PA"
    )
    zip_code = models.CharField(
        max_length=20, blank=True, null=True, validators=[validate_zip_code]
    )

    cell_phone_number = models.CharField(
        max_length=30, blank=True, null=True, validators=[validate_phone_number]
    )
    personal_email = models.EmailField(blank=True, null=True)
    directory_consent = models.BooleanField(
        default=True,
        verbose_name="OK to share name, address, email, and phone for student directory",
    )

    andrew_id = models.CharField(max_length=50, blank=True, null=True)
    andrew_email = models.EmailField(blank=True, null=True)

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

    # New contact fields
    primary_contact = models.ForeignKey(
        "Adult",
        on_delete=models.SET_NULL,
        related_name="primary_for",
        null=True,
        blank=True,
    )
    secondary_contact = models.ForeignKey(
        "Adult",
        on_delete=models.SET_NULL,
        related_name="secondary_for",
        null=True,
        blank=True,
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
        relationship to THIS student.
        """
        seen_ids = set()
        result = []

        # Helper to get relationship string
        rels = {}
        if self.pk:
            rels = {
                r.adult_id: r.relationship_to_student
                for r in self.adultstudentrelationship_set.all()
            }

        def add_adult(adult):
            if adult and adult.id not in seen_ids:
                adult.attached_rel = rels.get(adult.id, "parent")
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

    def __str__(self):
        pref = self.first_name or self.legal_first_name
        full = f"{pref} {self.last_name}".strip()
        return full or f"Student #{self.pk}"

    def _prune_dangling_contacts(self):
        """Clear dangling primary/secondary contact FKs in a single query."""
        pks = [pk for pk in (self.primary_contact_id, self.secondary_contact_id) if pk]
        if not pks:
            return
        try:
            existing = set(
                Adult.objects.filter(pk__in=pks).values_list("pk", flat=True)
            )
            if self.primary_contact_id and self.primary_contact_id not in existing:
                self.primary_contact_id = None
            if self.secondary_contact_id and self.secondary_contact_id not in existing:
                self.secondary_contact_id = None
        except Exception:
            logger.debug("Unexpected error in contact cleanup", exc_info=True)

    def save(self, *args, **kwargs):
        # Normalize any new photo upload to RGB JPEG in-memory (fixes EXIF orientation, handles HEIC).
        from .utils import normalize_image_field

        normalize_image_field(getattr(self, "photo", None), log_prefix="Student photo")
        self._prune_dangling_contacts()
        super().save(*args, **kwargs)

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

    def requires_background_check(self, program: "Program") -> bool:
        """Whether the student will be 18 at any point during the given program's dates.
        Returns False if insufficient data (no DOB or no program dates).
        """
        if not program or not program.start_date or not program.end_date:
            return False
        b18 = self.eighteenth_birthday()
        if not b18:
            return False
        # If the program end is on/after 18th birthday, and the start is on/before end.
        return program.end_date >= b18 and program.start_date <= program.end_date


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
        unique=True,
        help_text="Primary contact email (e.g. Gmail). Used for login and notifications.",
    )
    phone_number = models.CharField(
        max_length=30, blank=True, null=True, validators=[validate_phone_number]
    )
    cell_phone = models.CharField(
        max_length=30, blank=True, null=True, validators=[validate_phone_number]
    )
    home_phone = models.CharField(
        max_length=30, blank=True, null=True, validators=[validate_phone_number]
    )
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
    photo = models.ImageField(upload_to="photos/adults/", blank=True, null=True)

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

    # Clearances
    has_paca_clearance = models.BooleanField(default=False)
    has_patch_clearance = models.BooleanField(default=False)
    has_fbi_clearance = models.BooleanField(default=False)
    pa_clearances_expiration_date = models.DateField(blank=True, null=True)

    # Emergency contact
    emergency_contact_name = models.CharField(max_length=150, blank=True, null=True)
    emergency_contact_phone = models.CharField(
        max_length=30, blank=True, null=True, validators=[validate_phone_number]
    )

    # Status
    email_updates = models.BooleanField(
        default=False, help_text="If checked, this adult will receive email updates."
    )
    active = models.BooleanField(default=True, db_index=True)

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
                fields=["is_parent", "active"], name="adult_parent_active_idx"
            ),
            models.Index(
                fields=["is_mentor", "active"], name="adult_mentor_active_idx"
            ),
            models.Index(
                fields=["is_alumni", "active"], name="adult_alumni_active_idx"
            ),
        ]

    def __str__(self):
        pref = self.preferred_first_name or self.first_name
        return f"{pref} {self.last_name}".strip()

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

    def save(self, *args, **kwargs):
        # Normalize newly uploaded photo: RGB JPEG, fixed orientation, in-memory (shared with Student).
        from .utils import normalize_image_field

        normalize_image_field(getattr(self, "photo", None), log_prefix="Adult photo")
        super().save(*args, **kwargs)


class Fee(models.Model):
    program = models.ForeignKey(Program, on_delete=models.CASCADE, related_name="fees")
    name = models.CharField(max_length=200)
    amount = models.DecimalField(max_digits=8, decimal_places=2)
    # Editable date for when the fee is considered received/posted
    date = models.DateField(
        blank=True,
        null=True,
        help_text="Date the fee was posted/received (used for balance sheet sorting).",
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


class SlidingScale(models.Model):
    student = models.ForeignKey(
        "Student", on_delete=models.CASCADE, related_name="sliding_scales"
    )
    program = models.ForeignKey(
        "Program", on_delete=models.CASCADE, related_name="sliding_scales"
    )
    percent = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        help_text="Percent discount applied to total program fees (0–100).",
    )
    date = models.DateField(
        blank=True,
        null=True,
        help_text="Effective date the sliding scale starts. Only fees on or after this date will be discounted.",
    )
    family_size = models.PositiveIntegerField(blank=True, null=True)
    adjusted_gross_income = models.DecimalField(
        max_digits=10, decimal_places=2, blank=True, null=True
    )
    is_pending = models.BooleanField(default=False)
    notes = models.TextField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ("student", "program")
        ordering = ["program__name", "student__last_name", "student__first_name"]
        indexes = [
            models.Index(
                fields=["student", "program"], name="slidingscale_stu_prog_idx"
            ),
        ]

    def __str__(self):
        return f"Sliding scale {self.percent}% for {self.student} in {self.program}"


class TaxForm(models.Model):
    sliding_scale = models.ForeignKey(
        SlidingScale, on_delete=models.CASCADE, related_name="tax_forms"
    )
    file = EncryptedFileField(
        upload_to="tax_forms/",
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
    pid = instance.program_id or "unassigned"
    return f"program_documents/{pid}/{filename}"


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
