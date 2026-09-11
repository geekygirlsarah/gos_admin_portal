import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone
from django.utils.text import slugify


class CalendarFeed(models.Model):
    VISIBILITY_ANONYMOUS = "anonymous"
    VISIBILITY_MEMBERS = "members"
    VISIBILITY_INVITE_ONLY = "invite_only"
    VISIBILITY_CHOICES = [
        (VISIBILITY_ANONYMOUS, "Public (no login required)"),
        (VISIBILITY_MEMBERS, "Members (logged-in users)"),
        (VISIBILITY_INVITE_ONLY, "Invite Only (ACL-controlled)"),
    ]

    SOURCE_MANUAL = "manual"
    SOURCE_OUTREACH = "outreach"
    SOURCE_TRAVEL = "travel"
    SOURCE_PROGRAM_HOURS = "program_hours"
    SOURCE_CUSTOM = "custom"
    SOURCE_CHOICES = [
        (SOURCE_MANUAL, "Manually managed"),
        (SOURCE_OUTREACH, "Outreach events (auto-synced)"),
        (SOURCE_TRAVEL, "Travel events (auto-synced)"),
        (SOURCE_PROGRAM_HOURS, "Program hours (auto-synced)"),
        (SOURCE_CUSTOM, "Custom source"),
    ]

    name = models.CharField(max_length=200)
    slug = models.SlugField(max_length=100, unique=True)
    description = models.TextField(blank=True)
    color = models.CharField(
        max_length=7,
        default="#0d6efd",
        help_text="CSS hex color for calendar display (e.g. #0d6efd)",
    )
    visibility = models.CharField(
        max_length=20, choices=VISIBILITY_CHOICES, default=VISIBILITY_MEMBERS
    )
    source_type = models.CharField(
        max_length=20, choices=SOURCE_CHOICES, default=SOURCE_MANUAL
    )
    program = models.ForeignKey(
        "programs.Program",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="calendar_feeds",
        help_text="Program scope. Leave blank for org-wide feeds.",
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.name)
            # Ensure uniqueness
            base_slug = self.slug
            counter = 1
            while (
                CalendarFeed.objects.filter(slug=self.slug).exclude(pk=self.pk).exists()
            ):
                self.slug = f"{base_slug}-{counter}"
                counter += 1
        super().save(*args, **kwargs)

    @property
    def is_public(self):
        return self.visibility == self.VISIBILITY_ANONYMOUS

    @property
    def is_source_managed(self):
        return self.source_type != self.SOURCE_MANUAL

    def user_can_read(self, user):
        """Check if a user can read this feed based on visibility and ACLs.

        Public feeds (``visibility=anonymous``) require no login. All other
        feeds require login and are gated per-role by :class:`CalendarFeedACL`
        — Lead Mentors always bypass the ACL. A role with no matching ACL row
        (or a row with ``can_read=False``) cannot see the feed.
        """
        from programs.permission_views import (
            get_user_role,
            user_is_mentor_or_lead,
        )

        if not user.is_authenticated:
            return self.is_public

        if user_is_mentor_or_lead(user):
            return True

        role = get_user_role(user)
        if role is None:
            return self.is_public

        return self.acls.filter(role=role, can_read=True).exists()

    def user_can_write(self, user):
        """Check if a user can create/edit events on this feed.

        Source-managed feeds (outreach/travel/etc.) are read-only everywhere —
        edits happen in the originating app. Lead Mentors always get write.
        Everyone else needs a matching ACL row with ``can_write=True``.
        """
        from programs.permission_views import (
            get_user_role,
            user_is_mentor_or_lead,
        )

        if not user.is_authenticated:
            return False

        if self.is_source_managed:
            return False

        if user_is_mentor_or_lead(user):
            return True

        role = get_user_role(user)
        if role is None:
            return False

        return self.acls.filter(role=role, can_write=True).exists()

    def get_events_for_date_range(self, start_date, end_date):
        """Return all active events in this feed for the given date range,
        including expanded recurring events and applying exceptions."""
        from .utils import expand_events_for_range

        return expand_events_for_range(self, start_date, end_date)


class CalendarFeedACL(models.Model):
    """Role-based access control for calendar feeds."""

    ROLE_CHOICES = [
        ("Mentor", "Mentor"),
        ("Parent", "Parent"),
        ("Student", "Student"),
        ("Alumni", "Alumni"),
    ]

    feed = models.ForeignKey(
        CalendarFeed, on_delete=models.CASCADE, related_name="acls"
    )
    role = models.CharField(max_length=20, choices=ROLE_CHOICES)
    can_read = models.BooleanField(default=True)
    can_write = models.BooleanField(default=False)

    class Meta:
        unique_together = ("feed", "role")
        verbose_name = "Calendar Feed ACL"
        verbose_name_plural = "Calendar Feed ACLs"

    def __str__(self):
        perms = []
        if self.can_read:
            perms.append("read")
        if self.can_write:
            perms.append("write")
        return f"{self.role} on {self.feed.name}: {', '.join(perms) or 'none'}"


class CalendarEvent(models.Model):
    """A single event on a calendar feed.

    For manually-managed feeds, these are created/edited via the UI.
    For source-managed feeds, these are synced from the source (e.g. outreach shifts).
    """

    feed = models.ForeignKey(
        CalendarFeed, on_delete=models.CASCADE, related_name="events"
    )
    title = models.CharField(max_length=300)
    description = models.TextField(blank=True)
    start_datetime = models.DateTimeField()
    end_datetime = models.DateTimeField()
    all_day = models.BooleanField(default=False)
    location = models.CharField(max_length=500, blank=True)

    # Recurrence: iCalendar RRULE and EXDATE strings
    rrule_ical = models.TextField(
        blank=True,
        help_text="iCalendar RRULE string for recurring events (e.g. FREQ=WEEKLY;BYDAY=TU). Leave blank for one-time events.",
    )
    exdate_ical = models.TextField(
        blank=True,
        help_text="Comma-separated list of excluded dates for recurring events (YYYY-MM-DD format).",
    )

    # Unique identifier for ICS feed
    uid = models.CharField(max_length=255, unique=True, editable=False)

    # Source tracking for auto-populated events
    source_id = models.CharField(
        max_length=255,
        blank=True,
        null=True,
        help_text="Primary key of the source record (e.g. OutreachShift pk) for auto-synced events.",
    )

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_calendar_events",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["start_datetime", "title"]

    def __str__(self):
        return f"{self.title} ({self.start_datetime:%Y-%m-%d})"

    def save(self, *args, **kwargs):
        if not self.uid:
            self.uid = str(uuid.uuid4())
        super().save(*args, **kwargs)

    @property
    def is_recurring(self):
        return bool(self.rrule_ical)

    @property
    def is_past(self):
        return self.end_datetime < timezone.now()

    @property
    def exdate_list(self):
        """Parse exdate_ical into a list of date objects."""
        if not self.exdate_ical:
            return []
        from datetime import date

        dates = []
        for part in self.exdate_ical.split(","):
            part = part.strip()
            if part:
                try:
                    dates.append(date.fromisoformat(part))
                except ValueError:
                    continue
        return dates

    def has_exception_on_date(self, target_date):
        """Check if this event has an exception (moved/deleted) on a specific date."""
        return self.exceptions.filter(original_date=target_date).exists()

    def get_exception_on_date(self, target_date):
        """Get the exception for a specific date, or None."""
        return self.exceptions.filter(original_date=target_date).first()

    def clean(self):
        if self.end_datetime <= self.start_datetime and not self.all_day:
            raise ValidationError("End time must be after start time.")


class EventException(models.Model):
    """Represents a one-off change to a recurring event.

    For example: moving a single meeting to a different time, or deleting
    one instance of a weekly series.
    """

    CHANGE_MOVED = "moved"
    CHANGE_DELETED = "deleted"
    CHANGE_CHANGED = "changed"
    CHANGE_TYPE_CHOICES = [
        (CHANGE_MOVED, "Moved to different date/time"),
        (CHANGE_DELETED, "Deleted (skip this instance)"),
        (CHANGE_CHANGED, "Details changed (title/location/etc)"),
    ]

    event = models.ForeignKey(
        CalendarEvent, on_delete=models.CASCADE, related_name="exceptions"
    )
    original_date = models.DateField(
        help_text="The original date of the recurring event instance being modified."
    )
    change_type = models.CharField(max_length=10, choices=CHANGE_TYPE_CHOICES)

    # For moved events: the new date/time
    new_start_datetime = models.DateTimeField(null=True, blank=True)
    new_end_datetime = models.DateTimeField(null=True, blank=True)

    # For changed events: override fields (blank = keep original)
    new_title = models.CharField(max_length=300, blank=True)
    new_location = models.CharField(max_length=500, blank=True)
    new_description = models.TextField(blank=True)

    note = models.CharField(
        max_length=500,
        blank=True,
        help_text="Internal note about why this exception was made.",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("event", "original_date")
        ordering = ["original_date"]

    def __str__(self):
        return f"{self.event.title} on {self.original_date}: {self.get_change_type_display()}"

    def clean(self):
        if self.change_type == self.CHANGE_MOVED:
            if not self.new_start_datetime or not self.new_end_datetime:
                raise ValidationError("Moved events must have new start and end times.")
            if self.new_end_datetime <= self.new_start_datetime:
                raise ValidationError("New end time must be after new start time.")
