"""Management command to send calendar reminder emails.

Usage:
    python manage.py send_calendar_reminders --days-ahead 7
    python manage.py send_calendar_reminders --dry-run
"""

import logging
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db.models import Exists, OuterRef
from django.utils import timezone

from calendar_feeds.models import CalendarFeed, CalendarFeedACL
from organizations.models import UserOrganizationMembership

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Send weekly calendar reminder emails for upcoming events"

    def add_arguments(self, parser):
        parser.add_argument(
            "--days-ahead",
            type=int,
            default=7,
            help="Number of days ahead to look for events (default: 7)",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Print what would be sent without actually sending emails",
        )

    def _collect_events(self, start, end):
        events_by_feed = {}
        foreign_acls = CalendarFeedACL.objects.filter(feed_id=OuterRef("pk")).exclude(
            organization_id=OuterRef("organization_id")
        )
        feeds = (
            CalendarFeed.objects.filter(
                is_active=True,
                organization__is_active=True,
            )
            .select_related("organization", "program")
            .annotate(has_foreign_acl=Exists(foreign_acls))
        )
        for feed in feeds:
            if feed.program_id and feed.program.organization_id != feed.organization_id:
                logger.warning(
                    "Skipping calendar feed %s with cross-organization program",
                    feed.pk,
                )
                continue
            if not feed.is_public and feed.has_foreign_acl:
                logger.warning(
                    "Skipping calendar feed %s with cross-organization ACL", feed.pk
                )
                continue
            instances = feed.get_events_for_date_range(start, end)
            if instances:
                events_by_feed[feed] = instances
        return events_by_feed

    def _build_body(self, feed_events, days_ahead):
        body_lines = [f"Upcoming events for the next {days_ahead} days:\n"]
        for feed, instances in feed_events:
            body_lines.append(f"\n{feed.name}:")
            for inst in instances:
                if inst["all_day"]:
                    date_str = inst["date"].strftime("%A, %B %d")
                else:
                    date_str = inst["start_datetime"].strftime("%A, %B %d at %I:%M %p")
                body_lines.append(f"  - {inst['title']} ({date_str})")
                if inst["location"]:
                    body_lines.append(f"    Location: {inst['location']}")
        return "\n".join(body_lines)

    def _send(self, user, organization, feed_events, days_ahead):
        from organizations.utils import get_organization_email_settings
        from programs.utils.notifications import send_templated_notification

        portal_url = str(
            get_organization_email_settings(organization).get("portal_url") or ""
        ).rstrip("/")
        calendar_url = f"{portal_url}/calendar/" if portal_url else ""

        try:
            send_templated_notification(
                subject=f"Upcoming events for the next {days_ahead} days",
                template_name="calendar_feeds/emails/reminder.html",
                context={
                    "user": user,
                    "feed_events": feed_events,
                    "days_ahead": days_ahead,
                    "calendar_url": calendar_url,
                },
                recipient_list=[user.email],
                organization=organization,
            )
            logger.info("Sent calendar reminder.")
            return True
        except Exception as exc:
            logger.error("Calendar reminder dispatch failed (%s).", type(exc).__name__)
            return False

    def handle(self, *args, **options):
        days_ahead = options["days_ahead"]
        dry_run = options["dry_run"]

        start = timezone.localdate()
        end = start + timedelta(days=days_ahead)

        self.stdout.write(f"Checking for events between {start} and {end}...")
        logger.info(
            "Checking for calendar events between %s and %s (days_ahead=%d, dry_run=%s)",
            start,
            end,
            days_ahead,
            dry_run,
        )

        events_by_feed = self._collect_events(start, end)
        if not events_by_feed:
            self.stdout.write(self.style.SUCCESS("No upcoming events found."))
            logger.info("No upcoming calendar events found.")
            return

        organization_ids = {feed.organization_id for feed in events_by_feed}
        memberships = (
            UserOrganizationMembership.objects.filter(
                organization_id__in=organization_ids,
                organization__is_active=True,
                is_active=True,
                user__is_active=True,
                user__email__gt="",
            )
            .select_related("user", "organization")
            .order_by("organization_id", "user_id", "role")
        )

        total_sent = 0
        processed_user_organizations = set()
        for membership in memberships:
            user = membership.user
            organization = membership.organization
            user_organization = (user.pk, organization.pk)
            if user_organization in processed_user_organizations:
                continue
            processed_user_organizations.add(user_organization)

            user_feeds = [
                feed
                for feed in events_by_feed
                if feed.organization_id == organization.pk and feed.user_can_read(user)
            ]
            if not user_feeds:
                continue

            feed_events = [(feed, events_by_feed[feed]) for feed in user_feeds]
            body = self._build_body(feed_events, days_ahead)

            if dry_run:
                self.stdout.write("  [DRY RUN] Would send a calendar reminder.\n")
                logger.info("[DRY RUN] Would send a calendar reminder.")
            elif self._send(user, organization, feed_events, days_ahead):
                total_sent += 1

        if dry_run:
            self.stdout.write(self.style.WARNING("Dry run complete. No emails sent."))
            logger.info("Dry run complete for calendar reminders. No emails sent.")
        else:
            self.stdout.write(
                self.style.SUCCESS(f"Sent {total_sent} reminder email(s).")
            )
            logger.info("Completed calendar reminders: sent %d email(s)", total_sent)
