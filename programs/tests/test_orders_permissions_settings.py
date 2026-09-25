from django.contrib.auth.models import Group, User
from django.test import TestCase
from django.urls import reverse

from orders.models import OrderItem
from orders.tests.base import make_program, make_student_user
from programs.models import RolePermission


class OrdersPermissionsSettingsTests(TestCase):
    def setUp(self):
        self.password = "password123"  # nosec B105
        self.lead_mentor = User.objects.create_user(
            username="lead_mentor_user", password=self.password
        )
        self.lead_mentor_group, _ = Group.objects.get_or_create(name="LeadMentor")
        self.lead_mentor.groups.add(self.lead_mentor_group)

        self.program = make_program(with_orders_feature=True)
        self.student_user = make_student_user(
            username="student_tester", program=self.program
        )
        self.settings_url = reverse("portal_settings")
        self.update_url = reverse("portal_permissions_update")
        self.order_list_url = reverse("orders:order_list", args=[self.program.id])
        self.item_create_url = reverse("orders:item_create", args=[self.program.id])

    def test_settings_page_renders_orders_view_read_checked(self):
        self.client.force_login(self.lead_mentor)
        response = self.client.get(self.settings_url)
        self.assertEqual(response.status_code, 200)

        student_view_perm = RolePermission.objects.get(
            role="Student", section="orders-view"
        )
        mentor_view_perm = RolePermission.objects.get(
            role="Mentor", section="orders-view"
        )

        # The 'Read' radio must be checked for both Student and Mentor
        self.assertContains(
            response,
            f'name="perm_{student_view_perm.id}" id="perm_{student_view_perm.id}_read" value="read" checked',
        )
        self.assertContains(
            response,
            f'name="perm_{mentor_view_perm.id}" id="perm_{mentor_view_perm.id}_read" value="read" checked',
        )

    def test_saving_settings_preserves_orders_view_read_and_student_can_request_item(
        self,
    ):
        # 1. Lead mentor loads settings page to initialize permissions
        self.client.force_login(self.lead_mentor)
        get_resp = self.client.get(self.settings_url)
        self.assertEqual(get_resp.status_code, 200)

        student_view_perm = RolePermission.objects.get(
            role="Student", section="orders-view"
        )
        student_req_perm = RolePermission.objects.get(
            role="Student", section="orders-request"
        )

        # 2. Lead mentor saves permissions as rendered by the browser
        # For orders-view: student has 'read' checked.
        # For orders-request: student has 'write' checked.
        post_data = {
            f"perm_{student_view_perm.id}": "read",
            f"perm_{student_req_perm.id}": "write",
        }
        resp = self.client.post(self.update_url, post_data)
        self.assertEqual(resp.status_code, 302)

        student_view_perm.refresh_from_db()
        self.assertTrue(
            student_view_perm.can_read,
            "orders-view can_read should remain True after saving settings",
        )

        # 3. Student logs in and can view the orders page
        self.client.force_login(self.student_user)
        list_resp = self.client.get(self.order_list_url)
        self.assertEqual(
            list_resp.status_code,
            200,
            "Student should be able to view the orders list page",
        )
        self.assertTrue(list_resp.context["can_request_item"])

        # 4. Student can submit an item request on the order form
        create_resp = self.client.post(
            self.item_create_url,
            {
                "item_name": "Bearing 1/2 in",
                "quantity": "4",
                "unit_price": "5.00",
                "url": "https://example.com/bearing",
                "notes": "For drive gearbox",
            },
        )
        self.assertRedirects(create_resp, self.order_list_url)
        self.assertTrue(OrderItem.objects.filter(item_name="Bearing 1/2 in").exists())

    def test_lead_mentor_can_set_none_on_orders_view_to_revoke_access(self):
        self.client.force_login(self.lead_mentor)
        student_view_perm = RolePermission.objects.get_or_create(
            role="Student", section="orders-view"
        )[0]

        post_data = {
            f"perm_{student_view_perm.id}": "none",
        }
        resp = self.client.post(self.update_url, post_data)
        self.assertEqual(resp.status_code, 302)

        student_view_perm.refresh_from_db()
        self.assertFalse(student_view_perm.can_read)

        # Student is blocked from viewing orders
        self.client.force_login(self.student_user)
        list_resp = self.client.get(self.order_list_url)
        self.assertEqual(list_resp.status_code, 302)
        self.assertEqual(list_resp.url, reverse("home"))

    def test_student_with_view_permission_but_no_request_permission(self):
        self.client.force_login(self.lead_mentor)
        student_view_perm = RolePermission.objects.get_or_create(
            role="Student", section="orders-view"
        )[0]
        student_req_perm = RolePermission.objects.get_or_create(
            role="Student", section="orders-request"
        )[0]

        post_data = {
            f"perm_{student_view_perm.id}": "read",
            f"perm_{student_req_perm.id}": "none",
        }
        resp = self.client.post(self.update_url, post_data)
        self.assertEqual(resp.status_code, 302)

        student_view_perm.refresh_from_db()
        student_req_perm.refresh_from_db()
        self.assertTrue(student_view_perm.can_read)
        self.assertFalse(student_req_perm.can_write)

        # Student can view orders page, but cannot request items
        self.client.force_login(self.student_user)
        list_resp = self.client.get(self.order_list_url)
        self.assertEqual(list_resp.status_code, 200)
        self.assertFalse(list_resp.context["can_request_item"])

        # Attempting to create an item request directly redirects with an error
        create_resp = self.client.post(
            self.item_create_url,
            {
                "item_name": "Screws M4",
                "quantity": "10",
            },
        )
        self.assertEqual(create_resp.status_code, 302)
        self.assertFalse(OrderItem.objects.filter(item_name="Screws M4").exists())

    def test_mentor_and_parent_orders_permissions_rendering(self):
        self.client.force_login(self.lead_mentor)
        response = self.client.get(self.settings_url)
        self.assertEqual(response.status_code, 200)

        for section in [
            "orders-view",
            "orders-request",
            "orders-manage",
            "orders-shipping",
        ]:
            parent_perm = RolePermission.objects.get(role="Parent", section=section)
            # Parents have all radios disabled for order sections
            self.assertContains(
                response,
                f'id="perm_{parent_perm.id}_none" value="none" checked disabled',
            )

        # Mentors have write checked on action sections
        mentor_req_perm = RolePermission.objects.get(
            role="Mentor", section="orders-request"
        )
        self.assertContains(
            response,
            f'id="perm_{mentor_req_perm.id}_write" value="write" checked',
        )
