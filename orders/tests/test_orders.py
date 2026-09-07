from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from orders.models import Order, OrderItem, Vendor
from orders.tests.base import (
    make_item,
    make_lead_mentor_user,
    make_mentor_user,
    make_order,
    make_parent_user,
    make_program,
    make_student_user,
)


class OrderAccessTests(TestCase):
    def setUp(self):
        self.feature_program = make_program(with_orders_feature=True)
        self.no_feature_program = make_program(
            name="No Orders Program", with_orders_feature=False
        )
        self.feature_url = reverse("orders:order_list", args=[self.feature_program.id])
        self.no_feature_url = reverse(
            "orders:order_list", args=[self.no_feature_program.id]
        )

    def login(self, user):
        self.client.force_login(user)

    def test_student_can_view_when_program_has_feature(self):
        student = make_student_user(program=self.feature_program)
        self.login(student)
        resp = self.client.get(self.feature_url)
        self.assertEqual(resp.status_code, 200)

    def test_student_blocked_when_program_lacks_feature(self):
        student = make_student_user(
            username="student2", program=self.no_feature_program
        )
        self.login(student)
        resp = self.client.get(self.no_feature_url)
        # Feature-gated mixin raises Http404 (same pattern as outreach); the
        # project's handler404 converts it into a redirect to home.
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp.url, reverse("home"))

    def test_mentor_always_allowed_without_feature(self):
        mentor = make_mentor_user()
        self.login(mentor)
        resp = self.client.get(self.no_feature_url)
        self.assertEqual(resp.status_code, 200)

    def test_lead_mentor_always_allowed_without_feature(self):
        lead = make_lead_mentor_user()
        self.login(lead)
        resp = self.client.get(self.no_feature_url)
        self.assertEqual(resp.status_code, 200)

    def test_parent_blocked(self):
        parent = make_parent_user()
        self.login(parent)
        resp = self.client.get(self.feature_url)
        # The orders permission section denies parents; the read-permission
        # mixin redirects denied users to home.
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp.url, reverse("home"))


class ItemCreateEditTests(TestCase):
    def setUp(self):
        self.program = make_program()
        self.lead = make_lead_mentor_user()
        self.student = make_student_user(program=self.program)
        self.student2 = make_student_user(username="student2", program=self.program)
        self.list_url = reverse("orders:order_list", args=[self.program.id])
        self.create_url = reverse("orders:item_create", args=[self.program.id])

    def login(self, user):
        self.client.force_login(user)

    def test_student_creates_item(self):
        self.login(self.student)
        resp = self.client.post(
            self.create_url,
            {
                "item_name": "Zip Ties",
                "quantity": "10",
                "unit_price": "0.50",
                "url": "https://example.com/zip",
                "notes": "small ones",
            },
        )
        self.assertRedirects(resp, self.list_url)
        item = OrderItem.objects.get(item_name="Zip Ties")
        self.assertEqual(item.requested_by, self.student)
        self.assertEqual(item.program, self.program)
        self.assertIsNone(item.order_id)

    def test_student_can_edit_own_unassigned_item(self):
        item = make_item(self.program, requested_by=self.student)
        self.login(self.student)
        edit_url = reverse("orders:item_edit", args=[self.program.id, item.id])
        resp = self.client.post(edit_url, {"item_name": "Zip Ties v2", "quantity": "1"})
        self.assertRedirects(resp, self.list_url)
        item.refresh_from_db()
        self.assertEqual(item.item_name, "Zip Ties v2")

    def test_student_cannot_edit_someone_elses_item(self):
        item = make_item(self.program, requested_by=self.student)
        self.login(self.student2)
        edit_url = reverse("orders:item_edit", args=[self.program.id, item.id])
        resp = self.client.post(edit_url, {"item_name": "Hijacked", "quantity": "1"})
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp.url, reverse("home"))
        item.refresh_from_db()
        self.assertEqual(item.item_name, "Hex Driver")

    def test_owner_cannot_edit_item_once_grouped_into_an_order(self):
        order = make_order(self.program, created_by=self.lead)
        item = make_item(self.program, requested_by=self.student, order=order)
        self.login(self.student)
        edit_url = reverse("orders:item_edit", args=[self.program.id, item.id])
        resp = self.client.post(edit_url, {"item_name": "Nope", "quantity": "1"})
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp.url, reverse("home"))
        item.refresh_from_db()
        self.assertEqual(item.item_name, "Hex Driver")

    def test_lead_can_edit_any_item(self):
        item = make_item(self.program, requested_by=self.student)
        self.login(self.lead)
        edit_url = reverse("orders:item_edit", args=[self.program.id, item.id])
        resp = self.client.post(edit_url, {"item_name": "Lead edited", "quantity": "3"})
        self.assertRedirects(resp, self.list_url)
        item.refresh_from_db()
        self.assertEqual(item.item_name, "Lead edited")


class OrderGroupingTests(TestCase):
    def setUp(self):
        self.program = make_program()
        self.lead = make_lead_mentor_user()
        self.student = make_student_user(program=self.program)
        self.mentor = make_mentor_user()
        self.mentor2 = make_mentor_user(username="mentor2")
        self.vendor = Vendor.objects.create(name="Test Vendor")
        self.create_url = reverse("orders:order_create", args=[self.program.id])
        self.list_url = reverse("orders:order_list", args=[self.program.id])

    def login(self, user):
        self.client.force_login(user)

    def _item_data(self, items, **overrides):
        data = {
            "program": str(self.program.pk),
            "items": [str(i.pk) for i in items],
            "vendor_choice": str(self.vendor.pk),
            "notes": "",
            "item_name": "",
        }
        data.update(overrides)
        return data

    def test_student_cannot_create_order(self):
        self.login(self.student)
        resp = self.client.get(self.create_url)
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp.url, reverse("home"))

    def test_mentor_creates_order_with_items(self):
        item1 = make_item(self.program, requested_by=self.student)
        item2 = make_item(self.program, item_name="Zip Ties", requested_by=self.student)
        self.login(self.mentor)
        resp = self.client.post(self.create_url, self._item_data([item1, item2]))
        self.assertEqual(resp.status_code, 302)
        order = Order.objects.get()
        self.assertEqual(order.created_by, self.mentor)
        self.assertEqual(order.program, self.program)
        self.assertEqual(order.status, Order.STATUS_PENDING)
        item1.refresh_from_db()
        item2.refresh_from_db()
        self.assertEqual(item1.order, order)
        self.assertEqual(item2.order, order)

    def test_create_order_requires_at_least_one_item(self):
        self.login(self.mentor)
        resp = self.client.post(
            self.create_url,
            {
                "program": str(self.program.pk),
                "items": [],
                "vendor_choice": "",
                "notes": "",
            },
        )
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(Order.objects.exists())

    def test_mentor_cannot_edit_someone_elses_order(self):
        order = make_order(self.program, created_by=self.mentor)
        self.login(self.mentor2)
        edit_url = reverse("orders:order_edit", args=[self.program.id, order.id])
        resp = self.client.post(edit_url, {"vendor_choice": "", "notes": "hacked"})
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp.url, reverse("home"))
        order.refresh_from_db()
        self.assertEqual(order.notes, "")

    def test_lead_can_edit_any_order(self):
        order = make_order(self.program, created_by=self.mentor)
        self.login(self.lead)
        edit_url = reverse("orders:order_edit", args=[self.program.id, order.id])
        resp = self.client.post(
            edit_url,
            {"vendor_choice": str(self.vendor.pk), "notes": "Lead updated"},
        )
        self.assertRedirects(
            resp, reverse("orders:order_detail", args=[self.program.id, order.id])
        )
        order.refresh_from_db()
        self.assertEqual(order.notes, "Lead updated")

    def test_add_items_to_order(self):
        order = make_order(self.program, created_by=self.mentor)
        item = make_item(self.program, requested_by=self.student)
        self.login(self.mentor)

        add_url = reverse("orders:order_add_items", args=[self.program.id, order.id])
        resp = self.client.post(add_url, {"items": [str(item.pk)]})
        self.assertEqual(resp.status_code, 302)
        item.refresh_from_db()
        self.assertEqual(item.order, order)

    def test_student_cannot_add_items_to_order(self):
        order = make_order(self.program, created_by=self.mentor)
        item = make_item(self.program, requested_by=self.student)
        self.login(self.student)
        add_url = reverse("orders:order_add_items", args=[self.program.id, order.id])
        resp = self.client.post(add_url, {"items": [str(item.pk)]})
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp.url, reverse("home"))
        item.refresh_from_db()
        self.assertIsNone(item.order_id)

    def test_remove_item_from_order_moves_back_to_pool(self):
        order = make_order(self.program, created_by=self.mentor)
        item = make_item(self.program, requested_by=self.student, order=order)
        self.login(self.mentor)
        remove_url = reverse(
            "orders:order_remove_item",
            args=[self.program.id, order.id, item.id],
        )
        resp = self.client.post(remove_url)
        self.assertEqual(resp.status_code, 302)
        item.refresh_from_db()
        self.assertIsNone(item.order_id)

    def test_student_cannot_access_order_detail(self):
        order = make_order(self.program, created_by=self.mentor)
        self.login(self.student)
        detail_url = reverse("orders:order_detail", args=[self.program.id, order.id])
        resp = self.client.get(detail_url)
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp.url, reverse("home"))

    def test_mentor_can_view_order_detail(self):
        order = make_order(self.program, created_by=self.mentor)
        make_item(self.program, requested_by=self.student, order=order)
        self.login(self.mentor)
        detail_url = reverse("orders:order_detail", args=[self.program.id, order.id])
        resp = self.client.get(detail_url)
        self.assertEqual(resp.status_code, 200)


class OrderDeleteTests(TestCase):
    def setUp(self):
        self.program = make_program()
        self.lead = make_lead_mentor_user()
        self.student = make_student_user(program=self.program)
        self.list_url = reverse("orders:order_list", args=[self.program.id])

    def login(self, user):
        self.client.force_login(user)

    def test_delete_requires_login(self):
        order = make_order(self.program, created_by=self.student)
        self.client.logout()
        delete_url = reverse("orders:order_delete", args=[self.program.id, order.id])
        resp = self.client.get(delete_url)
        self.assertIn(resp.status_code, (302,))
        self.assertTrue(Order.objects.filter(pk=order.pk).exists())

    def test_deleting_order_returns_items_to_pool(self):
        order = make_order(self.program, created_by=self.lead)
        item = make_item(self.program, requested_by=self.student, order=order)
        self.login(self.lead)
        delete_url = reverse("orders:order_delete", args=[self.program.id, order.id])
        resp = self.client.post(delete_url)
        self.assertRedirects(resp, self.list_url)
        self.assertFalse(Order.objects.filter(pk=order.pk).exists())
        item.refresh_from_db()
        self.assertIsNone(item.order_id)

    def test_delete_item_requires_lead(self):
        item = make_item(self.program, requested_by=self.student)
        self.login(self.student)
        delete_url = reverse("orders:item_delete", args=[self.program.id, item.id])
        resp = self.client.post(delete_url)
        self.assertRedirects(resp, self.list_url)
        self.assertTrue(OrderItem.objects.filter(pk=item.pk).exists())

    def test_lead_can_delete_item(self):
        item = make_item(self.program, requested_by=self.student)
        self.login(self.lead)
        delete_url = reverse("orders:item_delete", args=[self.program.id, item.id])
        resp = self.client.get(delete_url)
        self.assertEqual(resp.status_code, 200)
        resp = self.client.post(delete_url)
        self.assertRedirects(resp, self.list_url)
        self.assertFalse(OrderItem.objects.filter(pk=item.pk).exists())


class OrderStatusTests(TestCase):
    def setUp(self):
        self.program = make_program()
        self.lead = make_lead_mentor_user()
        self.student = make_student_user(program=self.program)
        self.mentor = make_mentor_user()
        self.list_url = reverse("orders:order_list", args=[self.program.id])
        self.archive_url = reverse("orders:order_archive", args=[self.program.id])

    def login(self, user):
        self.client.force_login(user)

    def test_lead_marks_ordered_and_reopens(self):
        order = make_order(self.program, created_by=self.mentor)
        self.login(self.lead)

        mark_url = reverse(
            "orders:order_mark_ordered", args=[self.program.id, order.id]
        )
        resp = self.client.post(mark_url)
        self.assertEqual(resp.status_code, 302)
        order.refresh_from_db()
        self.assertEqual(order.status, Order.STATUS_ORDERED)
        self.assertIsNotNone(order.ordered_at)
        self.assertEqual(order.ordered_by, self.lead)

        reopen_url = reverse(
            "orders:order_mark_pending", args=[self.program.id, order.id]
        )
        resp = self.client.post(reopen_url)
        self.assertEqual(resp.status_code, 302)
        order.refresh_from_db()
        self.assertEqual(order.status, Order.STATUS_PENDING)
        self.assertIsNone(order.ordered_at)

    def test_student_cannot_mark_ordered(self):
        order = make_order(self.program, created_by=self.mentor)
        self.login(self.student)
        mark_url = reverse(
            "orders:order_mark_ordered", args=[self.program.id, order.id]
        )
        resp = self.client.post(mark_url)
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp.url, reverse("home"))
        order.refresh_from_db()
        self.assertEqual(order.status, Order.STATUS_PENDING)

    def test_archive_lists_only_ordered_orders(self):
        pending = make_order(self.program, created_by=self.mentor)
        make_item(
            self.program,
            item_name="Pending Item",
            requested_by=self.student,
            order=pending,
        )
        ordered = make_order(self.program, created_by=self.mentor)
        make_item(
            self.program,
            item_name="Ordered Item",
            requested_by=self.student,
            order=ordered,
        )
        ordered.status = Order.STATUS_ORDERED
        ordered.save(update_fields=["status"])
        self.login(self.lead)
        resp = self.client.get(self.archive_url)
        self.assertContains(resp, "Ordered Item")
        self.assertNotContains(resp, "Pending Item")

    def test_list_shows_pool_items_and_pending_orders_only(self):
        make_item(self.program, item_name="Pool Item", requested_by=self.student)
        pending = make_order(self.program, created_by=self.mentor)
        make_item(
            self.program,
            item_name="Pending Grouped Item",
            requested_by=self.student,
            order=pending,
        )
        ordered = make_order(self.program, created_by=self.mentor)
        make_item(
            self.program,
            item_name="Ordered Item",
            requested_by=self.student,
            order=ordered,
        )
        ordered.status = Order.STATUS_ORDERED
        ordered.save(update_fields=["status"])
        self.login(self.student)
        resp = self.client.get(self.list_url)
        self.assertContains(resp, "Pool Item")
        self.assertContains(resp, "Pending Grouped Item")
        self.assertNotContains(resp, "Ordered Item")


class OrderExportTests(TestCase):
    def setUp(self):
        self.program = make_program()
        self.lead = make_lead_mentor_user()
        self.student = make_student_user(program=self.program)
        self.mentor = make_mentor_user()
        self.list_url = reverse("orders:order_list", args=[self.program.id])

    def login(self, user):
        self.client.force_login(user)

    def test_lead_can_export_csv(self):
        make_item(self.program, item_name="Helix Gear", requested_by=self.student)
        self.login(self.lead)
        resp = self.client.get(self.list_url, {"export": "csv"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp["Content-Type"], "text/csv")
        self.assertContains(resp, "Helix Gear")

    def test_lead_can_export_xlsx(self):
        make_item(self.program, item_name="Helix Gear", requested_by=self.student)
        self.login(self.lead)
        resp = self.client.get(self.list_url, {"export": "xlsx"})
        self.assertEqual(resp.status_code, 200)
        self.assertIn(
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            resp["Content-Type"],
        )

    def test_student_cannot_export(self):
        self.login(self.student)
        resp = self.client.get(self.list_url, {"export": "csv"})
        self.assertRedirects(resp, self.list_url)


class OrderModelTests(TestCase):
    def test_item_total_and_normalized_quantity(self):
        program = make_program()
        student = make_student_user(program=program)
        item = make_item(
            program, requested_by=student, quantity="2.00", unit_price="5.50"
        )
        self.assertEqual(item.total, Decimal("11.00"))
        self.assertEqual(str(item.quantity_normalized), "2")
        self.assertIn("Hex Driver", str(item))
        self.assertEqual(item.requested_by_name, "Test Student")

    def test_item_total_none_without_price(self):
        program = make_program()
        student = make_student_user(program=program)
        item = make_item(program, requested_by=student, unit_price=None)
        self.assertIsNone(item.total)
        self.assertEqual(item.requested_by_name, "Test Student")

    def test_order_totals_and_item_count(self):
        program = make_program()
        student = make_student_user(program=program)
        order = make_order(program, created_by=student)
        make_item(
            program, requested_by=student, order=order, quantity="2", unit_price="5.50"
        )
        make_item(
            program,
            item_name="Zip Ties",
            requested_by=student,
            order=order,
            quantity="10",
            unit_price="0.50",
        )
        self.assertEqual(order.item_count, 2)
        self.assertEqual(order.total, Decimal("16.00"))

    def test_order_total_none_without_priced_items(self):
        program = make_program()
        student = make_student_user(program=program)
        order = make_order(program, created_by=student)
        make_item(program, requested_by=student, order=order, unit_price=None)
        self.assertEqual(order.item_count, 1)
        self.assertIsNone(order.total)
