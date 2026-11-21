import pghistory
from django.core.exceptions import ValidationError
from django.db import models

from organizations.models import OrganizationManager, OrganizationQuerySet


class EnrollmentManager(OrganizationManager):
    def get_queryset(self):
        return (
            super()
            .get_queryset()
            .filter(student__is_incomplete_application_profile=False)
        )


@pghistory.track()
class Enrollment(models.Model):
    organization = models.ForeignKey(
        "organizations.Organization",
        on_delete=models.PROTECT,
        related_name="enrollments",
        help_text="Organization that owns this enrollment (multi-tenant).",
    )
    objects = EnrollmentManager()
    all_objects = OrganizationManager.from_queryset(OrganizationQuerySet)()
    student = models.ForeignKey("Student", on_delete=models.CASCADE)
    program = models.ForeignKey("Program", on_delete=models.CASCADE)
    team = models.ForeignKey(
        "Team",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="enrollments",
    )
    groups = models.ManyToManyField("Group", blank=True, related_name="enrollments")
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
        default_manager_name = "objects"
        base_manager_name = "all_objects"
        unique_together = ("student", "program")
        verbose_name = "Enrollment"
        verbose_name_plural = "Enrollments"

    def __str__(self):
        return f"{self.student} → {self.program}"

    def save(self, *args, **kwargs):
        from .students import Student

        if (
            self.student_id
            and Student.all_objects.filter(
                pk=self.student_id,
                is_incomplete_application_profile=True,
            ).exists()
        ):
            raise ValidationError(
                "Incomplete application profiles cannot be enrolled in programs."
            )
        super().save(*args, **kwargs)
