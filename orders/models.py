from decimal import Decimal

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models


class PurchaseOrder(models.Model):
    """A request to purchase a part, tool, or supply.

    Orders are org-wide: the pending list shows every program's requests
    grouped by program (see ``OrderListView``). ``program`` is the program the
    request was placed from and is used for grouping; a Lead Mentor may also
    file an order not tied to a program by leaving it blank.
    """

    STATUS_PENDING = "pending"
    STATUS_ORDERED = "ordered"
    STATUS_CHOICES = [
        (STATUS_PENDING, "Pending"),
        (STATUS_ORDERED, "Ordered"),
    ]

    program = models.ForeignKey(
        "programs.Program",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="orders",
        verbose_name="Program",
        help_text="Program this order was placed for.",
    )
    item_name = models.CharField(max_length=255, verbose_name="Item")
    quantity = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=Decimal("1"),
        validators=[MinValueValidator(Decimal("0.01"))],
        verbose_name="Quantity",
        help_text="How many you need. Decimal quantities (e.g. 2.5 ft of extrusion) are fine.",
    )
    unit_price = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        validators=[MinValueValidator(Decimal("0"))],
        verbose_name="Unit price",
        help_text="Estimated price per item — helpful for budgeting. Leave blank if unknown.",
    )
    url = models.URLField(
        max_length=500,
        blank=True,
        verbose_name="Link to item",
        help_text="A URL to the part, tool, or product page.",
    )
    notes = models.TextField(blank=True, verbose_name="Notes")
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default=STATUS_PENDING,
        db_index=True,
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="orders_requested",
        verbose_name="Requested by",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    ordered_at = models.DateTimeField(null=True, blank=True, verbose_name="Ordered on")
    ordered_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="orders_marked_ordered",
        verbose_name="Marked ordered by",
    )

    class Meta:
        verbose_name = "Order request"
        verbose_name_plural = "Order requests"
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.item_name} x{self.quantity_normalized}"

    @property
    def quantity_normalized(self):
        """Quantity without trailing zeros (e.g. ``2.5`` not ``2.50``)."""
        return Decimal(str(self.quantity)).normalize()

    @property
    def total(self):
        """Estimated total (``quantity * unit_price``) or ``None`` when no
        price was given. Coerces strings so the property is safe on freshly
        constructed instances too."""
        if self.unit_price is None:
            return None
        qty = Decimal(str(self.quantity)) if self.quantity is not None else Decimal("0")
        return qty * Decimal(str(self.unit_price))

    @property
    def requested_by_name(self):
        """Display name of the person who requested this order."""
        user = self.created_by
        if user is None:
            return "Deleted user"
        from programs.models import Adult, Student

        try:
            return user.student_profile.display_name
        except Student.DoesNotExist:
            pass
        try:
            return user.adult_profile.display_name
        except Adult.DoesNotExist:
            pass
        return user.get_full_name() or user.username
