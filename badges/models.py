from django.conf import settings
from django.db import models


def badge_icon_upload_to(instance, filename):
    from programs.utils.files import sanitize_upload_filename

    filename = sanitize_upload_filename(filename)
    return f"badge_icons/{instance.id or 'new'}/{filename}"


class BadgeCategory(models.TextChoices):
    BASICS = "Basics", "Basics"
    DESIGN = "Design", "Design"
    SOFTWARE = "Software", "Software"
    MANUFACTURING = "Manufacturing", "Manufacturing"
    DATA_SCIENCE = "Data Science", "Data Science"
    ELECTRICAL = "Electrical", "Electrical"
    GENERAL = "General", "General"


class Badge(models.Model):
    name = models.CharField(max_length=200)
    icon = models.ImageField(upload_to=badge_icon_upload_to, blank=True, null=True)
    category = models.CharField(
        max_length=30, choices=BadgeCategory.choices, default=BadgeCategory.GENERAL
    )
    level = models.PositiveSmallIntegerField(default=1)
    description = models.TextField(blank=True)
    skills_required = models.TextField(blank=True, help_text="Visible to students")
    how_to_earn = models.TextField(blank=True, help_text="Only visible to mentors")
    prerequisites = models.ManyToManyField("self", symmetrical=False, blank=True)

    class Meta:
        unique_together = ("name", "level")
        ordering = ["category", "name", "level"]

    def __str__(self):
        return f"{self.name} Level {self.level}"


class StudentBadge(models.Model):
    student = models.ForeignKey(
        "programs.Student", on_delete=models.CASCADE, related_name="badges"
    )
    badge = models.ForeignKey(Badge, on_delete=models.CASCADE, related_name="awards")
    awarded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="awarded_badges",
    )
    awarded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("student", "badge")
        ordering = ["-awarded_at"]

    def __str__(self):
        return f"{self.student} - {self.badge}"
