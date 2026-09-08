"""Tests for the ``Vendor`` reference list and vendor selection on orders.

Vendors are an org-wide reference list that Lead Mentors manage. When a mentor
or Lead Mentor groups requested items into an ``Order``, they pick a vendor
from a dropdown or choose "Not listed" to type their own. Each ``Order``
stores a snapshot of the vendor name/website (``vendor_name``/``vendor_url``)
so the order text survives even if the linked ``Vendor`` row is later deleted.
"""

from django.test import TestCase
from django.urls import reverse

from orders.forms import NOT_LISTED_VENDOR, OrderForm
from orders.models import Order, Vendor
from orders.tests.base import (
    make_item,
    make_lead_mentor_user,
    make_mentor_user,
    make_order,
    make_program,
    make_student_user,
    make_vendor,
)


class VendorModelTests(TestCase):
    def test_str_returns_name(self):
        vendor = make_vendor()
        self.assertEqual(str(vendor), "McMaster-Carr")

    def test_orders_ordered_by_name(self):
        Vendor.objects.create(name="Zebra Parts")
        Vendor.objects.create(name="Alpha Supplies")
        names = list(Vendor.objects.values_list("name", flat=True))
        self.assertEqual(names, ["Alpha Supplies", "Zebra Parts"])


class VendorAccessTests(TestCase):
    def setUp(self):
        self.program = make_program()
        self.lead = make_lead_mentor_user()
        self.mentor = make_mentor_user()
        self.student = make_student_user(program=self.program)
        self.list_url = reverse("orders:vendor_list", args=[self.program.id])

    def login(self, user):
        self.client.force_login(user)

    def test_lead_can_list_vendors(self):
        make_vendor()
        self.login(self.lead)
        resp = self.client.get(self.list_url)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "McMaster-Carr")

    def test_mentor_blocked(self):
        make_vendor()
        self.login(self.mentor)
        resp = self.client.get(self.list_url)
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp.url, reverse("home"))

    def test_student_blocked(self):
        make_vendor()
        self.login(self.student)
        resp = self.client.get(self.list_url)
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp.url, reverse("home"))


class VendorManageTests(TestCase):
    def setUp(self):
        self.program = make_program()
        self.lead = make_lead_mentor_user()
        self.list_url = reverse("orders:vendor_list", args=[self.program.id])
        self.create_url = reverse("orders:vendor_create", args=[self.program.id])

    def login(self, user):
        self.client.force_login(user)

    def test_lead_creates_vendor(self):
        self.login(self.lead)
        resp = self.client.post(
            self.create_url,
            {
                "name": "Digi-Key",
                "website": "https://www.digikey.com",
                "contact_email": "sales@digikey.com",
                "contact_phone": "800-344-4539",
                "notes": "Electronics",
            },
        )
        self.assertRedirects(resp, self.list_url)
        vendor = Vendor.objects.get(name="Digi-Key")
        self.assertEqual(vendor.website, "https://www.digikey.com")
        self.assertEqual(vendor.contact_email, "sales@digikey.com")

    def test_lead_edits_vendor(self):
        vendor = make_vendor()
        self.login(self.lead)
        edit_url = reverse("orders:vendor_edit", args=[self.program.id, vendor.id])
        resp = self.client.post(
            edit_url,
            {
                "name": "McMaster-Carr",
                "website": "https://www.mcmaster.com",
                "notes": "Updated",
            },
        )
        self.assertRedirects(resp, self.list_url)
        vendor.refresh_from_db()
        self.assertEqual(vendor.website, "https://www.mcmaster.com")
        self.assertEqual(vendor.notes, "Updated")

    def test_lead_deletes_vendor(self):
        vendor = make_vendor()
        self.login(self.lead)
        delete_url = reverse("orders:vendor_delete", args=[self.program.id, vendor.id])
        resp = self.client.get(delete_url)
        self.assertEqual(resp.status_code, 200)
        resp = self.client.post(delete_url)
        self.assertRedirects(resp, self.list_url)
        self.assertFalse(Vendor.objects.filter(pk=vendor.pk).exists())

    def test_deleting_vendor_keeps_order_snapshots(self):
        vendor = make_vendor()
        order = make_order(
            self.program,
            vendor=vendor,
            vendor_name=vendor.name,
            vendor_url=vendor.website,
        )
        vendor.delete()
        order.refresh_from_db()
        self.assertIsNone(order.vendor_id)
        self.assertEqual(order.vendor_name, "McMaster-Carr")
        self.assertEqual(order.vendor_url, vendor.website)


class OrderFormVendorTests(TestCase):
    def _item(self):
        return make_item(make_program(), item_name="Hex Driver")

    def _base_data(self, item, **overrides):
        data = {
            "items": [str(item.pk)],
            "vendor_choice": "",
            "vendor_name": "",
            "vendor_url": "",
        }
        data.update(overrides)
        return data

    def test_selecting_vendor_sets_fk_and_snapshot(self):
        vendor = make_vendor()
        item = self._item()
        form = OrderForm(
            data=self._base_data(item, vendor_choice=str(vendor.pk)),
        )
        self.assertTrue(form.is_valid(), form.errors)
        order = form.save()
        self.assertEqual(order.vendor, vendor)
        self.assertEqual(order.vendor_name, vendor.name)
        self.assertEqual(order.vendor_url, vendor.website)
        item.refresh_from_db()
        self.assertEqual(item.order, order)

    def test_vendor_name_is_not_required_when_picking_listed_vendor(self):
        vendor = make_vendor()
        item = self._item()
        form = OrderForm(
            data=self._base_data(item, vendor_choice=str(vendor.pk), vendor_name=""),
        )
        self.assertTrue(form.is_valid(), form.errors)

    def test_not_listed_saves_custom_name_and_url(self):
        item = self._item()
        form = OrderForm(
            data=self._base_data(
                item,
                vendor_choice=NOT_LISTED_VENDOR,
                vendor_name="Jane's Robot Shop",
                vendor_url="https://janesrobots.example.com",
            ),
        )
        self.assertTrue(form.is_valid(), form.errors)
        order = form.save()
        self.assertIsNone(order.vendor_id)
        self.assertEqual(order.vendor_name, "Jane's Robot Shop")
        self.assertEqual(order.vendor_url, "https://janesrobots.example.com")

    def test_not_listed_without_name_is_valid(self):
        item = self._item()
        form = OrderForm(
            data=self._base_data(item, vendor_choice=NOT_LISTED_VENDOR, vendor_name=""),
        )
        self.assertTrue(form.is_valid(), form.errors)
        order = form.save()
        self.assertIsNone(order.vendor_id)
        self.assertEqual(order.vendor_name, "")

    def test_vendor_choice_is_required(self):
        item = self._item()
        form = OrderForm(data=self._base_data(item, vendor_choice=""))
        self.assertFalse(form.is_valid())
        self.assertIn("vendor_choice", form.errors)

    def test_blank_vendor_choice_on_edit_is_rejected(self):
        vendor = make_vendor()
        program = make_program()
        order = make_order(
            program,
            vendor=vendor,
            vendor_name=vendor.name,
            vendor_url=vendor.website,
        )
        form = OrderForm(
            instance=order,
            data={"vendor_choice": "", "notes": ""},
        )
        self.assertFalse(form.is_valid())
        self.assertIn("vendor_choice", form.errors)
        order.refresh_from_db()
        self.assertEqual(order.vendor, vendor)

    def test_dropdown_includes_vendors_and_not_listed(self):
        vendor = make_vendor(name="AndyMark")
        form = OrderForm()
        choices = dict(form.fields["vendor_choice"].choices)
        self.assertIn(vendor.pk, choices)
        self.assertIn(NOT_LISTED_VENDOR, choices)


class VendorOrderFlowTests(TestCase):
    def setUp(self):
        self.program = make_program()
        self.lead = make_lead_mentor_user()
        self.student = make_student_user(program=self.program)
        self.mentor = make_mentor_user()
        self.create_url = reverse("orders:order_create", args=[self.program.id])
        self.list_url = reverse("orders:order_list", args=[self.program.id])

    def login(self, user):
        self.client.force_login(user)

    def _item_data(self, item, **overrides):
        data = {
            "items": [str(item.pk)],
            "vendor_choice": "",
            "vendor_name": "",
            "vendor_url": "",
            "notes": "",
        }
        data.update(overrides)
        return data

    def test_order_created_with_vendor(self):
        vendor = make_vendor()
        item = make_item(self.program, requested_by=self.student)
        self.login(self.mentor)
        resp = self.client.post(
            self.create_url,
            self._item_data(item, vendor_choice=str(vendor.pk)),
        )
        self.assertEqual(resp.status_code, 302)
        order = Order.objects.get()
        self.assertEqual(order.vendor, vendor)
        self.assertEqual(order.vendor_name, vendor.name)

    def test_order_created_with_not_listed_vendor(self):
        item = make_item(self.program, requested_by=self.student)
        self.login(self.mentor)
        resp = self.client.post(
            self.create_url,
            self._item_data(
                item,
                vendor_choice=NOT_LISTED_VENDOR,
                vendor_name="Neighborhood Hardware",
                vendor_url="https://neighborhood-hardware.example.com",
            ),
        )
        self.assertEqual(resp.status_code, 302)
        order = Order.objects.get()
        self.assertIsNone(order.vendor_id)
        self.assertEqual(order.vendor_name, "Neighborhood Hardware")

    def test_vendor_list_is_org_wide_via_any_program(self):
        make_vendor(name="REV Robotics")
        other_program = make_program(name="Other Program")
        other_student = make_student_user(username="student2", program=other_program)
        make_item(other_program, requested_by=other_student)
        self.login(self.mentor)
        resp = self.client.get(reverse("orders:order_create", args=[other_program.id]))
        self.assertContains(resp, "REV Robotics")

    def test_order_list_shows_vendor_name(self):
        vendor = make_vendor()
        order = make_order(
            self.program,
            created_by=self.mentor,
            vendor=vendor,
            vendor_name=vendor.name,
            vendor_url=vendor.website,
        )
        make_item(self.program, requested_by=self.student, order=order)
        self.login(self.student)
        resp = self.client.get(self.list_url)
        self.assertContains(resp, "McMaster-Carr")

    def test_csv_export_includes_vendor_columns(self):
        vendor = make_vendor()
        order = make_order(
            self.program,
            created_by=self.mentor,
            vendor=vendor,
            vendor_name=vendor.name,
            vendor_url=vendor.website,
        )
        make_item(self.program, requested_by=self.student, order=order)
        self.login(self.lead)
        resp = self.client.get(self.list_url, {"export": "csv"})
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Vendor")
        self.assertContains(resp, "McMaster-Carr")
        self.assertContains(resp, vendor.website)
        self.assertContains(resp, "Status")
        self.assertContains(resp, "Ordered On")
