### Project Guidelines — GoS Admin Portal

These guidelines help contributors (and Junie) quickly understand the project, how to run it, and how to approach changes.

---

### Project Overview

GoS Admin Portal is a Django 4.2 web application for managing:
- Programs and student enrollments
- Students (profiles, photos, school, graduation year, demographics)
- Adults (Parents, Mentors, Volunteers, Alumni) and their relationships to students
- Mentors/volunteers and their access/clearances
- Program fees, payments, fee assignments, and sliding-scale discounts
- Per-student balance sheets within a program (with printable + emailable variants)
- Student attendance tracking via kiosks and RFIDs
- A public, multi-step **Applications wizard** (students, parents, prospective mentors) with a staff review workflow
- API access for external integrations (versioned at `/api/v1/`)

Key features visible in the repository:
- Authentication via Google (django-allauth). The root URL redirects to the programs list and requires login. For local development, you can log in with a Django superuser if Google OAuth isn’t configured. A custom `LoginRequiredMiddleware` (in `GoSAdminPortal/middleware.py`) enforces auth globally except for explicit exempt paths (`admin/`, allauth `accounts/`, `/apply/`, `MEDIA_URL`, `STATIC_URL`).
- Custom allauth adapter in `GoSAdminPortal/adapter.py`.
- Program detail view: enroll/remove students, quick-create a student, email program participants, manage fees and fee assignments, record payments, add sliding scale, manage program documents, view dues owed, signout sheets, maps, and settings.
- Student management: list, photo grid, by-grade, by-school, attendance, dues-owed, detail, emergency contacts, convert-to-alumni workflow.
- Adults split by role: dedicated list/form templates for adults, parents, mentors, alumni, and schools.
- Balance sheet per student per program compiling fees, sliding scale, and payments. Printable views and an email-out flow exist for balance sheets and payments.
- Attendance system: Kiosk device management, RFID card assignment, and event/session tracking.
- Public Applications wizard (`/apply/`): 9-step resumable flow (welcome → applicant type → program → verify email → student info → primary/secondary parent → confirm → documents) plus a mentor sub-flow (info, clearance interest, clearance detail, confirm, blocked). Includes email notifications (submitted, approved, declined, lead notification, parent handoff) and a staff review area (list, detail, edit, decline, delete).
- Program Features: dynamic feature toggles (Discord, clearances, Andrew ID, attendance, etc.) per program.
- Imports dashboard (`templates/imports/`) with CSV samples in `templates/samples/` (students, parents, mentors, schools, relationships, attendance).

Technologies:
- Django 4.2 (server-side MVC, Django Admin for data management)
- django-allauth (Google OAuth login + custom adapter)
- Bootstrap 5 (frontend styles/layout in templates)
- Pillow (image handling for photos)
- openpyxl (Excel import/export — verify views before use)

---

### Repository Structure (high-level)

- `.junie/guidelines.md` — This file (project guidance for Junie)
- `GoSAdminPortal/` — Django project settings, URL routing, middleware, allauth adapter
    - `settings.py`, `urls.py`, `asgi.py`, `wsgi.py`
    - `middleware.py` — `LoginRequiredMiddleware` with exempt prefixes
    - `adapter.py` — custom allauth account/social adapter
- `manage.py` — Django management utility
- `programs/` — Core app (programs, students, adults, fees, payments, sliding scale, schools, enrollments, role permissions)
    - `models.py`, `admin.py`, `forms.py`, `views.py`, `urls.py`
    - `permission_views.py` — views for managing `RolePermission`
    - `signals.py` — Django signal handlers
    - `utils.py` — shared helpers
    - `management/commands/` — `seed_db.py`, `convert_graduates.py`
    - `templatetags/`, `static/`
    - `tests/` — extensive suite (assignments, balance sheet, commands, crews, email balances, forms, models, sliding scale time, tax forms, teams, utils, views, issue reproductions, etc.)
- `applications/` — Public application wizard + staff review workflow (supersedes the legacy `StudentApplication` model in `programs`)
    - `models.py` — `Application`, `ApplicationDocumentSubmission`, etc.
    - `views.py`, `urls.py`, `forms.py`, `services.py`, `admin.py`
    - `review.py` — staff review views/logic
    - `templatetags/application_extras.py`
    - `tests/` — `test_mentor.py`, `test_models.py`, `test_review.py`, `test_step9.py`, `test_steps_5_8.py`, `test_views.py`, `test_welcome_back.py`
- `attendance/` — Student check-in/out
    - `models.py` — `KioskDevice`, `RFIDCard`, `AttendanceEvent`, `AttendanceSession`
    - `services.py` — Business logic for recording taps and resolving students
    - `views.py`, `admin.py`, `tests.py`
- `api/` — External API access (mounted at `/api/v1/`) and API key management (`/api-keys/`)
    - `models.py` — `ApiClientKey`
    - `auth.py` — API key authentication
    - `urls.py` (v1 endpoints), `manage_urls.py` (key management UI)
    - `forms.py`, `views.py`, `admin.py`
- `templates/`
    - `base.html`, `home.html`
    - `account/` — allauth login, logout, request/confirm login-code, email templates
    - `programs/` — `detail.html`, `form.html`, `settings.html`, `balance_sheet.html`, `balance_sheet_print.html`, `balance_sheet_email.html`, `payment_form.html`, `payment_detail.html`, `payment_print.html`, `fee_form.html`, `fee_print.html`, `fee_select.html`, `fee_assignment_form.html`, `sliding_scale_form.html`, `email_form.html`, `email_balances_form.html`, `dues_owed.html`, `signout_sheet.html`, `map.html`, `schools.html`, `assignment.html`, `application_review_list.html`, `application_review_detail.html`, `program_document_form.html`, `program_document_confirm_delete.html`
    - `students/` — `list.html`, `detail.html`, `form.html`, `_form_fields.html`, `photo_grid.html`, `by_grade.html`, `by_school.html`, `attendance.html`, `dues_owed.html`, `emergency_contacts.html`, `convert_to_alumni.html`, `convert_to_alumni_preview.html`
    - `adults/`, `parents/`, `mentors/`, `alumni/`, `schools/` — `list.html`/`form.html` per role
    - `applications/` — wizard steps `step1`…`step9`, `submitted.html`, `_wizard_base.html`, `continue_placeholder.html`, mentor sub-flow (`mentor_info.html`, `mentor_clearance_interest.html`, `mentor_clearance_detail.html`, `mentor_confirm.html`, `mentor_blocked.html`), `review/` (list/detail/edit/decline/delete/_base), `email/` (submitted, approved, declined, lead_notification, parent_handoff)
    - `apply/` — public landing templates (`wizard.html`, `thanks.html`, `handoff_sent.html`)
    - `api/` — `api_key_form.html`
    - `imports/dashboard.html`, `samples/*.csv` — CSV import dashboard + sample files
    - `attendance/student_attendance.html`
    - `django/forms/widgets/checkbox_select.html` — custom widget override
- `staticfiles/` — Collected static assets for development
- `media/` — Uploaded images (e.g., `photos/students/`, `photos/adults/`) and application document uploads
- `requirements.txt` — Python dependencies
- `build.sh` — Helper script for build/deploy tasks
- `run_ci.sh` / `run_ci.ps1` / `run_ci.bat` — Local CI runners (lint, format, security, tests)
- `db.sqlite3` — Local development database (not for production)

---

### How to Run Locally

Prerequisites:
- Python 3.13+ (3.13 is in use in the active venv `venv2/`)
- A virtual environment tool (venv)
- (Optional) Google OAuth client credentials for django-allauth if you want Google login locally; otherwise use a Django superuser

Steps:
1) Create and activate a virtual environment
    - Windows (PowerShell):
        - `python -m venv .venv`
        - `.\.venv\Scripts\Activate.ps1`
    - Note: the repo also contains an existing `venv2/` that may already be configured.
2) Install dependencies
    - `pip install -r requirements.txt`
3) Run migrations
    - `python manage.py migrate`
4) Create a superuser (for Django Admin)
    - `python manage.py createsuperuser`
5) (Optional) Seed development data
    - `python manage.py seed_db`
6) Configure environment variables (if using Google login locally)
    - Set django-allauth social application for Google in Django Admin or via fixtures.
    - Ensure the Sites framework SITE domain matches your callback URL.
    - For local dev you can skip Google and sign in with the superuser.
7) Start the dev server
    - `python manage.py runserver`
8) Access the app
    - Root `/` redirects to the programs list (`program_list`)
    - Django Admin: `http://127.0.0.1:8000/admin/`
    - Public applications wizard: `http://127.0.0.1:8000/apply/`

Media and static:
- Student/adult photos and application documents are stored via `ImageField`/`FileField`. Configure `MEDIA_ROOT` and `MEDIA_URL` in settings. In `DEBUG=False`, media is served via an explicit `serve` re_path in `GoSAdminPortal/urls.py`.

---

### Running CI Locally

Use the `run_ci.*` scripts to mirror the CI pipeline before pushing. They run, in order:
- `flake8` (critical errors then full report)
- `black --check` (excludes `venv`, `venv2`, `.venv`)
- `isort --check-only --profile black`
- `bandit -r .` (security)
- `safety check` (dependency CVEs — may warn)
- `semgrep --config auto` (static analysis — may warn)
- `python manage.py check`
- `python manage.py test`

Choose the script for your shell:
- PowerShell: `./run_ci.ps1`
- Bash/WSL: `./run_ci.sh`
- cmd: `run_ci.bat`

---

### Data Model Cheat Sheet

- **Program** — name, year, dates, active → has fees, features (via `ProgramFeature`), students (via `Enrollment`), program documents
- **ProgramFeature** — toggleable capability (e.g., `discord`, `background-checks`, `attendance`) linked to Program
- **RolePermission** — dynamic read/write access settings for Mentors, Parents, and Students per UI section (managed via `programs/permission_views.py`)
- **School** — standalone model linked from Student
- **Student** — identity (legal vs preferred), contact, school, graduation year, demographics (RaceEthnicity), Discord, photo, medical info; link to primary/secondary Adult contacts; enrollments to Programs; payments via related Fees
- **Enrollment** — links Student ↔ Program (unique together)
- **Adult** — unified model for Parent, Mentor, Alumni, Volunteer; identity, contact, role flags, access (CMU, Discord, Google), clearances, and student relationships
- **Fee** — per-Program fee with name, amount, optional date (unique `program`+`name`)
- **Payment** — by Student for a Fee; `paid_at`, method, optional check number/camp hours, notes; validation enforces enrollment and fee assignments
- **SlidingScale** — percent discount per Student per Program; may store family size and AGI (unique `student`+`program`)
- **FeeAssignment** — optional per-student restriction for a Fee within its Program (unique `fee`+`student`)
- **Application / ApplicationDocumentSubmission** (in `applications/`) — multi-step resumable application records; replaces the legacy `programs.StudentApplication`
- **AttendanceEvent** — individual check-in/out event, linked to Program, Student/Visitor, and KioskDevice
- **AttendanceSession** — computed session from events (check_in to check_out), tracks duration
- **RFIDCard** — mapping of RFID UID to Student
- **ApiClientKey** — shared secret for API authentication with read or write scope

---

### Permissions and Roles

- Templates check permissions such as `perms.programs.add_program`, `perms.programs.add_student`, `perms.programs.change_student`, plus dynamic `RolePermission` checks for Mentor/Parent/Student UI sections.
- A `lead_mentors` group is created via migration `applications/migrations/0004_lead_mentors_group.py` and is used for application review access.
- Admin classes expose advanced filtering and read-only audit fields (`created_at`, `updated_at`).
- New actions/views should align permission checks with Django’s model permissions or custom permissions (update admin and templates accordingly).

---

### Testing

- Unit/integration tests live in `programs/tests/`, `applications/tests/`, and `attendance/tests.py`.
- Coverage includes model validation, form logic, business services (attendance, applications, balances, email), permission flows, management commands, and view logic. Several `test_issue_reproduction.py`-style files exist — when fixing bugs, follow the same pattern and add a reproducer test first (TDD).

Running tests:
- All: `python manage.py test`
- A single app: `python manage.py test applications`
- A single test: `python manage.py test programs.tests.test_balance_sheet.SomeTestCase.test_method`

---

### Coding Standards

- Follow PEP 8 for Python code style.
- Code is formatted with **black** and imports sorted with **isort** (`--profile black`). Run them before committing — CI will fail otherwise.
- Lint with **flake8**; security-scan with **bandit**.
- Use meaningful verbose names for fields and `help_text` for admin clarity (consistent with existing models).
- Keep admin `list_display` and filters performant; use `select_related`/`prefetch_related` in `ModelAdmin` methods when adding computed columns.
- Use `Meta.ordering` on models for predictable listings.

---

### Migrations and Data Integrity

- Maintain explicit validators (e.g., year bounds on Program).
- When altering relationships (e.g., Fee/Payment/Assignment logic), update `clean` methods and add migrations.
- Preserve `unique_together` constraints: `(student, program)` for SlidingScale and Enrollment, `(program, name)` for Fee, `(fee, student)` for FeeAssignment.
- The `applications` app supersedes legacy `programs.StudentApplication`; new application logic belongs in `applications/`, not `programs/`.

---

### Security and Auth

- Google OAuth via django-allauth is used for login. Ensure proper `SocialApp` configuration per environment.
- `LoginRequiredMiddleware` enforces login globally; only `admin/`, `accounts/` (allauth), `/apply/`, `MEDIA_URL`, and `STATIC_URL` are exempt. New public routes must be explicitly exempted.
- Protect views with `@login_required` plus granular permission checks (as seen in templates).
- API endpoints under `/api/v1/` authenticate via `ApiClientKey` (`api/auth.py`); manage keys at `/api-keys/`.

---

### Junie-Specific Instructions

- Junie should:
    - **Always follow Test-Driven Development (TDD) when possible:** when implementing a new feature or fixing a bug, first provide the test case that reproduces the issue or defines the new behavior; only then provide the implementation.
    - Respect read-only mode unless explicitly asked to modify files.
    - When asked to add/modify code, propose exact diffs or full file replacements for a human to apply.
    - Prefer searching the codebase to infer structure before proposing changes (the `applications/` app and `programs/permission_views.py`, `signals.py`, `utils.py` are easy to miss).
    - When touching the public flow, remember the wizard lives in `applications/` with templates under `templates/applications/` (steps + staff review) and `templates/apply/` (public landing).
    - When touching templates for adults, prefer the role-specific folders (`adults/`, `parents/`, `mentors/`, `alumni/`) over a generic one.
    - If tests are requested, provide commands and expected outcomes; do not run interactive commands unless permitted.
    - Run `run_ci.ps1` (or its peers) before declaring a change complete when feasible.

---

### Contributing Workflow

- Create feature branches, open PRs for review.
- Include a brief description of data model changes and whether migrations are required.
- If you modify forms/admin/templates for Programs/Students/Payments/Applications, describe permission implications in the PR.
- Ensure black/isort/flake8/bandit pass and `manage.py test` is green.

---

### Deployment Notes (outline)

- Configure environment variables for Django `SECRET_KEY`, `DEBUG`, `ALLOWED_HOSTS`.
- Configure database (SQLite for dev, Postgres recommended for prod).
- Set up static and media serving (S3 or equivalent in prod). Note that production media is currently served via a Django `serve` view when `DEBUG=False` in `GoSAdminPortal/urls.py` — replace this with a real object-store/CDN setup for real deployments.
- Configure django-allauth `SocialApp` with production domains and OAuth credentials.

---

### Quick Links

- Admin site: `/admin/`
- Home (redirects to programs): `/`
- Programs list: `/programs/`
- Program detail: `/programs/<id>/`
- Per-student balance sheet: `/programs/<program_id>/students/<student_id>/balance/`
- Students: `/students/` (and by-grade/by-school/photo-grid/dues-owed variants)
- Student attendance: `/attendance/students/<student_id>/`
- Public applications wizard: `/apply/`
- Staff application review: under the programs app (`application_review_list` / `application_review_detail`)
- API (v1): `/api/v1/`
- API Key management: `/api-keys/`

---

If anything above differs from your environment, adjust the details and update this guidelines file accordingly.
