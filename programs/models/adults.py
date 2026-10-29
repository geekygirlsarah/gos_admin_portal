import datetime

import pghistory
from dateutil.relativedelta import relativedelta
from django.conf import settings
from django.core.signals import request_finished, request_started
from django.db import models
from django.utils import timezone

from organizations.models import OrganizationManager, OrganizationQuerySet
from programs.constants import (
    MENTOR_ROLE_CHOICES,
    PHONE_TYPE_CHOICES,
    RELATIONSHIP_CHOICES,
    STATE_CHOICES,
)
from programs.validators import validate_phone_number, validate_zip_code

from .fields import sync_linked_user

_ACTIVE_CHECK_TYPES_CACHE = {}


def get_active_background_check_types(org=None):
    org_id = org.id if org else None
    if org_id not in _ACTIVE_CHECK_TYPES_CACHE:
        types = list(
            BackgroundCheckType.objects.for_organization(org)
            .filter(is_active=True)
            .order_by("display_order", "name")
        )
        _ACTIVE_CHECK_TYPES_CACHE[org_id] = types
        if org_id is not None and None not in _ACTIVE_CHECK_TYPES_CACHE:
            _ACTIVE_CHECK_TYPES_CACHE[None] = types
    return _ACTIVE_CHECK_TYPES_CACHE[org_id]


def clear_active_background_check_types_cache(*args, **kwargs):
    _ACTIVE_CHECK_TYPES_CACHE.clear()


request_started.connect(clear_active_background_check_types_cache)
request_finished.connect(clear_active_background_check_types_cache)


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
    objects = OrganizationManager.from_queryset(OrganizationQuerySet)()

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

    def requires_background_check(self, active_types=None) -> bool:
        """Whether the adult must hold any background clearances based on active check types."""
        if active_types is None:
            active_types = get_active_background_check_types(
                getattr(self, "organization", None)
            )
        return any(t.applies_to_adult(self) for t in active_types)

    def needs_background_check(self, active_types=None) -> bool:
        """Whether the adult requires clearances AND is missing at least one valid check."""
        if active_types is None:
            active_types = get_active_background_check_types(
                getattr(self, "organization", None)
            )
        required_types = [t for t in active_types if t.applies_to_adult(self)]
        if not required_types:
            return False
        valid_type_ids = {
            bc.check_type_id for bc in self.background_checks.all() if bc.is_valid
        }
        required_type_ids = {t.id for t in required_types}
        return not required_type_ids.issubset(valid_type_ids)

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


class BackgroundCheckType(models.Model):
    # Constants for backward compatibility with migrations/constants
    STATE_POLICE = "state_police"
    CHILD_ABUSE = "child_abuse"
    FBI = "fbi"

    DEFAULT_PA_TYPES = [
        {
            "name": "PA State Police Criminal History (PATCH)",
            "description": "Pennsylvania State Police criminal history check.",
            "validity_years": 5,
            "applies_to_students": True,
            "applies_to_mentors": True,
            "applies_to_lead_mentors": True,
            "applies_to_parents": False,
            "applies_to_alumni": False,
            "student_min_age_by_date": 17,
            "student_min_age_cutoff_date": datetime.date(2000, 9, 1),
            "student_min_age_during_program": 18,
            "display_order": 1,
        },
        {
            "name": "PA Child Abuse History (Act 151)",
            "description": "Pennsylvania Department of Human Services child abuse clearance.",
            "validity_years": 5,
            "applies_to_students": True,
            "applies_to_mentors": True,
            "applies_to_lead_mentors": True,
            "applies_to_parents": False,
            "applies_to_alumni": False,
            "student_min_age_by_date": 17,
            "student_min_age_cutoff_date": datetime.date(2000, 9, 1),
            "student_min_age_during_program": 18,
            "display_order": 2,
        },
        {
            "name": "FBI Fingerprint (Act 114)",
            "description": "Federal Bureau of Investigation fingerprint background check.",
            "validity_years": 5,
            "applies_to_students": True,
            "applies_to_mentors": True,
            "applies_to_lead_mentors": True,
            "applies_to_parents": False,
            "applies_to_alumni": False,
            "student_min_age_by_date": 17,
            "student_min_age_cutoff_date": datetime.date(2000, 9, 1),
            "student_min_age_during_program": 18,
            "display_order": 3,
        },
    ]

    organization = models.ForeignKey(
        "organizations.Organization",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="background_check_types",
        help_text="Organization that owns this background check type.",
    )
    objects = OrganizationManager.from_queryset(OrganizationQuerySet)()

    name = models.CharField(
        max_length=200,
        help_text="Name of the background check / clearance type.",
    )
    description = models.TextField(
        blank=True,
        help_text="Optional description or instructions for obtaining this clearance.",
    )
    validity_years = models.PositiveIntegerField(
        default=5,
        help_text="Duration in years this clearance remains valid (0 for non-expiring).",
    )
    applies_to_students = models.BooleanField(
        default=True,
        help_text="Whether this check is required for students.",
    )
    applies_to_mentors = models.BooleanField(
        default=True,
        help_text="Whether this check is required for mentors.",
    )
    applies_to_lead_mentors = models.BooleanField(
        default=True,
        help_text="Whether this check is required for lead mentors.",
    )
    applies_to_parents = models.BooleanField(
        default=False,
        help_text="Whether this check is required for parents.",
    )
    applies_to_alumni = models.BooleanField(
        default=False,
        help_text="Whether this check is required for alumni.",
    )
    student_min_age_by_date = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text="For students: minimum age reached by the specified cutoff date (e.g. 17).",
    )
    student_min_age_cutoff_date = models.DateField(
        null=True,
        blank=True,
        help_text="Cutoff date (month and day used annually) for the minimum age requirement (e.g. Sept 1).",
    )
    student_min_age_during_program = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text="For students: minimum age reached during an active program enrollment (e.g. 18).",
    )
    is_active = models.BooleanField(
        default=True,
        help_text="Whether this background check type is actively required.",
    )
    display_order = models.PositiveIntegerField(
        default=0,
        help_text="Order in which this check is displayed in forms and lists.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["display_order", "name"]
        verbose_name = "Background Check Type"
        verbose_name_plural = "Background Check Types"

    def __str__(self):
        return self.name

    @property
    def student_min_age(self):
        """Compatibility property returning the primary minimum age constraint if set."""
        return self.student_min_age_by_date or self.student_min_age_during_program

    @student_min_age.setter
    def student_min_age(self, value):
        self.student_min_age_by_date = value

    @property
    def student_min_age_cutoff_month(self):
        """Cutoff month (1-12) or 9 (September) as default."""
        if self.student_min_age_cutoff_date:
            return self.student_min_age_cutoff_date.month
        return 9

    @property
    def student_min_age_cutoff_day(self):
        """Cutoff day (1-31) or 1 as default."""
        if self.student_min_age_cutoff_date:
            return self.student_min_age_cutoff_date.day
        return 1

    def save(self, *args, **kwargs):
        clear_active_background_check_types_cache()
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        clear_active_background_check_types_cache()
        super().delete(*args, **kwargs)

    def applies_to_student(self, student) -> bool:
        """Whether this check type applies to the given student based on configured criteria."""
        if not self.is_active or not self.applies_to_students:
            return False

        has_by_date_rule = self.student_min_age_by_date is not None
        has_during_program_rule = self.student_min_age_during_program is not None

        # If no age criteria configured, applies to all students
        if not has_by_date_rule and not has_during_program_rule:
            return True

        dob = getattr(student, "date_of_birth", None)
        if not dob:
            return False
        if isinstance(dob, str):
            try:
                dob = datetime.datetime.strptime(dob, "%Y-%m-%d").date()
            except ValueError:
                return False

        today = timezone.localdate()

        # Criterion 1: Minimum age by [date]
        if has_by_date_rule:
            cutoff_month = (
                self.student_min_age_cutoff_date.month
                if self.student_min_age_cutoff_date
                else 9
            )
            cutoff_day = (
                self.student_min_age_cutoff_date.day
                if self.student_min_age_cutoff_date
                else 1
            )
            if (today.month, today.day) >= (cutoff_month, cutoff_day):
                academic_year = today.year
            else:
                academic_year = today.year - 1
            oldest_dob = datetime.date(
                academic_year - self.student_min_age_by_date, cutoff_month, cutoff_day
            )
            if dob <= oldest_dob:
                return True

        # Criterion 2: Minimum age during program enrollment
        if has_during_program_rule:
            target_age = self.student_min_age_during_program
            try:
                birthday = dob.replace(year=dob.year + target_age)
            except ValueError:
                birthday = dob.replace(month=2, day=28, year=dob.year + target_age)

            if (
                hasattr(student, "_prefetched_objects_cache")
                and "enrollment_set" in student._prefetched_objects_cache
            ):
                enrollments = [
                    e
                    for e in student.enrollment_set.all()
                    if e.active and e.program and e.program.active
                ]
            else:
                enrollments = list(
                    student.enrollment_set.select_related("program").filter(
                        active=True, program__active=True
                    )
                )

            for enr in enrollments:
                prog = enr.program
                if prog.end_date:
                    if birthday <= prog.end_date:
                        return True
                elif prog.start_date:
                    prog_end_approx = prog.start_date.replace(
                        year=prog.start_date.year + 1
                    )
                    if birthday <= prog_end_approx:
                        return True
                else:
                    if birthday <= today or (today.year - dob.year) >= target_age:
                        return True

        return False

    def applies_to_adult(self, adult) -> bool:
        """Whether this check type applies to the given adult based on their role."""
        if not self.is_active:
            return False

        is_lead = False
        user = getattr(adult, "user", None)
        if user:
            if (
                getattr(user, "is_superuser", False)
                or user.groups.filter(name="LeadMentor").exists()
            ):
                is_lead = True

        if is_lead and self.applies_to_lead_mentors:
            return True
        if adult.is_mentor and self.applies_to_mentors:
            return True
        if adult.is_parent and self.applies_to_parents:
            return True
        if adult.is_alumni and self.applies_to_alumni:
            return True
        return False


# PA clearances are valid for 5 years for both adults and students.
CLEARANCE_VALIDITY_YEARS = 5


class BackgroundCheck(models.Model):
    """A single background clearance held by a student or an adult.

    One row per check type per person. Both ``student`` and ``adult`` are
    nullable but exactly one must be set. The university/org holds the actual
    forms/reports, so we track clearance status and (when known) the
    expiration/obtained dates.
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
    objects = OrganizationManager.from_queryset(OrganizationQuerySet)()

    adult = models.ForeignKey(
        Adult,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="background_checks",
    )
    check_type = models.ForeignKey(
        BackgroundCheckType,
        on_delete=models.CASCADE,
        related_name="checks",
    )
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
        ordering = ["check_type__display_order", "check_type__name"]

    def __str__(self):
        holder = self.student or self.adult
        return f"{self.get_check_type_display()} for {holder}"

    def get_check_type_display(self):
        """Compatibility method for templates/code expecting get_check_type_display()."""
        if self.check_type_id and self.check_type:
            return self.check_type.name
        return ""

    @property
    def expiration_date(self):
        """When this clearance expires.

        Derived from ``obtained_date`` and the check type's ``validity_years``.
        """
        if not self.obtained_date:
            return None
        validity_years = 5
        if self.check_type_id and self.check_type:
            validity_years = self.check_type.validity_years
        if not validity_years or validity_years <= 0:
            return None
        return self.obtained_date + relativedelta(years=validity_years)

    @property
    def date_acquired(self):
        return self.obtained_date

    @date_acquired.setter
    def date_acquired(self, value):
        self.obtained_date = value

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
