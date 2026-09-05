from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from orders.models import PurchaseOrder
from orders.tests.base import (
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


class OrderCreateEditTests(TestCase):
    def setUp(self):
        self.program = make_program()
        self.lead = make_lead_mentor_user()
        self.student = make_student_user(program=self.program)
        self.student2 = make_student_user(username="student2", program=self.program)
        self.list_url = reverse("orders:order_list", args=[self.program.id])
        self.create_url = reverse("orders:order_create", args=[self.program.id])

    def login(self, user):
        self.client.force_login(user)

    def test_student_creates_order(self):
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
        order = PurchaseOrder.objects.get(item_name="Zip Ties")
        self.assertEqual(order.created_by, self.student)
        self.assertEqual(order.program, self.program)
        self.assertEqual(order.status, PurchaseOrder.STATUS_PENDING)

    def test_student_can_edit_own_pending_order(self):
        order = make_order(self.program, created_by=self.student)
        self.login(self.student)
        edit_url = reverse("orders:order_edit", args=[self.program.id, order.id])
        resp = self.client.post(edit_url, {"item_name": "Zip Ties v2", "quantity": "1"})
        self.assertRedirects(resp, self.list_url)
        order.refresh_from_db()
        self.assertEqual(order.item_name, "Zip Ties v2")

    def test_student_cannot_edit_someone_elses_order(self):
        order = make_order(self.program, created_by=self.student)
        self.login(self.student2)
        edit_url = reverse("orders:order_edit", args=[self.program.id, order.id])
        resp = self.client.post(edit_url, {"item_name": "Hijacked", "quantity": "1"})
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp.url, reverse("home"))
        order.refresh_from_db()
        self.assertEqual(order.item_name, "Hex Driver")

    def test_owner_cannot_edit_an_ordered_item(self):
        order = make_order(
            self.program,
            created_by=self.student,
            status=PurchaseOrder.STATUS_ORDERED,
        )
        self.login(self.student)
        edit_url = reverse("orders:order_edit", args=[self.program.id, order.id])
        resp = self.client.post(edit_url, {"item_name": "Nope", "quantity": "1"})
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp.url, reverse("home"))
        order.refresh_from_db()
        self.assertEqual(order.item_name, "Hex Driver")

    def test_lead_mentor_can_edit_any_order(self):
        order = make_order(self.program, created_by=self.student)
        self.login(self.lead)
        edit_url = reverse("orders:order_edit", args=[self.program.id, order.id])
        resp = self.client.post(edit_url, {"item_name": "Lead edited", "quantity": "3"})
        self.assertRedirects(resp, self.list_url)
        order.refresh_from_db()
        self.assertEqual(order.item_name, "Lead edited")


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
        self.assertTrue(PurchaseOrder.objects.filter(pk=order.pk).exists())

    def test_student_cannot_delete(self):
        order = make_order(self.program, created_by=self.student)
        self.login(self.student)
        delete_url = reverse("orders:order_delete", args=[self.program.id, order.id])
        resp = self.client.post(delete_url)
        self.assertRedirects(resp, self.list_url)
        self.assertTrue(PurchaseOrder.objects.filter(pk=order.pk).exists())

    def test_lead_mentor_can_delete(self):
        order = make_order(self.program, created_by=self.student)
        self.login(self.lead)
        delete_url = reverse("orders:order_delete", args=[self.program.id, order.id])
        resp = self.client.get(delete_url)
        self.assertEqual(resp.status_code, 200)
        resp = self.client.post(delete_url)
        self.assertRedirects(resp, self.list_url)
        self.assertFalse(PurchaseOrder.objects.filter(pk=order.pk).exists())


class OrderStatusTests(TestCase):
    def setUp(self):
        self.program = make_program()
        self.lead = make_lead_mentor_user()
        self.student = make_student_user(program=self.program)
        self.list_url = reverse("orders:order_list", args=[self.program.id])
        self.archive_url = reverse("orders:order_archive", args=[self.program.id])

    def login(self, user):
        self.client.force_login(user)

    def test_lead_marks_ordered_and_reopens(self):
        order = make_order(self.program, created_by=self.student)
        self.login(self.lead)

        mark_url = reverse(
            "orders:order_mark_ordered", args=[self.program.id, order.id]
        )
        resp = self.client.post(mark_url)
        self.assertRedirects(resp, self.list_url)
        order.refresh_from_db()
        self.assertEqual(order.status, PurchaseOrder.STATUS_ORDERED)
        self.assertIsNotNone(order.ordered_at)
        self.assertEqual(order.ordered_by, self.lead)

        reopen_url = reverse(
            "orders:order_mark_pending", args=[self.program.id, order.id]
        )
        resp = self.client.post(reopen_url)
        self.assertRedirects(resp, self.archive_url)
        order.refresh_from_db()
        self.assertEqual(order.status, PurchaseOrder.STATUS_PENDING)
        self.assertIsNone(order.ordered_at)

    def test_student_cannot_mark_ordered(self):
        order = make_order(self.program, created_by=self.student)
        self.login(self.student)
        mark_url = reverse(
            "orders:order_mark_ordered", args=[self.program.id, order.id]
        )
        resp = self.client.post(mark_url)
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp.url, reverse("home"))
        order.refresh_from_db()
        self.assertEqual(order.status, PurchaseOrder.STATUS_PENDING)

    def test_archive_lists_only_ordered(self):
        pending = make_order(
            self.program, item_name="Pending Item", created_by=self.student
        )
        ordered = make_order(
            self.program,
            item_name="Ordered Item",
            created_by=self.student,
            status=PurchaseOrder.STATUS_ORDERED,
        )
        self.login(self.lead)
        resp = self.client.get(self.archive_url)
        self.assertContains(resp, "Ordered Item")
        self.assertNotContains(resp, "Pending Item")
        pending.refresh_from_db()
        ordered.refresh_from_db()
        self.assertEqual(pending.status, PurchaseOrder.STATUS_PENDING)

    def test_list_shows_only_pending(self):
        make_order(self.program, item_name="Pending Item", created_by=self.student)
        make_order(
            self.program,
            item_name="Ordered Item",
            created_by=self.student,
            status=PurchaseOrder.STATUS_ORDERED,
        )
        self.login(self.student)
        resp = self.client.get(self.list_url)
        self.assertContains(resp, "Pending Item")
        self.assertNotContains(resp, "Ordered Item")


class OrderExportTests(TestCase):
    def setUp(self):
        self.program = make_program()
        self.lead = make_lead_mentor_user()
        self.student = make_student_user(program=self.program)
        self.list_url = reverse("orders:order_list", args=[self.program.id])

    def login(self, user):
        self.client.force_login(user)

    def test_lead_can_export_csv(self):
        make_order(self.program, item_name="Helix Gear", created_by=self.student)
        self.login(self.lead)
        resp = self.client.get(self.list_url, {"export": "csv"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp["Content-Type"], "text/csv")
        self.assertContains(resp, "Helix Gear")

    def test_lead_can_export_xlsx(self):
        make_order(self.program, item_name="Helix Gear", created_by=self.student)
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
    def test_total_and_normalized_quantity(self):
        program = make_program()
        student = make_student_user(program=program)
        order = make_order(
            program, created_by=student, quantity="2.00", unit_price="5.50"
        )
        self.assertEqual(order.total, Decimal("11.00"))
        self.assertEqual(str(order.quantity_normalized), "2")
        self.assertIn("Hex Driver", str(order))
        self.assertEqual(order.requested_by_name, "Test Student")

    def test_total_none_without_price(self):
        program = make_program()
        student = make_student_user(program=program)
        order = make_order(program, created_by=student, unit_price=None)
        self.assertIsNone(order.total)
        self.assertEqual(order.requested_by_name, "Test Student")
