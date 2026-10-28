import datetime
from decimal import Decimal

from django.conf import settings
from django.db import models

from .fields import EncryptedFileField


class Fee(models.Model):
    organization = models.ForeignKey(
        "organizations.Organization",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="fees",
        help_text="Organization that owns this fee (multi-tenant; null until backfilled).",
    )
    program = models.ForeignKey(
        "Program", on_delete=models.CASCADE, related_name="fees"
    )
    name = models.CharField(max_length=200)
    amount = models.DecimalField(max_digits=8, decimal_places=2)
    # Editable date for when the fee is considered received/posted
    effective_date = models.DateField(
        blank=True,
        null=True,
        help_text="Effective date the fee was posted/received (used for balance sheet sorting).",
    )
    due_date = models.DateField(
        blank=True, null=True, help_text="Optional due date for the fee."
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["program__name", "name"]
        unique_together = ("program", "name")

    def __str__(self):
        return f"{self.program.name} — {self.name}: ${self.amount}"


class Payment(models.Model):
    organization = models.ForeignKey(
        "organizations.Organization",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="payments",
        help_text="Organization that owns this payment (multi-tenant; null until backfilled).",
    )

    PAID_VIA_CHOICES = [
        ("check", "Check"),
        ("credit_card", "Credit Card"),
        ("cash", "Cash"),
        ("camp", "Camp"),
        ("other", "Other"),
    ]

    student = models.ForeignKey(
        "Student", on_delete=models.CASCADE, related_name="payments"
    )
    program = models.ForeignKey(
        "Program", on_delete=models.CASCADE, related_name="payments"
    )
    amount = models.DecimalField(max_digits=8, decimal_places=2)
    paid_on = models.DateField()
    paid_via = models.CharField(
        max_length=20, choices=PAID_VIA_CHOICES, default="credit_card"
    )
    check_number = models.PositiveIntegerField(blank=True, null=True)
    camp_hours = models.PositiveIntegerField(blank=True, null=True)
    notes = models.TextField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-paid_on", "-created_at"]
        indexes = [
            models.Index(
                fields=["student", "program"], name="payment_student_program_idx"
            ),
            models.Index(
                fields=["program", "paid_on"], name="payment_program_date_idx"
            ),
        ]

    def __str__(self):
        via = dict(self.PAID_VIA_CHOICES).get(self.paid_via, self.paid_via)
        details = (
            f" (check #{self.check_number})"
            if (self.paid_via == "check" and self.check_number)
            else ""
        )
        return f"Payment ${self.amount} by {self.student} in {self.program.name} via {via}{details} on {self.paid_on}"

    def clean(self):
        # Ensure the student is enrolled in the payment's program
        from django.core.exceptions import ValidationError

        from .enrollments import Enrollment

        if (
            self.program_id
            and not Enrollment.objects.filter(
                student=self.student, program_id=self.program_id
            ).exists()
        ):
            raise ValidationError(
                "Student must be enrolled in the selected program for this payment."
            )


class SlidingScaleSettings(models.Model):
    """Singleton row holding the portal-wide sliding scale calculation constants.

    These are based on the federal poverty guidelines and can be edited by a
    Lead Mentor from the Portal Settings page.
    """

    base_amount = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=Decimal("10150.00"),
        help_text="Federal poverty guideline base amount (for household size).",
    )
    additional_member_amount = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=Decimal("5500.00"),
        help_text="Amount added per household member when computing the poverty guideline base.",
    )
    low_multiplier = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=Decimal("1.50"),
        help_text="Multiplier applied to the poverty guideline base to compute the lower income boundary.",
    )
    high_multiplier = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=Decimal("4.00"),
        help_text="Multiplier applied to the poverty guideline base to compute the upper income boundary.",
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Sliding Scale Settings"
        verbose_name_plural = "Sliding Scale Settings"

    def __str__(self):
        return "Sliding Scale Settings"

    @classmethod
    def get_solo(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj

    def compute_discount_percent(self, family_size, adjusted_gross_income):
        """Compute the suggested discount percent (0-100) for a household.

        Follows: fed_base = base_amount + family_size * additional_member_amount;
        low_boundary = fed_base * low_multiplier; high_boundary = fed_base * high_multiplier;
        percent_owed = (agi - low_boundary) / (high_boundary - low_boundary) * 100,
        clamped to [0, 100]. The discount percent is 100 - percent_owed.
        """
        if family_size is None or adjusted_gross_income is None:
            return None
        fed_base = self.base_amount + (
            Decimal(family_size) * self.additional_member_amount
        )
        low_boundary = fed_base * self.low_multiplier
        high_boundary = fed_base * self.high_multiplier
        if high_boundary == low_boundary:
            return Decimal("0.00")
        ratio = (Decimal(adjusted_gross_income) - low_boundary) / (
            high_boundary - low_boundary
        )
        ratio = max(Decimal("0"), min(Decimal("1"), ratio))
        percent_owed = ratio * Decimal("100")
        discount_percent = Decimal("100") - percent_owed
        return discount_percent.quantize(Decimal("0.01"))


class SlidingScale(models.Model):
    """An income-based discount applied to a student's fees across ALL of the
    programs they're enrolled in during the effective date range (not tied to
    a single program). A row represents one application/period; a parent
    applies (creating a "pending" row), and a Lead Mentor reviews it.
    """

    STATUS_PENDING = "pending"
    STATUS_APPROVED = "approved"
    STATUS_DECLINED = "declined"
    STATUS_CHOICES = [
        (STATUS_PENDING, "Pending Review"),
        (STATUS_APPROVED, "Approved"),
        (STATUS_DECLINED, "Declined"),
    ]

    organization = models.ForeignKey(
        "organizations.Organization",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="sliding_scales",
        help_text="Organization that owns this sliding-scale application (multi-tenant; null until backfilled).",
    )
    student = models.ForeignKey(
        "Student", on_delete=models.CASCADE, related_name="sliding_scales"
    )
    percent = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        blank=True,
        null=True,
        help_text="Percent discount applied to total fees (0–100), across all of the student's programs.",
    )
    date = models.DateField(
        blank=True,
        null=True,
        help_text=(
            "Effective start date. Only fees on or after this date "
            "(and before the expiration date, if any) will be discounted."
        ),
    )
    expiration_date = models.DateField(
        blank=True,
        null=True,
        help_text="Date the discount stops applying. Leave blank for no expiration.",
    )
    family_size = models.PositiveIntegerField(
        blank=True, null=True, verbose_name="Household size"
    )
    adjusted_gross_income = models.DecimalField(
        max_digits=10, decimal_places=2, blank=True, null=True
    )
    status = models.CharField(
        max_length=10, choices=STATUS_CHOICES, default=STATUS_APPROVED
    )
    decline_reason = models.TextField(
        blank=True,
        null=True,
        help_text="Shown to the parent when the application is declined.",
    )
    applied_by = models.ForeignKey(
        "Adult",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="sliding_scale_applications",
        help_text="Parent who submitted this application, if any.",
    )
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    reviewed_at = models.DateTimeField(blank=True, null=True)
    notes = models.TextField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(
                fields=["status", "-created_at"], name="slidingscale_status_idx"
            ),
            models.Index(
                fields=["student", "status", "-date", "-created_at"],
                name="slidingscale_active_lookup_idx",
            ),
        ]

    def __str__(self):
        return f"Sliding scale ({self.get_status_display()}) for {self.student}"

    @property
    def is_pending(self):
        return self.status == self.STATUS_PENDING

    def clean(self):
        super().clean()
        from django.core.exceptions import ValidationError

        if self.date and self.expiration_date and self.expiration_date < self.date:
            raise ValidationError(
                {
                    "expiration_date": "Expiration date cannot be earlier than start date."
                }
            )

    @property
    def is_active(self):
        """Whether this approved record currently applies (not expired)."""
        if self.status != self.STATUS_APPROVED:
            return False
        today = datetime.date.today()
        if self.expiration_date and self.expiration_date < today:
            return False
        return True

    @property
    def effective_dates_display(self):
        """Human-readable effective date range."""
        if self.date and self.expiration_date:
            start_str = f"{self.date.strftime('%b')} {self.date.day}, {self.date.year}"
            end_str = f"{self.expiration_date.strftime('%b')} {self.expiration_date.day}, {self.expiration_date.year}"
            return f"{start_str} \u2013 {end_str}"
        if self.date:
            return f"From {self.date.strftime('%b')} {self.date.day}, {self.date.year}"
        if self.expiration_date:
            return f"Through {self.expiration_date.strftime('%b')} {self.expiration_date.day}, {self.expiration_date.year}"
        return "No expiration"


def _tax_form_upload_to(instance, filename):
    from programs.utils.files import sanitize_upload_filename

    return f"tax_forms/{sanitize_upload_filename(filename)}"


class TaxForm(models.Model):
    sliding_scale = models.ForeignKey(
        SlidingScale, on_delete=models.CASCADE, related_name="tax_forms"
    )
    file = EncryptedFileField(
        upload_to=_tax_form_upload_to,
        max_length=255,
        help_text="Uploaded tax form for sliding scale verification. Will be deleted after review.",
    )
    uploaded_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Tax form for {self.sliding_scale}"


class FeeAssignment(models.Model):
    """
    Links a Fee to specific students (within the Fee's program).
    If a Fee has any assignments, it applies ONLY to those students.
    """

    fee = models.ForeignKey("Fee", on_delete=models.CASCADE, related_name="assignments")
    organization = models.ForeignKey(
        "organizations.Organization",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="fee_assignments",
        help_text="Organization that owns this fee assignment (multi-tenant; null until backfilled).",
    )
    student = models.ForeignKey(
        "Student", on_delete=models.CASCADE, related_name="fee_assignments"
    )

    # Optional note
    notes = models.TextField(blank=True, null=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ("fee", "student")
        ordering = [
            "fee__program__name",
            "fee__name",
            "student__last_name",
            "student__preferred_first_name",
        ]

    def __str__(self):
        return f"{self.fee.name} → {self.student}"

    def clean(self):
        # Ensure the student is enrolled in the same program as the fee
        from django.core.exceptions import ValidationError

        from .enrollments import Enrollment

        program = self.fee.program if self.fee_id else None
        if (
            program
            and not Enrollment.objects.filter(
                student=self.student, program=program
            ).exists()
        ):
            raise ValidationError(
                "Assigned student must be enrolled in the fee’s program."
            )
