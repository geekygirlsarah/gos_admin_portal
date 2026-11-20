from decimal import Decimal
from uuid import uuid4

from django.conf import settings
from django.core import signing
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Value
from django.db.models.functions import Coalesce, Lower, NullIf

from organizations.models import OrganizationManager, OrganizationQuerySet
from programs.models import Adult, Student
from programs.models.fields import EncryptedFileField


def _permission_form_upload_to(instance, filename):
    from programs.utils.files import build_upload_path

    return build_upload_path(
        "travel/permission_forms",
        instance=instance,
        filename=filename,
        id_attr="program_id",
        default_id="unassigned",
    )


class TravelEvent(models.Model):
    """A single trip a program is considering.

    The trip's overall window is ``start_date``/``end_date``; *when people
    might actually leave* is expressed by zero or more :class:`TravelDeparture`
    rows. ``is_past`` is always derived from ``end_date``, never stored.
    """

    organization = models.ForeignKey(
        "organizations.Organization",
        on_delete=models.PROTECT,
        related_name="travel_events",
        help_text="Organization that owns this travel event (multi-tenant).",
    )
    objects = OrganizationManager.from_queryset(OrganizationQuerySet)()

    program = models.ForeignKey(
        "programs.Program",
        on_delete=models.CASCADE,
        related_name="travel_events",
        null=True,
    )
    name = models.CharField(max_length=255, verbose_name="Trip / event name")
    start_date = models.DateField()
    end_date = models.DateField()
    location_name = models.CharField(max_length=255, blank=True)
    location_address = models.CharField(max_length=255, blank=True)
    requires_departure = models.BooleanField(
        default=False,
        help_text="Require students to select one of this trip's departures.",
    )
    description = models.TextField(
        blank=True,
        verbose_name="Plans and details",
        help_text="Itinerary, packing list, chaperone plans, or anything else "
        "students/parents should know.",
    )
    permission_form = models.FileField(
        upload_to=_permission_form_upload_to,
        blank=True,
        help_text="Optional PDF/permission slip. Parents must acknowledge it "
        "online before they can approve a signup when one is attached.",
    )
    permission_form_version = models.UUIDField(default=uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["start_date", "name"]

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        update_fields = kwargs.get("update_fields")
        if self.pk and (update_fields is None or "permission_form" in update_fields):
            previous = (
                type(self)
                .objects.filter(pk=self.pk)
                .values_list("permission_form", flat=True)
                .first()
            )
            current = self.permission_form
            if previous != (current.name or "") or (current and not current._committed):
                self.permission_form_version = uuid4()
                if update_fields is not None:
                    kwargs["update_fields"] = set(update_fields) | {
                        "permission_form_version"
                    }
        return super().save(*args, **kwargs)

    def clean(self):
        super().clean()
        if self.start_date and self.end_date and self.end_date < self.start_date:
            raise ValidationError("End date must be on or after the start date.")

    @property
    def ordered_departures(self):
        """Departures sorted chronologically (``TravelDeparture.Meta.ordering``),
        safe to call on prefetched rows."""
        return list(self.departures.all())

    @property
    def ordered_cost_options(self):
        return list(self.cost_options.all())

    @property
    def has_multiple_departures(self):
        return self.departures.count() > 1

    @property
    def is_past(self):
        from django.utils import timezone

        return bool(self.end_date and self.end_date < timezone.localdate())

    @property
    def requires_permission_form(self):
        return bool(self.permission_form)


class TravelDeparture(models.Model):
    """One "when we're leaving" group for a trip (e.g. 'Bus 1 5:00 AM',
    'Saturday flight'). A trip may have one or more of these."""

    organization = models.ForeignKey(
        "organizations.Organization",
        on_delete=models.PROTECT,
        related_name="travel_departures",
        help_text="Organization that owns this travel departure (multi-tenant).",
    )
    objects = OrganizationManager.from_queryset(OrganizationQuerySet)()

    event = models.ForeignKey(
        TravelEvent, on_delete=models.CASCADE, related_name="departures"
    )
    label = models.CharField(
        max_length=255,
        blank=True,
        help_text="e.g. 'Bus 1', '5:00 AM flight', 'leave together after practice'",
    )
    leave_date = models.DateField()
    leave_time = models.TimeField(blank=True, null=True)
    meeting_location = models.CharField(max_length=255, blank=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["leave_date", "leave_time", "id"]

    def __str__(self):
        label = self.label or (
            f"{self.leave_date} {self.leave_time}"
            if self.leave_time
            else f"{self.leave_date}"
        )
        return f"{self.event.name}: {label}"

    @property
    def display_label(self):
        parts = []
        if self.label:
            parts.append(self.label)
        when = f"{self.leave_date}"
        if self.leave_time:
            when += f" {self.leave_time:%H:%M}"
        parts.append(when)
        return " - ".join(parts)


class TravelCostOption(models.Model):
    """A check-list item that makes up a trip's cost (hotel, van, flight,
    any added fee). Lead mentors set the price; students check the options
    that apply to them and the signup total is their sum."""

    organization = models.ForeignKey(
        "organizations.Organization",
        on_delete=models.PROTECT,
        related_name="travel_cost_options",
        help_text="Organization that owns this travel cost option (multi-tenant).",
    )
    objects = OrganizationManager.from_queryset(OrganizationQuerySet)()

    event = models.ForeignKey(
        TravelEvent, on_delete=models.CASCADE, related_name="cost_options"
    )
    name = models.CharField(
        max_length=255,
        help_text="e.g. 'Hotel (2 nights)', 'Van ride', 'Flight'",
    )
    amount = models.DecimalField(max_digits=8, decimal_places=2)
    is_required = models.BooleanField(
        default=True,
        verbose_name="Required fee",
        help_text="Include this cost for every student traveling on this trip.",
    )
    unit_label = models.CharField(
        max_length=50,
        default="per person",
        help_text="e.g. 'per person', 'total', 'per night' (informational)",
    )
    notes = models.TextField(blank=True)
    sort_order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["sort_order", "id"]

    def __str__(self):
        return f"{self.name} (${self.amount})"


class TravelSignup(models.Model):
    """A student's interest in a trip, plus the parent-approval lifecycle.

    Status flow:
      * ``interested`` -- student signed up (or a lead mentor added them after
        a conversation); parents are emailed and must approve/decline.
      * ``approved`` -- a parent approved, recorded the approved cost (a
        snapshot so later price edits show the delta) and (when the trip has an
        attached permission form) acknowledged it online.
      * ``declined`` -- a parent declined.
      * ``withdrawn`` -- the student (or a lead mentor on their behalf) backed
        out. Statuses are kept as history; nothing is hard-deleted.
    """

    organization = models.ForeignKey(
        "organizations.Organization",
        on_delete=models.PROTECT,
        related_name="travel_signups",
        help_text="Organization that owns this travel signup (multi-tenant).",
    )
    objects = OrganizationManager.from_queryset(OrganizationQuerySet)()

    INTERESTED = "interested"
    APPROVED = "approved"
    DECLINED = "declined"
    WITHDRAWN = "withdrawn"

    STATUS_CHOICES = [
        (INTERESTED, "Interested (awaiting parent approval)"),
        (APPROVED, "Approved"),
        (DECLINED, "Declined by parent"),
        (WITHDRAWN, "Withdrawn"),
    ]

    student = models.ForeignKey(
        Student, on_delete=models.CASCADE, related_name="travel_signups"
    )
    event = models.ForeignKey(
        TravelEvent, on_delete=models.CASCADE, related_name="signups"
    )
    departure = models.ForeignKey(
        TravelDeparture,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="signups",
    )
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=INTERESTED)
    cost_items = models.ManyToManyField(
        TravelCostOption,
        blank=True,
        related_name="signups",
        help_text="The cost check-list items that apply to this student.",
    )
    approved_total = models.DecimalField(
        max_digits=8,
        decimal_places=2,
        null=True,
        blank=True,
        editable=False,
        help_text="Snapshot of the cost the parent approved.",
    )
    balance_amount = models.DecimalField(
        max_digits=8,
        decimal_places=2,
        null=True,
        blank=True,
        editable=False,
        help_text="Current trip charge included in the student's program balance.",
    )
    balance_effective_date = models.DateField(
        null=True,
        blank=True,
        editable=False,
        help_text="Date the current travel charge was posted to the program balance.",
    )
    approved_by = models.ForeignKey(
        Adult,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="approved_travel_signups",
        verbose_name="Approving parent",
    )
    approved_at = models.DateTimeField(null=True, blank=True)
    permission_acknowledged = models.BooleanField(default=False)
    permission_acknowledged_version = models.UUIDField(null=True, blank=True)
    permission_acknowledged_by = models.CharField(
        max_length=255, blank=True, verbose_name="Permission form acknowledged by"
    )
    permission_acknowledged_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ("student", "event")
        ordering = [
            Lower(
                Coalesce(
                    NullIf("student__preferred_first_name", Value("")),
                    "student__legal_first_name",
                )
            ),
            Lower("student__last_name"),
            "student__pk",
        ]

    def __str__(self):
        return f"{self.student} - {self.event.name} ({self.get_status_display()})"

    @property
    def total_cost(self):
        """Sum required costs and selected optional costs at current prices."""
        selected_ids = {
            option.pk
            for option in self.cost_items.all()
            if option.event_id == self.event_id
            and option.organization_id == self.organization_id
        }
        return sum(
            (
                option.amount
                for option in self.event.cost_options.all()
                if option.organization_id == self.organization_id
                and (option.is_required or option.pk in selected_ids)
            ),
            Decimal("0"),
        )

    @property
    def approval_costs(self):
        selected_ids = {
            option.pk
            for option in self.cost_items.all()
            if option.event_id == self.event_id
            and option.organization_id == self.organization_id
        }
        return [
            option
            for option in self.event.cost_options.all()
            if option.organization_id == self.organization_id
            and (option.is_required or option.pk in selected_ids)
        ]

    @property
    def approval_snapshot(self):
        event = self.event
        departure = self.departure
        costs = sorted(
            (
                option.pk,
                option.name,
                str(option.amount),
                option.is_required,
                option.unit_label,
                option.notes,
            )
            for option in event.cost_options.all()
            if option.organization_id == self.organization_id
        )
        selected = sorted(
            option.pk
            for option in self.cost_items.all()
            if option.organization_id == self.organization_id
            and option.event_id == self.event_id
        )
        return {
            "signup": self.pk,
            "event": event.pk,
            "name": event.name,
            "start": str(event.start_date),
            "end": str(event.end_date),
            "location": [event.location_name, event.location_address],
            "description": event.description,
            "form_version": str(event.permission_form_version),
            "form_present": bool(event.permission_form),
            "requires_departure": event.requires_departure,
            "departure": (
                [
                    departure.pk,
                    departure.label,
                    str(departure.leave_date),
                    str(departure.leave_time) if departure.leave_time else None,
                    departure.meeting_location,
                    departure.notes,
                ]
                if departure
                else None
            ),
            "costs": [list(cost) for cost in costs],
            "selected": selected,
        }

    @property
    def approval_token(self):
        return signing.dumps(
            self.approval_snapshot, salt="travel.approval", compress=True
        )

    @property
    def can_be_approved(self):
        return self.status == self.INTERESTED

    @property
    def can_be_declined(self):
        return self.status == self.INTERESTED

    @property
    def can_be_withdrawn(self):
        return self.status in (self.INTERESTED, self.APPROVED)

    @property
    def can_be_undone(self):
        return self.status in (self.APPROVED, self.DECLINED)

    @property
    def can_be_signed_up_again(self):
        return self.status == self.WITHDRAWN

    @property
    def current_signed_permission_form(self):
        if hasattr(self, "current_signed_permission_forms"):
            return next(iter(self.current_signed_permission_forms), None)
        return (
            self.signed_permission_forms.filter(
                organization_id=self.organization_id,
                form_version=self.event.permission_form_version,
            )
            .exclude(file="")
            .order_by("-uploaded_at", "-pk")
            .first()
        )

    @property
    def needs_permission_acknowledgment(self):
        return self.event.requires_permission_form and not (
            self.permission_acknowledged
            and self.permission_acknowledged_version
            == self.event.permission_form_version
            and self.current_signed_permission_form
        )

    def approve(self, adult, permission_acknowledged=False, acknowledged_by=""):
        event_requires_form = self.event.requires_permission_form
        if not self.can_be_approved:
            raise ValidationError(
                "This signup cannot be approved in its current state."
            )
        if self.needs_permission_acknowledgment and not permission_acknowledged:
            raise ValidationError(
                "This trip has a permission form that must be acknowledged before "
                "you can approve."
            )
        from django.utils import timezone

        self.status = self.APPROVED
        self.approved_total = self.total_cost
        self.balance_amount = self.approved_total
        self.balance_effective_date = timezone.localdate()
        # Keep a parental snapshot: ensure approved_by is never empty. Approval
        # is parent-only in views, so adult is the approving parent.
        self.approved_by = adult
        self.approved_at = timezone.now()
        if event_requires_form and permission_acknowledged:
            self.permission_acknowledged = True
            self.permission_acknowledged_version = self.event.permission_form_version
            self.permission_acknowledged_by = acknowledged_by or (
                adult.display_name if adult else ""
            )
            self.permission_acknowledged_at = timezone.now()
        elif not event_requires_form:
            self.permission_acknowledged = False
            self.permission_acknowledged_version = None
            self.permission_acknowledged_by = ""
            self.permission_acknowledged_at = None

    def decline(self):
        if not self.can_be_declined:
            raise ValidationError(
                "This signup cannot be declined in its current state."
            )
        self.status = self.DECLINED
        self.approved_by = None
        self.approved_at = None
        self.approved_total = None
        self.balance_amount = None
        self.balance_effective_date = None

    def undo_decision(self):
        """Return an approved/declined signup to 'interested' so the family can
        reconsider (e.g. the parent backed out of an approval)."""
        if not self.can_be_undone:
            raise ValidationError("This decision cannot be undone.")
        self.status = self.INTERESTED
        self.approved_by = None
        self.approved_at = None
        self.approved_total = None
        self.balance_amount = None
        self.balance_effective_date = None
        self.permission_acknowledged = False
        self.permission_acknowledged_version = None
        self.permission_acknowledged_by = ""
        self.permission_acknowledged_at = None

    def withdraw(self):
        if not self.can_be_withdrawn:
            raise ValidationError(
                "This signup cannot be withdrawn in its current state."
            )
        self.status = self.WITHDRAWN
        self.approved_by = None
        self.approved_at = None
        self.approved_total = None
        self.balance_amount = None
        self.balance_effective_date = None

    def rejoin(self):
        if not self.can_be_signed_up_again:
            raise ValidationError("This signup cannot be reactivated.")
        self.status = self.INTERESTED

    def clean(self):
        super().clean()
        if self.departure and self.departure.event_id != self.event_id:
            raise ValidationError("The departure must belong to this trip.")


def _signed_permission_upload_to(instance, filename):
    from pathlib import Path

    extension = Path(filename).suffix.lower()
    return f"private/travel/signed_permissions/{instance.organization_id}/{uuid4().hex}{extension}"


class TravelSignedPermissionForm(models.Model):
    organization = models.ForeignKey(
        "organizations.Organization", on_delete=models.PROTECT
    )
    objects = OrganizationManager.from_queryset(OrganizationQuerySet)()
    signup = models.ForeignKey(
        TravelSignup, on_delete=models.CASCADE, related_name="signed_permission_forms"
    )
    form_version = models.UUIDField()
    file = EncryptedFileField(upload_to=_signed_permission_upload_to, max_length=255)
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True
    )
    uploaded_at = models.DateTimeField(auto_now_add=True)


class TravelSignedFileDeletion(models.Model):
    organization = models.ForeignKey(
        "organizations.Organization", on_delete=models.PROTECT
    )
    objects = OrganizationManager.from_queryset(OrganizationQuerySet)()
    file_name = models.CharField(max_length=255, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)


class TravelReapprovalSnapshot(models.Model):
    organization = models.ForeignKey(
        "organizations.Organization", on_delete=models.PROTECT
    )
    objects = OrganizationManager.from_queryset(OrganizationQuerySet)()
    signup = models.OneToOneField(TravelSignup, on_delete=models.CASCADE)
    approved_by = models.ForeignKey(Adult, on_delete=models.SET_NULL, null=True)
    approved_at = models.DateTimeField(null=True)
    approved_total = models.DecimalField(max_digits=10, decimal_places=2, null=True)


class TravelReminderDelivery(models.Model):
    """Claim and outcome for one reminder trigger on a student signup."""

    class ReminderType(models.TextChoices):
        AFTER_SIGNUP = "after_signup", "Three days after signup"
        SEVEN_DAYS = "seven_days", "Seven days before travel"
        ONE_DAY = "one_day", "One day before travel"

    class Status(models.TextChoices):
        PENDING = "pending", "Pending review"
        ACCEPTED = "accepted", "Accepted"
        FAILED = "failed", "Confirmed pre-send failure"
        UNKNOWN = "unknown", "Unknown delivery outcome"
        NO_RECIPIENTS = "no_recipients", "No eligible recipients"

    organization = models.ForeignKey(
        "organizations.Organization",
        on_delete=models.PROTECT,
        related_name="travel_reminder_deliveries",
    )
    objects = OrganizationManager.from_queryset(OrganizationQuerySet)()
    signup = models.ForeignKey(
        TravelSignup,
        on_delete=models.CASCADE,
        related_name="reminder_deliveries",
    )
    reminder_type = models.CharField(max_length=16, choices=ReminderType.choices)
    target_date = models.DateField()
    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.PENDING
    )
    claimed_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    recipient_count = models.PositiveSmallIntegerField(default=0)
    accepted_count = models.PositiveSmallIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("signup", "reminder_type", "target_date"),
                name="travel_reminder_date_uniq",
            )
        ]
        indexes = [
            models.Index(
                fields=("organization", "status", "claimed_at"),
                name="travel_reminder_org_status_idx",
            )
        ]

    def clean(self):
        super().clean()
        if not self.signup_id or not self.organization_id:
            return
        if (
            self.signup.organization_id != self.organization_id
            or self.signup.event.organization_id != self.organization_id
            or self.signup.student.organization_id != self.organization_id
        ):
            raise ValidationError(
                {
                    "organization": "The reminder, signup, event, and student must share an organization."
                }
            )

    def save(self, *args, **kwargs):
        self.clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"Travel reminder for signup {self.signup_id} ({self.reminder_type})"


class TravelMentorSignup(models.Model):
    """A mentor going on a trip. Any number may sign up; ``is_driver`` is a
    Lead-Mentor-checked flag so mentors who are driving can be identified."""

    organization = models.ForeignKey(
        "organizations.Organization",
        on_delete=models.PROTECT,
        related_name="travel_mentor_signups",
        help_text="Organization that owns this travel mentor signup (multi-tenant).",
    )
    objects = OrganizationManager.from_queryset(OrganizationQuerySet)()

    adult = models.ForeignKey(
        Adult,
        on_delete=models.CASCADE,
        related_name="travel_mentor_signups",
        verbose_name="Mentor",
    )
    event = models.ForeignKey(
        TravelEvent, on_delete=models.CASCADE, related_name="mentor_signups"
    )
    departure = models.ForeignKey(
        TravelDeparture,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="mentor_signups",
    )
    is_driver = models.BooleanField(
        default=False,
        help_text="Check for mentors who are driving. Set by Lead Mentors.",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("adult", "event")
        ordering = [
            Lower(
                Coalesce(
                    NullIf("adult__preferred_first_name", Value("")),
                    "adult__legal_first_name",
                )
            ),
            Lower("adult__last_name"),
            "adult__pk",
        ]

    def __str__(self):
        return f"{self.adult} - {self.event.name}"


class TravelParentChaperoneSignup(models.Model):
    """A parent (guardian) volunteering to chaperone a trip.

    Kept separate from the mentor signups so parents and mentors show up and
    are managed independently. To qualify, the parent must have at least one
    active student in the trip's program (enforced in the views); Lead Mentors
    can add an eligible parent directly rather than waiting for them to sign
    up on their own.
    """

    organization = models.ForeignKey(
        "organizations.Organization",
        on_delete=models.PROTECT,
        related_name="travel_parent_chaperone_signups",
        help_text="Organization that owns this travel parent chaperone signup (multi-tenant).",
    )
    objects = OrganizationManager.from_queryset(OrganizationQuerySet)()

    adult = models.ForeignKey(
        Adult,
        on_delete=models.CASCADE,
        related_name="travel_parent_chaperone_signups",
        verbose_name="Parent",
    )
    event = models.ForeignKey(
        TravelEvent, on_delete=models.CASCADE, related_name="parent_chaperones"
    )
    departure = models.ForeignKey(
        TravelDeparture,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="parent_chaperone_signups",
        help_text="Which departure this parent plans to travel with (optional).",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("adult", "event")
        ordering = [
            Lower(
                Coalesce(
                    NullIf("adult__preferred_first_name", Value("")),
                    "adult__legal_first_name",
                )
            ),
            Lower("adult__last_name"),
            "adult__pk",
        ]

    def __str__(self):
        return f"{self.adult} - {self.event.name}"

    @classmethod
    def eligible_adults(cls, program):
        """Adults who may chaperone trips of ``program``: parents with at least
        one active student in the program."""
        from programs.utils import active_students_in_program

        organization = program.organization
        if organization is None:
            return Adult.objects.none()

        from programs.models import AdultStudentRelationship

        eligible_adult_ids = (
            AdultStudentRelationship.objects.for_organization(organization)
            .filter(
                organization=organization,
                adult__organization=organization,
                student__organization=organization,
                student__in=active_students_in_program(
                    program, organization=organization
                ),
            )
            .values("adult_id")
        )
        return (
            Adult.objects.for_organization(organization)
            .filter(
                is_parent=True,
                pk__in=eligible_adult_ids,
            )
            .distinct()
            .order_by(
                Lower(
                    Coalesce(
                        NullIf("preferred_first_name", Value("")),
                        "legal_first_name",
                    )
                ),
                Lower("last_name"),
            )
        )

    def clean(self):
        super().clean()
        if self.departure and self.departure.event_id != self.event_id:
            raise ValidationError("The departure must belong to this trip.")
