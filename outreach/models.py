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
    start_date = models.DateField()
    start_time = models.TimeField()
    end_date = models.DateField(
        null=True, blank=True, help_text="Optional for multi-day events"
    )
    end_time = models.TimeField()
    description = models.TextField(blank=True)
    max_champions = models.PositiveIntegerField(
        default=1, verbose_name="Number of champions"
    )
    max_helpers = models.PositiveIntegerField(
        default=5, verbose_name="Number of signups"
    )

    class Meta:
        ordering = ["start_date", "start_time"]

    def __str__(self):
        return self.name

    @property
    def champions(self):
        return self.signups.filter(role=OutreachSignup.CHAMPION)

    @property
    def helpers(self):
        return self.signups.filter(role=OutreachSignup.HELPER)

    @property
    def is_past(self):
        from django.utils import timezone

        today = timezone.now().date()
        if self.end_date:
            return self.end_date < today
        return self.start_date < today


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
    event = models.ForeignKey(
        OutreachEvent, on_delete=models.CASCADE, related_name="signups"
    )
    role = models.CharField(max_length=20, choices=ROLE_CHOICES)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("student", "event")

    def __str__(self):
        return f"{self.student} - {self.event} ({self.get_role_display()})"

    def clean(self):
        if self.role == self.CHAMPION:
            if (
                self.event.champions.exclude(id=self.id).count()
                >= self.event.max_champions
            ):
                raise ValidationError(
                    f"This event already has the maximum number of champions ({self.event.max_champions})."
                )
        elif self.role == self.HELPER:
            if self.event.helpers.exclude(id=self.id).count() >= self.event.max_helpers:
                raise ValidationError(
                    f"This event already has the maximum number of helpers ({self.event.max_helpers})."
                )
