from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from orders.forms import OrderForm
from orders.models import Order
from orders.tests.base import (
    make_carrier,
    make_item,
    make_lead_mentor_user,
    make_mentor_user,
    make_order,
    make_program,
    make_student_user,
    make_vendor,
)


class OrderPlacementCostsTests(TestCase):
    def setUp(self):
        self.program = make_program(with_orders_feature=True)
        self.lead = make_lead_mentor_user()
        self.mentor = make_mentor_user()
        self.student = make_student_user(program=self.program)
        self.vendor = make_vendor("McMaster-Carr")

    def login(self, user):
        self.client.force_login(user)

    def test_order_form_shipping_and_tax_are_optional(self):
        """When mentors bundle an order, shipping cost and tax are optional."""
        item = make_item(self.program, requested_by=self.student)
        form = OrderForm(
            data={
                "program": self.program.id,
                "vendor_choice": str(self.vendor.id),
                "items": [item.pk],
                "notes": "Urgent order for gearbox",
                "shipping_cost": "",
                "tax": "",
            }
        )
        self.assertTrue(form.is_valid(), form.errors)
        order = form.save()
        self.assertIsNone(order.shipping_cost)
        self.assertIsNone(order.tax)

    def test_order_form_with_explicit_shipping_and_tax(self):
        """Mentors can still provide shipping and tax upfront if known."""
        item = make_item(self.program, requested_by=self.student)
        form = OrderForm(
            data={
                "program": self.program.id,
                "vendor_choice": str(self.vendor.id),
                "items": [item.pk],
                "notes": "Pre-calculated quote",
                "shipping_cost": "14.99",
                "tax": "5.25",
            }
        )
        self.assertTrue(form.is_valid(), form.errors)
        order = form.save()
        self.assertEqual(order.shipping_cost, Decimal("14.99"))
        self.assertEqual(order.tax, Decimal("5.25"))

    def test_lead_marks_ordered_with_shipping_cost_and_tax(self):
        """When marking an order as placed, the admin person can submit shipping and tax."""
        order = make_order(self.program, created_by=self.mentor, vendor=self.vendor)
        make_item(
            self.program,
            item_name="Motor Controller",
            requested_by=self.student,
            order=order,
            quantity="2",
            unit_price="45.00",
        )
        self.login(self.lead)

        mark_url = reverse(
            "orders:order_mark_ordered", args=[self.program.id, order.id]
        )
        resp = self.client.post(
            mark_url,
            {
                "shipping_cost": "9.95",
                "tax": "7.20",
            },
        )
        self.assertRedirects(resp, reverse("orders:order_list", args=[self.program.id]))

        order.refresh_from_db()
        self.assertEqual(order.status, Order.STATUS_ORDERED)
        self.assertIsNotNone(order.ordered_at)
        self.assertEqual(order.ordered_by, self.lead)
        self.assertEqual(order.shipping_cost, Decimal("9.95"))
        self.assertEqual(order.tax, Decimal("7.20"))
        self.assertEqual(order.total, Decimal("107.15"))  # 90.00 + 9.95 + 7.20

    def test_lead_marks_ordered_without_shipping_or_tax_keeps_optional(self):
        """When marking an order as placed, leaving shipping/tax blank keeps them as None."""
        order = make_order(self.program, created_by=self.mentor, vendor=self.vendor)
        self.login(self.lead)

        mark_url = reverse(
            "orders:order_mark_ordered", args=[self.program.id, order.id]
        )
        resp = self.client.post(mark_url, {"shipping_cost": "", "tax": ""})
        self.assertRedirects(resp, reverse("orders:order_list", args=[self.program.id]))

        order.refresh_from_db()
        self.assertEqual(order.status, Order.STATUS_ORDERED)
        self.assertIsNone(order.shipping_cost)
        self.assertIsNone(order.tax)

    def test_shipping_info_form_updates_shipping_cost_and_tax_later(self):
        """Shipping and tax can be added or updated later via the shipping info form."""
        carrier = make_carrier("UPS", "https://ups.com/track?num={number}")
        order = make_order(
            self.program,
            created_by=self.mentor,
            status=Order.STATUS_ORDERED,
            vendor=self.vendor,
        )
        self.login(self.lead)

        update_url = reverse(
            "orders:order_update_shipping", args=[self.program.id, order.id]
        )
        resp = self.client.post(
            update_url,
            {
                "shipping_carrier": str(carrier.id),
                "tracking_number": "1Z9999999999999999",
                "shipped_date": "2026-09-24",
                "delivery_estimate": "2026-09-28",
                "shipping_cost": "18.50",
                "tax": "6.00",
            },
        )
        self.assertRedirects(
            resp, reverse("orders:order_detail", args=[self.program.id, order.id])
        )

        order.refresh_from_db()
        self.assertEqual(order.shipping_carrier, carrier)
        self.assertEqual(order.tracking_number, "1Z9999999999999999")
        self.assertEqual(order.shipping_cost, Decimal("18.50"))
        self.assertEqual(order.tax, Decimal("6.00"))

    def test_order_detail_renders_mark_ordered_modal_for_pending_order(self):
        """Order detail page contains the confirmation modal with cost inputs for pending orders."""
        order = make_order(self.program, created_by=self.mentor, vendor=self.vendor)
        self.login(self.lead)

        detail_url = reverse("orders:order_detail", args=[self.program.id, order.id])
        resp = self.client.get(detail_url)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'data-bs-target="#markOrderedModal"')
        self.assertContains(resp, 'id="markOrderedModal"')
        self.assertContains(resp, 'name="shipping_cost"')
        self.assertContains(resp, 'name="tax"')

    def test_order_list_renders_mark_ordered_modal_for_pending_orders(self):
        """Order list page contains confirmation modals for pending orders."""
        order = make_order(self.program, created_by=self.mentor, vendor=self.vendor)
        self.login(self.lead)

        list_url = reverse("orders:order_list", args=[self.program.id])
        resp = self.client.get(list_url)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, f'data-bs-target="#markOrderedModal{order.pk}"')
        self.assertContains(resp, f'id="markOrderedModal{order.pk}"')
        self.assertContains(resp, 'name="shipping_cost"')
        self.assertContains(resp, 'name="tax"')
