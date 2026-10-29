from django.db import models
from django.utils import timezone

from organizations.models import OrganizationManager, OrganizationQuerySet


class ProgramFeature(models.Model):
    """Toggleable capability that can be enabled per Program.

    Keep keys stable. Suggested keys to start with:
      - 'discord' — show/collect Discord fields and related UI
      - 'background-checks' — show/collect background clearance fields and logic
      - 'cmu-andrew' — show/collect CMU Andrew ID related fields
      - 'badges' — show/collect student badges and badge awards
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


class ProgramQuerySet(OrganizationQuerySet):
    """Program queryset helpers for organization (multi-tenant) scoping."""

    pass


class Program(models.Model):
    organization = models.ForeignKey(
        "organizations.Organization",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="programs",
        help_text="Organization that owns this program (multi-tenant; null until backfilled).",
    )
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

    objects = ProgramQuerySet.as_manager()

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
    def has_feature_travel(self) -> bool:
        return self.has_feature("travel")

    @property
    def status(self) -> str:
        """Return 'Active', 'Upcoming', or 'Inactive' based on active flag and dates."""
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


def _program_document_upload_to(instance, filename):
    """Files land at MEDIA_ROOT/program_documents/<program_id>/<filename>."""
    from programs.utils.files import build_upload_path

    return build_upload_path(
        "program_documents",
        instance=instance,
        filename=filename,
        id_attr="program_id",
        default_id="unassigned",
    )


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
