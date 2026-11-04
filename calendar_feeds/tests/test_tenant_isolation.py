"""Tenant isolation regressions for calendar feeds and subscriptions."""

from datetime import datetime, timedelta

from django.contrib.auth.models import AnonymousUser, User
from django.test import RequestFactory, TestCase
from django.utils import timezone

from calendar_feeds.forms import CalendarFeedForm
from calendar_feeds.ics import FeedICSView, PersonalICSFeedView, PublicICSFeedView
from calendar_feeds.models import (
    CalendarEvent,
    CalendarFeed,
    CalendarFeedACL,
    UserCalendarToken,
)
from organizations.models import Organization, UserOrganizationMembership
from programs.models import Program


class CalendarFeedTenantIsolationTests(TestCase):
    def setUp(self):
        self.gos = Organization.objects.get(slug="gos")
        self.other = Organization.objects.create(
            name="Other Organization", slug="calendar-other", subdomain="calendar-other"
        )
        self.other_host = "calendar-other.localhost"

    def make_feed(self, organization, *, name, slug, visibility, acls=None):
        feed = CalendarFeed.objects.create(
            organization=organization,
            name=name,
            slug=slug,
            visibility=visibility,
        )
        for role, (can_read, can_write) in (acls or {}).items():
            CalendarFeedACL.objects.create(
                feed=feed,
                role=role,
                can_read=can_read,
                can_write=can_write,
            )
        return feed

    def make_event(self, feed, title):
        start = timezone.make_aware(
            datetime.combine(
                timezone.localdate() + timedelta(days=7), datetime.min.time()
            ).replace(hour=10),
            timezone.get_current_timezone(),
        )
        return CalendarEvent.objects.create(
            organization=feed.organization,
            feed=feed,
            title=title,
            start_datetime=start,
            end_datetime=start + timedelta(hours=1),
        )

    def make_member(self, username, organization, role, *, is_active=True):
        user = User.objects.create_user(
            username=username, password="password123"
        )  # nosec B106
        UserOrganizationMembership.objects.create(
            user=user,
            organization=organization,
            role=role,
            is_active=is_active,
        )
        return user

    def make_token(self, user):
        return UserCalendarToken.objects.create(user=user).token

    def test_public_aggregate_feed_contains_only_request_organization_events(self):
        gos_feed = self.make_feed(
            self.gos,
            name="GoS Public",
            slug="gos-public-tenant-test",
            visibility=CalendarFeed.VISIBILITY_ANONYMOUS,
        )
        other_feed = self.make_feed(
            self.other,
            name="Other Public",
            slug="other-public-tenant-test",
            visibility=CalendarFeed.VISIBILITY_ANONYMOUS,
        )
        self.make_event(gos_feed, "GoS only public event")
        self.make_event(other_feed, "Other only public event")

        response = self.client.get("/calendar/feed.ics", HTTP_HOST=self.other_host)

        self.assertEqual(response.status_code, 200)
        body = response.content.decode("utf-8")
        self.assertIn("Other only public event", body)
        self.assertNotIn("GoS only public event", body)

    def test_feed_slug_is_resolved_inside_request_organization(self):
        gos_feed = self.make_feed(
            self.gos,
            name="GoS Shared Slug",
            slug="shared-tenant-slug",
            visibility=CalendarFeed.VISIBILITY_ANONYMOUS,
        )
        other_feed = self.make_feed(
            self.other,
            name="Other Shared Slug",
            slug="shared-tenant-slug",
            visibility=CalendarFeed.VISIBILITY_ANONYMOUS,
        )
        self.make_event(gos_feed, "GoS shared-slug event")
        self.make_event(other_feed, "Other shared-slug event")

        response = self.client.get(
            "/calendar/shared-tenant-slug/feed.ics", HTTP_HOST=self.other_host
        )

        self.assertEqual(response.status_code, 200)
        body = response.content.decode("utf-8")
        self.assertIn("Other shared-slug event", body)
        self.assertNotIn("GoS shared-slug event", body)

    def test_feed_does_not_emit_events_owned_by_another_organization(self):
        feed = self.make_feed(
            self.other,
            name="Other Event Ownership Feed",
            slug="other-event-ownership-test",
            visibility=CalendarFeed.VISIBILITY_ANONYMOUS,
        )
        event = self.make_event(feed, "Mismatched organization event")
        event.organization = self.gos
        event.save(update_fields=["organization"])

        response = self.client.get("/calendar/feed.ics", HTTP_HOST=self.other_host)

        self.assertEqual(response.status_code, 200)
        self.assertNotIn(
            "Mismatched organization event", response.content.decode("utf-8")
        )

    def test_private_feed_token_requires_active_membership_in_feed_organization(self):
        feed = self.make_feed(
            self.other,
            name="Other Private Feed",
            slug="other-private-membership-test",
            visibility=CalendarFeed.VISIBILITY_MEMBERS,
            acls={"LeadMentor": (True, True)},
        )
        self.make_event(feed, "Other private event")
        user = self.make_member("gos-only-token-user", self.gos, "LeadMentor")
        token = self.make_token(user)

        response = self.client.get(
            f"/calendar/{feed.slug}/feed.ics?token={token}",
            HTTP_HOST=self.other_host,
        )

        self.assertEqual(response.status_code, 403)

    def test_private_feed_acl_uses_role_from_feed_organization(self):
        feed = self.make_feed(
            self.other,
            name="Other Parent Feed",
            slug="other-parent-acl-test",
            visibility=CalendarFeed.VISIBILITY_MEMBERS,
            acls={"Parent": (False, False)},
        )
        self.make_event(feed, "Other parent-restricted event")
        user = self.make_member("multi-org-role-user", self.gos, "LeadMentor")
        UserOrganizationMembership.objects.create(
            user=user,
            organization=self.other,
            role="Parent",
            is_active=True,
        )
        token = self.make_token(user)

        response = self.client.get(
            f"/calendar/{feed.slug}/feed.ics?token={token}",
            HTTP_HOST=self.other_host,
        )

        self.assertEqual(response.status_code, 403)

    def test_private_feed_and_write_require_active_membership_in_feed_organization(
        self,
    ):
        feed = self.make_feed(
            self.other,
            name="Other Parent Write Feed",
            slug="other-parent-write-test",
            visibility=CalendarFeed.VISIBILITY_MEMBERS,
            acls={"Parent": (True, True)},
        )
        active_parent = self.make_member("active-other-parent", self.other, "Parent")
        inactive_lead = self.make_member(
            "inactive-other-lead", self.other, "LeadMentor", is_active=False
        )
        foreign_lead = self.make_member("foreign-lead", self.gos, "LeadMentor")
        superuser = User.objects.create(
            username="calendar-tenant-superuser", is_superuser=True, is_staff=True
        )

        self.assertTrue(feed.user_can_read(active_parent))
        self.assertTrue(feed.user_can_write(active_parent))
        self.assertFalse(feed.user_can_read(inactive_lead))
        self.assertFalse(feed.user_can_write(inactive_lead))
        self.assertFalse(feed.user_can_read(foreign_lead))
        self.assertFalse(feed.user_can_write(foreign_lead))
        self.assertTrue(feed.user_can_read(superuser))
        self.assertTrue(feed.user_can_write(superuser))

    def test_private_feed_token_rejects_inactive_organization_membership(self):
        feed = self.make_feed(
            self.other,
            name="Other Inactive Membership Feed",
            slug="other-inactive-membership-test",
            visibility=CalendarFeed.VISIBILITY_MEMBERS,
            acls={"LeadMentor": (True, True)},
        )
        user = self.make_member(
            "inactive-other-token-user", self.other, "LeadMentor", is_active=False
        )
        token = self.make_token(user)

        response = self.client.get(
            f"/calendar/{feed.slug}/feed.ics?token={token}",
            HTTP_HOST=self.other_host,
        )

        self.assertEqual(response.status_code, 403)

    def test_personal_subscription_contains_only_request_organization_events(self):
        gos_feed = self.make_feed(
            self.gos,
            name="GoS Personal Public",
            slug="gos-personal-public-test",
            visibility=CalendarFeed.VISIBILITY_ANONYMOUS,
        )
        other_feed = self.make_feed(
            self.other,
            name="Other Personal Public",
            slug="other-personal-public-test",
            visibility=CalendarFeed.VISIBILITY_ANONYMOUS,
        )
        self.make_event(gos_feed, "GoS personal-only event")
        self.make_event(other_feed, "Other personal-only event")
        user = self.make_member("multi-org-token-user", self.gos, "Parent")
        UserOrganizationMembership.objects.create(
            user=user,
            organization=self.other,
            role="Parent",
            is_active=True,
        )
        token = self.make_token(user)

        response = self.client.get(
            f"/calendar/my/{token}/feed.ics", HTTP_HOST=self.other_host
        )

        self.assertEqual(response.status_code, 200)
        body = response.content.decode("utf-8")
        self.assertIn("Other personal-only event", body)
        self.assertNotIn("GoS personal-only event", body)

    def test_personal_subscription_rejects_user_without_active_tenant_membership(self):
        feed = self.make_feed(
            self.other,
            name="Other Personal Private",
            slug="other-personal-private-test",
            visibility=CalendarFeed.VISIBILITY_MEMBERS,
            acls={"LeadMentor": (True, True)},
        )
        self.make_event(feed, "Other nonmember personal event")
        user = self.make_member("nonmember-personal-token-user", self.gos, "LeadMentor")
        token = self.make_token(user)

        response = self.client.get(
            f"/calendar/my/{token}/feed.ics", HTTP_HOST=self.other_host
        )

        self.assertEqual(response.status_code, 403)

    def test_personal_subscription_rejects_inactive_user(self):
        self.make_member("inactive-personal-token-user", self.other, "LeadMentor")
        user = User.objects.get(username="inactive-personal-token-user")
        user.is_active = False
        user.save(update_fields=["is_active"])
        token = self.make_token(user)

        response = self.client.get(
            f"/calendar/my/{token}/feed.ics", HTTP_HOST=self.other_host
        )

        self.assertEqual(response.status_code, 403)

    def test_feed_form_limits_programs_and_rejects_foreign_program(self):
        gos_program = Program.objects.create(
            organization=self.gos, name="GoS Calendar Program"
        )
        other_program = Program.objects.create(
            organization=self.other, name="Other Calendar Program"
        )
        form = CalendarFeedForm(organization=self.other)

        self.assertEqual(
            list(form.fields["program"].queryset.values_list("pk", flat=True)),
            [other_program.pk],
        )
        self.assertNotIn(
            gos_program.pk, form.fields["program"].queryset.values_list("pk", flat=True)
        )

        bound_form = CalendarFeedForm(
            organization=self.other,
            data={
                "name": "Other Calendar",
                "description": "",
                "color": "#0d6efd",
                "visibility": CalendarFeed.VISIBILITY_MEMBERS,
                "source_type": CalendarFeed.SOURCE_MANUAL,
                "program": str(gos_program.pk),
                "is_active": "on",
            },
        )
        self.assertFalse(bound_form.is_valid())
        self.assertIn("program", bound_form.errors)

    def test_ics_endpoints_reject_missing_organization_context(self):
        public_request = RequestFactory().get("/calendar/feed.ics")
        public_request.organization = None
        self.assertEqual(PublicICSFeedView.as_view()(public_request).status_code, 404)

        private_feed = self.make_feed(
            self.gos,
            name="Missing Context Feed",
            slug="missing-context-feed-test",
            visibility=CalendarFeed.VISIBILITY_ANONYMOUS,
        )
        feed_request = RequestFactory().get(
            "/calendar/missing-context-feed-test/feed.ics"
        )
        feed_request.organization = None
        feed_request.user = AnonymousUser()
        self.assertEqual(
            FeedICSView.as_view()(feed_request, slug=private_feed.slug).status_code,
            404,
        )

        token = self.make_token(
            self.make_member("missing-context-user", self.gos, "Parent")
        )
        personal_request = RequestFactory().get(f"/calendar/my/{token}/feed.ics")
        personal_request.organization = None
        self.assertEqual(
            PersonalICSFeedView.as_view()(personal_request, token=token).status_code,
            404,
        )
