from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import models

from programs.models import Adult, Student


def _permission_form_upload_to(instance, filename):
    return f"travel/permission_forms/{instance.program_id}/{filename}"


class TravelEvent(models.Model):
    """A single trip a program is considering.

    The trip's overall window is ``start_date``/``end_date``; *when people
    might actually leave* is expressed by zero or more :class:`TravelDeparture`
    rows. ``is_past`` is always derived from ``end_date``, never stored.
    """

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
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["start_date", "name"]

    def __str__(self):
        return self.name

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

    event = models.ForeignKey(
        TravelEvent, on_delete=models.CASCADE, related_name="cost_options"
    )
    name = models.CharField(
        max_length=255,
        help_text="e.g. 'Hotel (2 nights)', 'Van ride', 'Flight'",
    )
    amount = models.DecimalField(max_digits=8, decimal_places=2)
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
    permission_acknowledged_by = models.CharField(
        max_length=255, blank=True, verbose_name="Permission form acknowledged by"
    )
    permission_acknowledged_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ("student", "event")
        ordering = ["created_at"]

    def __str__(self):
        return f"{self.student} - {self.event.name} ({self.get_status_display()})"

    @property
    def total_cost(self):
        """Sum of the selected cost check-list items (current prices)."""
        amounts = self.cost_items.values_list("amount", flat=True)
        return sum((Decimal(a) for a in amounts), Decimal("0"))

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

    def approve(self, adult, permission_acknowledged=False, acknowledged_by=""):
        event_requires_form = self.event.requires_permission_form
        if not self.can_be_approved:
            raise ValidationError(
                "This signup cannot be approved in its current state."
            )
        if event_requires_form and not permission_acknowledged:
            raise ValidationError(
                "This trip has a permission form that must be acknowledged before "
                "you can approve."
            )
        from django.utils import timezone

        self.status = self.APPROVED
        self.approved_total = self.total_cost
        # Keep a parental snapshot: ensure approved_by is never empty. Approval
        # is parent-only in views, so adult is the approving parent.
        self.approved_by = adult
        self.approved_at = timezone.now()
        if event_requires_form:
            self.permission_acknowledged = True
            self.permission_acknowledged_by = acknowledged_by or (
                adult.display_name if adult else ""
            )
            self.permission_acknowledged_at = timezone.now()
        else:
            self.permission_acknowledged = False
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

    def undo_decision(self):
        """Return an approved/declined signup to 'interested' so the family can
        reconsider (e.g. the parent backed out of an approval)."""
        if not self.can_be_undone:
            raise ValidationError("This decision cannot be undone.")
        self.status = self.INTERESTED
        self.approved_by = None
        self.approved_at = None
        self.approved_total = None
        self.permission_acknowledged = False
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

    def rejoin(self):
        if not self.can_be_signed_up_again:
            raise ValidationError("This signup cannot be reactivated.")
        self.status = self.INTERESTED

    def clean(self):
        super().clean()
        if self.departure and self.departure.event_id != self.event_id:
            raise ValidationError("The departure must belong to this trip.")


class TravelMentorSignup(models.Model):
    """A mentor going on a trip. Any number may sign up; ``is_driver`` is a
    Lead-Mentor-checked flag so mentors who are driving can be identified."""

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
        ordering = ["created_at"]

    def __str__(self):
        return f"{self.adult} - {self.event.name}"

    @classmethod
    def eligible_adults(cls, program):
        """Adults who may chaperone trips of ``program``: parents with at least
        one active student in the program."""
        from programs.utils import active_students_in_program

        return (
            Adult.objects.filter(
                is_parent=True,
                students__in=active_students_in_program(program),
            )
            .distinct()
            .order_by("last_name", "legal_first_name")
        )

    def clean(self):
        super().clean()
        if self.departure and self.departure.event_id != self.event_id:
            raise ValidationError("The departure must belong to this trip.")
