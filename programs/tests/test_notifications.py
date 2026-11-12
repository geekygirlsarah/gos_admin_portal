import json
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.contrib.messages.storage.fallback import FallbackStorage
from django.core.exceptions import PermissionDenied
from django.db.models.signals import post_init
from django.http import Http404
from django.test import RequestFactory, TestCase
from django.urls import resolve, reverse
from django.utils import timezone

from GoSAdminPortal.test_runner import install_default_test_org_hook
from organizations.models import Organization, UserOrganizationMembership
from programs.models import Notification, UserNotificationStatus
from programs.notification_forms import NotificationForm
from programs.notification_utils import visible_notifications_for_user
from programs.views.notifications import (
    NotificationBadgeView,
    NotificationCreateView,
    NotificationListView,
    NotificationMarkAsReadView,
    NotificationUnreadCountView,
    PlatformNotificationCreateView,
)


class NotificationTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.organization = Organization.objects.create(
            name="Robotics One", slug="robotics-one"
        )
        self.other_organization = Organization.objects.create(
            name="Robotics Two", slug="robotics-two"
        )
        self.lead = User.objects.create_user(
            username="lead", password="password"
        )  # nosec B106
        self.student = User.objects.create_user(
            username="student", password="password"
        )  # nosec B106
        self.parent = User.objects.create_user(
            username="parent", password="password"
        )  # nosec B106
        self.other_lead = User.objects.create_user(
            username="other-lead", password="password"
        )  # nosec B106
        self.staff = User.objects.create_superuser(
            username="platform-admin", email="admin@example.test", password="password"
        )  # nosec B106
        UserOrganizationMembership.objects.create(
            user=self.lead, organization=self.organization, role="LeadMentor"
        )
        UserOrganizationMembership.objects.create(
            user=self.student, organization=self.organization, role="Student"
        )
        UserOrganizationMembership.objects.create(
            user=self.parent, organization=self.organization, role="Parent"
        )
        UserOrganizationMembership.objects.create(
            user=self.other_lead,
            organization=self.other_organization,
            role="LeadMentor",
        )

    def make_request(self, path, user, organization, data=None, method="get"):
        factory = RequestFactory()
        request = getattr(factory, method)(path, data or {})
        request.user = user
        request.organization = organization
        request.session = {}
        request._messages = FallbackStorage(request)
        return request

    def create_platform_notice(self, **kwargs):
        notice = Notification.objects.create(
            organization=self.organization,
            is_platform_wide=False,
            **kwargs,
        )
        Notification.objects.filter(pk=notice.pk).update(
            organization=None,
            is_platform_wide=True,
        )
        return notice

    def test_role_targeted_and_global_notifications_are_scoped_to_active_roles(self):
        global_student_notice = self.create_platform_notice(
            title="Student update",
            message="For students everywhere",
            target_roles=["student"],
        )
        org_parent_notice = Notification.objects.create(
            organization=self.organization,
            title="Parent update",
            message="For this organization's parents",
            target_roles=["parent"],
        )
        Notification.objects.create(
            organization=self.other_organization,
            title="Other organization",
            message="Must stay private",
            target_roles=[],
        )

        student_response = NotificationListView.as_view()(
            self.make_request(
                "/programs/notifications/", self.student, self.organization
            )
        )
        parent_response = NotificationListView.as_view()(
            self.make_request(
                "/programs/notifications/", self.parent, self.organization
            )
        )

        self.assertEqual(
            {item.pk for item in student_response.context_data["notifications"]},
            {global_student_notice.pk},
        )
        self.assertEqual(
            {item.pk for item in parent_response.context_data["notifications"]},
            {org_parent_notice.pk},
        )

    def test_all_role_notice_supports_users_with_multiple_active_roles(self):
        UserOrganizationMembership.objects.create(
            user=self.student, organization=self.organization, role="Parent"
        )
        notice = Notification.objects.create(
            organization=self.organization,
            title="All members",
            message="For every active member",
            target_roles=[],
        )

        response = NotificationListView.as_view()(
            self.make_request(
                "/programs/notifications/", self.student, self.organization
            )
        )

        self.assertIn(notice, response.context_data["notifications"])

    def test_staff_members_receive_all_member_notices_but_not_role_targeted_notices(
        self,
    ):
        User = get_user_model()
        staff_member = User.objects.create_user(
            username="staff-member", password="password"
        )  # nosec B106
        UserOrganizationMembership.objects.create(
            user=staff_member, organization=self.organization, role="Staff"
        )
        all_members_notice = Notification.objects.create(
            organization=self.organization,
            title="Everyone",
            message="For all active members",
            target_roles=[],
        )
        student_notice = Notification.objects.create(
            organization=self.organization,
            title="Students",
            message="For students only",
            target_roles=["student"],
        )
        legacy_staff_notice = Notification.objects.create(
            organization=self.organization,
            title="Legacy staff audience",
            message="No longer targeted to staff",
            target_roles=["staff"],
        )

        response = NotificationListView.as_view()(
            self.make_request(
                "/programs/notifications/", staff_member, self.organization
            )
        )

        self.assertEqual(
            {item.pk for item in response.context_data["notifications"]},
            {all_members_notice.pk},
        )
        self.assertNotIn(student_notice, response.context_data["notifications"])
        self.assertNotIn(legacy_staff_notice, response.context_data["notifications"])

    def test_staff_is_not_a_valid_notification_audience(self):
        self.assertNotIn("staff", dict(Notification.ROLE_CHOICES))
        for scope in ("tenant", "platform"):
            with self.subTest(scope=scope):
                choices = dict(NotificationForm(scope=scope).fields["audience"].choices)
                self.assertNotIn("staff", choices)
                form = NotificationForm(
                    data={
                        "title": "Audience test",
                        "message": "Testing role selection.",
                        "notification_type": "info",
                        "audience": ["staff"],
                    },
                    scope=scope,
                )

                self.assertFalse(form.is_valid())
                self.assertIn("audience", form.errors)

    def test_notification_page_renders_compact_unread_navigation_and_notice(self):
        notice = Notification.objects.create(
            organization=self.organization,
            title="New program feature",
            message="A new update is available.",
            target_roles=["student"],
        )
        request = self.make_request(
            "/programs/notifications/",
            self.student,
            self.organization,
        )
        request.resolver_match = resolve(request.path)

        response = NotificationListView.as_view()(request)
        response.render()

        self.assertContains(response, notice.title)
        self.assertContains(response, 'aria-label="Updates, 1 unread"')
        self.assertContains(response, 'class="bi bi-bell"')
        self.assertContains(response, 'aria-hidden="true">1</span>')
        self.assertNotContains(response, "Review updates")

    def test_notification_page_shows_author_and_source_without_foreign_notices(self):
        User = get_user_model()
        author = User.objects.create_user(
            username="org-publisher",
            first_name="Morgan",
            last_name="Mentor",
        )
        Notification.objects.create(
            organization=self.organization,
            title="Urgent organization update",
            message="For local students.",
            notification_type="error",
            target_roles=["student"],
            created_by=author,
        )
        self.create_platform_notice(
            title="Platform update",
            message="For students everywhere.",
            target_roles=["student"],
            created_by=self.staff,
        )
        Notification.objects.create(
            organization=self.other_organization,
            title="Foreign organization update",
            message="Must remain private.",
            target_roles=["student"],
            created_by=self.other_lead,
        )
        request = self.make_request(
            "/programs/notifications/",
            self.student,
            self.organization,
        )
        request.resolver_match = resolve(request.path)

        response = NotificationListView.as_view()(request)
        response.render()

        self.assertContains(response, "Morgan Mentor")
        self.assertContains(response, "Robotics One")
        self.assertContains(response, "platform-admin")
        self.assertContains(response, "Platform-wide")
        self.assertNotContains(response, "Foreign organization update")

    def test_platform_notification_page_shows_author_and_unknown_author_fallback(self):
        self.create_platform_notice(
            title="Platform administrator update",
            message="Published by a platform admin.",
            target_roles=["admin"],
            created_by=self.staff,
        )
        self.create_platform_notice(
            title="Legacy platform update",
            message="The original publisher is unavailable.",
            target_roles=["admin"],
            created_by=None,
        )
        self.client.force_login(self.staff)

        response = self.client.get(
            reverse("platform_admin:notification_list"), HTTP_HOST="localhost"
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "platform-admin")
        self.assertContains(response, "Platform-wide")
        self.assertContains(response, "Unknown sender")

    def test_notification_author_and_organization_loading_does_not_query_per_notice(
        self,
    ):
        for index in range(3):
            Notification.objects.create(
                organization=self.organization,
                title=f"Update {index}",
                message="Visible to the student.",
                target_roles=["student"],
                created_by=self.lead,
            )

        with self.assertNumQueries(2):
            notifications = visible_notifications_for_user(
                self.student, self.organization
            )
            for notification in notifications:
                self.assertEqual(notification.created_by.username, "lead")
                self.assertEqual(notification.organization.name, "Robotics One")

    def test_inactive_and_expired_notifications_are_not_shown(self):
        inactive = Notification.objects.create(
            organization=self.organization,
            title="Inactive",
            message="Not visible",
            is_active=False,
            target_roles=[],
        )
        expired = Notification.objects.create(
            organization=self.organization,
            title="Expired",
            message="Not visible",
            expires_at=timezone.now() - timedelta(minutes=1),
            target_roles=[],
        )

        response = NotificationListView.as_view()(
            self.make_request(
                "/programs/notifications/", self.student, self.organization
            )
        )

        self.assertNotIn(inactive, response.context_data["notifications"])
        self.assertNotIn(expired, response.context_data["notifications"])

    def test_inactive_membership_does_not_grant_notification_access(self):
        membership = UserOrganizationMembership.objects.get(
            user=self.student,
            organization=self.organization,
        )
        membership.is_active = False
        membership.save(update_fields=["is_active"])
        notice = Notification.objects.create(
            organization=self.organization,
            title="Active members only",
            message="Not for inactive members",
            target_roles=[],
        )

        response = NotificationListView.as_view()(
            self.make_request(
                "/programs/notifications/", self.student, self.organization
            )
        )

        self.assertNotIn(notice, response.context_data["notifications"])

    def test_json_endpoints_use_the_same_role_scoped_recipient_set(self):
        student_notice = self.create_platform_notice(
            title="Student update",
            message="For students",
            target_roles=["student"],
        )
        Notification.objects.create(
            organization=self.organization,
            title="Parent update",
            message="For parents only",
            target_roles=["parent"],
        )

        count_response = NotificationUnreadCountView.as_view()(
            self.make_request(
                "/programs/notifications/unread-count/", self.student, self.organization
            )
        )
        badge_response = NotificationBadgeView.as_view()(
            self.make_request(
                "/programs/notifications/badge/", self.student, self.organization
            )
        )

        self.assertEqual(json.loads(count_response.content), {"unread_count": 1})
        self.assertEqual(
            json.loads(badge_response.content)["notifications"][0]["id"],
            student_notice.pk,
        )

    def test_read_receipts_are_per_user_and_cannot_be_created_for_other_tenant(self):
        notice = Notification.objects.create(
            organization=self.organization,
            title="Student only",
            message="Read state is personal",
            target_roles=["student"],
        )
        post_request = self.make_request(
            f"/programs/notifications/{notice.pk}/read/",
            self.student,
            self.organization,
            method="post",
        )
        response = NotificationMarkAsReadView.as_view()(post_request, pk=notice.pk)
        self.assertEqual(response.status_code, 302)
        self.assertTrue(
            UserNotificationStatus.objects.get(
                user=self.student, notification=notice
            ).is_seen
        )
        self.assertFalse(
            UserNotificationStatus.objects.filter(
                user=self.parent, notification=notice
            ).exists()
        )

        other_notice = Notification.objects.create(
            organization=self.other_organization,
            title="Other tenant",
            message="No cross-tenant receipt",
            target_roles=[],
        )
        with self.assertRaises(Http404):
            NotificationMarkAsReadView.as_view()(
                self.make_request(
                    f"/programs/notifications/{other_notice.pk}/read/",
                    self.student,
                    self.organization,
                    method="post",
                ),
                pk=other_notice.pk,
            )

    def test_lead_can_publish_only_to_the_request_organization(self):
        request = self.make_request(
            "/programs/notifications/new/",
            self.lead,
            self.organization,
            {
                "title": "Team notice",
                "message": "Practice is moved.",
                "notification_type": "info",
                "audience": ["student", "parent"],
                "organization": str(self.other_organization.pk),
            },
            method="post",
        )

        response = NotificationCreateView.as_view()(request)

        self.assertEqual(response.status_code, 302)
        notice = Notification.objects.get(title="Team notice")
        self.assertEqual(notice.organization, self.organization)
        self.assertEqual(set(notice.target_roles), {"student", "parent"})
        self.assertEqual(notice.created_by, self.lead)

    def test_non_lead_and_lead_from_another_organization_cannot_publish(self):
        for user in (self.student, self.other_lead):
            request = self.make_request(
                "/programs/notifications/new/",
                user,
                self.organization,
                method="get",
            )
            response = NotificationCreateView.as_view()(request)
            self.assertEqual(response.status_code, 302)
        self.assertFalse(Notification.objects.exists())

    def test_platform_publisher_creates_global_notice_and_requires_superuser(self):
        post_data = {
            "title": "Platform notice",
            "message": "A new feature is ready.",
            "notification_type": "success",
            "audience": ["student", "mentor"],
        }
        hook_uid = f"default-test-org-{Notification._meta.label}"
        if post_init.disconnect(sender=Notification, dispatch_uid=hook_uid):
            self.addCleanup(install_default_test_org_hook)
        request = self.make_request(
            "/platform-admin/notifications/new/",
            self.staff,
            None,
            post_data,
            method="post",
        )
        response = PlatformNotificationCreateView.as_view()(request)

        self.assertEqual(response.status_code, 302)
        notice = Notification.objects.get(title="Platform notice")
        self.assertIsNone(notice.organization)
        self.assertEqual(notice.created_by, self.staff)
        self.assertTrue(notice.is_platform_wide)
        self.assertEqual(set(notice.target_roles), {"student", "mentor"})

        request = self.make_request(
            "/platform-admin/notifications/new/",
            self.lead,
            None,
            post_data,
            method="post",
        )
        with self.assertRaises(PermissionDenied):
            PlatformNotificationCreateView.as_view()(request)

        request = self.make_request(
            "/platform-admin/notifications/new/",
            self.staff,
            self.organization,
            post_data,
            method="post",
        )
        request.META["HTTP_X_ORG_SLUG"] = self.organization.slug
        with self.assertRaises(Http404):
            PlatformNotificationCreateView.as_view()(request)

    def test_platform_announcements_are_available_from_the_root_domain(self):
        notice = self.create_platform_notice(
            title="Platform notice",
            message="Visible from platform administration.",
            target_roles=["admin"],
        )
        organization_notice = Notification.objects.create(
            organization=self.organization,
            title="Organization notice",
            message="Not part of the platform notice list.",
            target_roles=[],
        )
        self.client.force_login(self.staff)

        response = self.client.get(
            reverse("platform_admin:notification_list"), HTTP_HOST="localhost"
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Platform notice")
        self.assertNotContains(response, "Organization notice")
        self.assertContains(response, 'aria-label="Updates, 1 unread"')
        self.assertContains(response, "bi-bell")
        self.assertNotContains(response, "Review updates")
        self.assertContains(response, 'href="/platform-admin/notifications/"')
        self.assertContains(
            response,
            reverse("platform_admin:notification_create"),
        )

        response = self.client.post(
            reverse(
                "platform_admin:notification_mark_read",
                kwargs={"pk": organization_notice.pk},
            ),
            HTTP_HOST="localhost",
        )

        self.assertRedirects(response, "/", fetch_redirect_response=False)
        self.assertFalse(
            UserNotificationStatus.objects.filter(
                user=self.staff, notification=organization_notice
            ).exists()
        )

        response = self.client.post(
            reverse("platform_admin:notification_mark_read", kwargs={"pk": notice.pk}),
            HTTP_HOST="localhost",
        )

        self.assertRedirects(
            response,
            reverse("platform_admin:notification_list"),
            fetch_redirect_response=False,
        )
        self.assertTrue(
            UserNotificationStatus.objects.get(
                user=self.staff, notification=notice
            ).is_seen
        )

    def test_legacy_notification_url_routes_root_admin_to_platform_updates(self):
        notice = self.create_platform_notice(
            title="Platform notice",
            message="Visible from the legacy root-domain entry point.",
            target_roles=["admin"],
        )
        self.client.force_login(self.staff)

        response = self.client.get("/", HTTP_HOST="localhost")
        self.assertContains(
            response,
            f'href="{reverse("platform_admin:notification_list")}"',
        )

        response = self.client.get(reverse("notification_list"), HTTP_HOST="localhost")

        self.assertRedirects(
            response,
            reverse("platform_admin:notification_list"),
            fetch_redirect_response=False,
        )

        response = self.client.get(
            reverse("platform_admin:notification_list"), HTTP_HOST="localhost"
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, notice.title)
        self.assertContains(response, reverse("platform_admin:notification_create"))

        response = self.client.get(
            reverse("platform_admin:dashboard"), HTTP_HOST="localhost"
        )
        self.assertContains(response, "bi-bell me-1")

    def test_tenant_notification_url_remains_organization_scoped(self):
        self.organization.subdomain = self.organization.slug
        self.organization.save(update_fields=["subdomain"])
        organization_notice = Notification.objects.create(
            organization=self.organization,
            title="Organization notice",
            message="Visible only in this organization.",
            target_roles=["student"],
        )
        self.create_platform_notice(
            title="Admin-only platform notice",
            message="Not visible to this student.",
            target_roles=["admin"],
        )
        self.client.force_login(self.student)

        response = self.client.get(
            reverse("notification_list"),
            HTTP_HOST=f"{self.organization.slug}.localhost",
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, organization_notice.title)
        self.assertNotContains(response, "Admin-only platform notice")

    def test_legacy_notification_url_does_not_redirect_root_posts_to_platform(self):
        self.client.force_login(self.staff)

        response = self.client.post(reverse("notification_list"), HTTP_HOST="localhost")

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], "/")

    def test_legacy_notification_url_does_not_redirect_root_non_superusers_to_platform(
        self,
    ):
        self.client.force_login(self.lead)

        response = self.client.get(reverse("notification_list"), HTTP_HOST="localhost")

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], "/")

    def test_platform_announcement_publishing_redirects_to_root_domain_list(self):
        hook_uid = f"default-test-org-{Notification._meta.label}"
        if post_init.disconnect(sender=Notification, dispatch_uid=hook_uid):
            self.addCleanup(install_default_test_org_hook)
        self.client.force_login(self.staff)
        response = self.client.get(
            reverse("platform_admin:notification_create"), HTTP_HOST="localhost"
        )
        self.assertContains(
            response,
            f'href="{reverse("platform_admin:notification_list")}"',
        )

        response = self.client.post(
            reverse("platform_admin:notification_create"),
            {
                "title": "Platform update",
                "message": "Published from platform administration.",
                "notification_type": "info",
                "audience": ["admin"],
            },
            HTTP_HOST="localhost",
        )

        self.assertRedirects(
            response,
            reverse("platform_admin:notification_list"),
            fetch_redirect_response=False,
        )
        notice = Notification.objects.get(title="Platform update")
        self.assertIsNone(notice.organization)
        self.assertTrue(notice.is_platform_wide)

    def test_platform_announcement_routes_reject_tenant_hosts(self):
        self.client.force_login(self.staff)
        tenant_host = f"{self.organization.slug}.localhost"

        response = self.client.get(
            reverse("platform_admin:notification_list"), HTTP_HOST=tenant_host
        )

        self.assertRedirects(
            response,
            "/",
            fetch_redirect_response=False,
        )

    def test_platform_announcement_routes_reject_non_superusers(self):
        self.client.force_login(self.lead)

        response = self.client.get(
            reverse("platform_admin:notification_list"), HTTP_HOST="localhost"
        )

        self.assertEqual(response.status_code, 403)

    def test_deleted_organization_notice_does_not_become_platform_wide(self):
        notice = Notification.objects.create(
            organization=self.organization,
            title="Archived organization notice",
            message="Retained but never promoted to global scope",
            target_roles=[],
        )

        self.organization.delete()

        notice.refresh_from_db()
        self.assertIsNone(notice.organization)
        self.assertFalse(notice.is_platform_wide)
        self.assertNotIn(
            notice,
            [
                item
                for item in NotificationListView.as_view()(
                    self.make_request(
                        "/programs/notifications/",
                        self.staff,
                        None,
                    )
                ).context_data["notifications"]
            ],
        )

    def test_established_program_routes_remain_reversible(self):
        self.assertEqual(reverse("program_list"), "/programs/")
        self.assertEqual(reverse("student_list"), "/programs/students/")
        self.assertEqual(
            reverse("students_sample_csv"), "/programs/imports/samples/students.csv"
        )
        self.assertEqual(reverse("notification_list"), "/programs/notifications/")
