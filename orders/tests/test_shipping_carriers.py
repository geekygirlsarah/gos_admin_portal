"""Tests for the ``ShippingCarrier`` reference list, tracking URL links, and
the ``ItemTag`` label list on order items.

Carriers are an org-wide, Lead-Mentor-managed list (like ``Vendor``) whose
``tracking_url_template`` (containing a ``{number}`` placeholder) turns an
order's tracking number into a clickable link. Tags are an org-wide label list
that items can be marked with (e.g. team/project/crew/subteam) purely for
organization — tagging never affects how items are grouped into orders.
"""

from django.test import TestCase
from django.urls import reverse

from orders.forms import NOT_LISTED_VENDOR
from orders.models import ItemTag, Order, OrderItem, ShippingCarrier
from orders.tests.base import (
    make_carrier,
    make_item,
    make_lead_mentor_user,
    make_mentor_user,
    make_order,
    make_program,
    make_student_user,
    make_tag,
)


class ShippingCarrierModelTests(TestCase):
    def test_str_returns_name(self):
        carrier = make_carrier("UPS")
        self.assertEqual(str(carrier), "UPS")

    def test_carriers_ordered_by_name(self):
        make_carrier("UPS")
        make_carrier("FedEx")
        names = list(ShippingCarrier.objects.values_list("name", flat=True))
        self.assertEqual(names, ["FedEx", "UPS"])

    def test_format_tracking_url_substitutes_number(self):
        carrier = make_carrier(
            "UPS",
            tracking_url_template="https://www.ups.com/track?track=yes&trackNums={number}",
        )
        self.assertEqual(
            carrier.format_tracking_url("1Z999AA10123456784"),
            "https://www.ups.com/track?track=yes&trackNums=1Z999AA10123456784",
        )

    def test_format_tracking_url_none_without_template(self):
        carrier = make_carrier("UPS")
        self.assertIsNone(carrier.format_tracking_url("1Z999AA10123456784"))

    def test_order_tracking_url_links_when_carrier_and_number_present(self):
        carrier = make_carrier(
            "UPS",
            tracking_url_template="https://www.ups.com/track?track=yes&trackNums={number}",
        )
        order = make_order(
            make_program(),
            shipping_carrier=carrier,
            tracking_number="1Z999AA10123456784",
        )
        self.assertEqual(
            order.tracking_url,
            "https://www.ups.com/track?track=yes&trackNums=1Z999AA10123456784",
        )

    def test_order_tracking_url_none_without_number_or_carrier(self):
        carrier = make_carrier("UPS")
        self.assertIsNone(
            make_order(make_program(), shipping_carrier=carrier).tracking_url
        )
        self.assertIsNone(
            make_order(make_program(), tracking_number="12345").tracking_url
        )

    def test_order_total_ignores_carrier(self):
        order = make_order(make_program(), shipping_carrier=make_carrier("UPS"))
        self.assertIsNone(order.total)


class ShippingCarrierAccessTests(TestCase):
    def setUp(self):
        self.program = make_program()
        self.lead = make_lead_mentor_user()
        self.mentor = make_mentor_user()
        self.student = make_student_user(program=self.program)
        self.list_url = reverse("orders:carrier_list", args=[self.program.id])

    def login(self, user):
        self.client.force_login(user)

    def test_lead_can_list_carriers(self):
        make_carrier("UPS")
        self.login(self.lead)
        resp = self.client.get(self.list_url)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "UPS")

    def test_mentor_blocked(self):
        make_carrier("UPS")
        self.login(self.mentor)
        resp = self.client.get(self.list_url)
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp.url, reverse("home"))

    def test_student_blocked(self):
        make_carrier("UPS")
        self.login(self.student)
        resp = self.client.get(self.list_url)
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp.url, reverse("home"))


class ShippingCarrierManageTests(TestCase):
    def setUp(self):
        self.program = make_program()
        self.lead = make_lead_mentor_user()
        self.list_url = reverse("orders:carrier_list", args=[self.program.id])
        self.create_url = reverse("orders:carrier_create", args=[self.program.id])

    def login(self, user):
        self.client.force_login(user)

    def test_lead_creates_carrier(self):
        self.login(self.lead)
        resp = self.client.post(
            self.create_url,
            {
                "name": "UPS",
                "tracking_url_template": "https://www.ups.com/track?track=yes&trackNums={number}",
            },
        )
        self.assertRedirects(resp, self.list_url)
        carrier = ShippingCarrier.objects.get(name="UPS")
        self.assertEqual(
            carrier.tracking_url_template,
            "https://www.ups.com/track?track=yes&trackNums={number}",
        )

    def test_carrier_without_number_placeholder_rejected(self):
        self.login(self.lead)
        resp = self.client.post(
            self.create_url,
            {"name": "UPS", "tracking_url_template": "https://www.ups.com/track"},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "{number}")
        self.assertFalse(ShippingCarrier.objects.filter(name="UPS").exists())

    def test_lead_edits_carrier(self):
        carrier = make_carrier("UPS")
        self.login(self.lead)
        edit_url = reverse("orders:carrier_edit", args=[self.program.id, carrier.id])
        resp = self.client.post(
            edit_url,
            {
                "name": "UPS",
                "tracking_url_template": "https://www.ups.com/{number}",
            },
        )
        self.assertRedirects(resp, self.list_url)
        carrier.refresh_from_db()
        self.assertEqual(carrier.tracking_url_template, "https://www.ups.com/{number}")

    def test_lead_deletes_carrier(self):
        carrier = make_carrier("UPS")
        self.login(self.lead)
        delete_url = reverse(
            "orders:carrier_delete", args=[self.program.id, carrier.id]
        )
        resp = self.client.get(delete_url)
        self.assertEqual(resp.status_code, 200)
        resp = self.client.post(delete_url)
        self.assertRedirects(resp, self.list_url)
        self.assertFalse(ShippingCarrier.objects.filter(pk=carrier.pk).exists())

    def test_deleting_carrier_keeps_tracking_number_but_drops_link(self):
        carrier = make_carrier(
            "UPS", tracking_url_template="https://www.ups.com/{number}"
        )
        order = make_order(
            self.program,
            shipping_carrier=carrier,
            tracking_number="1Z999AA10123456784",
        )
        self.assertEqual(order.shipping_carrier_id, carrier.pk)
        self.assertIsNotNone(order.tracking_url)
        carrier.delete()
        order.refresh_from_db()
        self.assertIsNone(order.shipping_carrier_id)
        self.assertEqual(order.tracking_number, "1Z999AA10123456784")
        self.assertIsNone(order.tracking_url)


class ItemTagModelTests(TestCase):
    def test_str_returns_name(self):
        tag = make_tag("Drivetrain")
        self.assertEqual(str(tag), "Drivetrain")

    def test_tags_ordered_by_name(self):
        make_tag("Zebras")
        make_tag("Alpha")
        names = list(ItemTag.objects.values_list("name", flat=True))
        self.assertEqual(names, ["Alpha", "Zebras"])


class ItemTagAccessTests(TestCase):
    def setUp(self):
        self.program = make_program()
        self.lead = make_lead_mentor_user()
        self.mentor = make_mentor_user()
        self.student = make_student_user(program=self.program)
        self.list_url = reverse("orders:tag_list", args=[self.program.id])

    def login(self, user):
        self.client.force_login(user)

    def test_lead_can_list_tags(self):
        make_tag("Drivetrain")
        self.login(self.lead)
        resp = self.client.get(self.list_url)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Drivetrain")

    def test_mentor_blocked(self):
        make_tag("Drivetrain")
        self.login(self.mentor)
        resp = self.client.get(self.list_url)
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp.url, reverse("home"))

    def test_student_blocked(self):
        make_tag("Drivetrain")
        self.login(self.student)
        resp = self.client.get(self.list_url)
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp.url, reverse("home"))


class ItemTagManageTests(TestCase):
    def setUp(self):
        self.program = make_program()
        self.lead = make_lead_mentor_user()
        self.list_url = reverse("orders:tag_list", args=[self.program.id])
        self.create_url = reverse("orders:tag_create", args=[self.program.id])

    def login(self, user):
        self.client.force_login(user)

    def test_lead_creates_tag(self):
        self.login(self.lead)
        resp = self.client.post(
            self.create_url, {"name": "Drivetrain", "color": "#dc3545"}
        )
        self.assertRedirects(resp, self.list_url)
        tag = ItemTag.objects.get(name="Drivetrain")
        self.assertEqual(tag.color, "#dc3545")

    def test_lead_edits_tag(self):
        tag = make_tag("Drivetrain")
        self.login(self.lead)
        edit_url = reverse("orders:tag_edit", args=[self.program.id, tag.id])
        resp = self.client.post(edit_url, {"name": "Drivetrain", "color": "#198754"})
        self.assertRedirects(resp, self.list_url)
        tag.refresh_from_db()
        self.assertEqual(tag.color, "#198754")

    def test_lead_deletes_tag(self):
        tag = make_tag("Drivetrain")
        self.login(self.lead)
        delete_url = reverse("orders:tag_delete", args=[self.program.id, tag.id])
        resp = self.client.get(delete_url)
        self.assertEqual(resp.status_code, 200)
        resp = self.client.post(delete_url)
        self.assertRedirects(resp, self.list_url)
        self.assertFalse(ItemTag.objects.filter(pk=tag.pk).exists())


class TrackingLinkViewTests(TestCase):
    def setUp(self):
        self.program = make_program()
        self.lead = make_lead_mentor_user()
        self.mentor = make_mentor_user()
        self.student = make_student_user(program=self.program)

    def login(self, user):
        self.client.force_login(user)

    def test_detail_renders_tracking_link_from_carrier_template(self):
        carrier = make_carrier(
            "UPS",
            tracking_url_template="https://www.ups.com/track?track=yes&trackNums={number}",
        )
        order = make_order(
            self.program,
            created_by=self.mentor,
            shipping_carrier=carrier,
            tracking_number="1Z999AA10123456784",
        )
        order.status = Order.STATUS_SHIPPED
        order.save(update_fields=["status"])
        # Students see the read-only shipping view (mentors/leads get an edit form).
        self.login(self.student)
        detail_url = reverse("orders:order_detail", args=[self.program.id, order.id])
        resp = self.client.get(detail_url)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(
            resp,
            "https://www.ups.com/track?track=yes&amp;trackNums=1Z999AA10123456784",
        )

    def test_detail_tracking_plain_without_template(self):
        carrier = make_carrier("UPS")
        order = make_order(
            self.program,
            created_by=self.mentor,
            shipping_carrier=carrier,
            tracking_number="1Z999AA10123456784",
        )
        order.status = Order.STATUS_SHIPPED
        order.save(update_fields=["status"])
        self.login(self.student)
        detail_url = reverse("orders:order_detail", args=[self.program.id, order.id])
        resp = self.client.get(detail_url)
        self.assertContains(resp, "1Z999AA10123456784")
        self.assertNotContains(resp, "www.ups.com")


class ItemTaggingTests(TestCase):
    def setUp(self):
        self.program = make_program()
        self.lead = make_lead_mentor_user()
        self.mentor = make_mentor_user()
        self.student = make_student_user(program=self.program)
        self.list_url = reverse("orders:order_list", args=[self.program.id])

    def login(self, user):
        self.client.force_login(user)

    def test_student_tags_item_on_request(self):
        tag = make_tag("Drivetrain")
        self.login(self.student)
        create_url = reverse("orders:item_create", args=[self.program.id])
        resp = self.client.post(
            create_url,
            {
                "item_name": "Hex Driver",
                "quantity": "1",
                "unit_price": "6.50",
                "tag": str(tag.pk),
                "vendor_choice": "",
            },
        )
        self.assertRedirects(resp, self.list_url)
        item = OrderItem.objects.get(item_name="Hex Driver")
        self.assertEqual(item.tag, tag)

    def test_item_without_tag_allowed(self):
        self.login(self.student)
        create_url = reverse("orders:item_create", args=[self.program.id])
        resp = self.client.post(
            create_url,
            {
                "item_name": "Bare Screw",
                "quantity": "1",
                "tag": "",
                "vendor_choice": "",
            },
        )
        self.assertRedirects(resp, self.list_url)
        item = OrderItem.objects.get(item_name="Bare Screw")
        self.assertIsNone(item.tag_id)

    def test_requests_list_shows_tag_pill(self):
        tag = make_tag("Drivetrain", color="#dc3545")
        make_item(self.program, tag=tag)
        self.login(self.lead)
        resp = self.client.get(self.list_url)
        self.assertContains(resp, "Drivetrain")

    def test_items_with_different_tags_group_into_one_order(self):
        """Tags are purely organizational — they do not affect grouping."""
        tag_a = make_tag("Drivetrain")
        tag_b = make_tag("Marketing")
        item1 = make_item(self.program, item_name="Hex Driver", tag=tag_a)
        item2 = make_item(self.program, item_name="Banner", tag=tag_b)
        self.login(self.lead)
        create_url = reverse("orders:order_create", args=[self.program.id])
        resp = self.client.post(
            create_url,
            {
                "items": [str(item1.pk), str(item2.pk)],
                "vendor_choice": NOT_LISTED_VENDOR,
                "vendor_name": "Test Vendor",
            },
        )
        self.assertEqual(resp.status_code, 302)
        order = Order.objects.get(vendor_name="Test Vendor")
        self.assertEqual(
            set(order.items.values_list("pk", flat=True)), {item1.pk, item2.pk}
        )

    def test_deleting_tag_keeps_item(self):
        tag = make_tag("Drivetrain")
        item = make_item(self.program, tag=tag)
        tag.delete()
        item.refresh_from_db()
        self.assertIsNone(item.tag_id)
        self.assertTrue(OrderItem.objects.filter(pk=item.pk).exists())
