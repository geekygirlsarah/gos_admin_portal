import pghistory
from dateutil.relativedelta import relativedelta
from django.conf import settings
from django.db import models
from django.utils import timezone

from programs.constants import (
    MENTOR_ROLE_CHOICES,
    PHONE_TYPE_CHOICES,
    RELATIONSHIP_CHOICES,
    STATE_CHOICES,
)
from programs.validators import validate_phone_number, validate_zip_code

from .fields import sync_linked_user


@pghistory.track()
class AdultStudentRelationship(models.Model):
    adult = models.ForeignKey("Adult", on_delete=models.CASCADE)
    student = models.ForeignKey("Student", on_delete=models.CASCADE)
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
    from programs.utils.files import build_upload_path

    return build_upload_path("photos/adults", instance=instance, filename=filename)


@pghistory.track()
class Adult(models.Model):
    organization = models.ForeignKey(
        "organizations.Organization",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="adults",
        help_text="Organization that owns this adult (multi-tenant; null until backfilled).",
    )
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
    legal_first_name = models.CharField(max_length=150, verbose_name="Legal first name")
    preferred_first_name = models.CharField(
        max_length=150, blank=True, null=True, verbose_name="Preferred first name"
    )
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
        "Student",
        related_name="adults",
        blank=True,
        through="AdultStudentRelationship",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["last_name", "legal_first_name"]
        indexes = [
            models.Index(
                fields=["last_name", "legal_first_name"], name="adult_name_idx"
            ),
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
    def email_delivery_issues(self):
        from .communication import EmailDeliveryEvent

        if (
            hasattr(self, "_prefetched_objects_cache")
            and "email_delivery_events" in self._prefetched_objects_cache
        ):
            return [
                e
                for e in self.email_delivery_events.all()
                if not e.resolved
                and e.event_type
                in (
                    EmailDeliveryEvent.EVENT_BOUNCED,
                    EmailDeliveryEvent.EVENT_COMPLAINT,
                )
            ]
        return self.email_delivery_events.filter(
            resolved=False,
            event_type__in=[
                EmailDeliveryEvent.EVENT_BOUNCED,
                EmailDeliveryEvent.EVENT_COMPLAINT,
            ],
        )

    @property
    def has_email_delivery_issue(self) -> bool:
        from .communication import EmailDeliveryEvent

        if (
            hasattr(self, "_prefetched_objects_cache")
            and "email_delivery_events" in self._prefetched_objects_cache
        ):
            return bool(self.email_delivery_issues)
        return self.email_delivery_events.filter(
            resolved=False,
            event_type__in=[
                EmailDeliveryEvent.EVENT_BOUNCED,
                EmailDeliveryEvent.EVENT_COMPLAINT,
            ],
        ).exists()

    @property
    def display_name(self):
        first = self.preferred_first_name or self.legal_first_name
        return f"{first} {self.last_name}".strip()

    @property
    def full_name(self):
        return self.display_name

    def __str__(self):
        return self.display_name

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

        from .students import Student

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
        from programs.utils import normalize_image_field

        normalize_image_field(getattr(self, "photo", None), log_prefix="Adult photo")
        super().save(*args, **kwargs)

        # Sync Adult name + login-active state to the linked User account.
        # Login stays enabled if they have login_enabled OR another active role
        # (parent/alumni).
        sync_linked_user(
            self,
            login_enabled=self.login_enabled or self.is_parent or self.is_alumni,
        )

    def has_accepted_current_agreement(self):
        """Whether this adult has accepted all current active MentorAgreements."""
        from .agreements import MentorAgreement, MentorAgreementAcceptance

        active = MentorAgreement.get_all_active()
        if not active.exists():
            return True
        accepted_ids = MentorAgreementAcceptance.objects.filter(
            adult=self, agreement__in=active
        ).values_list("agreement_id", flat=True)
        return active.filter(id__in=accepted_ids).count() == active.count()


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
        "Student",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="background_checks",
    )
    organization = models.ForeignKey(
        "organizations.Organization",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="background_checks",
        help_text="Organization that owns this background check (multi-tenant; null until backfilled).",
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
