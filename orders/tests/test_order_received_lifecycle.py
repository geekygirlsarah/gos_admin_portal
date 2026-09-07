from django.test import TestCase
from django.urls import reverse

from orders.models import Order
from orders.tests.base import (
    make_item,
    make_lead_mentor_user,
    make_mentor_user,
    make_order,
    make_program,
    make_student_user,
)


class OrderReceivedLifecycleTests(TestCase):
    """An order should stay active after it's placed and shipped, and only be
    archived once it's received.

    Regression: marking an order as placed (ordered) used to archive it
    immediately, before shipping/receiving details had been recorded.
    """

    def setUp(self):
        self.program = make_program()
        self.lead = make_lead_mentor_user()
        self.mentor = make_mentor_user()
        self.list_url = reverse("orders:order_list", args=[self.program.id])
        self.archive_url = reverse("orders:order_archive", args=[self.program.id])

    def login(self, user):
        self.client.force_login(user)

    def test_ordered_order_stays_on_active_list_not_archived(self):
        """After 'mark as placed', the order remains on the active list (so
        shipping can be recorded) and is NOT yet in the archive."""
        self.login(self.lead)
        order = make_order(self.program, created_by=self.mentor)
        make_item(self.program, item_name="Placed Item", order=order)
        mark_url = reverse(
            "orders:order_mark_ordered", args=[self.program.id, order.id]
        )
        self.client.post(mark_url)
        order.refresh_from_db()
        self.assertEqual(order.status, Order.STATUS_ORDERED)
        # Still on the active list, not archived
        self.assertContains(self.client.get(self.list_url), "Placed Item")
        self.assertNotContains(self.client.get(self.archive_url), "Placed Item")

    def test_shipped_order_stays_on_active_list_not_archived(self):
        """After shipping, the order stays active until it's received."""
        self.login(self.lead)
        order = make_order(self.program, created_by=self.mentor)
        make_item(self.program, item_name="Shipped Item", order=order)
        order.status = Order.STATUS_ORDERED
        order.save(update_fields=["status"])
        ship_url = reverse(
            "orders:order_mark_shipped", args=[self.program.id, order.id]
        )
        self.client.post(ship_url)
        order.refresh_from_db()
        self.assertEqual(order.status, Order.STATUS_SHIPPED)
        self.assertContains(self.client.get(self.list_url), "Shipped Item")
        self.assertNotContains(self.client.get(self.archive_url), "Shipped Item")

    def test_receiving_an_order_moves_it_to_the_archive(self):
        """Only when an order is marked received does it appear in the archive."""
        self.login(self.lead)
        order = make_order(self.program, created_by=self.mentor)
        make_item(self.program, item_name="Received Item", order=order)
        order.status = Order.STATUS_SHIPPED
        order.save(update_fields=["status"])
        receive_url = reverse(
            "orders:order_mark_received", args=[self.program.id, order.id]
        )
        resp = self.client.post(receive_url)
        self.assertEqual(resp.status_code, 302)
        order.refresh_from_db()
        self.assertEqual(order.status, Order.STATUS_RECEIVED)
        self.assertNotContains(self.client.get(self.list_url), "Received Item")
        self.assertContains(self.client.get(self.archive_url), "Received Item")

    def test_archive_lists_only_received_orders(self):
        """The archive contains only received orders, not pending/ordered/shipped."""
        self.login(self.lead)
        names = ["Still Pending", "Still Ordered", "Still Shipped", "Finished Received"]
        orders = []
        for name in names:
            o = make_order(self.program, created_by=self.mentor)
            make_item(self.program, item_name=name, order=o)
            orders.append(o)
        orders[1].status = Order.STATUS_ORDERED
        orders[1].save(update_fields=["status"])
        orders[2].status = Order.STATUS_SHIPPED
        orders[2].save(update_fields=["status"])
        orders[3].status = Order.STATUS_RECEIVED
        orders[3].save(update_fields=["status"])
        resp = self.client.get(self.archive_url)
        self.assertContains(resp, "Finished Received")
        self.assertNotContains(resp, "Still Pending")
        self.assertNotContains(resp, "Still Ordered")
        self.assertNotContains(resp, "Still Shipped")

    def test_student_cannot_mark_received(self):
        self.login(self.lead)
        order = make_order(self.program, created_by=self.mentor)
        order.status = Order.STATUS_SHIPPED
        order.save(update_fields=["status"])
        student = make_student_user(program=self.program)
        self.login(student)
        receive_url = reverse(
            "orders:order_mark_received", args=[self.program.id, order.id]
        )
        resp = self.client.post(receive_url)
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp.url, reverse("home"))
        order.refresh_from_db()
        self.assertEqual(order.status, Order.STATUS_SHIPPED)
