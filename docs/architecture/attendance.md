# Attendance, Kiosks, and Digital Sign-Out Architecture

This document details the attendance tracking, RFID events, kiosk configuration, digital sign-out stations, and daily presence management.

## Attendance Models & Indexing

Attendance data is tracked via two primary models in `attendance/models.py`:
- **`AttendanceEvent`**: Append-only log of physical or scanned RFID taps (`occurred_at`, `kiosk`, `student`, `adult`, `visitor_name`). Has no composite program indexes (queried by date hierarchy in admin/merge flows).
- **`AttendanceSession`**: Paired check-in and check-out durations (`check_in`, `check_out`, `program`, `student`, `adult`, `visitor_name`).

### Database Indexing Invariants
`AttendanceSession.Meta.indexes` is specifically optimized:
- `(program, student, check_in)` and `(program, adult, check_in)`: Composite indexes that serve aggregate hours and program statistics.
- `(student, check_in)`: Index serving per-student attendance views and history.
- Partial indexes on `check_out__isnull=True` (`program, student, check_in` / `program, adult, check_in`): Serves open-session queries (Who's Here Now, kiosk stale-close, active headcounts) with minimal table scan overhead.
- `(program, visitor_name, check_in)`: Covers CSV visitor import idempotency checks.
- `AttendanceSession.check_out` is deliberately **not** independently indexed (`db_index=False`) because partial indexes cover open-session lookups.

### Visitor Management (`VisitorManagementView`)
Visitor names are denormalized on attendance records. Lead Mentors can merge and standardize misspelled visitor names via the Visitor Management tool (`/attendance/visitors/`), ensuring consistent hour tracking and reporting.

---

## Reporting & Visualizations

### Hours Chart (`/attendance/hours-chart/`, `attendance_hours_chart_view`)
Visualizes attended hours per student in a program using Chart.js:
- **Axis Layout**: Defaults to horizontal bars (`indexAxis: 'y'`) so 50+ student rosters remain readable. Supports dynamic vertical orientation via `axis=horizontal|vertical` query parameters (preserved in sort links).
- **Expand Layout**: In vertical mode, an expand button (`#expandChartBtn`) applies a per-student `min-width` (`chart_min_width_px`) inside an `overflow-x: auto` wrapper (`#chartScrollWrap`).
- **Attended Days Count**: Calculates distinct attended days via `Count("check_in__date", distinct=True)`, matching active date-range and day-of-week filters (0 for unlogged students).
- **Average Lines & Tooltips**: Dynamically aligns annotation reference lines and tooltip parsing based on active orientation (`xMin`/`xMax` vs `yMin`/`yMax`).
- **Legacy URL**: `/attendance/program-hours/<id>/` permanently redirects to `attendance_hours_chart_view` with `program_id` pre-selected.

### Master Attendance List (`/attendance/all/`, `AllAttendanceView`)
Lead Mentor-only overview of all sessions across programs:
- GET filters: `program_id`, `person_type` (`student`/`mentor`/`visitor`), `status` (`open`/`closed`), date presets (`today`, `yesterday`, `week`, `month`, `custom`).
- KPI Cards: Total Sessions, Open Sessions, and Unique Attendees (broken down into distinct students, mentors, and visitors using separate `.distinct()` counts over the filtered queryset).
- Export: Full CSV and XLSX export support (`export=csv`/`export=xlsx`).

---

## Digital Sign-Out Stations & Parent Sign-Outs

The digital sign-out system allows parents/guardians to sign out students electronically.

### `DigitalSignoutConfig` & Public Station (`/signout/<config_id>/`)
- Login-exempt public route (included in `EXEMPT_PATH_PREFIXES`).
- Inactive configs trigger standard 404 behavior.
- **Mentor Unlock**: Mentor unlocks station using a credentialed session; sets an 8-hour unlock cookie (`_COOKIE_MAX_AGE = 8 * 60 * 60` in `attendance/signout_utils.py`). Note: Kiosk device tokens use a 7-day cookie in `attendance/kiosk_utils.py`.
- **Signature Capture**: Uses vendored `signature_pad` (v5.0.4). Resized dynamically via `fitCanvas` to maintain 1:1 stroke alignment on touch screens.
- **PRG Pattern**: Submissions follow Post-Redirect-Get (`?done=<pk>`) to prevent duplicate records upon refresh.
- **Undo Workflow**: Dimmed signed-out rows allow undoing sign-outs for the current day via `#undoForm` POST (`?notice=undone`).
- **Signer Selection**: Dropdown populated from the student's linked `Adult` parents (`AdultStudentRelationship` with `is_parent=True`) with a fallback manual text input.
- **Encrypted Storage**: Signatures are decoded from base64 PNGs and stored in encrypted `EncryptedFileField` storage (`DigitalSignout.signature`).

### Daily Presence Tracking (`StudentPresence`)
- Tracks per-day presence (`present` / `absent`) per program and student (`unique_together = ("program", "student", "date")`).
- Mentors manage attendance from the "Who's Here Today?" card (`ProgramDigitalSignoutView`).
- **Tablet Gating Invariant**: Unmarked students default to absent. **Only students explicitly marked present appear on the digital sign-out station.** Submissions for absent/unmarked students are rejected server-side.

### Feature Flag Gating
A single program feature toggle, `signout-sheet` ("Parent sign-outs"), gates both the printable PDF sign-out sheet and the digital sign-out button on the program detail page.
