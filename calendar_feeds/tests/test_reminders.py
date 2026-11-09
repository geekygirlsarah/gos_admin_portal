"""Tests for the send_calendar_reminders management command."""

from datetime import timedelta
from io import StringIO

from django.contrib.auth.models import User
from django.core import mail
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone

from calendar_feeds.models import CalendarEvent, CalendarFeed, CalendarFeedACL
from calendar_feeds.tests.factories import (
    make_event,
    make_feed,
    make_lead_mentor_user,
    make_student_user,
)
from organizations.models import (
    Organization,
    OrganizationDomain,
    OrganizationSettings,
    UserOrganizationMembership,
)


class SendCalendarRemindersCommandTests(TestCase):
    def setUp(self):
        self.out = StringIO()

    def test_no_events_no_emails(self):
        call_command("send_calendar_reminders", stdout=self.out)
        self.assertEqual(len(mail.outbox), 0)

    def test_upcoming_event_triggers_reminder(self):
        mail.outbox.clear()
        feed = make_feed(visibility=CalendarFeed.VISIBILITY_ANONYMOUS)
        lead = make_lead_mentor_user()
        lead.email = "lead@example.com"
        lead.save(update_fields=["email"])
        user, _ = make_student_user()
        user.email = "student@example.com"
        user.save(update_fields=["email"])
        UserOrganizationMembership.objects.get_or_create(
            user=user,
            organization=feed.organization,
            role="Student",
            defaults={"is_active": True},
        )

        tomorrow = timezone.make_aware(
            timezone.datetime.now() + timedelta(days=1, hours=3)
        )
        make_event(
            feed,
            title="Build Night",
            start_datetime=tomorrow,
            end_datetime=tomorrow + timedelta(hours=2),
        )
        with self.assertLogs(
            "calendar_feeds.management.commands.send_calendar_reminders", level="INFO"
        ) as cm:
            call_command("send_calendar_reminders", stdout=self.out)
        self.assertGreater(len(mail.outbox), 0)
        recipients = {address for message in mail.outbox for address in message.to}
        self.assertIn("lead@example.com", recipients)
        self.assertIn("student@example.com", recipients)
        logs = "\n".join(cm.output)
        self.assertIn("Sent calendar reminder.", logs)
        self.assertNotIn("lead@example.com", logs)
        self.assertNotIn("student@example.com", logs)
        self.assertNotIn(lead.username, logs)
        self.assertNotIn(user.username, logs)
        self.assertNotIn("lead@example.com", self.out.getvalue())
        self.assertNotIn("student@example.com", self.out.getvalue())

    def test_future_event_no_reminder(self):
        mail.outbox.clear()
        feed = make_feed(visibility=CalendarFeed.VISIBILITY_ANONYMOUS)
        two_weeks = timezone.make_aware(timezone.datetime.now() + timedelta(days=14))
        make_event(
            feed,
            title="Far Future",
            start_datetime=two_weeks,
            end_datetime=two_weeks + timedelta(hours=1),
        )
        call_command("send_calendar_reminders", stdout=self.out)
        self.assertEqual(len(mail.outbox), 0)

    def test_dry_run_does_not_send(self):
        mail.outbox.clear()
        feed = make_feed(visibility=CalendarFeed.VISIBILITY_ANONYMOUS)
        user, _ = make_student_user()
        user.email = "student_dry@example.com"
        user.save(update_fields=["email"])
        UserOrganizationMembership.objects.get_or_create(
            user=user,
            organization=feed.organization,
            role="Student",
            defaults={"is_active": True},
        )
        tomorrow = timezone.make_aware(
            timezone.datetime.now() + timedelta(days=1, hours=2)
        )
        make_event(
            feed,
            title="Tomorrow",
            start_datetime=tomorrow,
            end_datetime=tomorrow + timedelta(hours=1),
        )
        with self.assertLogs(
            "calendar_feeds.management.commands.send_calendar_reminders", level="INFO"
        ) as cm:
            call_command("send_calendar_reminders", dry_run=True, stdout=self.out)
        self.assertEqual(len(mail.outbox), 0)
        logs = "\n".join(cm.output)
        output = self.out.getvalue()
        self.assertIn("[DRY RUN]", logs)
        self.assertIn("Would send a calendar reminder.", logs)
        self.assertNotIn("student_dry@example.com", logs)
        self.assertNotIn(user.username, logs)
        self.assertNotIn("student_dry@example.com", output)


@override_settings(MOMENTUM_ROOT_DOMAIN="momentumteam.app")
class CalendarReminderTenantIsolationTests(TestCase):
    def make_organization(self, slug, name, *, subdomain=None):
        return Organization.objects.create(
            slug=slug,
            name=name,
            subdomain=subdomain or slug,
        )

    def make_member(self, username, organization, role="Mentor", *, active=True):
        user = User.objects.create(
            username=username,
            email=f"{username}@example.test",
            is_active=True,
        )
        UserOrganizationMembership.objects.create(
            user=user,
            organization=organization,
            role=role,
            is_active=active,
        )
        return user

    def make_feed(self, organization, *, visibility, name="Reminder Feed", acl=None):
        feed = CalendarFeed.objects.create(
            organization=organization,
            name=name,
            slug=f"{organization.slug}-{name.lower().replace(' ', '-')}",
            visibility=visibility,
        )
        if acl:
            role, can_read = acl
            CalendarFeedACL.objects.create(
                organization=organization,
                feed=feed,
                role=role,
                can_read=can_read,
                can_write=False,
            )
        return feed

    def make_upcoming_event(self, feed, title):
        start = timezone.now() + timedelta(days=1)
        return CalendarEvent.objects.create(
            organization=feed.organization,
            feed=feed,
            title=title,
            start_datetime=start,
            end_datetime=start + timedelta(hours=1),
        )

    def run_reminders(self):
        mail.outbox.clear()
        call_command("send_calendar_reminders", stdout=StringIO())
        return list(mail.outbox)

    def html_body(self, message):
        return next(
            content
            for content, content_type in message.alternatives
            if content_type == "text/html"
        )

    def test_public_feed_does_not_email_nonmembers_or_inactive_members(self):
        organization = self.make_organization(
            "calendar-reminder-owner", "Reminder Owner"
        )
        other = self.make_organization("calendar-reminder-other", "Other Team")
        feed = self.make_feed(
            organization,
            visibility=CalendarFeed.VISIBILITY_ANONYMOUS,
        )
        self.make_upcoming_event(feed, "Owner event")
        User.objects.create(username="no-membership", email="none@example.test")
        self.make_member("other-tenant-member", other)
        self.make_member("inactive-owner-member", organization, active=False)

        self.assertEqual(self.run_reminders(), [])

    def test_member_feed_acl_still_controls_reminder_access(self):
        organization = self.make_organization("calendar-reminder-acl", "ACL Team")
        feed = self.make_feed(
            organization,
            visibility=CalendarFeed.VISIBILITY_MEMBERS,
            acl=("Parent", False),
        )
        self.make_upcoming_event(feed, "Restricted event")
        self.make_member("acl-parent", organization, role="Parent")

        self.assertEqual(self.run_reminders(), [])

    def test_foreign_acl_cannot_override_local_denial_for_reminders(self):
        organization = self.make_organization("calendar-acl-owner", "Owner")
        other = self.make_organization("calendar-acl-foreign", "Other")
        feed = self.make_feed(
            organization,
            visibility=CalendarFeed.VISIBILITY_MEMBERS,
            acl=("Parent", False),
        )
        CalendarFeedACL.objects.create(
            organization=other, feed=feed, role="Parent", can_read=True
        )
        self.make_upcoming_event(feed, "Restricted event")
        self.make_member("local-parent", organization, role="Parent")

        self.assertEqual(self.run_reminders(), [])

    def test_local_acl_allows_reminders(self):
        organization = self.make_organization("calendar-acl-allowed", "Owner")
        feed = self.make_feed(
            organization,
            visibility=CalendarFeed.VISIBILITY_MEMBERS,
            acl=("Parent", True),
        )
        self.make_upcoming_event(feed, "Allowed event")
        user = self.make_member("allowed-parent", organization, role="Parent")

        messages = self.run_reminders()

        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0].to, [user.email])
        self.assertIn("Allowed event", self.html_body(messages[0]))

    def test_multi_organization_member_gets_separate_branded_canonical_emails(self):
        alpha = self.make_organization(
            "calendar-reminder-alpha", "Alpha Team", subdomain="alpha"
        )
        beta = self.make_organization(
            "calendar-reminder-beta", "Beta Team", subdomain="beta"
        )
        OrganizationSettings.objects.create(
            organization=alpha,
            data={"email_settings": {"organization_name": "Alpha Branding"}},
        )
        OrganizationSettings.objects.create(
            organization=beta,
            data={"email_settings": {"organization_name": "Beta Branding"}},
        )
        operator = User.objects.create(username="domain-operator")
        OrganizationDomain.objects.create(
            organization=alpha,
            hostname="alpha.example.org",
            status=OrganizationDomain.STATUS_ACTIVE,
            dns_verified_at=timezone.now(),
            activated_at=timezone.now(),
            activated_by=operator,
        )
        user = self.make_member("multi-team-member", alpha)
        UserOrganizationMembership.objects.create(
            user=user,
            organization=beta,
            role="Mentor",
            is_active=True,
        )
        alpha_feed = self.make_feed(
            alpha,
            visibility=CalendarFeed.VISIBILITY_ANONYMOUS,
            name="Alpha Calendar",
        )
        beta_feed = self.make_feed(
            beta,
            visibility=CalendarFeed.VISIBILITY_ANONYMOUS,
            name="Beta Calendar",
        )
        self.make_upcoming_event(alpha_feed, "Alpha-only event")
        self.make_upcoming_event(beta_feed, "Beta-only event")

        messages = self.run_reminders()

        self.assertEqual(len(messages), 2)
        alpha_message = next(
            message
            for message in messages
            if "Alpha-only event" in self.html_body(message)
        )
        beta_message = next(
            message
            for message in messages
            if "Beta-only event" in self.html_body(message)
        )
        alpha_html = self.html_body(alpha_message)
        beta_html = self.html_body(beta_message)
        self.assertIn("Alpha Branding", alpha_html)
        self.assertIn("https://alpha.example.org/calendar/", alpha_html)
        self.assertNotIn("Beta-only event", alpha_html)
        self.assertIn("Beta Branding", beta_html)
        self.assertIn("https://beta.momentumteam.app/calendar/", beta_html)
        self.assertNotIn("Alpha-only event", beta_html)

    @override_settings(MOMENTUM_ROOT_DOMAIN="")
    def test_reminder_omits_calendar_link_when_no_canonical_host_exists(self):
        organization = self.make_organization(
            "calendar-reminder-no-host", "No Host Team", subdomain=""
        )
        feed = self.make_feed(
            organization,
            visibility=CalendarFeed.VISIBILITY_ANONYMOUS,
        )
        self.make_member("no-host-member", organization)
        self.make_upcoming_event(feed, "Still deliverable event")

        messages = self.run_reminders()

        self.assertEqual(len(messages), 1)
        body = self.html_body(messages[0])
        self.assertIn("Still deliverable event", body)
        self.assertNotIn("Log in to the portal to view the full calendar", body)
        self.assertNotIn('href="#"', body)
        self.assertNotIn("portal.girlsofsteel.org", body)
