from django.db import models

from organizations.models import OrganizationManager, OrganizationQuerySet
from programs.constants import TEAM_TYPES


class Team(models.Model):
    organization = models.ForeignKey(
        "organizations.Organization",
        on_delete=models.PROTECT,
        related_name="teams",
        help_text="Organization that owns this team (multi-tenant).",
    )
    objects = OrganizationManager.from_queryset(OrganizationQuerySet)()

    team_type = models.CharField(max_length=20, choices=TEAM_TYPES)
    number = models.IntegerField()
    name = models.CharField(max_length=100, blank=True, null=True)
    color = models.CharField(
        max_length=7, default="#0000ff", help_text="Hex color code (e.g. #0000ff)"
    )

    class Meta:
        unique_together = ("organization", "team_type", "number")
        ordering = ["team_type", "number"]

    def __str__(self):
        if self.name:
            return f"{self.team_type} {self.number} {self.name}"
        return f"{self.team_type} {self.number}"


class Crew(models.Model):
    organization = models.ForeignKey(
        "organizations.Organization",
        on_delete=models.PROTECT,
        related_name="crews",
        help_text="Organization that owns this crew (multi-tenant).",
    )
    objects = OrganizationManager.from_queryset(OrganizationQuerySet)()

    name = models.CharField(max_length=100)
    program = models.ForeignKey(
        "Program", on_delete=models.CASCADE, related_name="crews"
    )
    color = models.CharField(
        max_length=7, default="#0000ff", help_text="Hex color code (e.g. #0000ff)"
    )

    class Meta:
        unique_together = ("organization", "name", "program")
        ordering = ["name"]

    def __str__(self):
        return f"{self.name} ({self.program.name})"


class SubTeam(models.Model):
    organization = models.ForeignKey(
        "organizations.Organization",
        on_delete=models.PROTECT,
        related_name="subteams",
        help_text="Organization that owns this subteam (multi-tenant).",
    )
    objects = OrganizationManager.from_queryset(OrganizationQuerySet)()

    name = models.CharField(max_length=100)
    program = models.ForeignKey(
        "Program", on_delete=models.CASCADE, related_name="subteams"
    )
    color = models.CharField(
        max_length=7, default="#0000ff", help_text="Hex color code (e.g. #0000ff)"
    )

    class Meta:
        unique_together = ("organization", "name", "program")
        ordering = ["name"]

    def __str__(self):
        return f"{self.name} ({self.program.name})"
