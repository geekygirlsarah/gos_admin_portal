import json
from datetime import timedelta
from unittest.mock import patch

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
from programs.notification_rendering import notification_message_for_display
from programs.notification_utils import visible_notifications_for_user
from programs.views.notifications import (
    NotificationBadgeView,
    NotificationCreateView,
    NotificationDismissView,
    NotificationListView,
    NotificationManagementListView,
    NotificationMarkAsReadView,
    NotificationUnreadCountView,
    NotificationWithdrawView,
    PlatformNotificationCreateView,
    PlatformNotificationManagementListView,
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
        self.assertContains(response, 'aria-label="Announcements, 1 unread"')
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

        for route_name in ("notification_list", "notification_list_legacy"):
            with self.subTest(route=route_name):
                response = self.client.get(
                    reverse(f"platform_admin:{route_name}"), HTTP_HOST="localhost"
                )

                self.assertEqual(response.status_code, 200)
                self.assertContains(response, "Platform notice")
                self.assertNotContains(response, "Organization notice")
                self.assertContains(response, 'aria-label="Announcements, 1 unread"')
                self.assertContains(response, "bi-bell")
                self.assertNotContains(response, "Review updates")
                self.assertContains(
                    response,
                    f'href="{reverse("platform_admin:notification_list")}"',
                )
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

    def test_announcement_routes_are_canonical_and_keep_legacy_aliases(self):
        tenant_routes = (
            ("notification_list", "notification_list_legacy", {}, ""),
            (
                "notification_unread_count",
                "notification_unread_count_legacy",
                {},
                "unread-count/",
            ),
            (
                "notification_badge",
                "notification_badge_legacy",
                {},
                "badge/",
            ),
            (
                "notification_create",
                "notification_create_legacy",
                {},
                "new/",
            ),
            (
                "notification_management",
                "notification_management_legacy",
                {},
                "manage/",
            ),
            (
                "notification_dismiss",
                "notification_dismiss_legacy",
                {"pk": 123},
                "123/dismiss/",
            ),
            (
                "notification_withdraw",
                "notification_withdraw_legacy",
                {"pk": 123},
                "123/withdraw/",
            ),
            (
                "notification_mark_read",
                "notification_mark_read_legacy",
                {"pk": 123},
                "123/read/",
            ),
        )
        for canonical_name, legacy_name, kwargs, suffix in tenant_routes:
            with self.subTest(route=canonical_name):
                canonical_path = reverse(canonical_name, kwargs=kwargs or None)
                legacy_path = reverse(legacy_name, kwargs=kwargs or None)
                self.assertEqual(canonical_path, f"/programs/announcements/{suffix}")
                self.assertEqual(legacy_path, f"/programs/notifications/{suffix}")
                self.assertIs(
                    resolve(canonical_path).func.view_class,
                    resolve(legacy_path).func.view_class,
                )

        platform_routes = (
            ("notification_list", ""),
            ("notification_management", "manage/"),
            ("notification_create", "new/"),
            ("notification_dismiss", "123/dismiss/"),
            ("notification_withdraw", "123/withdraw/"),
            ("notification_mark_read", "123/read/"),
        )
        for route_name, suffix in platform_routes:
            kwargs = {"pk": 123} if "123" in suffix else None
            canonical_name = f"platform_admin:{route_name}"
            legacy_name = f"platform_admin:{route_name}_legacy"
            with self.subTest(route=canonical_name):
                canonical_path = reverse(canonical_name, kwargs=kwargs)
                legacy_path = reverse(legacy_name, kwargs=kwargs)
                self.assertEqual(
                    canonical_path, f"/platform-admin/announcements/{suffix}"
                )
                self.assertEqual(legacy_path, f"/platform-admin/notifications/{suffix}")
                self.assertIs(
                    resolve(canonical_path).func.view_class,
                    resolve(legacy_path).func.view_class,
                )

    def test_legacy_tenant_announcement_actions_still_accept_posts(self):
        self.organization.subdomain = self.organization.slug
        self.organization.save(update_fields=["subdomain"])
        read_notice = Notification.objects.create(
            organization=self.organization,
            title="Read announcement",
            message="Read through the legacy URL.",
            target_roles=["student"],
        )
        dismiss_notice = Notification.objects.create(
            organization=self.organization,
            title="Dismissed announcement",
            message="Dismiss through the legacy URL.",
            target_roles=["student"],
        )
        withdraw_notice = Notification.objects.create(
            organization=self.organization,
            title="Withdrawn announcement",
            message="Withdraw through the legacy URL.",
            target_roles=[],
        )
        host = f"{self.organization.slug}.localhost"

        self.client.force_login(self.student)
        response = self.client.post(
            reverse("notification_mark_read_legacy", kwargs={"pk": read_notice.pk}),
            HTTP_HOST=host,
        )
        self.assertRedirects(
            response, reverse("notification_list"), fetch_redirect_response=False
        )
        self.assertTrue(
            UserNotificationStatus.objects.get(
                user=self.student, notification=read_notice
            ).is_seen
        )

        response = self.client.post(
            reverse("notification_dismiss_legacy", kwargs={"pk": dismiss_notice.pk}),
            HTTP_HOST=host,
        )
        self.assertRedirects(
            response, reverse("notification_list"), fetch_redirect_response=False
        )
        self.assertTrue(
            UserNotificationStatus.objects.get(
                user=self.student, notification=dismiss_notice
            ).is_dismissed
        )

        self.client.force_login(self.lead)
        response = self.client.post(
            reverse("notification_withdraw_legacy", kwargs={"pk": withdraw_notice.pk}),
            HTTP_HOST=host,
        )
        self.assertRedirects(
            response,
            reverse("notification_management"),
            fetch_redirect_response=False,
        )
        withdraw_notice.refresh_from_db()
        self.assertFalse(withdraw_notice.is_active)

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

        for route_name in ("notification_list", "notification_list_legacy"):
            with self.subTest(route=route_name):
                response = self.client.get(
                    reverse(route_name),
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

    def test_notification_form_sanitizes_limited_html_and_rejects_empty_markup(self):
        form = NotificationForm(
            data={
                "title": "Formatted update",
                "message": (
                    "<p>Bring <strong>closed-toe shoes</strong>.</p>"
                    '<ul><li onclick="bad()">Safety glasses</li></ul>'
                    "<script>bad()</script>"
                ),
                "notification_type": "info",
                "audience": ["student"],
                "expires_at": "",
            }
        )

        self.assertTrue(form.is_valid(), form.errors)
        notice = form.save(organization=self.organization, created_by=self.lead)

        self.assertIn("<strong>closed-toe shoes</strong>", notice.message)
        self.assertIn("<ul><li>Safety glasses</li></ul>", notice.message)
        self.assertNotIn("onclick", notice.message)
        self.assertNotIn("<script", notice.message)
        self.assertTrue(notice.message_is_html)

        empty_form = NotificationForm(
            data={
                "title": "Empty update",
                "message": "<p><br></p>",
                "notification_type": "info",
                "audience": ["student"],
                "expires_at": "",
            }
        )
        self.assertFalse(empty_form.is_valid())
        self.assertIn("message", empty_form.errors)

    def test_notification_page_separates_new_and_previous_updates_and_escapes_legacy(
        self,
    ):
        new_notice = Notification.objects.create(
            organization=self.organization,
            title="New formatted update",
            message="<p><strong>New</strong> update</p>",
            message_is_html=True,
            target_roles=["student"],
        )
        previous_notice = Notification.objects.create(
            organization=self.organization,
            title="Previous legacy update",
            message="<img src=x onerror=alert(1)>",
            target_roles=["student"],
        )
        UserNotificationStatus.objects.create(
            user=self.student,
            notification=previous_notice,
            is_seen=True,
        )
        request = self.make_request(
            "/programs/notifications/",
            self.student,
            self.organization,
        )

        response = NotificationListView.as_view()(request)
        response.render()

        self.assertContains(response, "New announcements")
        self.assertContains(response, "Previous announcements")
        self.assertContains(response, "<strong>New</strong> update", html=True)
        self.assertContains(response, "&lt;img src=x onerror=alert(1)&gt;")
        self.assertNotContains(response, '<img src="x"')
        self.assertLess(
            response.content.index(new_notice.title.encode()),
            response.content.index(previous_notice.title.encode()),
        )

    def test_notification_display_rebuilds_only_escaped_allowlisted_markup(self):
        notice = Notification(
            message="Stored announcement",
            message_is_html=True,
        )
        sanitized_markup = (
            '<p class="notice" onclick="run()">One '
            '<strong onmouseover="run()">two</strong> '
            "&lt;img src=x onerror=run()&gt;</p>"
            "<p><b>B</b><em>E</em><i>I</i><u>U &amp; V</u></p>"
            "<br><ul><li>Safe</li></ul><ol><li>Numbered</li></ol>"
            "<script>run()</script><svg onload=run()>markup</svg>"
        )

        with patch(
            "programs.notification_rendering.sanitize_notification_html",
            return_value=sanitized_markup,
        ):
            display_message = notification_message_for_display(notice)

        self.assertEqual(
            display_message,
            "<p>One <strong>two</strong> &lt;img src=x onerror=run()&gt;</p>"
            "<p><b>B</b><em>E</em><i>I</i><u>U &amp; V</u></p>"
            "<br><ul><li>Safe</li></ul><ol><li>Numbered</li></ol>run()markup",
        )

    def test_sanitized_announcement_renders_for_recipient_and_publisher(self):
        notice = Notification.objects.create(
            organization=self.organization,
            title="Safe formatted update",
            message=(
                "<p title='unsafe'>Read &lt;img src=x onerror=run()&gt; and "
                "<strong>follow along</strong>.</p>"
                "<script>run()</script><ul><li onclick='run()'>First item</li>"
                "<li>Second item</ul>"
            ),
            message_is_html=True,
            target_roles=["student"],
        )

        recipient_response = NotificationListView.as_view()(
            self.make_request(
                "/programs/notifications/",
                self.student,
                self.organization,
            )
        )
        recipient_response.render()

        publisher_response = NotificationManagementListView.as_view()(
            self.make_request(
                "/programs/notifications/manage/",
                self.lead,
                self.organization,
            )
        )
        publisher_response.render()

        for response in (recipient_response, publisher_response):
            self.assertContains(response, "<strong>follow along</strong>", html=True)
            self.assertContains(response, "&lt;img src=x onerror=run()&gt;")
            self.assertContains(response, "<li>First item</li>", html=True)
            self.assertNotContains(response, "<script>")
            self.assertNotContains(response, "onclick")
            self.assertNotContains(response, 'title="unsafe"')

    def test_notification_dismissal_is_per_user_and_scoped_to_visible_updates(self):
        notice = self.create_platform_notice(
            title="Dismissible platform update",
            message="Visible in each member organization.",
            target_roles=["student"],
        )
        UserOrganizationMembership.objects.create(
            user=self.student,
            organization=self.other_organization,
            role="Student",
        )
        other_student = get_user_model().objects.create_user(
            username="other-student",
        )
        UserOrganizationMembership.objects.create(
            user=other_student,
            organization=self.organization,
            role="Student",
        )

        request = self.make_request(
            f"/programs/notifications/{notice.pk}/dismiss/",
            self.student,
            self.organization,
            method="post",
        )
        response = NotificationDismissView.as_view()(request, pk=notice.pk)

        self.assertEqual(response.status_code, 302)
        status = UserNotificationStatus.objects.get(
            user=self.student,
            notification=notice,
        )
        self.assertTrue(status.is_dismissed)
        self.assertFalse(status.is_seen)
        self.assertNotIn(
            notice,
            visible_notifications_for_user(self.student, self.organization),
        )
        self.assertNotIn(
            notice,
            visible_notifications_for_user(self.student, self.other_organization),
        )
        self.assertIn(
            notice,
            visible_notifications_for_user(other_student, self.organization),
        )

    def test_notification_dismissal_rejects_another_organizations_update(self):
        notice = Notification.objects.create(
            organization=self.other_organization,
            title="Private organization update",
            message="Must remain scoped.",
            target_roles=["student"],
        )
        request = self.make_request(
            f"/programs/notifications/{notice.pk}/dismiss/",
            self.student,
            self.organization,
            method="post",
        )

        with self.assertRaises(Http404):
            NotificationDismissView.as_view()(request, pk=notice.pk)
        self.assertFalse(
            UserNotificationStatus.objects.filter(
                user=self.student,
                notification=notice,
            ).exists()
        )

    def test_tenant_management_and_withdrawal_require_a_lead_mentor(self):
        notice = Notification.objects.create(
            organization=self.organization,
            title="Protected tenant update",
            message="Only a publisher can withdraw this.",
            target_roles=[],
        )
        list_request = self.make_request(
            "/programs/notifications/manage/",
            self.student,
            self.organization,
        )
        list_response = NotificationManagementListView.as_view()(list_request)
        self.assertEqual(list_response.status_code, 302)
        self.assertEqual(list_response["Location"], reverse("home"))

        withdraw_request = self.make_request(
            f"/programs/notifications/{notice.pk}/withdraw/",
            self.student,
            self.organization,
            method="post",
        )
        withdraw_response = NotificationWithdrawView.as_view()(
            withdraw_request,
            pk=notice.pk,
        )
        self.assertEqual(withdraw_response.status_code, 302)
        self.assertEqual(withdraw_response["Location"], reverse("home"))

        notice.refresh_from_db()
        self.assertTrue(notice.is_active)

    def test_tenant_management_includes_dismissed_and_role_targeted_updates_only_in_scope(
        self,
    ):
        dismissed_notice = Notification.objects.create(
            organization=self.organization,
            title="Dismissed lead update",
            message="Still manageable.",
            target_roles=["lead_mentor"],
            created_by=self.lead,
        )
        role_targeted_notice = Notification.objects.create(
            organization=self.organization,
            title="Student-only update",
            message="Not addressed to the publisher.",
            target_roles=["student"],
            created_by=self.lead,
        )
        foreign_notice = Notification.objects.create(
            organization=self.other_organization,
            title="Other organization update",
            message="Must stay private.",
            target_roles=[],
        )
        self.client.force_login(self.lead)

        dismiss_request = self.make_request(
            f"/programs/notifications/{dismissed_notice.pk}/dismiss/",
            self.lead,
            self.organization,
            method="post",
        )
        NotificationDismissView.as_view()(dismiss_request, pk=dismissed_notice.pk)
        request = self.make_request(
            "/programs/notifications/manage/",
            self.lead,
            self.organization,
        )
        response = NotificationManagementListView.as_view()(request)
        response.render()

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, dismissed_notice.title)
        self.assertContains(response, role_targeted_notice.title)
        self.assertNotContains(response, foreign_notice.title)

        request = self.make_request(
            f"/programs/notifications/{foreign_notice.pk}/withdraw/",
            self.lead,
            self.organization,
            method="post",
        )

        with self.assertRaises(Http404):
            NotificationWithdrawView.as_view()(request, pk=foreign_notice.pk)
        dismissed_notice.refresh_from_db()
        foreign_notice.refresh_from_db()
        self.assertTrue(dismissed_notice.is_active)
        self.assertTrue(foreign_notice.is_active)

    def test_tenant_publisher_can_withdraw_own_scope_without_deleting_announcement(
        self,
    ):
        notice = Notification.objects.create(
            organization=self.organization,
            title="Retire this update",
            message="Keep its history.",
            target_roles=[],
            created_by=self.lead,
        )
        self.client.force_login(self.lead)

        response = self.client.post(
            reverse("notification_withdraw", kwargs={"pk": notice.pk}),
            HTTP_HOST=f"{self.organization.slug}.localhost",
        )

        self.assertEqual(response.status_code, 302)
        notice.refresh_from_db()
        self.assertFalse(notice.is_active)
        self.assertTrue(Notification.objects.filter(pk=notice.pk).exists())

    def test_platform_management_is_root_superuser_only_and_withdraws_without_deleting(
        self,
    ):
        platform_notice = self.create_platform_notice(
            title="Global update to manage",
            message="Visible only on the platform list.",
            target_roles=["admin"],
        )
        tenant_notice = Notification.objects.create(
            organization=self.organization,
            title="Tenant update not on platform list",
            message="Must remain scoped.",
            target_roles=[],
        )
        self.client.force_login(self.staff)

        for route_name in (
            "notification_management",
            "notification_management_legacy",
        ):
            with self.subTest(route=route_name):
                response = self.client.get(
                    reverse(f"platform_admin:{route_name}"),
                    HTTP_HOST="localhost",
                )

                self.assertEqual(response.status_code, 200)
                self.assertContains(response, platform_notice.title)
                self.assertNotContains(response, tenant_notice.title)
                self.assertContains(response, "Manage active announcements")

        response = self.client.post(
            reverse(
                "platform_admin:notification_withdraw",
                kwargs={"pk": platform_notice.pk},
            ),
            HTTP_HOST="localhost",
        )

        self.assertEqual(response.status_code, 302)
        platform_notice.refresh_from_db()
        self.assertFalse(platform_notice.is_active)
        self.assertTrue(Notification.objects.filter(pk=platform_notice.pk).exists())

        self.client.force_login(self.lead)
        response = self.client.get(
            reverse("platform_admin:notification_management"),
            HTTP_HOST="localhost",
        )
        self.assertEqual(response.status_code, 403)

        tenant_request = self.make_request(
            "/platform-admin/notifications/manage/",
            self.staff,
            self.organization,
        )
        tenant_request.META["HTTP_HOST"] = f"{self.organization.slug}.localhost"
        with self.assertRaises(Http404):
            PlatformNotificationManagementListView.as_view()(tenant_request)

    def test_notification_badge_preview_contains_text_not_announcement_markup(self):
        self.create_platform_notice(
            title="List preview",
            message="<p>First line</p><ul><li>Next step</li></ul>",
            message_is_html=True,
            target_roles=["student"],
        )
        self.client.force_login(self.student)

        response = self.client.get(
            reverse("notification_badge"),
            HTTP_HOST=f"{self.organization.slug}.localhost",
        )

        self.assertEqual(response.status_code, 200)
        payload = json.loads(response.content)
        self.assertNotIn("<p>", payload["notifications"][0]["message"])
        self.assertNotIn("<li>", payload["notifications"][0]["message"])

    def test_announcement_editor_uses_only_basic_formatting_controls(self):
        self.client.force_login(self.lead)

        response = self.client.get(
            reverse("notification_create"),
            HTTP_HOST=f"{self.organization.slug}.localhost",
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Send an announcement")
        self.assertContains(response, "Announcement type")
        self.assertContains(response, 'data-email-editor-mode="announcement"')

    def test_established_program_routes_remain_reversible(self):
        self.assertEqual(reverse("program_list"), "/programs/")
        self.assertEqual(reverse("student_list"), "/programs/students/")
        self.assertEqual(
            reverse("students_sample_csv"), "/programs/imports/samples/students.csv"
        )
        self.assertEqual(reverse("notification_list"), "/programs/announcements/")
