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

## Data Model Summary

- **Program**: Central entity with fees, features, and enrollments.
- **Student**: Identity, school, graduation, demographics, medical, and relationships to adults.
- **Adult**: Unified model for Parent, Mentor, Alumni, Volunteer.
- **Enrollment**: Links Student ↔ Program.
- **Fee**: Per-Program costs.
- **Payment**: Recorded against a Fee for a Student.
- **SlidingScale**: Percent discount per Student/Program.
- **Application**: Multi-step resumable application records (in `applications/`).
- **AttendanceEvent / Session**: RFID-based check-in/out tracking.

## Coding Standards

- **PEP 8**: Follow standard Python style.
- **Formatting**: Use `black` and `isort --profile black`.
- **Linting**: Use `flake8` and `bandit` for security.
- **Admin**: Use meaningful `verbose_name` and `help_text`. Keep `list_display` performant with `select_related`/`prefetch_related`.
- **Migrations**: Maintain validators and preserve unique constraints.

## Security and Permissions

- **Global Auth**: `LoginRequiredMiddleware` enforces login except for `admin/`, `accounts/`, `/apply/`, and static/media.
- **Dynamic Roles**: `RolePermission` checks for Mentor/Parent/Student UI sections.
- **API Keys**: Authenticate via `ApiClientKey` in `api/auth.py`.
- Sanitize all user input and use existing auth middleware.

## Testing Strategy and Contribution

- **Location**: Tests live in `programs/tests/`, `applications/tests/`, and `attendance/tests.py`.
- **TDD Requirement**: When fixing bugs, add a reproducer test file (e.g., `test_issue_reproduction.py`) before applying the fix.
- **Scope**: Include model validation, forms, services, and view logic.
- **Bandit**: If adding new tests that have hard-coded passwords, ensure Bandit will pass despite this.
- Add unit tests for new business logic.
- Keep changes minimal and avoid large refactors in feature tasks.
- Do not rename files without a valid technical reason.
- Make small, targeted changes instead of building for hypothetical future needs.

# UI and architecture guidelines
- Use Bootstrap to ensure a consistent appearance.
- Avoid inline styles if possible.
- Ensure pages remain accessible and responsive.
- Update `CHANGELOG.md` for user-facing changes.

## Junie-Specific Tips

- Prefer role-specific template folders (`adults/`, `parents/`, `mentors/`, `alumni/`).
- The `applications` app supersedes legacy models in `programs`.
- Use `run_ci.ps1` (or equivalent) before finishing a task.
- Search the codebase to infer structure; `programs/permission_views.py`, `signals.py`, and `utils.py` have important reusable code blocks. Reuse those whenever possible, or add similar code blocks into these files.
