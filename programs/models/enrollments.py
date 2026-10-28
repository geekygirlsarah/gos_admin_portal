import pghistory
from django.db import models


@pghistory.track()
class Enrollment(models.Model):
    organization = models.ForeignKey(
        "organizations.Organization",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="enrollments",
        help_text="Organization that owns this enrollment (multi-tenant; null until backfilled).",
    )
    student = models.ForeignKey("Student", on_delete=models.CASCADE)
    program = models.ForeignKey("Program", on_delete=models.CASCADE)
    team = models.ForeignKey(
        "Team",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="enrollments",
    )
    crew = models.ForeignKey(
        "Crew",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="enrollments",
    )
    subteam = models.ForeignKey(
        "SubTeam",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="enrollments",
    )
    active = models.BooleanField(default=True)
    clearance_due = models.BooleanField(
        default=False,
        help_text="Set when the enrolled student requires PA background clearances "
        "and is missing at least one valid clearance.",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("student", "program")
        verbose_name = "Enrollment"
        verbose_name_plural = "Enrollments"

    def __str__(self):
        return f"{self.student} → {self.program}"
