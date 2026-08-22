from django.core.exceptions import ValidationError
from django.db import models

from programs.models import Student


class OutreachEvent(models.Model):
    program = models.ForeignKey(
        "programs.Program",
        on_delete=models.CASCADE,
        related_name="outreach_events",
        null=True,
    )
    name = models.CharField(max_length=255)
    location_name = models.CharField(max_length=255)
    location_address = models.CharField(max_length=255)
    description = models.TextField(blank=True)

    def __str__(self):
        return self.name

    @property
    def champions(self):
        return OutreachSignup.objects.filter(
            shift__event=self, role=OutreachSignup.CHAMPION
        )

    @property
    def helpers(self):
        return OutreachSignup.objects.filter(
            shift__event=self, role=OutreachSignup.HELPER
        )

    @property
    def ordered_shifts(self):
        """Shifts for this event, sorted chronologically.

        Relies on ``OutreachShift.Meta.ordering`` and works with
        ``prefetch_related("shifts")`` to avoid extra queries.
        """
        return list(self.shifts.all())

    @property
    def first_shift(self):
        shifts = self.ordered_shifts
        return shifts[0] if shifts else None

    @property
    def last_shift(self):
        shifts = self.ordered_shifts
        return shifts[-1] if shifts else None

    @property
    def start_date(self):
        shift = self.first_shift
        return shift.date if shift else None

    @property
    def start_time(self):
        shift = self.first_shift
        return shift.start_time if shift else None

    @property
    def end_date(self):
        shift = self.last_shift
        return shift.date if shift else None

    @property
    def end_time(self):
        shift = self.last_shift
        return shift.end_time if shift else None

    @property
    def is_past(self):
        from datetime import datetime

        from django.utils import timezone

        shift = self.last_shift
        if not shift:
            return False

        end_dt = datetime.combine(shift.date, shift.end_time)
        now = timezone.now()

        if timezone.is_naive(end_dt):
            end_dt = timezone.make_aware(end_dt)

        return end_dt < now

    @property
    def duration_hours(self):
        return sum(shift.duration_hours for shift in self.ordered_shifts)


class OutreachShift(models.Model):
    event = models.ForeignKey(
        OutreachEvent, on_delete=models.CASCADE, related_name="shifts"
    )
    date = models.DateField()
    start_time = models.TimeField()
    end_time = models.TimeField()
    max_champions = models.PositiveIntegerField(
        default=1, verbose_name="Number of champions"
    )
    max_helpers = models.PositiveIntegerField(
        default=5, verbose_name="Number of signups"
    )

    class Meta:
        ordering = ["date", "start_time"]

    def __str__(self):
        return f"{self.event.name}: {self.date} {self.start_time} - {self.end_time}"

    @property
    def champions(self):
        return self.signups.filter(role=OutreachSignup.CHAMPION)

    @property
    def helpers(self):
        return self.signups.filter(role=OutreachSignup.HELPER)

    @property
    def is_past(self):
        from datetime import datetime

        from django.utils import timezone

        end_dt = datetime.combine(self.date, self.end_time)
        now = timezone.now()

        if timezone.is_naive(end_dt):
            end_dt = timezone.make_aware(end_dt)

        return end_dt < now

    @property
    def duration_hours(self):
        from datetime import datetime

        start = datetime.combine(self.date, self.start_time)
        end = datetime.combine(self.date, self.end_time)

        diff = end - start
        return max(0, diff.total_seconds() / 3600.0)

    def clean(self):
        super().clean()
        if self.date and self.start_time and self.end_time:
            if self.end_time <= self.start_time:
                raise ValidationError("End time must be after start time.")


class OutreachSignup(models.Model):
    CHAMPION = "champion"
    HELPER = "helper"
    ROLE_CHOICES = [
        (CHAMPION, "Champion"),
        (HELPER, "Helper"),
    ]

    student = models.ForeignKey(
        Student, on_delete=models.CASCADE, related_name="outreach_signups"
    )
    shift = models.ForeignKey(
        OutreachShift, on_delete=models.CASCADE, related_name="signups"
    )
    role = models.CharField(max_length=20, choices=ROLE_CHOICES)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("student", "shift")

    def __str__(self):
        return f"{self.student} - {self.shift.event} ({self.get_role_display()})"

    @property
    def event(self):
        return self.shift.event

    def clean(self):
        if self.role == self.CHAMPION:
            if (
                self.shift.champions.exclude(id=self.id).count()
                >= self.shift.max_champions
            ):
                raise ValidationError(
                    f"This shift already has the maximum number of champions ({self.shift.max_champions})."
                )
        elif self.role == self.HELPER:
            if self.shift.helpers.exclude(id=self.id).count() >= self.shift.max_helpers:
                raise ValidationError(
                    f"This shift already has the maximum number of helpers ({self.shift.max_helpers})."
                )
