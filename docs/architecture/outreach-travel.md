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

---

## Competition & Team Travel (`travel/`)

Program-scoped travel management for competition trips and events (`/programs/<id>/travel/`). Gated by the `ProgramFeature` key `"travel"`.

### Travel Models
- **`TravelEvent`**: Destination, `start_date`, `end_date`, location, and optional `permission_form` file upload.
- **`TravelDeparture`**: Specific bus/flight departures and departure times.
- **`TravelCostOption`**: Selectable checklist fee options whose totals sum into the signup `cost_total`.
- **`TravelSignup`**: Student registration (`status`: `interested` → `approved` / `declined` / `withdrawn`).
- **`TravelMentorSignup`**: Mentor registrations with `is_driver` flag and departure choice.
- **`TravelParentChaperoneSignup`**: Parent chaperone roster.

### Parent Approval Workflow & Notifications
- Students submit initial sign-ups (`interested`).
- **Guardian Approval Invariant**: **Only a student's linked parents can approve or decline** (`_ParentSignupActionView` verifies `parent.students.filter(pk=...)`).
- All state transitions dispatch transactional notification emails via `travel/services.py` (`notify_signup_created`, `notify_parent_approval_requested`, `notify_signup_approved`).
- Email templates must not rely on `request` context; they direct users to log in directly to the portal.

### Parent Chaperone System
- Any parent with at least one active student in the program can self-register as a chaperone (`TravelParentChaperoneSignup.eligible_adults(program)`).
- Lead Mentors can add or remove chaperones via `TravelParentChaperoneStaffForm`.
