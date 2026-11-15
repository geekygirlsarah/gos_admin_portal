from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from orders.models import OrderItem
from organizations.models import Organization, UserOrganizationMembership
from programs.models import Enrollment, Group, Program, RolePermission, Student
from programs.permission_views import can_user_delete


class ProgramGroupsManagementTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.get(slug="gos")
        self.other_org = Organization.objects.create(
            name="Other", slug="other-groups-ui"
        )
        self.program = Program.objects.create(name="Robotics", organization=self.org)
        self.other_program = Program.objects.create(
            name="Other program", organization=self.org
        )
        self.foreign_program = Program.objects.create(
            name="Private program", organization=self.other_org
        )
        self.group = Group.objects.create(
            name="Design", color="#123456", program=self.program, organization=self.org
        )
        self.other_group = Group.objects.create(
            name="Other group", program=self.other_program, organization=self.org
        )
        self.lead = User.objects.create_user(username="group-lead")
        self.mentor = User.objects.create_user(username="group-mentor")
        for user, role in ((self.lead, "LeadMentor"), (self.mentor, "Mentor")):
            UserOrganizationMembership.objects.create(
                user=user, organization=self.org, role=role, is_active=True
            )
        self.url = reverse("program_groups", args=[self.program.pk])

    def set_permission(self, read, write):
        RolePermission.objects.update_or_create(
            organization=self.org,
            role="Mentor",
            section="program_groups",
            defaults={"can_read": read, "can_write": write},
        )

    def test_lead_sees_program_groups_and_compatibility_link(self):
        self.client.force_login(self.lead)
        response = self.client.get(self.url)
        self.assertContains(response, "Design")
        self.assertNotContains(response, "Other group")
        self.assertContains(response, "Add Group")
        self.assertContains(response, "Delete")
        self.assertContains(
            self.client.get(reverse("program_detail", args=[self.program.pk])), self.url
        )
        self.assertContains(
            self.client.get(reverse("portal_settings") + "?tab=groups"), self.url
        )

    def test_mentor_default_and_permission_settings(self):
        self.client.force_login(self.mentor)
        self.assertNotEqual(self.client.get(self.url).status_code, 200)
        self.client.force_login(self.lead)
        response = self.client.get(reverse("portal_settings") + "?tab=permissions")
        perm = RolePermission.objects.get(
            organization=self.org, role="Mentor", section="program_groups"
        )
        self.assertFalse(perm.can_read)
        self.assertFalse(perm.can_write)
        self.assertContains(response, "Programs - Groups")
        program_perm = RolePermission.objects.get(
            organization=self.org, role="Mentor", section="programs"
        )
        self.client.post(
            reverse("portal_permissions_update"),
            {f"perm_{perm.pk}": "write", f"perm_{program_perm.pk}": "read"},
        )
        self.client.force_login(self.mentor)
        self.assertEqual(self.client.get(self.url).status_code, 200)
        self.set_permission(False, False)
        self.assertNotEqual(self.client.get(self.url).status_code, 200)

    def test_read_only_and_write_permissions_and_delete_restriction(self):
        self.client.force_login(self.mentor)
        self.set_permission(True, False)
        response = self.client.get(self.url)
        self.assertContains(response, "Design")
        self.assertNotContains(response, "Add Group")
        self.assertNotContains(response, "Delete")
        self.client.post(
            self.url, {"action": "add_group", "name": "Build", "color": "#123456"}
        )
        self.assertFalse(
            Group.objects.filter(program=self.program, name="Build").exists()
        )
        self.set_permission(True, True)
        self.client.post(
            self.url, {"action": "add_group", "name": "Build", "color": "#123456"}
        )
        self.assertTrue(
            Group.objects.filter(program=self.program, name="Build").exists()
        )
        self.client.post(
            self.url,
            {
                "action": "update_group",
                "group_id": self.group.pk,
                "name": "Engineering",
                "color": "#654321",
            },
        )
        self.group.refresh_from_db()
        self.assertEqual(self.group.name, "Engineering")
        self.client.post(
            self.url, {"action": "delete_group", "group_id": self.group.pk}
        )
        self.assertTrue(Group.objects.filter(pk=self.group.pk).exists())
        self.assertFalse(
            can_user_delete(self.mentor, "program_groups", self.program, org=self.org)
        )

    def test_program_and_group_are_scoped(self):
        self.client.force_login(self.lead)
        foreign_url = reverse("program_groups", args=[self.foreign_program.pk])
        response = self.client.get(foreign_url)
        self.assertNotEqual(response.status_code, 200)
        response = self.client.post(
            foreign_url, {"action": "add_group", "name": "No", "color": "#123456"}
        )
        self.assertNotEqual(response.status_code, 200)
        self.assertFalse(
            Group.objects.filter(program=self.foreign_program, name="No").exists()
        )
        self.client.post(
            self.url,
            {
                "action": "update_group",
                "group_id": self.other_group.pk,
                "name": "Changed",
                "color": "#123456",
            },
        )
        self.other_group.refresh_from_db()
        self.assertEqual(self.other_group.name, "Other group")

    def test_lead_can_delete_and_invalid_group_values_are_rejected(self):
        self.client.force_login(self.lead)
        for name, color in (
            ("", "#123456"),
            ("Design", "#123456"),
            ("Build", "invalid"),
        ):
            self.client.post(
                self.url, {"action": "add_group", "name": name, "color": color}
            )
        self.assertEqual(Group.objects.filter(program=self.program).count(), 1)
        self.client.post(
            self.url, {"action": "delete_group", "group_id": self.group.pk}
        )
        self.assertFalse(Group.objects.filter(pk=self.group.pk).exists())

    def test_invalid_group_identifier_is_rejected(self):
        self.client.force_login(self.lead)
        response = self.client.post(
            self.url,
            {"action": "update_group", "group_id": "invalid", "name": "Build"},
            follow=True,
        )
        self.assertContains(response, "That group could not be found.")
        self.group.refresh_from_db()
        self.assertEqual(self.group.name, "Design")

    def test_mentor_cannot_access_inactive_program_even_with_permission(self):
        self.set_permission(True, True)
        self.program.active = False
        self.program.save()
        self.client.force_login(self.mentor)
        self.assertNotEqual(self.client.get(self.url).status_code, 200)
        self.client.post(
            self.url, {"action": "add_group", "name": "Build", "color": "#123456"}
        )
        self.assertFalse(
            Group.objects.filter(program=self.program, name="Build").exists()
        )

    def test_group_linked_to_foreign_enrollment_cannot_be_deleted(self):
        student = Student.objects.create(
            organization=self.other_org, legal_first_name="Other", last_name="Student"
        )
        enrollment = Enrollment.objects.create(
            organization=self.other_org, program=self.foreign_program, student=student
        )
        enrollment.groups.add(self.group)
        self.client.force_login(self.lead)
        response = self.client.post(
            self.url, {"action": "delete_group", "group_id": self.group.pk}, follow=True
        )
        self.assertContains(response, "linked to records owned by another organization")
        self.assertTrue(Group.objects.filter(pk=self.group.pk).exists())

    def test_group_linked_to_foreign_order_item_cannot_be_deleted(self):
        item = OrderItem.objects.create(
            organization=self.other_org, program=self.foreign_program, item_name="Part"
        )
        item.groups.add(self.group)
        self.client.force_login(self.lead)
        response = self.client.post(
            self.url, {"action": "delete_group", "group_id": self.group.pk}, follow=True
        )
        self.assertContains(response, "linked to records owned by another organization")
        self.assertTrue(Group.objects.filter(pk=self.group.pk).exists())

    def test_non_mentor_cannot_use_permission_even_if_configured(self):
        user = User.objects.create_user(username="group-parent")
        UserOrganizationMembership.objects.create(
            user=user, organization=self.org, role="Parent", is_active=True
        )
        RolePermission.objects.create(
            organization=self.org,
            role="Parent",
            section="program_groups",
            can_read=True,
            can_write=True,
        )
        self.client.force_login(user)
        self.assertNotEqual(self.client.get(self.url).status_code, 200)
        self.client.post(
            self.url, {"action": "add_group", "name": "Build", "color": "#123456"}
        )
        self.assertFalse(
            Group.objects.filter(program=self.program, name="Build").exists()
        )
