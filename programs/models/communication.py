from django.conf import settings
from django.db import models


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
    payload = models.JSONField(default=dict, blank=True)

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
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

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

    def resolve_entities(self):
        """Link this delivery event to Student, Adult, or Application matching recipient_email."""
        email = (self.recipient_email or "").strip().lower()
        if not email:
            return
        updated = False
        if not self.student:
            from .students import Student

            student = Student.objects.filter(
                models.Q(personal_email__iexact=email)
                | models.Q(andrew_email__iexact=email)
            ).first()
            if student:
                self.student = student
                updated = True
        if not self.adult:
            from .adults import Adult

            adult = Adult.objects.filter(
                models.Q(personal_email__iexact=email)
                | models.Q(andrew_email__iexact=email)
            ).first()
            if adult:
                self.adult = adult
                updated = True
        if not self.application:
            try:
                from applications.models import Application

                app = Application.objects.filter(email__iexact=email).first()
                if not app:
                    app = Application.objects.filter(data__icontains=email).first()
                if app:
                    self.application = app
                    updated = True
            except Exception:  # nosec B110
                pass
        if updated and self.pk:
            self.save(update_fields=["student", "adult", "application"])
