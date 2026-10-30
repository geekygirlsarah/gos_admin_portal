# Organization Multi-Feed Calendars & iCalendar Subscriptions

This document details org-wide calendar feeds (`calendar_feeds/`), access control lists, recurrence calculation, auto-syncing from outreach/travel, and ICS subscription feeds.

## Calendar Architecture & Authentication

Multi-feed calendars are served at `/calendar/`:
- **Login Exemption**: The public calendar view (`/calendar/`) and public iCal endpoints are login-exempt (included in `EXEMPT_PATH_PREFIXES` in `GoSAdminPortal/middleware.py`). All management endpoints under `/calendar/` require authentication.
- **Visibility Levels**:
  - `anonymous`: Publicly viewable by unauthenticated users.
  - `members`: Requires user authentication and explicit ACL permissions.

### Core Models
- **`CalendarFeed`**: Represents a calendar category (`name`, `slug`, `color`, `visibility`, `source_type`).
  - Source types: `manual`, `outreach` (auto-synced), `travel` (auto-synced), `custom`.
  - Permissions: Checked via `feed.user_can_read(user)` and `feed.user_can_write(user)`. Lead Mentors bypass ACL checks; source-managed feeds remain strictly read-only.
- **`CalendarFeedACL`**: Role-based access rules (Role → `can_read` / `can_write`).
- **`CalendarEvent`**: Event instance (`title`, `start_datetime`, `end_datetime`, `all_day`, `location`, `description`, `rrule_ical`, `exdate_ical`, `uid`).
- **`EventException`**: Overrides for recurring occurrences (`CHANGE_DELETED`, `CHANGE_MOVED`, `CHANGE_CHANGED`).
- **`UserCalendarToken`**: Personal tokenized subscription key enabling secure ICS calendar subscriptions in external apps without session cookies.

---

## Recurrence Rules & Expansion Engine

### Recurrence Builder (`CalendarEventForm`)
- UI generates RFC-5545 compliant `rrule_ical` strings.
- Supports Daily, Weekly (with specific day selectors), Monthly, interval frequencies, and termination conditions (`COUNT` or `UNTIL`).

### Expansion & Override Engine (`calendar_feeds/utils.py`)
- `expand_events_for_range()` evaluates recurrence patterns dynamically using `dateutil.rrule.rrulestr`.
- Applies exclusion dates (`exdate_list`) and overlays `EventException` modifications for moved or deleted instances.

---

## Automated Feed Syncing

- **Outreach Synchronization** (`signals.py` & `sources/outreach.py`): Synchronizes `OutreachShift` records into the program's linked outreach calendar feed.
- **Travel Synchronization** (`signals.py` & `sources/travel.py`): Synchronizes `TravelEvent` and `TravelDeparture` dates into the program's travel calendar feed.
- Creating or editing a feed triggers an initial automatic synchronization.

---

## ICS Subscription Endpoints

- **Per-Feed Public**: `/calendar/<slug>/feed.ics`
- **Per-Feed Tokenized**: `/calendar/<slug>/<token>/feed.ics`
- **Aggregate Public Feed**: `/calendar/feed.ics` (all active `anonymous` feeds, covering -30 days to +1 year).
- **Personal Unified Subscription**: `/calendar/my/<token>/feed.ics` (aggregates all feeds the user has permission to read).

---

## Scheduled Reminders (`send_calendar_reminders`)

Sends periodic personalized calendar digest emails to users for feeds they are authorized to read:
```bash
python manage.py send_calendar_reminders --days-ahead 3 [--dry-run]
```
Uses `send_templated_notification` with template `templates/calendar_feeds/emails/reminder.html`.
