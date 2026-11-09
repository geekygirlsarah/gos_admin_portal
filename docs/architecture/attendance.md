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

### RFID Card Management (`/attendance/rfid/`)
Assigned cards are listed only when the linked student or adult belongs to the
card's organization. A card owned locally but linked to a foreign person is
hidden; assignment by that card's UID is refused before existing local cards
are deactivated or the inconsistent link is changed. Leave such records intact
for separate review.

---

## Reporting & Visualizations

### Hours Chart (`/attendance/hours-chart/`, `attendance_hours_chart_view`)
Visualizes attended hours per student in a program using Chart.js:
- **Axis Layout**: Defaults to horizontal bars (`indexAxis: 'y'`) so 50+ student rosters remain readable. Supports dynamic vertical orientation via `axis=horizontal|vertical` query parameters (preserved in sort links).
- **Expand Layout**: In vertical mode, an expand button (`#expandChartBtn`) applies a per-student `min-width` (`chart_min_width_px`) inside an `overflow-x: auto` wrapper (`#chartScrollWrap`).
- **Attended Days Count**: Calculates distinct attended days via `Count("check_in__date", distinct=True)`, matching active date-range and day-of-week filters (0 for unlogged students).
- **Outreach Overlap**: When the viewer can read outreach, the student table includes the career-to-date overlap of finalized outreach check-ins and attendance sessions, independent of the chart's date/day filters. The shared calculation is organization-scoped and caches closed-session results by interval fingerprint; open-session results are recalculated.
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
- Configurations are served only on their owning organization's host, and the linked program must have the same organization. Student, enrollment, presence, parent, and sign-out lookups are scoped to that organization; newly recorded sign-outs explicitly inherit it.
- **Unlock**: An existing active mentor or lead mentor with active membership in the station's organization, or an active platform superuser, can request a one-time email code. Eligibility is rechecked at redemption; codes expire after ten minutes, have independent request and attempt limits, and fail closed on cache errors. Account provisioning from a submitted email is not allowed.
- Successful unlocks set an eight-hour HTTP-only signed cookie bound to the station and organization (`attendance/signout_utils.py`). Its `Secure` attribute follows `SESSION_COOKIE_SECURE`; the cookie is host-only and uses a signing salt separate from attendance kiosk cookies.
- Previously unlocked stations must be unlocked again; legacy plain-value cookies are rejected. Removing membership does not revoke an already issued sign-out cookie.
- Production multi-worker deployments require a shared cache backend with atomic `add` and `incr` operations and no premature eviction of unexpired code, readiness, rate-limit, or one-use keys. Cache failures deny code issuance/redemption; the default local-memory cache is not suitable. Losing a one-use marker while its code remains cached can permit replay.
- **Signature Capture**: Uses vendored `signature_pad` (v5.0.4). Resized dynamically via `fitCanvas` to maintain 1:1 stroke alignment on touch screens.
- **PRG Pattern**: Submissions follow Post-Redirect-Get (`?done=<pk>`) to prevent duplicate records upon refresh.
- **Undo Workflow**: Dimmed signed-out rows allow undoing sign-outs for the current day via `#undoForm` POST (`?notice=undone`).
- **Signer Selection**: Dropdown populated from the student's linked `Adult` parents (`AdultStudentRelationship` with `is_parent=True`) with a fallback manual text input.
- **Encrypted Storage**: Signatures are decoded from base64 PNGs and stored in encrypted `EncryptedFileField` storage (`DigitalSignout.signature`).

### Public Attendance Kiosks (`/kiosk/`, `/api/v1/kiosk/`)
- Kiosk pages and API requests require an active resolved organization; kiosk and program ownership must both match that organization. Attendance events and sessions inherit the program's organization, and kiosk lookups scope students, mentors, RFID cards, and existing sessions to it.
- Unlock codes are available only to a uniquely matched active account with an active tenant membership as a mentor or lead mentor, or to an active platform superuser. Eligibility is checked again when redeeming the code.
- Successful unlocks set an HTTP-only, signed cookie bound to the kiosk and organization, expiring after seven days. Its `Secure` attribute follows `SESSION_COOKIE_SECURE`; enable this for HTTPS deployments. Existing kiosks must be unlocked again; legacy plain-value cookies are rejected.
- Removing the unlocking user's membership does not revoke an already-issued kiosk cookie. Locking clears only that browser's cookie; disabling the kiosk blocks all its cookies while it remains disabled.
- Code issuance and verification attempts are independently rate-limited with atomic shared-cache counters. Each request stores the current code record before delivery; successful delivery adds a nonce-specific readiness key instead of rewriting that record, so a late email completion cannot restore a superseded code. Production multi-worker deployments must use a shared cache backend with atomic `add` and `incr` operations and no premature eviction of unexpired code, readiness, or one-use keys; the default local-memory cache is not suitable. Losing a one-use marker while its code remains cached can permit replay. Cache failures deny code issuance/redemption, and requesting a replacement code does not reset verification-attempt counters.
- Kiosk and digital sign-out station cookies, codes, rate limits, and cache keys are separate and cannot be interchanged.

### Daily Presence Tracking (`StudentPresence`)
- Tracks per-day presence (`present` / `absent`) per program and student (`unique_together = ("program", "student", "date")`).
- Mentors manage attendance from the "Who's Here Today?" card (`ProgramDigitalSignoutView`).
- **Tablet Gating Invariant**: Unmarked students default to absent. **Only students explicitly marked present appear on the digital sign-out station.** Submissions for absent/unmarked students are rejected server-side.

### Feature Flag Gating
A single program feature toggle, `signout-sheet` ("Parent sign-outs"), gates both the printable PDF sign-out sheet and the digital sign-out button on the program detail page.
