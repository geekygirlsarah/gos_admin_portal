"""Models for guest forms."""

from __future__ import annotations

import enum

from django.db import models
from django.utils.text import slugify

from programs.constants import PHONE_TYPE_CHOICES
from programs.validators import validate_phone_number


class GuestFormType(enum.StrEnum):
    """Type of guest form."""

    STUDENT = "student"
    ADULT = "adult"


class EmergencyContactRelationship(models.TextChoices):
    PARENT_GUARDIAN = "parent_guardian", "Parent/Guardian"
    SPOUSE_PARTNER = "spouse_partner", "Spouse/Partner"
    OTHER = "other", "Other"


def _guest_form_upload_to(instance, filename):
    """Files land at MEDIA_ROOT/guest_forms/<slug>/<filename>."""
    from programs.utils.files import sanitize_upload_filename

    filename = sanitize_upload_filename(filename)
    return f"guest_forms/{instance.slug}/{filename}"


def _guest_submission_upload_to(instance, filename):
    """Files land at MEDIA_ROOT/guest_form_submissions/<guest_form_slug>/<filename>."""
    from programs.utils.files import sanitize_upload_filename

    filename = sanitize_upload_filename(filename)
    return f"guest_form_submissions/{instance.guest_form.slug}/{filename}"


class GuestForm(models.Model):
    """A guest permission form.

    Lead mentors can create and manage these forms. Each form has a type
    (student or adult) that determines what fields are shown to the guest.
    """

    class Meta:
        ordering = ["display_order", "name"]
        permissions = [
            (
                "review_guestform",
                "Can review guest form submissions",
            ),
        ]

    slug = models.SlugField(
        max_length=100,
        unique=True,
        blank=True,
        help_text="URL-friendly identifier for direct links (e.g. 'photo-release').",
    )
    form_type = models.CharField(
        max_length=10,
        choices=[(t.value, t.value.title()) for t in GuestFormType],
        help_text="Whether this form is for students or adults.",
    )
    name = models.CharField(
        max_length=200,
        help_text="Short name shown to guests (e.g. 'Photo Release Form').",
    )
    description = models.TextField(
        blank=True,
        default="",
        help_text="Optional longer explanation shown next to the download link.",
    )
    file = models.FileField(
        upload_to=_guest_form_upload_to,
        max_length=255,
        help_text="The blank PDF (or other file) for the guest to download, fill out, and re-upload.",
    )
    is_required = models.BooleanField(
        default=True,
        help_text="If checked, guests must upload a signed copy.",
    )
    display_order = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(
        default=True,
        help_text="Uncheck to hide this form from guests without deleting it.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    # URLs for required agreements
    legal_notices_url = models.URLField(
        default="https://www.cmu.edu/legal/",
        help_text="URL for CMU legal notices agreement.",
    )
    safety_guidelines_url = models.URLField(
        blank=True,
        default="https://docs.google.com/document/d/1xG734QEL2fZbzQ8PxABA5uzUwf5XEt52H3aUGPN6-1c/edit?tab=t.0",
        help_text="URL for Practice Field Safety and Use Guidelines.",
    )

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if not self.slug:
            base_slug = slugify(self.name)
            self.slug = base_slug
            # Ensure uniqueness
            counter = 1
            original_slug = base_slug
            while GuestForm.objects.filter(slug=self.slug).exclude(pk=self.pk).exists():
                self.slug = f"{original_slug}-{counter}"
                counter += 1
        super().save(*args, **kwargs)


class GuestFormSubmission(models.Model):
    """A submitted guest form with the guest's information and uploaded file."""

    guest_form = models.ForeignKey(
        GuestForm,
        on_delete=models.CASCADE,
        related_name="submissions",
    )

    # Participant information (used for both student and adult)
    participant_first_name = models.CharField(max_length=150, blank=True, default="")
    participant_last_name = models.CharField(max_length=150, blank=True, default="")
    email = models.EmailField(blank=True, default="")
    phone_number = models.CharField(
        max_length=30,
        validators=[validate_phone_number],
        help_text="Participant or parent/guardian phone number.",
        blank=True,
        default="",
    )
    phone_type = models.CharField(
        max_length=20,
        choices=PHONE_TYPE_CHOICES,
        default="cell",
    )

    # Program affiliation (team number)
    team_number = models.CharField(
        max_length=20,
        blank=True,
        help_text="Team number (e.g., FRC 3504).",
    )

    # Emergency contact
    emergency_contact_name = models.CharField(max_length=150, blank=True, default="")
    emergency_contact_phone = models.CharField(
        max_length=30,
        validators=[validate_phone_number],
        blank=True,
        default="",
    )
    emergency_contact_relationship = models.CharField(
        max_length=20,
        choices=EmergencyContactRelationship.choices,
        default=EmergencyContactRelationship.PARENT_GUARDIAN,
    )
    emergency_contact_other = models.CharField(
        max_length=100,
        blank=True,
        help_text="Specify if 'Other' relationship.",
    )

    # Agreement checkboxes
    agreed_legal_notices = models.BooleanField(
        default=False,
        help_text="I have read and reviewed the notices at CMU Legal.",
    )
    agreed_safety_guidelines = models.BooleanField(
        default=False,
        help_text="I have read and reviewed the Practice Field Safety and Use Guidelines.",
    )

    # Submission details
    file = models.FileField(
        upload_to=_guest_submission_upload_to,
        max_length=255,
        help_text="The signed form uploaded by the guest.",
    )
    submitted_at = models.DateTimeField(auto_now_add=True)
    submitted_ip = models.GenericIPAddressField(blank=True, null=True)

    class Meta:
        ordering = ["-submitted_at"]

    def __str__(self):
        name = f"{self.participant_first_name} {self.participant_last_name}".strip()
        return f"{name} - {self.guest_form.name}"

    @property
    def participant_name(self):
        """Return the participant's full name."""
        return f"{self.participant_first_name} {self.participant_last_name}".strip()

    @property
    def emergency_contact_relationship_display(self):
        """Return human-readable emergency contact relationship."""
        if self.emergency_contact_relationship == EmergencyContactRelationship.OTHER:
            return f"Other: {self.emergency_contact_other}"
        return EmergencyContactRelationship(self.emergency_contact_relationship).label
