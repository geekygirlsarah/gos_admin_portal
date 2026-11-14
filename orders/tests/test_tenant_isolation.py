from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db import connection
from django.db.models import Prefetch
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from audit.events import AuditEvent
from audit.models import AuditLog
from orders.forms import OrderItemForm
from orders.models import Order, OrderItem, ShippingCarrier
from organizations.models import Organization, UserOrganizationMembership
from programs.models import Group, Program, ProgramFeature


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

    def test_group_selection_is_scoped_for_new_and_existing_items(self):
        other_alpha_program = Program.objects.create(
            organization=self.alpha, name="Other Alpha Program", active=True
        )
        alpha_group = Group.objects.create(
            organization=self.alpha,
            program=self.alpha_program,
            name="Alpha Group",
        )
        other_program_group = Group.objects.create(
            organization=self.alpha,
            program=other_alpha_program,
            name="Different Program Group",
        )
        beta_group = Group.objects.create(
            organization=self.beta,
            program=self.beta_program,
            name="Beta Group",
        )
        forged_group_ids = [str(other_program_group.pk), str(beta_group.pk)]
        form_data = {
            "item_name": "Alpha item",
            "quantity": "1",
            "groups": forged_group_ids,
            "vendor_choice": "",
        }

        new_item_form = OrderItemForm(
            data=form_data,
            program=self.alpha_program,
            organization=self.alpha,
        )
        self.assertIn(alpha_group, new_item_form.fields["groups"].queryset)
        self.assertNotIn(other_program_group, new_item_form.fields["groups"].queryset)
        self.assertNotIn(beta_group, new_item_form.fields["groups"].queryset)
        self.assertFalse(new_item_form.is_valid())
        self.assertIn("groups", new_item_form.errors)

        existing_item = OrderItem.objects.create(
            organization=self.alpha,
            program=self.alpha_program,
            item_name="Existing alpha item",
            quantity=1,
        )
        existing_item.groups.add(alpha_group)
        edit_form = OrderItemForm(
            data=form_data,
            instance=existing_item,
            program=existing_item.program,
            organization=self.alpha,
        )
        self.assertIn(alpha_group, edit_form.fields["groups"].queryset)
        self.assertFalse(edit_form.is_valid())
        self.assertIn("groups", edit_form.errors)

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

    def test_delete_is_blocked_without_changing_foreign_items_or_audit_history(self):
        order = Order.objects.create(
            organization=self.alpha,
            program=self.alpha_program,
            created_by=self.user,
            vendor_name="Alpha Vendor",
        )
        local_item = OrderItem.objects.create(
            organization=self.alpha,
            program=self.alpha_program,
            order=order,
            item_name="Alpha Item",
            unit_price=Decimal("4.00"),
        )
        foreign_item = OrderItem.objects.create(
            organization=self.beta,
            program=self.beta_program,
            order=order,
            item_name="Beta Private Item",
            unit_price=Decimal("90.00"),
        )
        delete_url = reverse(
            "orders:order_delete", args=[self.alpha_program.pk, order.pk]
        )
        detail_url = reverse(
            "orders:order_detail", args=[self.alpha_program.pk, order.pk]
        )
        deletion_audit = AuditLog.objects.filter(
            event=AuditEvent.RECORD_DELETED,
            resource_type="Order",
            resource_id=str(order.pk),
        )
        audit_count = deletion_audit.count()

        response = self.client.post(delete_url, HTTP_HOST=self.alpha_host)

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, detail_url)
        self.assertTrue(Order.objects.filter(pk=order.pk).exists())
        local_item.refresh_from_db()
        foreign_item.refresh_from_db()
        self.assertEqual(local_item.order_id, order.pk)
        self.assertEqual(foreign_item.order_id, order.pk)
        self.assertEqual(deletion_audit.count(), audit_count)

        detail = self.tenant_get(detail_url)
        self.assertContains(
            detail, "This order cannot be deleted because related records need review."
        )
        self.assertNotContains(detail, "Beta Private Item")

    def test_local_order_delete_returns_items_and_records_audit(self):
        order = Order.objects.create(
            organization=self.alpha,
            program=self.alpha_program,
            created_by=self.user,
        )
        item = OrderItem.objects.create(
            organization=self.alpha,
            program=self.alpha_program,
            order=order,
            item_name="Alpha Item",
            unit_price=Decimal("4.00"),
        )

        response = self.client.post(
            reverse("orders:order_delete", args=[self.alpha_program.pk, order.pk]),
            HTTP_HOST=self.alpha_host,
        )

        self.assertEqual(response.status_code, 302)
        self.assertFalse(Order.objects.filter(pk=order.pk).exists())
        item.refresh_from_db()
        self.assertIsNone(item.order_id)
        self.assertTrue(
            AuditLog.objects.filter(
                event=AuditEvent.RECORD_DELETED,
                resource_type="Order",
                resource_id=str(order.pk),
            ).exists()
        )

    def test_active_order_page_scopes_items_totals_and_carrier_tracking(self):
        foreign_carrier = ShippingCarrier.objects.create(
            organization=self.beta,
            name="Beta Private Carrier",
            tracking_url_template="https://beta.example/track/{number}",
        )
        local_carrier = ShippingCarrier.objects.create(
            organization=self.alpha,
            name="Alpha Local Carrier",
            tracking_url_template="https://alpha.example/track/{number}",
        )
        order = Order.objects.create(
            organization=self.alpha,
            program=self.alpha_program,
            status=Order.STATUS_SHIPPED,
            created_by=self.user,
            vendor_name="Alpha Vendor",
            shipping_carrier=foreign_carrier,
            tracking_number="BETA-TRACK-SECRET",
        )
        OrderItem.objects.create(
            organization=self.alpha,
            program=self.alpha_program,
            order=order,
            item_name="Alpha Local Item",
            quantity=2,
            unit_price=Decimal("3.00"),
        )
        OrderItem.objects.create(
            organization=self.beta,
            program=self.beta_program,
            order=order,
            item_name="Beta Private Item",
            quantity=7,
            unit_price=Decimal("111.00"),
            url="https://beta.example/private-item",
        )

        local_order = Order.objects.create(
            organization=self.alpha,
            program=self.alpha_program,
            status=Order.STATUS_SHIPPED,
            created_by=self.user,
            vendor_name="Alpha Local Vendor",
            shipping_carrier=local_carrier,
            tracking_number="ALPHA-TRACK-VALID",
        )
        OrderItem.objects.create(
            organization=self.alpha,
            program=self.alpha_program,
            order=local_order,
            item_name="Alpha Valid Item",
            quantity=1,
            unit_price=Decimal("2.00"),
        )

        response = self.tenant_get(
            reverse("orders:order_list", args=[self.alpha_program.pk])
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Alpha Local Item")
        self.assertContains(response, "Alpha Valid Item")
        self.assertNotContains(response, "Beta Private Item")
        self.assertNotContains(response, "Beta Private Carrier")
        self.assertNotContains(response, "https://beta.example/track/BETA-TRACK-SECRET")
        self.assertContains(response, "Alpha Local Carrier")
        self.assertContains(response, "https://alpha.example/track/ALPHA-TRACK-VALID")
        self.assertEqual(response.context["order_groups_total"], Decimal("8.00"))
        self.assertEqual(order.total, Decimal("6.00"))
        self.assertEqual(order.item_count, 1)

    def test_archive_detail_and_confirmation_hide_mismatched_items_and_carriers(self):
        foreign_carrier = ShippingCarrier.objects.create(
            organization=self.beta,
            name="Beta Archive Carrier",
            tracking_url_template="https://beta.example/archive/{number}",
        )
        order = Order.objects.create(
            organization=self.alpha,
            program=self.alpha_program,
            status=Order.STATUS_RECEIVED,
            created_by=self.user,
            vendor_name="Alpha Archive Vendor",
            shipping_carrier=foreign_carrier,
            tracking_number="BETA-ARCHIVE-SECRET",
        )
        OrderItem.objects.create(
            organization=self.alpha,
            program=self.alpha_program,
            order=order,
            item_name="Alpha Archived Item",
            quantity=1,
            unit_price=Decimal("4.00"),
        )
        OrderItem.objects.create(
            organization=self.beta,
            program=self.beta_program,
            order=order,
            item_name="Beta Archived Private Item",
            quantity=3,
            unit_price=Decimal("80.00"),
        )

        archive = self.tenant_get(
            reverse("orders:order_archive", args=[self.alpha_program.pk])
        )

        self.assertEqual(archive.status_code, 200)
        self.assertContains(archive, "Alpha Archived Item")
        self.assertNotContains(archive, "Beta Archived Private Item")
        self.assertNotContains(archive, "Beta Archive Carrier")
        self.assertNotContains(
            archive, "https://beta.example/archive/BETA-ARCHIVE-SECRET"
        )
        self.assertEqual(archive.context["archive_total"], Decimal("4.00"))

        detail = self.tenant_get(
            reverse("orders:order_detail", args=[self.alpha_program.pk, order.pk])
        )
        self.assertEqual(detail.status_code, 200)
        self.assertContains(detail, "Alpha Archived Item")
        self.assertNotContains(detail, "Beta Archived Private Item")
        self.assertNotContains(detail, "Beta Archive Carrier")
        self.assertEqual(len(detail.context["items"]), 1)
        self.assertEqual(detail.context["order"].total, Decimal("4.00"))
        self.assertEqual(detail.context["order"].item_count, 1)

        confirmation = self.tenant_get(
            reverse("orders:order_delete", args=[self.alpha_program.pk, order.pk])
        )
        self.assertEqual(confirmation.status_code, 200)
        self.assertContains(confirmation, "Its 1 item")
        self.assertNotContains(confirmation, "Its 2 items")

    def test_active_and_archive_exports_exclude_local_items_linked_to_foreign_orders(
        self,
    ):
        active_foreign_order = Order.objects.create(
            organization=self.beta,
            program=self.beta_program,
            status=Order.STATUS_ORDERED,
            created_by=self.user,
            vendor_name="Beta Active Vendor Secret",
            vendor_url="https://beta.example/active-vendor",
        )
        active_mismatch = OrderItem.objects.create(
            organization=self.alpha,
            program=self.alpha_program,
            order=active_foreign_order,
            item_name="Alpha Item Linked to Foreign Active Order",
            unit_price=Decimal("5.00"),
        )
        local_active_order = Order.objects.create(
            organization=self.alpha,
            program=self.alpha_program,
            status=Order.STATUS_ORDERED,
            created_by=self.user,
        )
        foreign_active_item = OrderItem.objects.create(
            organization=self.beta,
            program=self.beta_program,
            order=local_active_order,
            item_name="Beta Item Linked to Local Active Order",
            unit_price=Decimal("50.00"),
        )
        pool_item = OrderItem.objects.create(
            organization=self.alpha,
            program=self.alpha_program,
            item_name="Alpha Pool Item",
            unit_price=Decimal("1.00"),
        )
        archived_foreign_order = Order.objects.create(
            organization=self.beta,
            program=self.beta_program,
            status=Order.STATUS_RECEIVED,
            created_by=self.user,
            vendor_name="Beta Archive Vendor Secret",
            vendor_url="https://beta.example/archive-vendor",
        )
        archived_mismatch = OrderItem.objects.create(
            organization=self.alpha,
            program=self.alpha_program,
            order=archived_foreign_order,
            item_name="Alpha Item Linked to Foreign Archived Order",
            unit_price=Decimal("9.00"),
        )
        local_archived_order = Order.objects.create(
            organization=self.alpha,
            program=self.alpha_program,
            status=Order.STATUS_RECEIVED,
            created_by=self.user,
        )
        foreign_archived_item = OrderItem.objects.create(
            organization=self.beta,
            program=self.beta_program,
            order=local_archived_order,
            item_name="Beta Item Linked to Local Archived Order",
            unit_price=Decimal("90.00"),
        )

        active_export = self.tenant_get(
            reverse("orders:order_list", args=[self.alpha_program.pk]) + "?export=csv"
        )
        archive_export = self.tenant_get(
            reverse("orders:order_archive", args=[self.alpha_program.pk])
            + "?export=csv"
        )

        self.assertEqual(active_export.status_code, 200)
        self.assertIn(b"Alpha Pool Item", active_export.content)
        self.assertNotIn(active_mismatch.item_name.encode(), active_export.content)
        self.assertNotIn(foreign_active_item.item_name.encode(), active_export.content)
        self.assertNotIn(b"Beta Active Vendor Secret", active_export.content)
        self.assertNotIn(b"https://beta.example/active-vendor", active_export.content)
        self.assertEqual(archive_export.status_code, 200)
        self.assertNotIn(archived_mismatch.item_name.encode(), archive_export.content)
        self.assertNotIn(
            foreign_archived_item.item_name.encode(), archive_export.content
        )
        self.assertNotIn(b"Beta Archive Vendor Secret", archive_export.content)
        self.assertNotIn(b"https://beta.example/archive-vendor", archive_export.content)

    def test_prefetched_order_totals_and_counts_do_not_query_per_order(self):
        order = Order.objects.create(
            organization=self.alpha,
            program=self.alpha_program,
            created_by=self.user,
        )
        OrderItem.objects.create(
            organization=self.alpha,
            program=self.alpha_program,
            order=order,
            item_name="Alpha Item",
            unit_price=Decimal("3.00"),
        )
        OrderItem.objects.create(
            organization=self.beta,
            program=self.beta_program,
            order=order,
            item_name="Beta Item",
            unit_price=Decimal("100.00"),
        )

        with CaptureQueriesContext(connection) as captured:
            orders = list(
                Order.objects.filter(pk=order.pk).prefetch_related(
                    Prefetch("items", queryset=OrderItem.objects.all())
                )
            )
            values = [(item.item_count, item.total) for item in orders]

        item_queries = [
            query
            for query in captured.captured_queries
            if "orders_orderitem" in query["sql"].lower()
        ]
        self.assertEqual(len(item_queries), 1)
        self.assertEqual(values, [(1, Decimal("3.00"))])
        self.assertEqual(
            [item.item_name for item in orders[0].organization_items], ["Alpha Item"]
        )
