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
  - Permissions: Checked via `feed.user_can_read(user)` and `feed.user_can_write(user)`. Lead Mentors bypass valid same-organization ACL checks; source-managed feeds remain strictly read-only.
- **`CalendarFeedACL`**: Role-based access rules (Role → `can_read` / `can_write`), owned by the same organization as the feed. A private feed with any cross-organization ACL row fails closed for member reads and writes until an operator repairs the inconsistent data; public anonymous reads remain public.
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
- Source records, feeds, programs, and generated events must belong to the same organization; mismatched departures are omitted. Synchronization and deletion leave inconsistent cross-organization records untouched rather than cleaning up historical data.
- Source deletion preserves a generated calendar event if any attached exception belongs to another organization. Safe local rows and their local exceptions are still removed; retained rows and exceptions remain available for separate review.

---

## ICS Subscription Endpoints

- **Per-Feed Public**: `/calendar/<slug>/feed.ics`
- **Per-Feed Tokenized**: `/calendar/<slug>/<token>/feed.ics`
- **Aggregate Public Feed**: `/calendar/feed.ics` (all active `anonymous` feeds, covering -30 days to +1 year).
- **Personal Unified Subscription**: `/calendar/my/<token>/feed.ics` (aggregates all feeds the user has permission to read).

All public and tokenized ICS endpoints are scoped to the organization resolved from the request hostname. Feed slugs are resolved within that organization, and event expansion excludes events whose organization does not match their feed. Personal tokens require an active account with an active membership in the organization currently hosting the subscription (superusers retain their existing membership exception). A token does not grant access to feeds on another organization's host; public feeds remain anonymously readable on their own organization's host. Requests without an organization context are rejected.

Calendar feed forms only offer programs belonging to the current organization and reject submitted program IDs from other organizations.
Feed management shows a linked program name only when the program belongs to the feed organization. An inconsistent existing link is shown with a generic review marker instead of the foreign program name.

ACL editing is limited to rows owned by the feed organization, and newly saved rows inherit that organization. Event and exception management validates the full organization → feed → event → exception ownership chain; recurrence expansion, exception lists, and iCalendar output ignore foreign-owned exceptions. Event or feed deletion is blocked when it would cascade to child records owned by another organization. Calendar page, event API, and tenant-scoped management requests without a resolved organization are rejected; anonymous public access remains available on a resolved tenant host.

---

## Scheduled Reminders (`send_calendar_reminders`)

Sends separate personalized digests per organization to active users with an active membership in that organization. Private feed ACLs still apply; public visibility alone does not enroll users from other organizations in reminders. Each email uses the organization's branding and canonical calendar address; the calendar link is omitted when no safe address is available.

Reminders skip private feeds containing ACL rows owned by another organization and log the inconsistency; the shared feed permission checks now also fail closed for those inconsistent private-feed ACLs.

```bash
python manage.py send_calendar_reminders --days-ahead 3 [--dry-run]
```
Uses `send_templated_notification` with template `templates/calendar_feeds/emails/reminder.html`.
