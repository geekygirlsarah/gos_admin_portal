"""Tests for the ``Vendor`` reference list and vendor selection on orders.

Vendors are an org-wide reference list that Lead Mentors manage. Students and
mentors pick a vendor from a dropdown when placing an order, or choose "Not
listed" to type their own. Each ``PurchaseOrder`` stores a snapshot of the
vendor name/website (``vendor_name``/``vendor_url``) so the order text survives
even if the linked ``Vendor`` row is later deleted.
"""

from django.test import TestCase
from django.urls import reverse

from orders.forms import NOT_LISTED_VENDOR, OrderForm
from orders.models import PurchaseOrder, Vendor
from orders.tests.base import (
    make_lead_mentor_user,
    make_mentor_user,
    make_program,
    make_student_user,
)


def make_vendor(name="McMaster-Carr", **kwargs):
    data = {
        "name": name,
        "website": f"https://{name.lower().replace(' ', '').replace('.', '')}.com",
        "notes": "Fast shipping",
    }
    data.update(kwargs)
    return Vendor.objects.create(**data)


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
        self.student = make_student_user(program=self.program)
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
        order = PurchaseOrder.objects.create(
            program=self.program,
            item_name="Hex Driver",
            quantity="2",
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
    def _base_data(self, **overrides):
        data = {
            "item_name": "Hex Driver",
            "quantity": "2",
            "unit_price": "5.50",
            "url": "",
            "notes": "",
        }
        data.update(overrides)
        return data

    def test_selecting_vendor_sets_fk_and_snapshot(self):
        vendor = make_vendor()
        form = OrderForm(
            data=self._base_data(vendor_choice=str(vendor.pk)),
        )
        self.assertTrue(form.is_valid(), form.errors)
        order = form.save()
        self.assertEqual(order.vendor, vendor)
        self.assertEqual(order.vendor_name, vendor.name)
        self.assertEqual(order.vendor_url, vendor.website)

    def test_vendor_name_is_not_required_when_picking_listed_vendor(self):
        vendor = make_vendor()
        form = OrderForm(
            data=self._base_data(vendor_choice=str(vendor.pk), vendor_name=""),
        )
        self.assertTrue(form.is_valid(), form.errors)

    def test_not_listed_saves_custom_name_and_url(self):
        form = OrderForm(
            data=self._base_data(
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

    def test_not_listed_requires_custom_name(self):
        form = OrderForm(
            data=self._base_data(vendor_choice=NOT_LISTED_VENDOR, vendor_name=""),
        )
        self.assertFalse(form.is_valid())
        self.assertIn("vendor_name", form.errors)

    def test_blank_vendor_choice_is_allowed(self):
        form = OrderForm(data=self._base_data(vendor_choice=""))
        self.assertTrue(form.is_valid(), form.errors)
        order = form.save()
        self.assertIsNone(order.vendor_id)
        self.assertEqual(order.vendor_name, "")

    def test_blanking_vendor_on_edit_clears_snapshot(self):
        vendor = make_vendor()
        order = PurchaseOrder.objects.create(
            program=make_program(),
            item_name="Gear",
            quantity="1",
            vendor=vendor,
            vendor_name=vendor.name,
            vendor_url=vendor.website,
        )
        form = OrderForm(
            instance=order,
            data=self._base_data(vendor_choice=""),
        )
        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        order.refresh_from_db()
        self.assertIsNone(order.vendor_id)
        self.assertEqual(order.vendor_name, "")

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
        self.create_url = reverse("orders:order_create", args=[self.program.id])
        self.list_url = reverse("orders:order_list", args=[self.program.id])

    def login(self, user):
        self.client.force_login(user)

    def test_order_created_with_vendor(self):
        vendor = make_vendor()
        self.login(self.student)
        resp = self.client.post(
            self.create_url,
            {
                "item_name": "Zip Ties",
                "quantity": "10",
                "vendor_choice": str(vendor.pk),
            },
        )
        self.assertRedirects(resp, self.list_url)
        order = PurchaseOrder.objects.get(item_name="Zip Ties")
        self.assertEqual(order.vendor, vendor)
        self.assertEqual(order.vendor_name, vendor.name)

    def test_order_created_with_not_listed_vendor(self):
        self.login(self.student)
        resp = self.client.post(
            self.create_url,
            {
                "item_name": "Zip Ties",
                "quantity": "10",
                "vendor_choice": NOT_LISTED_VENDOR,
                "vendor_name": "Neighborhood Hardware",
                "vendor_url": "",
            },
        )
        self.assertRedirects(resp, self.list_url)
        order = PurchaseOrder.objects.get(item_name="Zip Ties")
        self.assertIsNone(order.vendor_id)
        self.assertEqual(order.vendor_name, "Neighborhood Hardware")

    def test_vendor_list_is_org_wide_via_any_program(self):
        make_vendor(name="REV Robotics")
        other_program = make_program(name="Other Program")
        other_student = make_student_user(username="student2", program=other_program)
        self.login(other_student)
        resp = self.client.get(reverse("orders:order_create", args=[other_program.id]))
        self.assertContains(resp, "REV Robotics")

    def test_order_list_shows_vendor_name(self):
        vendor = make_vendor()
        PurchaseOrder.objects.create(
            program=self.program,
            item_name="Hex Driver",
            quantity="2",
            created_by=self.student,
            vendor=vendor,
            vendor_name=vendor.name,
            vendor_url=vendor.website,
        )
        self.login(self.student)
        resp = self.client.get(self.list_url)
        self.assertContains(resp, "McMaster-Carr")

    def test_csv_export_includes_vendor_columns(self):
        vendor = make_vendor()
        PurchaseOrder.objects.create(
            program=self.program,
            item_name="Hex Driver",
            quantity="2",
            created_by=self.student,
            vendor=vendor,
            vendor_name=vendor.name,
            vendor_url=vendor.website,
        )
        self.login(self.lead)
        resp = self.client.get(self.list_url, {"export": "csv"})
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Vendor")
        self.assertContains(resp, "McMaster-Carr")
        self.assertContains(resp, vendor.website)
