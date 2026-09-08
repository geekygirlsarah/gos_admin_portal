from decimal import Decimal

from django.core.validators import MinValueValidator
from django.db import migrations, models


def backfill_carriers(apps, schema_editor):
    Order = apps.get_model("orders", "Order")
    ShippingCarrier = apps.get_model("orders", "ShippingCarrier")
    for order in Order.objects.exclude(shipping_carrier=""):
        carrier, _ = ShippingCarrier.objects.get_or_create(name=order.shipping_carrier)
        order.shipping_carrier_fk = carrier
        order.save(update_fields=["shipping_carrier_fk"])


def unbackfill_carriers(apps, schema_editor):
    Order = apps.get_model("orders", "Order")
    for order in Order.objects.exclude(shipping_carrier_fk=None):
        order.shipping_carrier = order.shipping_carrier_fk.name
        order.shipping_carrier_fk = None
        order.save(update_fields=["shipping_carrier", "shipping_carrier_fk"])


class Migration(migrations.Migration):

    dependencies = [
        ("orders", "0006_alter_order_status"),
    ]

    operations = [
        migrations.CreateModel(
            name="ShippingCarrier",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "name",
                    models.CharField(max_length=255, unique=True, verbose_name="Name"),
                ),
                (
                    "tracking_url_template",
                    models.CharField(
                        blank=True,
                        max_length=500,
                        verbose_name="Tracking URL template",
                        help_text=(
                            "URL to look up a package, with {number} where the "
                            "tracking number goes. e.g. "
                            "https://www.ups.com/track?track=yes&trackNums={number}."
                        ),
                    ),
                ),
            ],
            options={
                "verbose_name": "Shipping company",
                "verbose_name_plural": "Shipping companies",
                "ordering": ["name"],
            },
        ),
        migrations.CreateModel(
            name="ItemTag",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "name",
                    models.CharField(
                        max_length=100,
                        unique=True,
                        verbose_name="Name",
                        help_text="e.g. 'Drivetrain', 'Marketing', 'FRC Team 5987'.",
                    ),
                ),
                (
                    "color",
                    models.CharField(
                        default="#0000ff",
                        max_length=7,
                        verbose_name="Color",
                        help_text="Hex color code (e.g. #0000ff) used for the tag's pill.",
                    ),
                ),
            ],
            options={
                "verbose_name": "Item tag",
                "verbose_name_plural": "Item tags",
                "ordering": ["name"],
            },
        ),
        migrations.AddField(
            model_name="order",
            name="shipping_carrier_fk",
            field=models.ForeignKey(
                to="orders.ShippingCarrier",
                on_delete=models.SET_NULL,
                null=True,
                blank=True,
                related_name="orders",
                verbose_name="Shipping company",
                help_text=(
                    "Carrier used to ship this order (from the shipping companies list)."
                ),
            ),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="order",
            name="shipping_cost",
            field=models.DecimalField(
                blank=True,
                decimal_places=2,
                help_text="Amount charged for shipping, added to the order total.",
                max_digits=10,
                null=True,
                validators=[MinValueValidator(Decimal("0"))],
                verbose_name="Shipping cost",
            ),
        ),
        migrations.AddField(
            model_name="order",
            name="tax",
            field=models.DecimalField(
                blank=True,
                decimal_places=2,
                help_text="Sales tax charged, added to the order total.",
                max_digits=10,
                null=True,
                validators=[MinValueValidator(Decimal("0"))],
                verbose_name="Tax",
            ),
        ),
        migrations.AddField(
            model_name="orderitem",
            name="tag",
            field=models.ForeignKey(
                to="orders.ItemTag",
                on_delete=models.SET_NULL,
                null=True,
                blank=True,
                related_name="order_items",
                verbose_name="Tag",
                help_text=(
                    "Optional label the purchase is for (e.g. a team, project, crew, "
                    "or subteam). Only used for organization and later filtering."
                ),
            ),
            preserve_default=False,
        ),
        migrations.RunPython(backfill_carriers, unbackfill_carriers),
        migrations.RemoveField(
            model_name="order",
            name="shipping_carrier",
        ),
        migrations.RenameField(
            model_name="order",
            old_name="shipping_carrier_fk",
            new_name="shipping_carrier",
        ),
    ]
