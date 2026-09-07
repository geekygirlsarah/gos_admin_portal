from decimal import Decimal

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models


class Vendor(models.Model):
    """A reference-list vendor the team orders from.

    Vendors are org-wide (not tied to a single program) so every program's
    order form shares the same dropdown. Mentors/Lead Mentors pick a vendor
    when grouping requested items into an ``Order``, or choose "Not listed"
    and type their own; see ``Order.vendor_name`` for the snapshot design.
    """

    name = models.CharField(max_length=255, unique=True, verbose_name="Name")
    website = models.URLField(max_length=500, blank=True, verbose_name="Website")
    contact_email = models.EmailField(blank=True, verbose_name="Contact email")
    contact_phone = models.CharField(
        max_length=50, blank=True, verbose_name="Contact phone"
    )
    notes = models.TextField(blank=True, verbose_name="Notes")

    class Meta:
        verbose_name = "Vendor"
        verbose_name_plural = "Vendors"
        ordering = ["name"]

    def __str__(self):
        return self.name


class Order(models.Model):
    """A grouped purchase order: a list of ``OrderItem`` requests placed
    together with a single vendor.

    Students and mentors place individual ``OrderItem`` requests, which sit in
    the unassigned pool until a mentor/Lead Mentor groups them into an
    ``Order``. The order carries the vendor (from the reference list or a
    free-text "Not listed" entry) and the overall pending/ordered status.
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
        help_text="Program this order is being placed for.",
    )
    vendor = models.ForeignKey(
        Vendor,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="orders",
        verbose_name="Vendor",
        help_text="The reference-list vendor this order is placed with (optional).",
    )
    vendor_name = models.CharField(
        max_length=255,
        blank=True,
        verbose_name="Vendor name",
        help_text=(
            "Snapshot of the vendor name (from the reference list, or the "
            "'Not listed' name a mentor typed). Kept so order text survives "
            "a vendor being renamed or deleted."
        ),
    )
    vendor_url = models.URLField(
        max_length=500,
        blank=True,
        verbose_name="Vendor website",
        help_text="Snapshot of the vendor website (reference list or custom).",
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
        related_name="orders_created",
        verbose_name="Created by",
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
        verbose_name = "Order"
        verbose_name_plural = "Orders"
        ordering = ["-created_at"]

    def __str__(self):
        vendor = self.vendor_name or "no vendor"
        return f"Order #{self.pk} ({vendor})"

    @property
    def item_count(self):
        return self.items.count()

    @property
    def total(self):
        """Estimated total across priced items, or ``None`` when no item has a
        price."""
        totals = [item.total for item in self.items.all() if item.total is not None]
        if not totals:
            return None
        return sum(totals, Decimal("0"))


class OrderItem(models.Model):
    """A single requested item, placed by a student or mentor.

    Items start in the unassigned "pool" (``order`` is null). A mentor/Lead
    Mentor groups them into an ``Order``, after which the item's creator can
    no longer edit it. ``program`` records where the request came from so the
    pool stays grouped by program on the requests page.
    """

    order = models.ForeignKey(
        Order,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="items",
        verbose_name="Order",
        help_text=(
            "The order this item was grouped into. Items with no order are "
            "still awaiting grouping."
        ),
    )
    program = models.ForeignKey(
        "programs.Program",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="order_items",
        verbose_name="Program",
        help_text="Program this item was requested from.",
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
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="order_items_requested",
        verbose_name="Requested by",
    )
    requested_at = models.DateTimeField(auto_now_add=True, verbose_name="Requested on")

    class Meta:
        verbose_name = "Order item"
        verbose_name_plural = "Order items"
        ordering = ["-requested_at"]

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
        """Display name of the person who requested this item."""
        return user_display_name(self.requested_by)


def user_display_name(user):
    """Best display name for a user who requested items or created an order."""
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
