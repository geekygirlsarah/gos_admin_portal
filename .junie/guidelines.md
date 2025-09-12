### Project Guidelines — GoS Admin Portal

These guidelines help contributors (and Junie) quickly understand the project, how to run it, and how to approach changes.

---

### Project Overview

GoS Admin Portal is a Django 4.2 web application for managing:
- Programs and student enrollments
- Students (profiles, photos, school, graduation year)
- Parents/guardians and relationships to students
- Mentors/volunteers and their access/clearances
- Program fees, payments, and sliding-scale discounts
- Per-student balance sheets within a program

Key features visible in the repository:
- Authentication via Google (django-allauth). The home page requires login and shows Programs when authenticated.
- Program detail view: enroll/remove students, quick-create a student, email program participants, manage fees, record payments, and add sliding scale.
- Student list and a photo grid view.
- Balance sheet per student per program compiling fees, sliding scale, and payments.

Technologies:
- Django 4.2 (server-side MVC, Django Admin for data management)
- django-allauth (Google OAuth login)
- Pillow (image handling for photos)
- openpyxl (Excel import/export likely for roster/finance—verify views before use)

---

### Repository Structure (high-level)

- .junie/guidelines.md — This file (project guidance for Junie)
- programs/ — Django app containing models and admin configuration
    - models.py — Program, School, Student, Enrollment, Parent, Mentor, Fee, Payment, SlidingScale, FeeAssignment
    - admin.py — Django admin registrations and list/field configurations
- templates/
    - home.html — Auth-gated homepage listing programs
    - programs/
        - detail.html — Program overview, enrollment management, actions
        - balance_sheet.html — Per-student program balance view
    - students/
        - list.html — Tabular student listing with search/edit
        - photo_grid.html — Student photo grid view
- requirements.txt — Python dependencies

Other common Django files (manage.py, settings, urls) are expected in the repo but not shown above; refer to the actual tree if needed.

---

### How to Run Locally

Prerequisites:
- Python 3.10+
- A virtual environment tool (venv)
- Google OAuth client credentials for django-allauth (if you want social login locally)

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
6) Start the dev server
    - python manage.py runserver
7) Access the app
    - http://127.0.0.1:8000/
    - Django Admin: http://127.0.0.1:8000/admin/

Media and static:
- Student and mentor photos are stored via ImageField (e.g., photos/students/, photos/mentors/). Configure MEDIA_ROOT and MEDIA_URL in settings for local development, and ensure your server serves media in development.

---

### Data Model Cheat Sheet

- Program: name, description, active, year → has fees and students (via Enrollment)
- School: standalone model linked from Student
- Student: identity, contact, school, graduation year, Discord, photo; link to primary/secondary Parent; enrollments to Programs; payments via related Fees
- Enrollment: links Student ↔ Program
- Parent: identity/contact; many-to-many with Students
- Mentor: identity/contact, role, IDs, Discord, access flags, clearances, emergency contact, status
- Fee: per Program fee with name, amount, optional date
- Payment: by Student for a Fee (which belongs to a Program); paid_at, method, optional check number/camp hours, notes; validation enforces enrollment and fee assignments
- SlidingScale: percent discount per Student per Program; may store family size and AGI
- FeeAssignment: optional per-student restriction for a Fee within its Program

---

### Permissions and Roles

- Templates check permissions such as perms.programs.add_program, perms.programs.add_student, perms.programs.change_student.
- Admin classes expose advanced filtering and read-only audit fields (created_at, updated_at).
- If you introduce new actions or views, align permission checks with Django’s model permissions or custom permissions (update admin and templates accordingly).

---

### Testing

- No explicit tests are visible in the provided snapshot. Recommended next steps:
    - Add unit tests for model validation rules (e.g., Payment.clean and FeeAssignment.clean logic)
    - Add view tests for program enrollment, payment creation, and sliding-scale calculations

Running tests (once added):
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
- Program balance sheet (per student): /programs/<program_id>/students/<student_id>/balance/

---

If anything above differs from your environment, adjust the details and update this guidelines file accordingly.