from django.test import TestCase

from programs.models import RolePermission


class RolePermissionModelTests(TestCase):
    def test_unique_role_section(self):
        RolePermission.objects.create(
            role="Mentor", section="student_info", can_read=True
        )
        with self.assertRaises(Exception):  # unique_together
            RolePermission.objects.create(
                role="Mentor", section="student_info", can_read=False
            )

    def test_str_representation(self):
        rp = RolePermission(
            role="Parent", section="payments", can_read=True, can_write=True
        )
        self.assertIn("Parent", str(rp))
        self.assertIn("Payments - General", str(rp))
        self.assertIn("R:True, W:True", str(rp))
