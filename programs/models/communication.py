from django.conf import settings
from django.db import models
from django.db.models import F, Q
from django.utils import timezone

from organizations.models import OrganizationManager, OrganizationQuerySet

from .fields import EncryptedTextField


class EmailDeliveryEventQuerySet(OrganizationQuerySet):
    def verified(self):
        return self.filter(
            Q(student__isnull=True) | Q(student__organization_id=F("organization_id")),
            Q(adult__isnull=True) | Q(adult__organization_id=F("organization_id")),
            Q(application__isnull=True)
            | Q(
                application__organization_id=F("organization_id"),
                application__program__isnull=True,
            )
            | Q(
                application__organization_id=F("organization_id"),
                application__program__organization_id=F("organization_id"),
            ),
            Q(student__isnull=True) | Q(adult__isnull=True),
            Q(student__isnull=True) | Q(application__isnull=True),
            Q(adult__isnull=True) | Q(application__isnull=True),
            is_verified=True,
            trusted_mapping__isnull=False,
            trusted_mapping__is_ambiguous=False,
            trusted_mapping__organization_id=F("organization_id"),
            trusted_mapping__provider_message_id=F("message_id"),
            trusted_mapping__recipient_fingerprint=F("recipient_fingerprint"),
        ).distinct()

    def verified_for_organization(self, organization):
        if organization is None:
            return self.none()
        organization_id = getattr(organization, "pk", organization)
        return (
            self.verified()
            .for_organization(organization_id)
            .filter(organization_id=organization_id)
        )


class EmailDeliveryEvent(models.Model):
    """Tracks email delivery events (bounces, complaints) from toSend webhooks."""

    EVENT_BOUNCED = "bounced"
    EVENT_COMPLAINT = "complaint"
    EVENT_UNSUBSCRIBED = "unsubscribed"
    EVENT_DELIVERED = "delivered"

    EVENT_CHOICES = [
        (EVENT_BOUNCED, "Bounced"),
        (EVENT_COMPLAINT, "Spam Complaint"),
        (EVENT_UNSUBSCRIBED, "Unsubscribed"),
        (EVENT_DELIVERED, "Delivered"),
    ]

    BOUNCE_HARD = "hard"
    BOUNCE_SOFT = "soft"
    BOUNCE_CHOICES = [
        (BOUNCE_HARD, "Hard Bounce (Permanent)"),
        (BOUNCE_SOFT, "Soft Bounce (Transient)"),
    ]

    recipient_email = models.EmailField(db_index=True)
    event_type = models.CharField(
        max_length=30, choices=EVENT_CHOICES, db_index=True, default=EVENT_BOUNCED
    )
    bounce_type = models.CharField(
        max_length=20, choices=BOUNCE_CHOICES, blank=True, default=""
    )
    reason = models.TextField(blank=True, default="")
    message_id = models.CharField(max_length=255, blank=True, default="", db_index=True)
    recipient_fingerprint = models.CharField(max_length=64, blank=True, db_index=True)
    webhook_deduplication_key = models.CharField(
        max_length=64, unique=True, null=True, blank=True
    )
    payload = models.JSONField(default=dict, blank=True)
    trusted_mapping = models.ForeignKey(
        "TrustedOutgoingEmailRecipient",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="delivery_events",
    )

    student = models.ForeignKey(
        "Student",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="email_delivery_events",
    )
    adult = models.ForeignKey(
        "Adult",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="email_delivery_events",
    )
    application = models.ForeignKey(
        "applications.Application",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="email_delivery_events",
    )

    resolved = models.BooleanField(default=False, db_index=True)
    resolved_at = models.DateTimeField(null=True, blank=True)
    resolved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="resolved_email_delivery_events",
    )
    organization = models.ForeignKey(
        "organizations.Organization",
        on_delete=models.PROTECT,
        related_name="email_delivery_events",
        help_text="Organization that owns this email delivery event (multi-tenant).",
    )
    objects = OrganizationManager.from_queryset(EmailDeliveryEventQuerySet)()
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    is_verified = models.BooleanField(
        default=False,
        db_index=True,
        help_text="True only when trusted outgoing-message ownership verified this event.",
    )

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(
                fields=["recipient_email", "resolved"],
                name="email_event_email_res_idx",
            ),
            models.Index(
                fields=["event_type", "resolved"],
                name="email_event_type_res_idx",
            ),
        ]
        verbose_name = "Email Delivery Event"
        verbose_name_plural = "Email Delivery Events"

    def __str__(self):
        return f"{self.get_event_type_display()} for {self.recipient_email}"

    @property
    def is_hard_bounce(self) -> bool:
        if self.event_type != self.EVENT_BOUNCED:
            return False
        if self.bounce_type == self.BOUNCE_HARD:
            return True
        if self.payload and self.payload.get("is_hard_bounce") is True:
            return True
        return False

    @property
    def is_critical(self) -> bool:
        return self.is_hard_bounce or self.event_type == self.EVENT_COMPLAINT

    def has_consistent_organization_links(self, organization_id):
        if not organization_id or self.organization_id != organization_id:
            return False
        if not self.trusted_mapping_id:
            return False
        mapping = self.trusted_mapping
        if (
            mapping.is_ambiguous
            or mapping.organization_id != organization_id
            or mapping.provider_message_id != self.message_id
            or mapping.recipient_fingerprint != self.recipient_fingerprint
        ):
            return False
        if (
            sum(
                bool(value)
                for value in (self.student_id, self.adult_id, self.application_id)
            )
            > 1
        ):
            return False
        for relation in ("student", "adult"):
            related_id = getattr(self, f"{relation}_id")
            if (
                related_id
                and getattr(getattr(self, relation), "organization_id", None)
                != organization_id
            ):
                return False
        if self.application_id:
            application = self.application
            if application.organization_id != organization_id:
                return False
            if (
                application.program_id
                and application.program.organization_id != organization_id
            ):
                return False
        return True

    def resolve_entities(self):
        """Link only one unambiguous local entity for a verified delivery event."""
        if not self.is_verified or not self.organization_id:
            return

        organization_id = self.organization_id
        if not self.has_consistent_organization_links(organization_id):
            return
        if self.student_id or self.adult_id or self.application_id:
            return

        email = (self.recipient_email or "").strip().lower()
        if not email:
            return

        from applications.models import Application

        from .adults import Adult
        from .students import Student

        matches = []
        student_matches = list(
            Student.objects.for_organization(organization_id)
            .filter(organization_id=organization_id)
            .filter(Q(personal_email__iexact=email) | Q(org_email__iexact=email))
            .distinct()[:2]
        )
        adult_matches = list(
            Adult.objects.for_organization(organization_id)
            .filter(organization_id=organization_id)
            .filter(Q(personal_email__iexact=email) | Q(org_email__iexact=email))
            .distinct()[:2]
        )
        application_matches = list(
            Application.objects.for_organization(organization_id)
            .filter(organization_id=organization_id, email__iexact=email)
            .filter(
                Q(program__isnull=True) | Q(program__organization_id=organization_id)
            )
            .distinct()[:2]
        )
        matches.extend(("student", item) for item in student_matches)
        matches.extend(("adult", item) for item in adult_matches)
        matches.extend(("application", item) for item in application_matches)
        if len(matches) != 1:
            return

        entity_type, entity = matches[0]
        setattr(self, entity_type, entity)
        self.save(update_fields=["student", "adult", "application"])


class TrustedOutgoingEmailRecipient(models.Model):
    organization = models.ForeignKey(
        "organizations.Organization",
        on_delete=models.PROTECT,
        related_name="outgoing_email_delivery_mappings",
    )
    provider_message_id = models.CharField(max_length=255, db_index=True)
    recipient_email = EncryptedTextField()
    recipient_fingerprint = models.CharField(max_length=64)
    is_ambiguous = models.BooleanField(default=False, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    objects = OrganizationManager.from_queryset(OrganizationQuerySet)()

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("organization", "provider_message_id", "recipient_fingerprint"),
                name="unique_out_email_recipient",
            )
        ]
        indexes = [
            models.Index(
                fields=("provider_message_id", "recipient_fingerprint"),
                name="out_email_provider_rcpt_idx",
            )
        ]

    def __str__(self):
        return f"Outgoing email recipient mapping {self.pk}"


class EmailDeliveryQuarantine(models.Model):
    REASON_UNMATCHED = "unmatched"
    REASON_AMBIGUOUS = "ambiguous"
    REASON_MALFORMED = "malformed"
    REASON_CHOICES = [
        (REASON_UNMATCHED, "Unmatched ownership"),
        (REASON_AMBIGUOUS, "Ambiguous correlation"),
        (REASON_MALFORMED, "Malformed event"),
    ]

    deduplication_key = models.CharField(max_length=64, unique=True, editable=False)
    reason_code = models.CharField(max_length=20, choices=REASON_CHOICES, db_index=True)
    encrypted_diagnostic = EncryptedTextField(blank=True, default="")
    message_id_fingerprint = models.CharField(max_length=64, blank=True, db_index=True)
    recipient_fingerprint = models.CharField(max_length=64, blank=True, db_index=True)
    received_at = models.DateTimeField(default=timezone.now, db_index=True)
    reconciled_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-received_at"]
        verbose_name = "Quarantined Email Delivery Event"
        verbose_name_plural = "Quarantined Email Delivery Events"

    def __str__(self):
        return f"Quarantined delivery event ({self.reason_code}) at {self.received_at:%Y-%m-%d %H:%M}"


class EmailHistoryBatch(models.Model):
    organization = models.ForeignKey(
        "organizations.Organization",
        on_delete=models.PROTECT,
        related_name="email_history_batches",
    )
    initiated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="initiated_email_history_batches",
    )
    category = models.CharField(max_length=80)
    created_at = models.DateTimeField(default=timezone.now, db_index=True)

    objects = OrganizationManager.from_queryset(OrganizationQuerySet)()

    class Meta:
        ordering = ["-created_at", "-pk"]
        indexes = [
            models.Index(
                fields=("organization", "created_at"),
                name="email_hist_batch_org_idx",
            )
        ]

    def __str__(self):
        return f"Email history batch {self.pk} ({self.category})"


class EmailHistory(models.Model):
    STATUS_QUEUED = "queued"
    STATUS_ATTEMPTED = "attempted"
    STATUS_ACCEPTED = "accepted"
    STATUS_FAILED = "failed"
    STATUS_UNKNOWN = "unknown"
    STATUS_CHOICES = [
        (STATUS_QUEUED, "Queued"),
        (STATUS_ATTEMPTED, "Attempted"),
        (STATUS_ACCEPTED, "Accepted by provider"),
        (STATUS_FAILED, "Failed"),
        (STATUS_UNKNOWN, "Unknown"),
    ]

    organization = models.ForeignKey(
        "organizations.Organization",
        on_delete=models.PROTECT,
        related_name="email_history_entries",
    )
    batch = models.ForeignKey(
        EmailHistoryBatch,
        on_delete=models.CASCADE,
        related_name="messages",
    )
    subject = EncryptedTextField(null=True, blank=True, default=None)
    body_text = EncryptedTextField(null=True, blank=True, default=None)
    body_html = EncryptedTextField(null=True, blank=True, default=None)
    body_retained = models.BooleanField(default=False)
    status = models.CharField(
        max_length=12, choices=STATUS_CHOICES, default=STATUS_QUEUED, db_index=True
    )
    created_at = models.DateTimeField(default=timezone.now, db_index=True)
    attempted_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    objects = OrganizationManager.from_queryset(OrganizationQuerySet)()

    class Meta:
        ordering = ["-created_at", "-pk"]
        indexes = [
            models.Index(
                fields=("organization", "created_at"),
                name="email_hist_org_created_idx",
            )
        ]

    def __str__(self):
        return f"Email history entry {self.pk} ({self.status})"


class EmailHistoryAddress(models.Model):
    KIND_FROM = "from"
    KIND_TO = "to"
    KIND_CC = "cc"
    KIND_BCC = "bcc"
    KIND_CHOICES = [
        (KIND_FROM, "From"),
        (KIND_TO, "To"),
        (KIND_CC, "Cc"),
        (KIND_BCC, "Bcc"),
    ]

    organization = models.ForeignKey(
        "organizations.Organization",
        on_delete=models.PROTECT,
        related_name="email_history_addresses",
    )
    history = models.ForeignKey(
        EmailHistory,
        on_delete=models.CASCADE,
        related_name="addresses",
    )
    kind = models.CharField(max_length=4, choices=KIND_CHOICES)
    email_address = EncryptedTextField()
    email_fingerprint = models.CharField(max_length=64, db_index=True)

    objects = OrganizationManager.from_queryset(OrganizationQuerySet)()

    class Meta:
        indexes = [
            models.Index(
                fields=("organization", "email_fingerprint"),
                name="email_hist_addr_fpr_idx",
            )
        ]

    def __str__(self):
        return f"Email history address {self.pk} ({self.kind})"
