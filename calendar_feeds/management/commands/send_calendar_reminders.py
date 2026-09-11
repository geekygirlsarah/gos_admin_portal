"""Management command to send calendar reminder emails.

Usage:
    python manage.py send_calendar_reminders --days-ahead 7
    python manage.py send_calendar_reminders --dry-run
"""

import logging
from datetime import date, timedelta

from django.contrib.auth.models import User
from django.core.management.base import BaseCommand

from calendar_feeds.models import CalendarFeed

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
        for feed in CalendarFeed.objects.filter(is_active=True):
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

    def _send(self, user, feed_events, days_ahead):
        from programs.utils.notifications import send_templated_notification

        try:
            send_templated_notification(
                subject=f"Upcoming events for the next {days_ahead} days",
                template_name="calendar_feeds/emails/reminder.html",
                context={
                    "user": user,
                    "feed_events": feed_events,
                    "days_ahead": days_ahead,
                    "calendar_url": "https://portal.girlsofsteel.org/calendar/",
                },
                recipient_list=[user.email],
            )
            return True
        except Exception:
            logger.exception("Failed to send calendar reminder to %s", user.email)
            return False

    def handle(self, *args, **options):
        days_ahead = options["days_ahead"]
        dry_run = options["dry_run"]

        start = date.today()
        end = start + timedelta(days=days_ahead)

        self.stdout.write(f"Checking for events between {start} and {end}...")

        events_by_feed = self._collect_events(start, end)
        if not events_by_feed:
            self.stdout.write(self.style.SUCCESS("No upcoming events found."))
            return

        # Collect all users who should receive reminders
        users = User.objects.filter(
            is_active=True,
            email__isnull=False,
        ).exclude(email="")

        total_sent = 0
        for user in users:
            # Determine which feeds this user can read
            user_feeds = [feed for feed in events_by_feed if feed.user_can_read(user)]
            if not user_feeds:
                continue

            feed_events = [(feed, events_by_feed[feed]) for feed in user_feeds]
            body = self._build_body(feed_events, days_ahead)

            if dry_run:
                self.stdout.write(f"  Would send to {user.email}:\n{body}\n")
            elif self._send(user, feed_events, days_ahead):
                total_sent += 1

        if dry_run:
            self.stdout.write(self.style.WARNING("Dry run complete. No emails sent."))
        else:
            self.stdout.write(
                self.style.SUCCESS(f"Sent {total_sent} reminder email(s).")
            )
