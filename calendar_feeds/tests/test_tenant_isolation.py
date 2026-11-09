"""Tenant isolation regressions for calendar feeds and subscriptions."""

from datetime import datetime, timedelta
from unittest.mock import patch

from django.contrib.auth.models import AnonymousUser, User
from django.http import Http404
from django.test import RequestFactory, TestCase
from django.urls import reverse
from django.utils import timezone

from audit.events import AuditEvent
from audit.models import AuditLog
from calendar_feeds.forms import CalendarFeedACLInlineFormSet, CalendarFeedForm
from calendar_feeds.ics import FeedICSView, PersonalICSFeedView, PublicICSFeedView
from calendar_feeds.models import (
    CalendarEvent,
    CalendarFeed,
    CalendarFeedACL,
    EventException,
    UserCalendarToken,
)
from calendar_feeds.views import CalendarEventsAPIView, CalendarView, FeedACLUpdateView
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
                organization=organization,
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

    def test_foreign_acl_cannot_grant_private_feed_read_or_write(self):
        feed = self.make_feed(
            self.other,
            name="Other Parent Foreign ACL Feed",
            slug="other-parent-foreign-acl-test",
            visibility=CalendarFeed.VISIBILITY_MEMBERS,
        )
        CalendarFeedACL.objects.create(
            organization=self.other,
            feed=feed,
            role="Parent",
            can_read=False,
            can_write=False,
        )
        CalendarFeedACL.objects.create(
            organization=self.gos,
            feed=feed,
            role="Parent",
            can_read=True,
            can_write=True,
        )
        parent = self.make_member("other-parent-foreign-acl", self.other, "Parent")

        self.assertFalse(feed.user_can_read(parent))
        self.assertFalse(feed.user_can_write(parent))

        # Role authorization must not rely on a separate consistency precheck.
        with patch.object(feed, "has_foreign_acls", side_effect=AssertionError):
            self.assertFalse(feed.user_can_read(parent))
            self.assertFalse(feed.user_can_write(parent))

    def test_public_feed_remains_readable_with_foreign_acl(self):
        feed = self.make_feed(
            self.other,
            name="Public Feed With Foreign ACL",
            slug="public-foreign-acl-test",
            visibility=CalendarFeed.VISIBILITY_ANONYMOUS,
        )
        CalendarFeedACL.objects.create(
            organization=self.gos,
            feed=feed,
            role="Parent",
            can_read=False,
            can_write=False,
        )

        self.assertTrue(feed.user_can_read(AnonymousUser()))

    def test_acl_consistency_and_role_checks_share_one_query(self):
        feed = self.make_feed(
            self.other,
            name="Efficient ACL Feed",
            slug="efficient-acl-tenant-test",
            visibility=CalendarFeed.VISIBILITY_MEMBERS,
            acls={"Parent": (True, True)},
        )
        parent = self.make_member("efficient-acl-parent", self.other, "Parent")

        with self.assertNumQueries(3):
            self.assertTrue(feed.user_can_read(parent))
        with self.assertNumQueries(3):
            self.assertTrue(feed.user_can_write(parent))

    def test_acl_formset_does_not_include_foreign_owned_rows(self):
        feed = self.make_feed(
            self.other,
            name="ACL Formset Feed",
            slug="acl-formset-foreign-row-test",
            visibility=CalendarFeed.VISIBILITY_MEMBERS,
        )
        foreign_acl = CalendarFeedACL.objects.create(
            organization=self.gos,
            feed=feed,
            role="Parent",
            can_read=True,
            can_write=True,
        )

        formset = CalendarFeedACLInlineFormSet(instance=feed, prefix="acls")

        self.assertNotIn(
            foreign_acl.pk,
            [form.instance.pk for form in formset.forms if form.instance.pk],
        )

        submitted_formset = CalendarFeedACLInlineFormSet(
            data={
                "acls-TOTAL_FORMS": "1",
                "acls-INITIAL_FORMS": "1",
                "acls-MIN_NUM_FORMS": "0",
                "acls-MAX_NUM_FORMS": "1000",
                "acls-0-id": str(foreign_acl.pk),
                "acls-0-role": "Parent",
                "acls-0-can_read": "",
                "acls-0-can_write": "",
            },
            instance=feed,
            prefix="acls",
        )

        self.assertTrue(submitted_formset.is_valid(), submitted_formset.errors)
        submitted_formset.save()
        foreign_acl.refresh_from_db()
        self.assertTrue(foreign_acl.can_read)
        self.assertTrue(foreign_acl.can_write)

    def test_calendar_page_and_events_api_reject_missing_organization(self):
        page_request = RequestFactory().get("/calendar/")
        page_request.organization = None
        page_request.user = AnonymousUser()
        with self.assertRaises(Http404):
            CalendarView.as_view()(page_request)

        api_request = RequestFactory().get(
            "/calendar/api/events/", {"start": "2026-01-01", "end": "2026-01-02"}
        )
        api_request.organization = None
        api_request.user = AnonymousUser()
        with self.assertRaises(Http404):
            CalendarEventsAPIView.as_view()(api_request)

    def test_calendar_acl_management_rejects_missing_organization(self):
        request = RequestFactory().get("/calendar/feeds/1/acl/")
        request.organization = None
        request.user = AnonymousUser()

        with self.assertRaises(Http404):
            FeedACLUpdateView.as_view()(request, pk=1)

    def test_calendar_api_ignores_foreign_exception_on_local_event(self):
        feed = self.make_feed(
            self.other,
            name="Exception Isolation Feed",
            slug="exception-isolation-feed-test",
            visibility=CalendarFeed.VISIBILITY_ANONYMOUS,
        )
        event = self.make_event(feed, "Local recurring event")
        event.rrule_ical = "FREQ=DAILY;COUNT=3"
        event.save(update_fields=["rrule_ical"])
        EventException.objects.create(
            organization=self.gos,
            event=event,
            original_date=event.start_datetime.date(),
            change_type=EventException.CHANGE_DELETED,
        )
        EventException.objects.create(
            organization=self.gos,
            event=event,
            original_date=event.start_datetime.date() + timedelta(days=1),
            change_type=EventException.CHANGE_MOVED,
            new_start_datetime=event.start_datetime + timedelta(days=1, hours=2),
            new_end_datetime=event.end_datetime + timedelta(days=1, hours=2),
            new_title="Foreign exception title",
            new_location="Foreign exception location",
        )

        response = self.client.get(
            "/calendar/api/events/",
            {
                "start": event.start_datetime.date().isoformat(),
                "end": event.start_datetime.date().isoformat(),
            },
            HTTP_HOST=self.other_host,
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(
            any(
                item["extendedProps"]["event_pk"] == event.pk
                for item in response.json()["events"]
            )
        )

        ics_response = self.client.get(
            f"/calendar/{feed.slug}/feed.ics", HTTP_HOST=self.other_host
        )
        self.assertEqual(ics_response.status_code, 200)
        self.assertNotIn(
            f"EXDATE:{event.start_datetime.date():%Y%m%d}",
            ics_response.content.decode("utf-8"),
        )
        self.assertNotIn(
            "Foreign exception title", ics_response.content.decode("utf-8")
        )
        self.assertNotIn(
            "Foreign exception location", ics_response.content.decode("utf-8")
        )

    def test_calendar_expansion_loads_event_feeds_without_per_event_queries(self):
        feed = self.make_feed(
            self.other,
            name="Efficient Event Feed",
            slug="efficient-event-feed-test",
            visibility=CalendarFeed.VISIBILITY_ANONYMOUS,
        )
        first = self.make_event(feed, "First event")
        second = self.make_event(feed, "Second event")
        target_date = first.start_datetime.date()

        with self.assertNumQueries(1):
            instances = feed.get_events_for_date_range(target_date, target_date)

        self.assertEqual(
            {instance["pk"] for instance in instances}, {first.pk, second.pk}
        )

    def test_exception_list_excludes_foreign_owned_exceptions(self):
        feed = self.make_feed(
            self.other,
            name="Exception List Feed",
            slug="exception-list-feed-test",
            visibility=CalendarFeed.VISIBILITY_MEMBERS,
        )
        event = self.make_event(feed, "Exception list event")
        local_exception = EventException.objects.create(
            organization=self.other,
            event=event,
            original_date=event.start_datetime.date() + timedelta(days=1),
            change_type=EventException.CHANGE_DELETED,
        )
        EventException.objects.create(
            organization=self.gos,
            event=event,
            original_date=event.start_datetime.date() + timedelta(days=2),
            change_type=EventException.CHANGE_DELETED,
        )
        user = self.make_member("exception-list-lead", self.other, "LeadMentor")
        self.client.force_login(user)

        response = self.client.get(
            f"/calendar/events/{event.pk}/exceptions/", HTTP_HOST=self.other_host
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [exception.pk for exception in response.context["exceptions"]],
            [local_exception.pk],
        )

    def test_event_delete_refuses_foreign_owned_exception_cascade(self):
        feed = self.make_feed(
            self.other,
            name="Event Delete Feed",
            slug="event-delete-feed-test",
            visibility=CalendarFeed.VISIBILITY_MEMBERS,
        )
        event = self.make_event(feed, "Event with foreign exception")
        foreign_exception = EventException.objects.create(
            organization=self.gos,
            event=event,
            original_date=event.start_datetime.date() + timedelta(days=1),
            change_type=EventException.CHANGE_DELETED,
        )
        user = self.make_member("event-delete-lead", self.other, "LeadMentor")
        self.client.force_login(user)

        response = self.client.post(
            f"/calendar/events/{event.pk}/delete/", HTTP_HOST=self.other_host
        )

        self.assertEqual(response.status_code, 302)
        self.assertTrue(CalendarEvent.objects.filter(pk=event.pk).exists())
        self.assertTrue(EventException.objects.filter(pk=foreign_exception.pk).exists())
        self.assertFalse(
            AuditLog.objects.filter(
                event=AuditEvent.RECORD_DELETED,
                resource_type="CalendarEvent",
                resource_id=str(event.pk),
            ).exists()
        )

    def test_event_with_foreign_feed_is_not_accessible_on_owner_host(self):
        foreign_feed = self.make_feed(
            self.gos,
            name="Foreign Feed",
            slug="foreign-event-feed-test",
            visibility=CalendarFeed.VISIBILITY_MEMBERS,
        )
        start = timezone.now() + timedelta(days=7)
        event = CalendarEvent.objects.create(
            organization=self.other,
            feed=foreign_feed,
            title="Mismatched event",
            start_datetime=start,
            end_datetime=start + timedelta(hours=1),
        )
        user = self.make_member("mismatched-event-lead", self.other, "LeadMentor")
        UserOrganizationMembership.objects.create(
            user=user,
            organization=self.gos,
            role="LeadMentor",
            is_active=True,
        )
        self.client.force_login(user)

        response = self.client.get(
            f"/calendar/events/{event.pk}/edit/", HTTP_HOST=self.other_host
        )

        self.assertNotEqual(response.status_code, 200)
        response = self.client.post(
            f"/calendar/events/{event.pk}/edit/",
            {
                "title": "Cross-tenant update",
                "start_datetime": event.start_datetime.strftime("%Y-%m-%d %H:%M"),
                "end_datetime": event.end_datetime.strftime("%Y-%m-%d %H:%M"),
                "description": "",
                "location": "",
                "all_day": False,
            },
            HTTP_HOST=self.other_host,
        )

        self.assertNotEqual(response.status_code, 200)
        event.refresh_from_db()
        self.assertEqual(event.title, "Mismatched event")

    def test_exception_writes_reject_mismatched_event_or_feed_ownership(self):
        user = self.make_member("exception-chain-lead", self.other, "LeadMentor")
        UserOrganizationMembership.objects.create(
            user=user, organization=self.gos, role="LeadMentor", is_active=True
        )
        self.client.force_login(user)

        for index, (feed_owner, event_owner) in enumerate(
            ((self.other, self.gos), (self.gos, self.other))
        ):
            with self.subTest(feed_owner=feed_owner.pk, event_owner=event_owner.pk):
                feed = self.make_feed(
                    feed_owner,
                    name=f"Mismatched Exception Feed {index}",
                    slug=f"mismatched-exception-feed-{index}",
                    visibility=CalendarFeed.VISIBILITY_MEMBERS,
                )
                event = self.make_event(feed, "Mismatched exception event")
                CalendarEvent.objects.filter(pk=event.pk).update(
                    organization=event_owner
                )
                exception = EventException.objects.create(
                    organization=self.other,
                    event=event,
                    original_date=event.start_datetime.date(),
                    change_type=EventException.CHANGE_DELETED,
                    note="Original note",
                )
                edit_url = f"/calendar/exceptions/{exception.pk}/edit/"

                response = self.client.get(edit_url, HTTP_HOST=self.other_host)
                self.assertNotEqual(response.status_code, 200)
                self.client.post(
                    edit_url,
                    {
                        "original_date": exception.original_date.isoformat(),
                        "change_type": EventException.CHANGE_DELETED,
                        "note": "Tampered note",
                    },
                    HTTP_HOST=self.other_host,
                )
                exception.refresh_from_db()
                self.assertEqual(exception.note, "Original note")
                self.client.post(
                    f"/calendar/exceptions/{exception.pk}/delete/",
                    HTTP_HOST=self.other_host,
                )
                self.assertTrue(EventException.objects.filter(pk=exception.pk).exists())

    def test_local_exception_delete_preserves_audit(self):
        feed = self.make_feed(
            self.other,
            name="Audited Exception Feed",
            slug="audited-exception-feed-test",
            visibility=CalendarFeed.VISIBILITY_MEMBERS,
        )
        event = self.make_event(feed, "Audited exception event")
        exception = EventException.objects.create(
            organization=self.other,
            event=event,
            original_date=event.start_datetime.date(),
            change_type=EventException.CHANGE_DELETED,
        )
        user = self.make_member("exception-audit-lead", self.other, "LeadMentor")
        self.client.force_login(user)

        self.client.post(
            f"/calendar/exceptions/{exception.pk}/delete/", HTTP_HOST=self.other_host
        )

        self.assertFalse(EventException.objects.filter(pk=exception.pk).exists())
        self.assertTrue(
            AuditLog.objects.filter(
                event=AuditEvent.RECORD_DELETED,
                resource_type="EventException",
                resource_id=str(exception.pk),
            ).exists()
        )

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

    def test_feed_list_hides_foreign_program_name_for_mismatched_feed(self):
        gos_program = Program.objects.create(
            organization=self.gos, name="GoS Local Calendar Program"
        )
        other_program = Program.objects.create(
            organization=self.other, name="Other Private Calendar Program"
        )
        CalendarFeed.objects.create(
            organization=self.gos,
            name="Mismatched Feed",
            slug="mismatched-program-feed",
            program=other_program,
        )
        CalendarFeed.objects.create(
            organization=self.gos,
            name="Local Feed",
            slug="local-program-feed",
            program=gos_program,
        )
        user = self.make_member("calendar-feed-lead", self.gos, "LeadMentor")
        self.client.force_login(user)

        response = self.client.get(
            reverse("calendar_feeds:feed_list"), HTTP_HOST="gos.localhost"
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "GoS Local Calendar Program")
        self.assertContains(response, "(needs review)")
        self.assertNotContains(response, "Other Private Calendar Program")

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
