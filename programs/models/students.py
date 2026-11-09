import datetime
import logging
import os
import uuid

import pghistory
from django.conf import settings
from django.db import models
from django.utils import timezone

from organizations.models import OrganizationManager, OrganizationQuerySet
from programs.constants import (
    PHONE_TYPE_CHOICES,
    STATE_CHOICES,
    TSHIRT_SIZE_CHOICES,
)
from programs.validators import validate_phone_number, validate_zip_code

from .fields import EncryptedFileField, EncryptedTextField, sync_linked_user

logger = logging.getLogger(__name__)


def _student_photo_upload_to(instance, filename):
    from programs.utils.files import build_upload_path

    extension = os.path.splitext(filename or "")[1].lower()
    if len(extension) > 10 or not extension[1:].isalnum():
        extension = ""
    return build_upload_path(
        "private/student_photos",
        instance=instance,
        filename=f"{uuid.uuid4().hex}{extension}",
        id_attr="organization_id",
    )


def _student_document_upload_to(instance, filename):
    """Files land at MEDIA_ROOT/student_documents/<student_id>/<filename>."""
    from programs.utils.files import build_upload_path

    return build_upload_path(
        "student_documents",
        instance=instance,
        filename=filename,
        id_attr="student_id",
        default_id="unassigned",
    )


@pghistory.track()
class Student(models.Model):
    organization = models.ForeignKey(
        "organizations.Organization",
        on_delete=models.PROTECT,
        related_name="students",
        help_text="Organization that owns this student (multi-tenant).",
    )
    objects = OrganizationManager.from_queryset(OrganizationQuerySet)()

    # Optional link to a User so students can self-manage later if desired
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="student_profile",
    )
    legal_first_name = models.CharField(max_length=150, verbose_name="Legal first name")
    preferred_first_name = models.CharField(
        max_length=150, blank=True, null=True, verbose_name="Preferred first name"
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

    org_account_id = models.CharField(max_length=50, blank=True, null=True)
    org_email = models.EmailField(blank=True, null=True)
    org_email_expiration = models.DateField(
        blank=True,
        null=True,
        help_text="Expiration date of this organization account / email.",
    )
    org_email_sponsor = models.ForeignKey(
        "Adult",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="sponsored_student_org_emails",
        help_text="The Adult (mentor) who sponsored this organization account.",
    )

    @property
    def andrew_id(self):
        return self.org_account_id

    @andrew_id.setter
    def andrew_id(self, value):
        self.org_account_id = value

    @property
    def andrew_email(self):
        return self.org_email

    @andrew_email.setter
    def andrew_email(self, value):
        self.org_email = value

    @property
    def andrew_id_expiration(self):
        return self.org_email_expiration

    @andrew_id_expiration.setter
    def andrew_id_expiration(self, value):
        self.org_email_expiration = value

    @property
    def andrew_id_sponsor(self):
        return self.org_email_sponsor

    @andrew_id_sponsor.setter
    def andrew_id_sponsor(self, value):
        self.org_email_sponsor = value

    @property
    def andrew_id_sponsor_id(self):
        return self.org_email_sponsor_id

    @andrew_id_sponsor_id.setter
    def andrew_id_sponsor_id(self, value):
        self.org_email_sponsor_id = value

    def __init__(self, *args, **kwargs):
        if "andrew_id" in kwargs:
            kwargs["org_account_id"] = kwargs.pop("andrew_id")
        if "andrew_email" in kwargs:
            kwargs["org_email"] = kwargs.pop("andrew_email")
        if "andrew_id_expiration" in kwargs:
            kwargs["org_email_expiration"] = kwargs.pop("andrew_id_expiration")
        if "andrew_id_sponsor" in kwargs:
            kwargs["org_email_sponsor"] = kwargs.pop("andrew_id_sponsor")
        if "andrew_id_sponsor_id" in kwargs:
            kwargs["org_email_sponsor_id"] = kwargs.pop("andrew_id_sponsor_id")
        super().__init__(*args, **kwargs)

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
        if (
            rel is None
            or self.organization_id is None
            or rel.student_id != self.pk
            or rel.organization_id != self.organization_id
        ):
            return None
        adult = rel.adult
        return adult if adult.organization_id == self.organization_id else None

    @primary_contact.setter
    def primary_contact(self, adult):
        self._set_contact("primary", adult)

    @property
    def primary_contact_id(self):
        adult = self.primary_contact
        return adult.pk if adult is not None else None

    @primary_contact_id.setter
    def primary_contact_id(self, value):
        from .adults import Adult

        if value in (None, ""):
            self.primary_contact = None
        else:
            self.primary_contact = Adult.objects.filter(pk=value).first()

    @property
    def secondary_contact(self):
        """Backwards-compatible accessor: the secondary-contact Adult."""
        rel = self.secondary_contact_relationship
        if (
            rel is None
            or self.organization_id is None
            or rel.student_id != self.pk
            or rel.organization_id != self.organization_id
        ):
            return None
        adult = rel.adult
        return adult if adult.organization_id == self.organization_id else None

    @secondary_contact.setter
    def secondary_contact(self, adult):
        self._set_contact("secondary", adult)

    @property
    def secondary_contact_id(self):
        adult = self.secondary_contact
        return adult.pk if adult is not None else None

    @secondary_contact_id.setter
    def secondary_contact_id(self, value):
        from .adults import Adult

        if value in (None, ""):
            self.secondary_contact = None
        else:
            self.secondary_contact = Adult.objects.filter(pk=value).first()

    def _set_contact(self, slot, adult):
        """Assign a primary/secondary contact, creating the through row."""
        from django.core.exceptions import ValidationError

        from .adults import Adult, AdultStudentRelationship

        if adult is None:
            setattr(self, f"{slot}_contact_relationship", None)
            self.__dict__.pop(f"_pending_{slot}_contact", None)
            return
        if (
            self.organization_id is not None
            and adult.organization_id != self.organization_id
        ):
            raise ValidationError(
                "A student contact must belong to the same organization."
            )
        if self.pk is None:
            # Unsaved Student: defer creating the through row until save() so
            # there's a student PK to reference.
            self.__dict__[f"_pending_{slot}_contact"] = adult
            setattr(self, f"{slot}_contact_relationship", None)
            return
        rel, _ = AdultStudentRelationship.objects.for_organization(
            self.organization_id
        ).get_or_create(
            organization_id=self.organization_id,
            adult=adult,
            student=self,
        )
        setattr(self, f"{slot}_contact_relationship", rel)
        self.__dict__.pop(f"_pending_{slot}_contact", None)
        if not adult.is_parent:
            Adult.objects.filter(
                pk=adult.pk, organization_id=self.organization_id
            ).update(is_parent=True)

    def _same_organization_relationships(self):
        from .adults import AdultStudentRelationship

        if self.pk is None or self.organization_id is None:
            return AdultStudentRelationship.objects.none()
        prefetched_relationships = getattr(self, "tenant_contact_relationships", None)
        if prefetched_relationships is not None:
            return prefetched_relationships
        return (
            AdultStudentRelationship.objects.for_organization(self.organization)
            .filter(
                student=self,
                adult__organization=self.organization,
                student__organization=self.organization,
            )
            .select_related("adult", "student")
        )

    def _resolve_deferred_contacts(self):
        """Create through rows for contacts assigned before the first save."""
        from django.core.exceptions import ValidationError

        from .adults import Adult, AdultStudentRelationship

        changed = False
        for slot in ("primary", "secondary"):
            pending = self.__dict__.pop(f"_pending_{slot}_contact", None)
            if pending is None:
                continue
            if (
                self.organization_id is None
                or pending.organization_id != self.organization_id
            ):
                raise ValidationError(
                    "A student contact must belong to the same organization."
                )
            rel, _ = AdultStudentRelationship.objects.for_organization(
                self.organization_id
            ).get_or_create(
                organization_id=self.organization_id,
                adult=pending,
                student=self,
            )
            setattr(self, f"{slot}_contact_relationship", rel)
            if not pending.is_parent:
                Adult.objects.filter(
                    pk=pending.pk, organization_id=self.organization_id
                ).update(is_parent=True)
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
        from .adults import Adult

        relationships = self._same_organization_relationships()
        if self.organization_id is None:
            return Adult.objects.none()
        return Adult.objects.for_organization(self.organization).filter(
            pk__in=relationships.values("adult_id")
        )

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
        relationships = list(self._same_organization_relationships())
        rels = {
            r.adult_id: (r.relationship_to_student, r.specific_relationship or "")
            for r in relationships
        }
        relationships_by_pk = {r.pk: r for r in relationships}

        def add_adult(adult):
            if adult and adult.id not in seen_ids:
                rel_data = rels.get(adult.id, ("parent", ""))
                adult.attached_rel = rel_data[0]
                adult.specific_rel = rel_data[1]
                result.append(adult)
                seen_ids.add(adult.id)

        for relation_id in (
            self.primary_contact_relationship_id,
            self.secondary_contact_relationship_id,
        ):
            relationship = relationships_by_pk.get(relation_id)
            if relationship is not None:
                add_adult(relationship.adult)

        for relationship in relationships:
            add_adult(relationship.adult)

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
        ordering = ["preferred_first_name", "last_name"]
        indexes = [
            models.Index(
                fields=["last_name", "preferred_first_name"],
                name="student_name_idx",
            ),
            models.Index(
                fields=["school", "graduation_year"], name="student_school_grad_idx"
            ),
            models.Index(fields=["graduated"], name="student_graduated_idx"),
        ]

    @property
    def display_name(self):
        first = self.preferred_first_name or self.legal_first_name
        return f"{first} {self.last_name}".strip()

    @property
    def full_name(self):
        return self.display_name

    def __str__(self):
        return self.display_name or f"Student #{self.pk}"

    def _prune_dangling_contacts(self):
        """Clear relationship pointers whose Adult is missing.

        A Student may carry a primary/secondary relationship pointer whose
        Adult has since been deleted (AdultStudentRelationship cascades with
        the Adult, which would clear the pointer via SET NULL, but keep this
        as a safety net for legacy/orphaned data).
        """
        from .adults import AdultStudentRelationship

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

        from .adults import Adult

        # Students shouldn't have duplicate emails with each other or with parents.
        if self.personal_email:
            email = self.personal_email.strip().lower()
            # Check other students
            others = Student.objects.filter(
                Q(personal_email__iexact=email) | Q(org_email__iexact=email)
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
                Q(personal_email__iexact=email) | Q(org_email__iexact=email)
            ).exists():
                raise ValidationError(
                    {
                        "personal_email": "This email is already in use by a parent or mentor."
                    }
                )

        if self.org_email:
            email = self.org_email.strip().lower()
            # Check other students
            others = Student.objects.filter(
                Q(personal_email__iexact=email) | Q(org_email__iexact=email)
            )
            if self.pk:
                others = others.exclude(pk=self.pk)
            if others.exists():
                raise ValidationError(
                    {"org_email": "This email is already in use by another student."}
                )

            # Check parents/mentors
            if Adult.objects.filter(
                Q(personal_email__iexact=email) | Q(org_email__iexact=email)
            ).exists():
                raise ValidationError(
                    {"org_email": "This email is already in use by a parent or mentor."}
                )

    def save(self, *args, **kwargs):
        # Normalize any new photo upload to RGB JPEG in-memory (fixes EXIF orientation, handles HEIC).
        from programs.utils import normalize_image_field

        if "update_fields" in kwargs and kwargs["update_fields"] is not None:
            field_map = {
                "andrew_id": "org_account_id",
                "andrew_email": "org_email",
                "andrew_id_expiration": "org_email_expiration",
                "andrew_id_sponsor": "org_email_sponsor",
                "andrew_id_sponsor_id": "org_email_sponsor_id",
            }
            kwargs["update_fields"] = [
                field_map.get(f, f) for f in kwargs["update_fields"]
            ]

        normalize_image_field(getattr(self, "photo", None), log_prefix="Student photo")
        self._prune_dangling_contacts()
        super().save(*args, **kwargs)
        # Create through rows for any primary/secondary contacts assigned
        # before the Student had a PK, then persist the pointers.
        self._resolve_deferred_contacts()

        # Sync Student name + login-active state to the linked User account.
        sync_linked_user(self, login_enabled=not self.graduated)

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
    def email_delivery_issues(self):
        from .communication import EmailDeliveryEvent

        if not self.pk or not self.organization_id:
            return EmailDeliveryEvent.objects.none()
        if (
            hasattr(self, "_prefetched_objects_cache")
            and "email_delivery_events" in self._prefetched_objects_cache
        ):
            return [
                e
                for e in self.email_delivery_events.all()
                if e.is_verified
                and e.has_consistent_organization_links(self.organization_id)
                and not e.resolved
                and e.event_type
                in (
                    EmailDeliveryEvent.EVENT_BOUNCED,
                    EmailDeliveryEvent.EVENT_COMPLAINT,
                )
            ]
        return EmailDeliveryEvent.objects.verified_for_organization(
            self.organization_id
        ).filter(
            student_id=self.pk,
            resolved=False,
            event_type__in=[
                EmailDeliveryEvent.EVENT_BOUNCED,
                EmailDeliveryEvent.EVENT_COMPLAINT,
            ],
        )

    @property
    def has_email_delivery_issue(self) -> bool:
        from .communication import EmailDeliveryEvent

        issues = self.email_delivery_issues
        return issues.exists() if hasattr(issues, "exists") else bool(issues)

    @property
    def grade_display(self):
        """Return a human-readable grade label: 'K', '1st Grade', …, '12th Grade',
        'Graduated', or ``None`` if ``graduation_year`` is not set.
        """
        if not self.graduation_year:
            return None
        from programs.utils import format_grade

        return format_grade(self.current_grade)

    @property
    def member_since_year(self) -> int:
        """Return the inception year when the student joined/participated.

        Derived from the earliest program start date, earliest enrollment
        creation date, or student record creation date.
        """
        years: list[int] = []
        if self.created_at:
            years.append(self.created_at.year)

        if (
            hasattr(self, "_prefetched_objects_cache")
            and "enrollment_set" in self._prefetched_objects_cache
        ):
            for enr in self.enrollment_set.all():
                if enr.program and enr.program.start_date:
                    years.append(enr.program.start_date.year)
                elif enr.created_at:
                    years.append(enr.created_at.year)
        elif self.pk:
            min_prog_date = (
                self.programs.filter(start_date__isnull=False)
                .aggregate(min_date=models.Min("start_date"))
                .get("min_date")
            )
            if min_prog_date:
                years.append(min_prog_date.year)

            min_enr_created = self.enrollment_set.aggregate(
                min_created=models.Min("created_at")
            ).get("min_created")
            if min_enr_created:
                years.append(min_enr_created.year)

        if years:
            return min(years)
        return timezone.now().year

    def requires_background_check(self, active_types=None) -> bool:
        """Whether the student must hold background clearances based on active check types."""
        from .adults import get_active_background_check_types

        if active_types is None:
            active_types = [
                t
                for t in get_active_background_check_types(
                    getattr(self, "organization", None)
                )
                if t.applies_to_students
            ]
        return any(t.applies_to_student(self) for t in active_types)

    def needs_background_check(self, active_types=None) -> bool:
        """Whether the student requires clearances AND is missing at least one.

        The requirement is derived from active check types; only the
        clearance records themselves are stored state. Returns False if the
        student does not require clearances.
        """
        from .adults import get_active_background_check_types

        if active_types is None:
            active_types = [
                t
                for t in get_active_background_check_types(
                    getattr(self, "organization", None)
                )
                if t.applies_to_students
            ]
        if not self.requires_background_check(active_types=active_types):
            return False
        required_types = [t for t in active_types if t.applies_to_student(self)]
        if not required_types:
            return False
        valid_type_ids = {
            bc.check_type_id for bc in self.background_checks.all() if bc.is_valid
        }
        required_type_ids = {t.id for t in required_types}
        return not required_type_ids.issubset(valid_type_ids)


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
        "ProgramDocument",
        on_delete=models.CASCADE,
        related_name="student_submissions",
    )
    file = EncryptedFileField(
        upload_to=_student_document_upload_to,
        max_length=255,
    )
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("student", "program_document")
        ordering = ["-uploaded_at"]

    def __str__(self):
        return f"{self.program_document.name} for {self.student}"
