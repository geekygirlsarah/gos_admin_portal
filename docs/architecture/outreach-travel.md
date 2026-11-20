# Outreach Events and Travel Management

This document details community outreach events, shifts, champion roles, competition travel events, chaperones, and check-in workflows.

## Outreach Events (`outreach/`)

Outreach events manage volunteer community opportunities where students participate as "champions" (student leads) or "helpers", supported by adult mentors.

### Shift-Based Model Hierarchy
- **`OutreachEvent`**: High-level event definition (name, program, description, location snapshots). Has no direct date or capacity fields.
- **`OutreachShift`**: Specific time slots under an event (`date`, `start_time`, `end_time`, `max_champions`, `max_helpers`).
  - Derived event properties (`start_date`, `end_date`, `is_past`, `duration_hours`) are computed from the shift collection.
- **`OutreachSignup`**: Student sign-up linked to a specific `OutreachShift` (`unique_together = ("student", "shift")`).
- **`OutreachMentorSignup`**: Adult mentor registration supporting a shift (`unique_together = ("adult", "shift")`, no capacity limit).

Tenant event editors include only shifts owned by the event organization. Signup
management rejects a selected student or mentor when an existing signup for
the same shift is owned by another organization; the conflicting row is left
unchanged and no notification is sent.

Student overlap validation compares only records whose signup, student, shift,
and event belong to the same organization. An inconsistent foreign overlap does
not block a local signup or reveal the other event's name. Event deletion is
refused for review if it would cascade into a foreign-owned shift or signup; the
refusal happens before notification or audit logging, and preserves the records.

Scheduled outreach reminders send only for shifts, signups, students or mentors,
events, and optional programs with consistent organization ownership. The weekly
mentor digest similarly scopes outreach shifts, attendance, and travel highlights
to the mentor's organization. Mismatched rows are omitted rather than repaired.

### Saved Locations (`OutreachLocation`)
- Directory of venues (`name`, `address`) managed by mentors.
- Event creation form uses an AJAX modal to add locations dynamically without leaving the form.
- Events snapshot `location_name` and `location_address` so modifying a saved location never mutates historical event records.

### Check-In Workflow & Time Adjustments (`OutreachShiftCheckInView`)
- Mobile-friendly check-in console at `/programs/<id>/outreach/shifts/<pk>/check-in/`.
- Operators (mentors and shift champions) record check-in/out stamps (`check_in_all`, `check_out_all`, `walk_up` helper additions).
- **Time Adjustments**: Operators can manually correct or backdate times via `set_times` (`OutreachSetTimesForm`).
- **Access Gating**: Mentors and Lead Mentors have perpetual access; student champions lose check-in access once all sign-ups are completed or 4 hours after shift end.

### Attendance Hours Crediting
- `compute_outreach_stats` in `outreach/utils.py` tracks:
  - **Completed Hours**: Finalized signups with both `checked_in_at` and `checked_out_at` on past shifts.
  - **Pending Hours**: Upcoming signups + past signups not yet finalized.
- The student stats modal also reports **Overlap Hours**: the union of finalized outreach check-in intervals that intersect kiosk attendance sessions from any program in the same organization. The attendance hours chart shows the same career-to-date value when the viewer can read outreach. Overlap is informational, does not reduce credited hours, and an open kiosk session is counted through the current time.
- Closed-session overlap calculations are cached per organization, program, and student using fingerprints of the signup and attendance timestamps plus signup finalization state. The interval rows are re-read in bulk to detect corrections and bulk edits; open-session results are not cached because the overlap changes with the current time.

---

## Competition & Team Travel (`travel/`)

Program-scoped travel management for competition trips and events (`/programs/<id>/travel/`). Gated by the `ProgramFeature` key `"travel"`.

Mentor access to trip details is controlled by the organization-scoped
`RolePermission` section `travel_details`. Mentors default to read-only access;
write access allows creating and editing trip details, including departures,
costs, and permission-form uploads. Mentors with read or write access can view
student signup names, statuses, and departures; approved costs, permission-form
acknowledgements, trip deletion, and student/parent/mentor roster management
remain Lead-Mentor-only. This setting does not change the
separate student and parent signup, approval, or chaperone self-service rules.
Mentor travel links, profile commitments, and digest highlights follow the
trip-details read permission; calendar feeds retain their separate access rules.

### Travel Models
- **`TravelEvent`**: Destination, `start_date`, `end_date`, location, optional `permission_form` file upload, and an opt-in `requires_departure` setting. Existing trips remain optional; trips with no cost options are free.
- **`TravelDeparture`**: Specific bus/flight departures and departure times.
- **`TravelCostOption`**: Per-traveler fee options. Required options are included for every signup; new options default to required, while existing options are migrated as optional.
- **`TravelSignup`**: Student registration (`status`: `interested` → `approved` / `declined` / `withdrawn`). Approval posts the approved total to the student's program balance.
- **`TravelSignedPermissionForm`**: Encrypted signed upload tied to the signup and the version of the blank form. Each upload is retained until the trip is deleted.
- **`TravelMentorSignup`**: Mentor registrations with `is_driver` flag and departure choice.
- **`TravelParentChaperoneSignup`**: Parent chaperone roster.

Departure and cost-option editors and event-list rosters are scoped to the
resolved organization, and signup POSTs accept only departures owned by that
organization. Mentor and parent signup profiles and their adult-student
eligibility relationships must also belong to that organization. Existing
mismatched links are hidden from tenant rosters and are not changed or notified;
signup conflicts are left for separate review.

Student signup transitions lock the event before locking and reloading the
tenant-scoped signup. Rejoin, withdrawal, guardian decline, and decision undo
recheck the current state under those locks. The Django admin exposes travel
records only as read-only to platform superusers outside tenant context; it is
not a supported path for creating or changing travel data.

Students can update the departure and optional cost selections on their own
active signup. An actual change returns an approved signup to pending guardian
approval, recalculates a tracked balance charge, and notifies the student and
eligible local guardians after the update commits. No-op edits preserve approval
and do not send notices. Lead Mentors can reassign an active student's departure;
this preserves the approval and charge and sends the student and eligible local
guardians a notice that no new approval is needed.

Trip deletion is refused before audit logging if a foreign-owned departure,
cost option, or signup would be deleted or changed. This includes foreign
signups attached to a local departure or cost option; the trip and all linked
records are preserved for review rather than detached or reassigned.

### Parent Approval Workflow & Notifications
- Students submit initial sign-ups (`interested`).
- **Guardian Approval Invariant**: **Only a student's linked parents can approve or decline** (`_ParentSignupActionView` verifies `parent.students.filter(pk=...)`).
- State-transition notification functions live in `travel/services.py` (`notify_signup_created`, `notify_parent_approval_requested`, `notify_signup_approved`). Signup and selected costs commit atomically before the confirmation/approval-request email is sent. A delivery exception leaves the signup intact, produces a user-facing warning and a log entry containing identifiers and the exception type only; delivery is not automatically retried.
- Direct signup and adult-notice helpers fail closed when signup, event, program, participant, or departure organization ownership is inconsistent; guardian recipients are additionally restricted to the event organization.
- When a trip is edited, students with approved sign-ups, their eligible local guardians, and signed-up local mentors and parent chaperones receive an email listing changed trip fields, departures, and costs when an address is available. Adult recipients must have login enabled and email updates opted in; inconsistent organization or departure ownership is excluded, and shared addresses receive one email.
- If an edit changes a student's computed total, the current balance charge is updated immediately, the approval snapshot is cleared, and the signup returns to pending parent approval. An after-commit email with that student's previous and current totals goes only to the student and eligible local guardians; other attendees do not receive the individualized amount. Edits that do not change a student's total preserve the approval. Charge changes are audit logged.
- Email templates must not rely on `request` context; they direct users to log in directly to the portal.

Lead Mentors see each active signup's outstanding parent approval, signed form,
and required departure. Linked local guardians and Lead Mentors may upload signed
forms; only those guardians and Lead Mentors can download them. Uploading is
separate from approving the current trip choices. A previously acknowledged form
does not need acknowledgment again on reapproval if its signed upload is still
current. Replacing the blank form requires a new signed upload, but does not
automatically revoke the current approval. Approval submits a signed snapshot of
the displayed choices, prices, and form version; a stale page must be reloaded
before it can be approved. Signed files are encrypted and stored
under the protected `private/` prefix. PDF, JPEG, and PNG uploads are limited to
10 MB.

The reapproval migration resets approved signups with a permission form, including
past trips, and preserves their existing balance charges. It records the previous
approval details for review; inconsistent cross-organization records are not
changed. It sends no notification. Families can approve past trips from the travel
page. Reversing this migration is refused when snapshots exist, because a later
trip change can make the original approval invalid. Plan a backup and manual
review before deployment; pause trip writes while the migration runs.

Deleting a trip or its program queues signed-file deletion after commit. Failed
deletions remain in the tenant-owned cleanup queue; operators may retry using
`python manage.py retry_signed_permission_cleanup --organization-id <id>` after
reviewing the target environment and receiving any required operational approval.
Deleting a student or signup is blocked while signed travel forms exist. Storage
origins and proxies must continue to deny public access to the `private/` prefix.

### Parent Chaperone System
- Any parent with at least one active student in the program can self-register as a chaperone (`TravelParentChaperoneSignup.eligible_adults(program)`).
- Lead Mentors can add or remove chaperones via `TravelParentChaperoneStaffForm`.
- Lead Mentors can add active mentors from their organization to a trip; mentor candidates and departure choices are scoped to that program and event.
