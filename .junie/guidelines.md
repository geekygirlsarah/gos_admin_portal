### Project Guidelines — GoS Admin Portal

These guidelines help contributors (and Junie) quickly understand the project, how to run it, and how to approach changes.

---

### Project Overview

GoS Admin Portal is a Django 4.2 web application for managing:
- Programs and student enrollments
- Students (profiles, photos, school, graduation year, demographics)
- Adults (Parents, Mentors, Volunteers, Alumni) and their relationships to students
- Mentors/volunteers and their access/clearances
- Program fees, payments, and sliding-scale discounts
- Per-student balance sheets within a program
- Student attendance tracking via kiosks and RFIDs
- API access for external integrations

Key features visible in the repository:
- Authentication via Google (django-allauth). The home page requires login and shows Programs when authenticated. For local development, you can log in with a Django superuser if Google OAuth isn’t configured.
- Program detail view: enroll/remove students, quick-create a student, email program participants, manage fees, record payments, and add sliding scale.
- Student list and a photo grid view.
- Balance sheet per student per program compiling fees, sliding scale, and payments. Printable views are available for balance sheets and payments.
- Attendance system: Kiosk device management, RFID card assignment, and event/session tracking.
- Online Student Applications: Workflow for approving new student applications.
- Program Features: Dynamic feature toggles (Discord, clearances, Andrew ID) per program.

Technologies:
- Django 4.2 (server-side MVC, Django Admin for data management)
- django-allauth (Google OAuth login)
- Bootstrap 5 (frontend styles/layout in templates)
- Pillow (image handling for photos)
- openpyxl (Excel import/export likely for roster/finance—verify views before use)

---

### Repository Structure (high-level)

- .junie/guidelines.md — This file (project guidance for Junie)
- GoSAdminPortal/ — Django project settings and URL routing
- manage.py — Django management utility
- programs/ — Django app containing core models and logic
    - models.py — Program, ProgramFeature, RolePermission, School, Student, Enrollment, Adult, Fee, Payment, SlidingScale, FeeAssignment, StudentApplication
    - admin.py — Django admin registrations and list/field configurations
- attendance/ — Django app for student check-in/out
    - models.py — KioskDevice, RFIDCard, AttendanceEvent, AttendanceSession
    - services.py — Business logic for recording taps and resolving students
- api/ — Django app for external API access
    - models.py — ApiClientKey
- templates/
    - home.html — Auth-gated homepage listing programs
    - programs/
        - detail.html — Program overview, enrollment management, actions
        - balance_sheet.html — Per-student program balance view
        - payment_print.html — Printable payment receipt view
        - balance_sheet_print.html — Printable balance sheet view
    - students/
        - list.html — Tabular student listing with search/edit
        - photo_grid.html — Student photo grid view
    - attendance/
        - student_attendance.html — View student attendance records
- staticfiles/ — Collected static assets for development
- media/ — Uploaded images (e.g., photos/students/, photos/adults/)
- requirements.txt — Python dependencies
- build.sh — Helper script for build/deploy tasks (if used)
- db.sqlite3 — Local development database (not for production)

Other common Django files (manage.py, settings, urls) are expected in the repo but not shown above; refer to the actual tree if needed.

---

### How to Run Locally

Prerequisites:
- Python 3.11+ (recommended)
- A virtual environment tool (venv)
- (Optional) Google OAuth client credentials for django-allauth if you want Google login locally; otherwise use a Django superuser

Steps:
1) Create and activate a virtual environment
    - Windows (PowerShell):
        - python -m venv .venv
        - .\.venv\Scripts\Activate.ps1
2) Install dependencies
    - pip install -r requirements.txt
3) Run migrations
    - python manage.py migrate
4) Create a superuser (for Django Admin)
    - python manage.py createsuperuser
5) Configure environment variables (if using Google login locally)
    - Set django-allauth social application for Google in Django Admin or via fixtures.
    - Ensure SITE domain is configured (Sites framework) to match your callback URL.
    - Note: For local development you can skip Google and sign in with the superuser created in step 4.
6) Start the dev server
    - python manage.py runserver
7) Access the app
    - http://127.0.0.1:8000/
    - Django Admin: http://127.0.0.1:8000/admin/

Media and static:
- Student and adult photos are stored via ImageField (e.g., photos/students/, photos/adults/). Configure MEDIA_ROOT and MEDIA_URL in settings for local development, and ensure your server serves media in development.

---

### Data Model Cheat Sheet

- Program: name, year, dates, active → has fees, features (via ProgramFeature), and students (via Enrollment)
- ProgramFeature: toggleable capability (e.g., 'discord', 'background-checks', 'attendance') linked to Program
- RolePermission: dynamic read/write access settings for Mentors, Parents, and Students per UI section
- School: standalone model linked from Student
- Student: identity (legal vs preferred), contact, school, graduation year, demographics (RaceEthnicity), Discord, photo, medical info; link to primary/secondary Adult contacts; enrollments to Programs; payments via related Fees
- Enrollment: links Student ↔ Program
- Adult: unified model for Parent, Mentor, Alumni, Volunteer; identity, contact, role flags, access (CMU, Discord, Google), clearances, and student relationships
- StudentApplication: online application for students, including an approval workflow to create Student/Adult records
- Fee: per Program fee with name, amount, optional date
- Payment: by Student for a Fee (which belongs to a Program); paid_at, method, optional check number/camp hours, notes; validation enforces enrollment and fee assignments
- SlidingScale: percent discount per Student per Program; may store family size and AGI
- FeeAssignment: optional per-student restriction for a Fee within its Program
- AttendanceEvent: individual check-in/out event, linked to Program, Student/Visitor, and KioskDevice
- AttendanceSession: computed session from events (check_in to check_out), tracks duration
- RFIDCard: mapping of RFID UID to Student
- ApiClientKey: shared secret for API authentication with read or write scope

---

### Permissions and Roles

- Templates check permissions such as perms.programs.add_program, perms.programs.add_student, perms.programs.change_student.
- Admin classes expose advanced filtering and read-only audit fields (created_at, updated_at).
- If you introduce new actions or views, align permission checks with Django’s model permissions or custom permissions (update admin and templates accordingly).

---

### Testing

- Unit tests and integration tests are located in `programs/tests/` and `attendance/tests.py`.
- Tests cover model validation, form logic, business services (attendance), and some view logic.

Running tests:
- python manage.py test

---

### Coding Standards

- Follow PEP 8 for Python code style.
- Use meaningful verbose names for fields and help_text for admin clarity (consistent with existing models).
- Keep admin list_display and filters performant; use select_related/prefetch_related in ModelAdmin methods if adding computed columns.
- Use Meta.ordering on models for predictable listings (already used in several models).

Optional tooling suggestions (not enforced by requirements.txt):
- black, isort, flake8 for formatting and linting

---

### Migrations and Data Integrity

- Maintain explicit validators (e.g., year bounds on Program)
- When altering relationships (e.g., Fee/Payment/Assignment logic), update clean methods and add migrations
- Preserve unique_together constraints: (student, program) for SlidingScale and Enrollment, (program, name) for Fee, (fee, student) for FeeAssignment

---

### Security and Auth

- Google OAuth via django-allauth is used for login on the home page. Ensure proper SocialApp configuration per environment.
- Protect views with @login_required and granular permission checks (as seen in templates).

---

### Junie-Specific Instructions

- Junie should:
    - **Always follow Test-Driven Development (TDD) when possible:** When asked to implement a new feature or fix a bug, first provide the test case that reproduces the issue or defines the new behavior. Only after the test is established should Junie provide the implementation code.
    - Respect read-only mode unless explicitly asked to modify files.
    - When asked to add/modify code, propose exact diffs or full file replacements in the response for a human to apply.
    - Prefer searching the codebase to infer structure before proposing changes.
    - If tests are requested, provide commands and expected outcomes; do not run interactive commands unless permitted.

---

### Contributing Workflow

- Create feature branches, open PRs for review.
- Include a brief description of data model changes and whether migrations are required.
- If you modify forms/admin/templates for Programs/Students/Payments, describe permission implications in the PR.

---

### Deployment Notes (outline)

- Configure environment variables for Django SECRET_KEY, DEBUG, ALLOWED_HOSTS
- Configure database (SQLite for dev, Postgres recommended for prod)
- Set up static and media serving (S3 or equivalent in prod)
- Configure django-allauth SocialApp with production domains and OAuth credentials

---

### Quick Links

- Admin site: /admin/
- Programs list: /programs/
- Program detail: /programs/<id>/
- Students: /students/
- Student photo grid: /students/photos/
- Student attendance: /attendance/students/<student_id>/
- Program balance sheet (per student): /programs/<program_id>/students/<student_id>/balance/
- API Key management: /api/manage/

---

If anything above differs from your environment, adjust the details and update this guidelines file accordingly.