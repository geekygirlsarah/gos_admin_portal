"""Tests for Phase 5: Org-scoped RolePermission and can_user_read/write/delete."""

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import RequestFactory, TestCase

from organizations.models import Organization
from programs.models import Adult, Program, RolePermission, Student
from programs.permission_views import (
    can_user_delete,
    can_user_read,
    can_user_write,
    get_user_role,
)
from programs.views.mixins import (
    DynamicReadPermissionMixin,
    DynamicWritePermissionMixin,
)


class OrgScopedPermissionTests(TestCase):
    def setUp(self):
        RolePermission.objects.all().delete()
        self.gos = Organization.objects.get(slug="gos")
        self.other_org = Organization.objects.create(
            name="RoboBuilders", slug="robobuilders"
        )

        User = get_user_model()
        self.mentor_user = User.objects.create_user(
            username="mentor_user", password="password123"  # nosec B106
        )
        self.mentor_adult = Adult.objects.create(
            user=self.mentor_user,
            legal_first_name="Mentor",
            last_name="Test",
            is_mentor=True,
            mentor_active=True,
            organization=self.gos,
        )

        self.parent_user = User.objects.create_user(
            username="parent_user", password="password123"  # nosec B106
        )
        self.parent_adult = Adult.objects.create(
            user=self.parent_user,
            legal_first_name="Parent",
            last_name="Test",
            is_parent=True,
            organization=self.gos,
        )

        self.student_user = User.objects.create_user(
            username="student_user", password="password123"  # nosec B106
        )
        self.student = Student.objects.create(
            user=self.student_user,
            legal_first_name="Student",
            last_name="Test",
            organization=self.gos,
        )
        self.student.adults.add(self.parent_adult)

    def test_role_permission_has_organization_and_scoping(self):
        """RolePermission model supports organization FK and for_organization scoping."""
        p_gos = RolePermission.objects.create(
            organization=self.gos,
            role="Mentor",
            section="attendance",
            can_read=True,
            can_write=True,
        )
        p_other = RolePermission.objects.create(
            organization=self.other_org,
            role="Mentor",
            section="attendance",
            can_read=False,
            can_write=False,
        )
        p_unscoped = RolePermission.objects.create(
            organization=None,
            role="Parent",
            section="attendance",
            can_read=True,
            can_write=False,
        )

        # Scoping tests
        gos_perms = set(RolePermission.objects.for_organization(self.gos))
        self.assertIn(p_gos, gos_perms)
        self.assertIn(p_unscoped, gos_perms)
        self.assertNotIn(p_other, gos_perms)

        all_perms = set(RolePermission.objects.for_organization(None))
        self.assertIn(p_gos, all_perms)
        self.assertIn(p_other, all_perms)
        self.assertIn(p_unscoped, all_perms)

    def test_unique_together_per_organization(self):
        """Same role and section can exist for different organizations."""
        RolePermission.objects.create(
            organization=self.gos,
            role="Mentor",
            section="student_info",
            can_read=True,
        )
        # Creating same role/section for different org succeeds
        rp_other = RolePermission.objects.create(
            organization=self.other_org,
            role="Mentor",
            section="student_info",
            can_read=False,
        )
        self.assertIsNotNone(rp_other.pk)

        # Creating duplicate for same org fails
        with self.assertRaises(Exception):
            RolePermission.objects.create(
                organization=self.gos,
                role="Mentor",
                section="student_info",
                can_read=False,
            )

    def test_can_user_read_with_explicit_org(self):
        """can_user_read respects the org parameter when passed."""
        # For GoS: Mentor can read attendance
        RolePermission.objects.create(
            organization=self.gos,
            role="Mentor",
            section="attendance",
            can_read=True,
            can_write=False,
        )
        # For Other Org: Mentor cannot read attendance
        RolePermission.objects.create(
            organization=self.other_org,
            role="Mentor",
            section="attendance",
            can_read=False,
            can_write=False,
        )

        self.assertTrue(can_user_read(self.mentor_user, "attendance", org=self.gos))
        self.assertFalse(
            can_user_read(self.mentor_user, "attendance", org=self.other_org)
        )

    def test_can_user_write_with_explicit_org(self):
        """can_user_write respects the org parameter when passed."""
        RolePermission.objects.create(
            organization=self.gos,
            role="Mentor",
            section="team_assignments",
            can_read=True,
            can_write=True,
        )
        RolePermission.objects.create(
            organization=self.other_org,
            role="Mentor",
            section="team_assignments",
            can_read=True,
            can_write=False,
        )

        self.assertTrue(
            can_user_write(self.mentor_user, "team_assignments", org=self.gos)
        )
        self.assertFalse(
            can_user_write(self.mentor_user, "team_assignments", org=self.other_org)
        )

    def test_can_user_delete_with_explicit_org(self):
        """can_user_delete delegates to can_user_write with org parameter."""
        RolePermission.objects.create(
            organization=self.gos,
            role="Mentor",
            section="team_assignments",
            can_read=True,
            can_write=True,
        )
        RolePermission.objects.create(
            organization=self.other_org,
            role="Mentor",
            section="team_assignments",
            can_read=True,
            can_write=False,
        )

        self.assertTrue(
            can_user_delete(self.mentor_user, "team_assignments", org=self.gos)
        )
        self.assertFalse(
            can_user_delete(self.mentor_user, "team_assignments", org=self.other_org)
        )

    def test_can_user_read_write_infers_org_from_obj(self):
        """When org is not passed, can_user_read/write infers org from obj.organization."""
        prog_gos = Program.objects.create(
            name="GoS Robotics", organization=self.gos, active=True
        )
        prog_other = Program.objects.create(
            name="Other Robotics", organization=self.other_org, active=True
        )

        RolePermission.objects.create(
            organization=self.gos,
            role="Mentor",
            section="team_assignments",
            can_read=True,
            can_write=True,
        )
        RolePermission.objects.create(
            organization=self.other_org,
            role="Mentor",
            section="team_assignments",
            can_read=False,
            can_write=False,
        )

        # Infer from prog_gos
        self.assertTrue(
            can_user_read(self.mentor_user, "team_assignments", obj=prog_gos)
        )
        self.assertTrue(
            can_user_write(self.mentor_user, "team_assignments", obj=prog_gos)
        )

        # Infer from prog_other
        self.assertFalse(
            can_user_read(self.mentor_user, "team_assignments", obj=prog_other)
        )
        self.assertFalse(
            can_user_write(self.mentor_user, "team_assignments", obj=prog_other)
        )

    def test_get_user_role_stays_global(self):
        """get_user_role determines user's role globally regardless of org."""
        self.assertEqual(get_user_role(self.mentor_user), "Mentor")
        self.assertEqual(get_user_role(self.parent_user), "Parent")
        self.assertEqual(get_user_role(self.student_user), "Student")

    def test_dynamic_permission_mixin_uses_request_organization(self):
        """DynamicPermissionMixin extracts request.organization and passes it to check."""
        RolePermission.objects.create(
            organization=self.gos,
            role="Mentor",
            section="attendance",
            can_read=True,
            can_write=True,
        )
        RolePermission.objects.create(
            organization=self.other_org,
            role="Mentor",
            section="attendance",
            can_read=False,
            can_write=False,
        )

        rf = RequestFactory()

        # Test read mixin with GoS org
        req_gos = rf.get("/attendance/")
        req_gos.user = self.mentor_user
        req_gos.organization = self.gos

        class TestReadView(DynamicReadPermissionMixin):
            section = "attendance"
            request = req_gos

        self.assertTrue(TestReadView().test_func())

        # Test read mixin with Other org
        req_other = rf.get("/attendance/")
        req_other.user = self.mentor_user
        req_other.organization = self.other_org

        class TestOtherReadView(DynamicReadPermissionMixin):
            section = "attendance"
            request = req_other

        self.assertFalse(TestOtherReadView().test_func())

    def test_permission_template_tags_use_request_organization(self):
        """can_read, can_write, and can_delete template tags use request.organization."""
        from django.template import Context, Template

        RolePermission.objects.create(
            organization=self.gos,
            role="Mentor",
            section="attendance",
            can_read=True,
            can_write=True,
        )
        RolePermission.objects.create(
            organization=self.other_org,
            role="Mentor",
            section="attendance",
            can_read=False,
            can_write=False,
        )

        rf = RequestFactory()
        req_gos = rf.get("/")
        req_gos.user = self.mentor_user
        req_gos.organization = self.gos

        req_other = rf.get("/")
        req_other.user = self.mentor_user
        req_other.organization = self.other_org

        tpl = Template(
            "{% load permission_tags %}"
            "{% can_read user 'attendance' as r %}"
            "{% can_write user 'attendance' as w %}"
            "{{ r }}-{{ w }}"
        )

        out_gos = tpl.render(Context({"request": req_gos, "user": self.mentor_user}))
        self.assertEqual(out_gos, "True-True")

        out_other = tpl.render(
            Context({"request": req_other, "user": self.mentor_user})
        )
        self.assertEqual(out_other, "False-False")

    def test_portal_settings_view_and_update_are_org_scoped(self):
        """PortalSettingsView and PortalPermissionsUpdateView work with active organization."""
        from programs.views.portal_settings import (
            PortalPermissionsUpdateView,
            PortalSettingsView,
        )

        lead_user = get_user_model().objects.create_superuser(
            username="lead_user", password="password123"  # nosec B106
        )
        Group.objects.get_or_create(name="LeadMentor")

        rf = RequestFactory()

        # GET settings for other_org initializes role permissions for other_org
        req_get = rf.get("/programs/settings/?tab=permissions")
        req_get.user = lead_user
        req_get.organization = self.other_org

        view = PortalSettingsView.as_view()
        response = view(req_get)
        self.assertEqual(response.status_code, 200)

        # Verify permissions were created for other_org
        self.assertTrue(
            RolePermission.objects.filter(organization=self.other_org).exists()
        )

        # POST update permissions for other_org
        perm = RolePermission.objects.filter(
            organization=self.other_org, role="Mentor", section="attendance"
        ).first()
        self.assertIsNotNone(perm)

        req_post = rf.post(
            "/programs/settings/permissions/",
            {
                f"read_{perm.id}": "on",
                f"write_{perm.id}": "on",
            },
        )
        req_post.user = lead_user
        req_post.organization = self.other_org
        from django.contrib.messages.storage.fallback import FallbackStorage

        setattr(req_post, "session", {})
        setattr(req_post, "_messages", FallbackStorage(req_post))

        update_view = PortalPermissionsUpdateView.as_view()
        resp_post = update_view(req_post)
        self.assertEqual(resp_post.status_code, 302)

        perm.refresh_from_db()
        self.assertTrue(perm.can_read)
        self.assertTrue(perm.can_write)
        self.assertEqual(perm.organization, self.other_org)
