from django.db import models


def _mentor_agreement_upload_to(instance, filename):
    from programs.utils.files import sanitize_upload_filename

    slug = instance.slug or "unassigned"
    filename = sanitize_upload_filename(filename)
    return f"mentor_agreements/{slug}/{filename}"


class MentorAgreement(models.Model):
    """Versioned document that mentors must accept.

    Each unique ``slug`` groups versions of the same document. Only one
    version per slug should be active at a time — ``save()`` automatically
    deactivates other active versions of the same slug. Content can be
    provided as markdown *or* a uploaded document (PDF, etc.), or both.
    """

    slug = models.SlugField(
        help_text="URL-friendly identifier that groups versions of the same document.",
    )
    version = models.PositiveIntegerField()
    title = models.CharField(max_length=200)
    content = models.TextField(
        blank=True,
        help_text="Markdown content of the agreement. Leave blank for document-only agreements.",
    )
    document = models.FileField(
        upload_to=_mentor_agreement_upload_to,
        blank=True,
        max_length=255,
        help_text="Uploaded document (PDF, etc.) for the agreement. Leave blank for markdown-only agreements.",
    )
    effective_date = models.DateField()
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ("slug", "version")
        ordering = ["-version"]
        verbose_name = "Mentor Agreement"
        verbose_name_plural = "Mentor Agreements"

    def __str__(self):
        return f"{self.title} (v{self.version})"

    def save(self, *args, **kwargs):
        if self.is_active:
            MentorAgreement.objects.filter(is_active=True, slug=self.slug).exclude(
                pk=self.pk
            ).update(is_active=False)
        super().save(*args, **kwargs)

    @classmethod
    def get_active(cls, slug=None):
        """Return the current active agreement, optionally filtered by slug."""
        qs = cls.objects.filter(is_active=True)
        if slug:
            qs = qs.filter(slug=slug)
        return qs.first()

    @classmethod
    def get_all_active(cls):
        """Return all active agreements (one per slug)."""
        return cls.objects.filter(is_active=True)


class MentorAgreementAcceptance(models.Model):
    """Records that an Adult has accepted a specific MentorAgreement version."""

    adult = models.ForeignKey(
        "Adult",
        on_delete=models.CASCADE,
        related_name="agreement_acceptances",
    )
    agreement = models.ForeignKey(
        MentorAgreement,
        on_delete=models.CASCADE,
        related_name="acceptances",
    )
    accepted_at = models.DateTimeField(auto_now_add=True)
    ip_address = models.GenericIPAddressField(
        null=True,
        blank=True,
        help_text="IP address of the acceptor (for audit trail).",
    )

    class Meta:
        unique_together = ("adult", "agreement")
        ordering = ["-accepted_at"]

    def __str__(self):
        return f"{self.adult} accepted {self.agreement}"

    @classmethod
    def has_accepted_for_user(cls, user):
        """Check if a user has accepted all current active agreements.

        Returns True if there are no active agreements (graceful degradation)
        or if the user's Adult profile has acceptances for every active version.
        Returns False if the user has no Adult profile.
        """
        from .adults import Adult

        active = MentorAgreement.get_all_active()
        if not active.exists():
            return True
        try:
            adult = user.adult_profile
        except (AttributeError, Adult.DoesNotExist):
            return False
        accepted_ids = cls.objects.filter(
            adult=adult, agreement__in=active
        ).values_list("agreement_id", flat=True)
        return active.filter(id__in=accepted_ids).count() == active.count()


def _agreement_submission_upload_to(instance, filename):
    """Files land at MEDIA_ROOT/agreement_submissions/<adult_id>/<filename>."""
    from programs.utils.files import sanitize_upload_filename

    adult_id = instance.adult_id or "unassigned"
    filename = sanitize_upload_filename(filename)
    return f"agreement_submissions/{adult_id}/{filename}"


class MentorAgreementSubmission(models.Model):
    """A signed document uploaded by an Adult in response to a
    :class:`MentorAgreement` that has an attached document (PDF).

    One row per (adult, agreement). Re-uploading replaces the file.
    """

    adult = models.ForeignKey(
        "Adult",
        on_delete=models.CASCADE,
        related_name="agreement_submissions",
    )
    agreement = models.ForeignKey(
        MentorAgreement,
        on_delete=models.CASCADE,
        related_name="submissions",
    )
    file = models.FileField(
        upload_to=_agreement_submission_upload_to,
        max_length=255,
    )
    uploaded_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ("adult", "agreement")
        ordering = ["-uploaded_at"]
        verbose_name = "Mentor Agreement Submission"
        verbose_name_plural = "Mentor Agreement Submissions"

    def __str__(self):
        return f"Signed copy for {self.agreement} by {self.adult}"
