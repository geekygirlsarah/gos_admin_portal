from django.test import TestCase
from django.urls import reverse

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


class OrderUIImprovementsTests(TestCase):
    def setUp(self):
        self.program = make_program()
        self.lead = make_lead_mentor_user()
        self.mentor = make_mentor_user()
        self.student = make_student_user(program=self.program)
        self.list_url = reverse("orders:order_list", args=[self.program.id])
        self.archive_url = reverse("orders:order_archive", args=[self.program.id])

    def login(self, user):
        self.client.force_login(user)

    def test_navigation_tabs_and_dropdowns_on_list_view_for_lead(self):
        self.login(self.lead)
        resp = self.client.get(self.list_url)
        self.assertEqual(resp.status_code, 200)

        # Nav tabs / pills
        self.assertContains(resp, "Active Requests &amp; Orders")
        self.assertContains(resp, "Order Archive")

        # Consolidated dropdowns for Lead Mentor
        self.assertContains(resp, "Export")
        self.assertContains(resp, "?export=csv")
        self.assertContains(resp, "?export=xlsx")
        self.assertContains(resp, "Manage")
        self.assertContains(resp, reverse("orders:vendor_list", args=[self.program.id]))
        self.assertContains(
            resp, reverse("orders:carrier_list", args=[self.program.id])
        )

    def test_navigation_tabs_and_dropdowns_on_archive_view_for_lead(self):
        self.login(self.lead)
        resp = self.client.get(self.archive_url)
        self.assertEqual(resp.status_code, 200)

        # Nav tabs / pills
        self.assertContains(resp, "Active Requests &amp; Orders")
        self.assertContains(resp, "Order Archive")

        # Consolidated dropdowns for Lead Mentor
        self.assertContains(resp, "Export")
        self.assertContains(resp, "?export=csv")
        self.assertContains(resp, "?export=xlsx")
        self.assertContains(resp, "Manage")
        self.assertContains(resp, reverse("orders:vendor_list", args=[self.program.id]))
        self.assertContains(
            resp, reverse("orders:carrier_list", args=[self.program.id])
        )

    def test_kpi_summary_cards_on_list_view(self):
        vendor = make_vendor(name="AndyMark")
        # 1 pool item
        make_item(
            self.program, item_name="Motor", unit_price=25, quantity=2, vendor=vendor
        )
        # 1 pending order
        make_order(
            self.program,
            vendor=vendor,
            created_by=self.mentor,
            status=Order.STATUS_PENDING,
        )
        # 1 shipped order
        make_order(
            self.program,
            vendor=vendor,
            created_by=self.mentor,
            status=Order.STATUS_SHIPPED,
        )

        self.login(self.lead)
        resp = self.client.get(self.list_url)
        self.assertEqual(resp.status_code, 200)

        self.assertContains(resp, "Pending Requests")
        self.assertContains(resp, "Orders in Progress")
        self.assertContains(resp, "In Transit")

    def test_requests_table_streamlined_columns(self):
        vendor = make_vendor(name="AndyMark")
        item = make_item(
            self.program,
            item_name="Spur Gear",
            unit_price=15,
            quantity=2,
            vendor=vendor,
            notes="Need by Friday",
            requested_by=self.student,
        )
        self.login(self.student)
        resp = self.client.get(self.list_url)
        self.assertEqual(resp.status_code, 200)

        self.assertContains(resp, "Spur Gear")
        self.assertContains(resp, "Need by Friday")
        self.assertContains(resp, "AndyMark")

    def test_order_detail_lifecycle_stepper_and_collapsible_add_items(self):
        vendor = make_vendor(name="REV")
        order = make_order(
            self.program,
            vendor=vendor,
            created_by=self.mentor,
            status=Order.STATUS_ORDERED,
        )
        make_item(self.program, item_name="Hub", order=order)
        # Unassigned item in pool
        make_item(self.program, item_name="Bracket")

        self.login(self.lead)
        detail_url = reverse("orders:order_detail", args=[self.program.id, order.id])
        resp = self.client.get(detail_url)
        self.assertEqual(resp.status_code, 200)

        # Lifecycle stepper stages
        self.assertContains(resp, "Ready to Order")
        self.assertContains(resp, "Ordered")
        self.assertContains(resp, "Shipped")
        self.assertContains(resp, "Received")

        # Collapsible add items section
        self.assertContains(resp, "addItemsCollapse")
        self.assertContains(resp, "Bracket")
