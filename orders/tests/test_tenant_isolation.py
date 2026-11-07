from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from orders.models import Order, OrderItem, ShippingCarrier
from organizations.models import Organization, UserOrganizationMembership
from programs.models import Program, ProgramFeature


class OrderTenantIsolationTests(TestCase):
    def setUp(self):
        self.alpha = Organization.objects.create(
            name="Orders Alpha", slug="orders-alpha", subdomain="orders-alpha"
        )
        self.beta = Organization.objects.create(
            name="Orders Beta", slug="orders-beta", subdomain="orders-beta"
        )
        self.orders_feature, _ = ProgramFeature.objects.get_or_create(
            key="orders", defaults={"name": "Order Requests"}
        )
        self.alpha_program = Program.objects.create(
            organization=self.alpha, name="Alpha Program", active=True
        )
        self.alpha_program.features.add(self.orders_feature)
        self.beta_program = Program.objects.create(
            organization=self.beta, name="Beta Program", active=True
        )
        self.beta_program.features.add(self.orders_feature)
        self.user = get_user_model().objects.create_user(
            username="orders-alpha-lead", password="password123"
        )  # nosec B106
        UserOrganizationMembership.objects.create(
            user=self.user, organization=self.alpha, role="LeadMentor", is_active=True
        )
        self.client.force_login(self.user)
        self.alpha_host = "orders-alpha.localhost"

    def tenant_get(self, path):
        return self.client.get(path, HTTP_HOST=self.alpha_host)

    def test_membership_only_lead_has_order_controls_and_can_export(self):
        url = reverse("orders:order_list", args=[self.alpha_program.pk])
        response = self.tenant_get(url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["user_role"], "LeadMentor")
        self.assertTrue(response.context["can_request_item"])
        self.assertTrue(response.context["can_place_order"])
        self.assertTrue(response.context["can_manage_items"])

        export = self.tenant_get(f"{url}?export=csv")
        self.assertEqual(export.status_code, 200)
        self.assertEqual(export["Content-Type"], "text/csv")

    def test_foreign_program_order_pages_are_not_visible(self):
        OrderItem.objects.create(
            organization=self.beta,
            program=self.beta_program,
            item_name="Beta private order request",
            quantity=1,
            url="https://example.test/private",
        )
        paths = (
            reverse("orders:order_list", args=[self.beta_program.pk]),
            reverse("orders:order_archive", args=[self.beta_program.pk]),
        )

        for path in paths:
            with self.subTest(path=path):
                response = self.tenant_get(path)
                self.assertNotEqual(response.status_code, 200)
                self.assertNotIn(b"Beta private order request", response.content)

    def test_shipping_carriers_are_scoped_for_display_and_post(self):
        order = Order.objects.create(
            organization=self.alpha,
            program=self.alpha_program,
            status=Order.STATUS_ORDERED,
            created_by=self.user,
        )
        local_carrier = ShippingCarrier.objects.create(
            organization=self.alpha,
            name="Alpha Carrier",
            tracking_url_template="https://alpha.example/{number}",
        )
        foreign_carrier = ShippingCarrier.objects.create(
            organization=self.beta,
            name="Beta Carrier",
            tracking_url_template="https://beta.example/{number}",
        )
        detail_url = reverse(
            "orders:order_detail", args=[self.alpha_program.pk, order.pk]
        )
        response = self.client.get(detail_url, HTTP_HOST=self.alpha_host)

        self.assertEqual(response.status_code, 200)
        carrier_ids = list(
            response.context["shipping_form"]
            .fields["shipping_carrier"]
            .queryset.values_list("pk", flat=True)
        )
        self.assertEqual(carrier_ids, [local_carrier.pk])
        self.assertContains(response, "Alpha Carrier")
        self.assertNotContains(response, "Beta Carrier")

        update_url = reverse(
            "orders:order_update_shipping", args=[self.alpha_program.pk, order.pk]
        )
        self.client.post(
            update_url,
            {"shipping_carrier": str(foreign_carrier.pk), "tracking_number": "123"},
            HTTP_HOST=self.alpha_host,
        )
        order.refresh_from_db()
        self.assertIsNone(order.shipping_carrier_id)
        self.assertEqual(order.tracking_number, "")

        self.client.post(
            update_url,
            {"shipping_carrier": str(local_carrier.pk), "tracking_number": "456"},
            HTTP_HOST=self.alpha_host,
        )
        order.refresh_from_db()
        self.assertEqual(order.shipping_carrier, local_carrier)
        self.assertEqual(order.tracking_number, "456")
