# GoS Admin Portal — Agent Guidelines

These guidelines help coding agents (like Junie) understand the project, its structure, and how to contribute effectively.

## Critical Rules

- **Always follow Test-Driven Development (TDD):** When implementing a new feature or fixing a bug, YOU MUST first provide the test case that reproduces the issue or defines the new behavior. Only then provide the implementation.
- **Respect read-only mode:** Do not modify files unless explicitly asked.
- **Follow existing style:** Match the project's use of Black, Isort, and PEP 8.
- **No interactive commands:** All terminal commands must be non-interactive.
- **Don't make large assumptions:** If something is unclear, ask before making assumptions.

## Project Overview

Girls of Steel (GoS) Admin Portal is a Django 5 web application for managing:
- Programs and student enrollments
- Students (profiles, photos, school, graduation year, demographics)
- Adults (Parents, Mentors, Volunteers, Alumni) and their relationships to students
- Mentors/volunteers and their access/clearances
- Program fees, payments, fee assignments, and sliding-scale discounts
- Per-student balance sheets within a program
- Student attendance tracking via kiosks and RFIDs
- Public Applications wizard (`/apply/`) with a staff review workflow
- API access for external integrations (`/api/v1/`)

### Key Technologies
- Django 5.2 (with goals of migrating towards 6)
- django-allauth (Email OTP login)
- Bootstrap 5
- Pillow (Images)
- openpyxl (Excel)
- PostgreSQL in production, SQLite for local testing

## Repository Structure

- `AGENTS.md` — This file (project guidance for agents)
- `GoSAdminPortal/` — Settings, URLs, middleware (`LoginRequiredMiddleware`), allauth adapter
- `programs/` — Core app (programs, students, adults, fees, payments, sliding scale, enrollments)
- `applications/` — Public application wizard + staff review workflow (supersedes legacy `StudentApplication`)
- `attendance/` — Student check-in/out logic and RFID management
- `api/` — Versioned API and key management
- `templates/` — Django templates organized by app/role
- `audit/` — Audit log app (inspect models before assuming it's fully wired up)
- `portal/` — Inspect before use; may contain legacy or shared views

## Local Development

| Task | Command |
|------|---------|
| Install dependencies | `pip install -r requirements.txt` |
| Run migrations | `python manage.py migrate` |
| Create superuser | `python manage.py createsuperuser` |
| Seed dev data | `python manage.py seed_db` |
| Start dev server | `python manage.py runserver` |
| Run all tests | `python manage.py test` |
| Run CI checks | `.\run_ci.ps1` (Windows) or `./run_ci.sh` (Linux) |
| Run tests with coverage | `coverage run manage.py test --parallel; coverage combine; coverage report` |

## Data Model Summary

- **Program**: Central entity with fees, features, and enrollments.
- **Student**: Identity, school, graduation, demographics, medical, and relationships to adults. A student is considered **inactive** once marked `graduated=True`; within a program they're inactive if their `Enrollment.active` is `False` or they've graduated. When building student dropdowns/selection lists, reuse `active_students()` / `active_students_in_program(program)` in `programs/utils.py` so inactive students stay out. On program-scoped list pages, separate inactive students into their own section (as `programs/views.py` does for the program detail, assignment, photo grid, and dues-owed views). Student↔Adult links are stored in the `AdultStudentRelationship` through model (also the source of `Student.adults` / `Adult.students`). Each student's primary/secondary parent/guardian is normalized there via the `primary_contact_relationship` / `secondary_contact_relationship` FKs; `Student.primary_contact`, `Student.secondary_contact`, `Student.primary_contact_id`, and `Student.secondary_contact_id` are **compat properties** (with setters) that keep the through row and pointer in sync. When filtering on primary/secondary in queries, use the relationship FKs (e.g. `primary_contact_relationship__adult=...`) — never the compat property names.
- **Adult**: Unified model for Parent, Mentor, Alumni, Volunteer.
- **Enrollment**: Links Student ↔ Program.
- **Fee**: Per-Program costs.
- **Payment**: Recorded against a Fee for a Student.
- **SlidingScale**: Percent discount tied to a Student (not a single Program) with a `status` (`pending`/`approved`/`declined`), an effective `date`/`expiration_date`, and household-size/AGI questionnaire fields. An approved, non-expired record applies to that student's fees across **all** of their programs — see `get_active_sliding_scale()` / `get_student_balance_data()` in `programs/utils.py`. Parents apply from the Payments page (`sliding_scale_apply`); Lead Mentors review from the `sliding_scale_review_list`/`sliding_scale_review_decide` views, which auto-delete uploaded `TaxForm` documents once a decision is made. The base/multiplier numbers used to suggest a discount percent live in the singleton `SlidingScaleSettings` model, editable from Portal Settings → Sliding Scale tab.
- **Application**: Multi-step resumable application records (in `applications/`).
- **AttendanceEvent / Session**: RFID-based check-in/out tracking. Visitor names are denormalized on these records; Lead Mentors can manage and merge inconsistent visitor names via the **Visitor Management** tool (`VisitorManagementView`) to ensure standardized reporting.
- **AddressGeocode**: Cache of geocoded student addresses used by the program map view (in `programs/views/programs.py` `ProgramStudentMapView`). Addresses are geocoded server-side via Nominatim and cached keyed by a normalized address (see `programs/utils/geocoding.py`); students who share an address reuse the same row. The map page renders markers from pre-geocoded coordinates (no client-side geocoding) and `fitBounds` to the students' area. Lead mentors can pre-populate the cache with `python manage.py geocode_student_addresses`. For Parents/Students the map is a "Carpool Map" (buttons on their dashboards) and only includes students with `directory_consent=True`. Tuning knobs live in settings: `GEOCODING_URL`, `GEOCODING_USER_AGENT`, `GEOCODING_TIMEOUT`, `GEOCODING_DELAY_SECONDS`.
- **BackgroundCheck**: A single PA clearance (PA State Police, PA Child Abuse/Act 151, or FBI) held by a **Student** or an **Adult** (exactly one of `student`/`adult` is set; not enforced in the DB because a student becoming an alumni may hold both records). Per PA rules clearances are valid 5 years, so each row stores `cleared` and the `obtained_date` (when it became active); `expiration_date` is a **derived property** = `obtained_date + 5 years` (never stored). The university holds the actual forms — we only track status/dates, no uploads. **Whether a student *requires* clearances is always derived**, never cached: `Student.requires_background_check()` (17 or older on Sept 1 of the current academic year) and `Student.needs_background_check()` (required **and** missing a valid clearance of one of the three types) in `programs/models.py`. List pages flag "BG Check Needed" via the `needs_bg` template tag (in `programs/templatetags/form_tags.py`). `Enrollment.post_save` auto-sets `Enrollment.clearance_due` for enrolled students who need checks (see `programs/signals.py`). Exposed read-only on all Adult/Student view screens and editable inline via the `BackgroundChecksForm` (in `programs/forms.py`), wired into the student/adult/mentor/parent update views through the `BackgroundChecksInlineMixin` (in `programs/views/mixins.py`). Editing is gated on `can_user_write('background_checks', obj)` (Lead Mentors only by default); other roles see clearances read-only. Also managed in admin via `BackgroundCheckInline`.

## Coding Standards

- **PEP 8**: Follow standard Python style.
- **Formatting**: Use `black` and `isort --profile black`.
- **Linting**: Use `flake8` and `bandit` for security.
- **Admin**: Use meaningful `verbose_name` and `help_text`. Keep `list_display` performant with `select_related`/`prefetch_related`.
- **Migrations**: Maintain validators and preserve unique constraints. Do not remove or modify existing migrations without careful consideration.
- **Documentation**: Update `AGENTS.md` with any significant changes to the project architecture.

## Security and Permissions

- **Global Auth**: `LoginRequiredMiddleware` in `GoSAdminPortal/middleware.py` enforces login globally. Unknown paths redirect to login (not bypass it).
- **Exempt URLs**: `/apply/`, `/accounts/*`, `admin/`, `privacy_policy`, `non_discrimination_policy`, and static/media. These are listed in `EXEMPT_URL_NAMES` inside the middleware.
- **Wizard Rate Limiting**: The public wizard (`/apply/`) is throttled because it's anonymous and login-exempt. `ApplyRateLimitMiddleware` caps each client IP at `APPLY_IP_POST_LIMIT` (default 10) POSTs per `APPLY_IP_POST_WINDOW_SECONDS` (default 60). OTP limits are enforced in the views via `applications/rate_limiting.py`: `APPLY_OTP_SEND_LIMIT` (default 5) code requests per email per hour and `APPLY_OTP_VERIFY_LIMIT` (default 10) verify attempts per application per hour. All limits are disabled while running tests (`APPLY_RATE_LIMIT_ENABLED` defaults to `not TESTING`) because the shared test-client IP and process-local cache would otherwise leak hits between tests; re-enable in tests with `override_settings(APPLY_RATE_LIMIT_ENABLED=True, TESTING=False)` (clear the cache in `setUp`). Counters use the Django cache — configure a shared backend (`CACHES` via `CACHE_BACKEND`/`CACHE_LOCATION`) in production so limits apply across app servers. Exceeded limits return HTTP 429 with a `Retry-After` header and render `templates/429.html`.
- **Roles**: Determined by `get_user_role(user)` in `programs/permission_views.py`. Priority order: `LeadMentor` (superuser or `LeadMentor` group) → `Mentor` → `Parent` → `Alumni` → `Student` → `Staff`/None. Because an Adult can hold several roles at once (e.g. a parent who also mentors), `get_user_role` alone is not enough to answer "is this person a parent?" — use the flag helpers `user_is_parent(user)`, `user_is_mentor(user)`, and `user_is_alumni(user)` (same file) for role-specific features like the Payments page and balance sheets. Finance sections in `can_user_read` treat any parent as a parent even if they'd otherwise resolve to `Mentor`/`Alumni`.
- **Dynamic Permissions**: `RolePermission` model lets Lead Mentors configure per-section read/write access for each role. Check with `can_user_read(user, section, obj=None)` and `can_user_write(user, section, obj=None)`. By default Mentors get read+write on the `attendance` section (so they can close stale sessions on the Who's Here Now page and manage RFID cards); deletion is still blocked for them via `can_user_delete()`.
- **View Mixins**: Use `LeadMentorRequiredMixin`, `DynamicReadPermissionMixin`, or `DynamicWritePermissionMixin` (all in `programs/permission_views.py`). Do NOT use raw `has_perm()` checks for portal views.
- **Object-Level Access**: `can_user_read`/`can_user_write` accept an `obj` argument for per-object checks (e.g., a Parent can only read their own students). Always pass `obj` when checking access to a specific record.
- **Mentor Adult Access**: Mentors can only view Adults with `is_parent=True` who have a student in an active program. This is enforced in both the queryset and `can_user_read`.
- **API Keys**: Authenticate via `ApiClientKey` in `api/auth.py`.
- **One Lead Mentor group**: There is only `"LeadMentor"` (no space). A single membership grants access to all Lead Mentor features including application review.

## Testing Strategy and Contribution

- **Location**: Tests live in `programs/tests/`, `applications/tests/`, and `attendance/tests/` (with `test_models.py`, `test_views.py`, `test_kiosk.py`, `test_permissions.py`, `test_reliability.py`, `test_who_is_here.py`).
- **Story Integration Tests**: For complex lifecycles (e.g., Application -> Conversion -> Financials), use "Story" tests that exercise multiple views and signals in a single test case (see `programs/tests/test_integration_flows.py` for examples). These help catch regressions in side-effects (like emails or balance calculations) that unit tests often miss.
- **TDD Requirement**: When fixing bugs, add a reproducer test file (e.g., `test_issue_reproduction.py`) before applying the fix.
- **UI Automation**: For JS-heavy components (like DualListbox or the multi-step application wizard), consider browser-based tests using Playwright.
- **Scope**: Include model validation, forms, services, and view logic.
- **Bandit**: If adding new tests that have hard-coded passwords, add `# nosec B106` on the line with the password string.
- **Groups in tests**: Use `Group.objects.get_or_create(name="LeadMentor")` (no space). Do not create a `"Lead Mentors"` group — it no longer exists.
- Add unit tests for new business logic.
- Keep changes minimal and avoid large refactors in feature tasks.
- Do not rename files without a valid technical reason.
- Make small, targeted changes instead of building for hypothetical future needs.
- Try to run parallelized tests (`python manage.py test` commands with the `--parallel=16` option) to speed up work. If traces are needed, run without parallelization.

## UI and Architecture Guidelines
- Use Bootstrap to ensure a consistent appearance.
- Avoid inline styles if possible.
- Ensure pages remain accessible and responsive.
- Add to `CHANGELOG.md` for user-facing changes, and describe them in a way that general users can understand. Keep entries already in there and just add on to it.
- Update `AGENTS.md` with any significant changes to the project architecture.
- While you should update the Django admin site to reflect changes in the project architecture, usually a page needs to be edited in the main app as well.
- Ask before making significant changes to architecture, and write tests to ensure they will work after.

## Agent-Specific Tips (Junie, Copilot, OpenCode, etc.)

- Prefer role-specific template folders (`adults/`, `parents/`, `mentors/`, `alumni/`).
- The `applications` app supersedes legacy models in `programs`.
- Use `run_ci.ps1` (or equivalent) before finishing a task.
- Search the codebase to infer structure; `programs/permission_views.py`, `signals.py`, and `utils.py` have important reusable code blocks. Reuse those whenever possible, or add similar code blocks into these files.
- When adding a new view, pick the right mixin from `programs/permission_views.py`: `LeadMentorRequiredMixin` for Lead Mentor-only actions, `DynamicReadPermissionMixin` / `DynamicWritePermissionMixin` (with `permission_section`) for role-configurable pages, `LoginRequiredMixin` for any authenticated user.
- When filtering querysets by role, use `StudentQuerysetRoleMixin.filter_students_by_role()` (in `programs/views.py`) instead of duplicating the Parent/Student filter pattern.
- When limiting a student queryset to active students, use `active_students()` (non-graduated) for global lists and `active_students_in_program(program)` (active enrollment + non-graduated) for program-scoped lists, both in `programs/utils.py`. Don't build the `enrollment__active=True, graduated=False` filter inline.
- `signals.py` uses string-based lazy senders (`sender="programs.Adult"`) — not lambdas. Follow this pattern for any new signal receivers.
- The `PortalSettingsView` handles GET only. Permission updates, team, crew, and subteam changes each have their own dedicated view (`PortalPermissionsUpdateView`, `PortalTeamView`, `PortalCrewView`, `PortalSubteamView`).
