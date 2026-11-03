from django.db import IntegrityError, transaction
from django.db.models import PROTECT
from django.test import TestCase

from organizations.models import Organization

STRICT_MODELS = [
    "orders.Vendor",
    "orders.ShippingCarrier",
    "orders.Order",
    "orders.OrderItem",
    "badges.Badge",
    "badges.StudentBadge",
]


class OrganizationRequiredTests(TestCase):
    """Org-owned models must always belong to exactly one organization."""

    def test_org_fk_is_required_and_protected(self):
        from django.apps import apps

        for label in STRICT_MODELS:
            with self.subTest(model=label):
                field = apps.get_model(label)._meta.get_field("organization")
                self.assertFalse(field.null)
                self.assertIs(field.remote_field.on_delete, PROTECT)

    def test_every_organization_fk_is_required_and_protected(self):
        from django.apps import apps

        for model in apps.get_models():
            field = {f.name: f for f in model._meta.fields}.get("organization")
            if field is None or field.related_model is not Organization:
                continue
            with self.subTest(model=model._meta.label):
                self.assertFalse(field.null)
                self.assertIs(field.remote_field.on_delete, PROTECT)

    def test_order_without_organization_rejected(self):
        from orders.models import Order

        order = Order.objects.create(status="pending")
        with self.assertRaises(IntegrityError), transaction.atomic():
            # update() bypasses the test-runner default-org shim.
            Order.objects.filter(pk=order.pk).update(organization=None)

    def test_org_with_orders_cannot_be_deleted(self):
        from django.db.models import ProtectedError

        from orders.models import Order

        org = Organization.objects.create(name="Temp", slug="temp", subdomain="temp")
        Order.objects.create(status="pending", organization=org)
        with self.assertRaises(ProtectedError):
            org.delete()
