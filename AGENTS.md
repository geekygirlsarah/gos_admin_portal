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
- **Student**: Identity, school, graduation, demographics, medical, and relationships to adults.
- **Adult**: Unified model for Parent, Mentor, Alumni, Volunteer.
- **Enrollment**: Links Student ↔ Program.
- **Fee**: Per-Program costs.
- **Payment**: Recorded against a Fee for a Student.
- **SlidingScale**: Percent discount tied to a Student (not a single Program) with a `status` (`pending`/`approved`/`declined`), an effective `date`/`expiration_date`, and household-size/AGI questionnaire fields. An approved, non-expired record applies to that student's fees across **all** of their programs — see `get_active_sliding_scale()` / `get_student_balance_data()` in `programs/utils.py`. Parents apply from the Payments page (`sliding_scale_apply`); Lead Mentors review from the `sliding_scale_review_list`/`sliding_scale_review_decide` views, which auto-delete uploaded `TaxForm` documents once a decision is made. The base/multiplier numbers used to suggest a discount percent live in the singleton `SlidingScaleSettings` model, editable from Portal Settings → Sliding Scale tab.
- **Application**: Multi-step resumable application records (in `applications/`).
- **AttendanceEvent / Session**: RFID-based check-in/out tracking.

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
- **Roles**: Determined by `get_user_role(user)` in `programs/permission_views.py`. Priority order: `LeadMentor` (superuser or `LeadMentor` group) → `Mentor` → `Parent` → `Alumni` → `Student` → `Staff`/None.
- **Dynamic Permissions**: `RolePermission` model lets Lead Mentors configure per-section read/write access for each role. Check with `can_user_read(user, section, obj=None)` and `can_user_write(user, section, obj=None)`.
- **View Mixins**: Use `LeadMentorRequiredMixin`, `DynamicReadPermissionMixin`, or `DynamicWritePermissionMixin` (all in `programs/permission_views.py`). Do NOT use raw `has_perm()` checks for portal views.
- **Object-Level Access**: `can_user_read`/`can_user_write` accept an `obj` argument for per-object checks (e.g., a Parent can only read their own students). Always pass `obj` when checking access to a specific record.
- **Mentor Adult Access**: Mentors can only view Adults with `is_parent=True` who have a student in an active program. This is enforced in both the queryset and `can_user_read`.
- **API Keys**: Authenticate via `ApiClientKey` in `api/auth.py`.
- **One Lead Mentor group**: There is only `"LeadMentor"` (no space). A single membership grants access to all Lead Mentor features including application review.

## Testing Strategy and Contribution

- **Location**: Tests live in `programs/tests/`, `applications/tests/`, `attendance/tests.py`, and `attendance/test_attendance_permissions.py`.
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

## Junie-Specific Tips

- Prefer role-specific template folders (`adults/`, `parents/`, `mentors/`, `alumni/`).
- The `applications` app supersedes legacy models in `programs`.
- Use `run_ci.ps1` (or equivalent) before finishing a task.
- Search the codebase to infer structure; `programs/permission_views.py`, `signals.py`, and `utils.py` have important reusable code blocks. Reuse those whenever possible, or add similar code blocks into these files.
- When adding a new view, pick the right mixin from `programs/permission_views.py`: `LeadMentorRequiredMixin` for Lead Mentor-only actions, `DynamicReadPermissionMixin` / `DynamicWritePermissionMixin` (with `permission_section`) for role-configurable pages, `LoginRequiredMixin` for any authenticated user.
- When filtering querysets by role, use `StudentQuerysetRoleMixin.filter_students_by_role()` (in `programs/views.py`) instead of duplicating the Parent/Student filter pattern.
- `signals.py` uses string-based lazy senders (`sender="programs.Adult"`) — not lambdas. Follow this pattern for any new signal receivers.
- The `PortalSettingsView` handles GET only. Permission updates, team, crew, and subteam changes each have their own dedicated view (`PortalPermissionsUpdateView`, `PortalTeamView`, `PortalCrewView`, `PortalSubteamView`).
