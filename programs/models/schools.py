import re

from django.db import models

from programs.constants import STATE_CHOICES
from programs.validators import validate_zip_code


class SchoolDistrict(models.Model):
    name = models.CharField(max_length=150, unique=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class School(models.Model):
    name = models.CharField(max_length=150, unique=True)
    district = models.ForeignKey(
        SchoolDistrict,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="schools",
        verbose_name="School district",
    )
    street_address = models.CharField(max_length=255, blank=True, null=True)
    city = models.CharField(max_length=100, blank=True, null=True)
    state = models.CharField(
        max_length=50, choices=STATE_CHOICES, blank=True, null=True, default="PA"
    )
    zip_code = models.CharField(
        max_length=20, blank=True, null=True, validators=[validate_zip_code]
    )

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class RaceEthnicity(models.Model):
    """Canonical race/ethnicity options for Students (multi-select)."""

    key = models.SlugField(max_length=50, unique=True)
    name = models.CharField(max_length=100, unique=True)
    display_order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["display_order", "name"]
        verbose_name_plural = "Race/Ethnicity Options"

    def __str__(self):
        return self.name

    @classmethod
    def match_from_text(cls, text: str):
        """Best-effort mapping from a free-text race/ethnicity string to option queryset.
        Matches by keyword; supports comma/semicolon-separated lists.
        """
        if not text:
            return cls.objects.none()

        s = (text or "").lower()
        # Split on common separators to get tokens too
        parts = [p.strip() for p in re.split(r"[;,/\\]|\band\b", s) if p.strip()]
        hay = " " + s + " "
        keys = set()

        def has(substr):
            return substr in hay

        # American Indian or Alaska Native
        if (
            any(
                "american indian" in p or "alaska" in p or "native american" in p
                for p in parts
            )
            or has("american indian")
            or has("alaska")
            or has("native american")
        ):
            keys.add("american-indian-or-alaska-native")
        # Asian
        if has(" asian"):
            keys.add("asian")
        # Black or African-American
        if has("black") or has("african-american") or has("african american"):
            keys.add("black-or-african-american")
        # Hispanic or Latino
        if has("hispanic") or has("latino") or has("latina") or has("latinx"):
            keys.add("hispanic-or-latino")
        # Middle Eastern or North African
        if has("middle eastern") or has("north african") or has("mena"):
            keys.add("middle-eastern-or-north-african")
        # Native Hawaiian or Other Pacific Islander
        if has("hawaiian") or has("pacific islander"):
            keys.add("native-hawaiian-or-other-pacific-islander")
        # White
        if has(" white"):
            keys.add("white")
        # Other
        if has("other") or (not keys and s.strip()):
            # If text provided but no match, classify as other
            keys.add("other")
        return cls.objects.filter(key__in=keys)
