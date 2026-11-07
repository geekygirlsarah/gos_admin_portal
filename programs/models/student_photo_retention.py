from django.db import models

from organizations.models import OrganizationManager, OrganizationQuerySet


class StudentPhotoRetentionInventory(models.Model):
    """Identity and retention start time for an unreferenced private photo."""

    organization = models.ForeignKey(
        "organizations.Organization",
        on_delete=models.PROTECT,
        related_name="student_photo_retention_entries",
        help_text="Organization that owns this student photo retention entry.",
    )
    objects = OrganizationManager.from_queryset(OrganizationQuerySet)()

    path = models.CharField(max_length=255, unique=True)
    size = models.PositiveBigIntegerField()
    sha256 = models.CharField(max_length=64)
    first_unreferenced_at = models.DateTimeField(db_index=True)

    class Meta:
        ordering = ["first_unreferenced_at", "path"]
        verbose_name = "Student photo retention inventory entry"
        verbose_name_plural = "Student photo retention inventory entries"
