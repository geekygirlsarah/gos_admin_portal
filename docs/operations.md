# Operations, CLI Commands, and Reliability

This document covers local development workflows, management commands, deployment tasks, CI checks, and database maintenance guidelines.

## Local Development Commands

| Task | Command |
|---|---|
| Install dependencies | `pip install -r requirements.txt` (runtime) then `pip install -r requirements-dev.txt` (linters/CI) |
| Run migrations | `python manage.py migrate` |
| Create superuser | `python manage.py createsuperuser` |
| Seed dev data | `python manage.py seed_db` |
| Seed mentor agreement | `python manage.py seed_mentor_agreement` |
| Start dev server | `python manage.py runserver` |
| Start production server (Render) | `./start.sh` (gunicorn + uvicorn with worker recycling via `--max-requests`) |
| Build frontend bundle | `npm ci && npm run build` (esbuild bundle for email editor) |
| Run frontend tests | `npm run test:js` (vitest + jsdom) |
| Run backend tests | `python manage.py test` (or `python manage.py test --parallel`) |
| Run CI suite | `.\run_ci.ps1` (Windows) or `./run_ci.sh` (Linux) |
| Coverage report | `coverage run manage.py test --parallel; coverage combine; coverage report` |

---

## Background Digests & Scheduled Tasks

| Task | Command | Notes |
|---|---|---|
| Weekly Digest | `python manage.py send_weekly_digest --days-ahead 7` | Flags: `--role student/parent/mentor`, `--date YYYY-MM-DD`, `--dry-run` |
| Outreach Reminders | `python manage.py send_outreach_reminders --days-ahead 1` | Flags: `--date YYYY-MM-DD`, `--dry-run` |
| Monthly Balances | `python manage.py send_monthly_balances --all-balances` | Flags: `--days-back 30`, `--student-id <pk>`, `--dry-run` |
| Calendar Reminders | `python manage.py send_calendar_reminders --days-ahead 3` | Flags: `--dry-run` |
| Audit Digest | `python manage.py audit_digest --days 7` | Generates summary of critical audit events |

---

## Data Management & Reconciliation Commands

- **Find Disconnected Accounts**:
  ```bash
  python manage.py find_disconnected_accounts [--fix]
  ```
  Audits unlinked Adult/Student users and re-links unambiguous matches.
- **Find Duplicate Parents**:
  ```bash
  python manage.py find_duplicate_parents [--fix]
  ```
  Identifies and safely merges duplicate parent records sharing name/email or name/phone. Never auto-merges ambiguous candidates or records with independent logins.
- **Find Disassociated Parents**:
  ```bash
  python manage.py find_disassociated_parents [--fix] [--all]
  ```
  Re-links orphaned parent records into empty contact slots on converted student applications.
- **Merge User Accounts**:
  ```bash
  python manage.py merge_user_accounts --source <pk> --target <pk> [--execute]
  ```
  Transfers emails, memberships, permissions, and links from source to target user.
- **Anonymize Emails**:
  ```bash
  python manage.py anonymize_emails --domain example.com --force [--dry-run]
  ```
  Scrubs real emails across Students, Adults, Users, and Applications in test databases.
- **Pre-populate Geocoding Cache**:
  ```bash
  python manage.py geocode_student_addresses [--retry-missing]
  ```

---

## Database Safety & Migration Invariants

- **Idempotency**: All seed commands (`seed_db`, `seed_mentor_agreement`) must be strictly idempotent and safe to re-run.
- **Migration Reversibility**: All custom data migrations must include a valid `reverse_code` or explicit justification.
- **Large Table Safety**: Never run locking table rewrites or un-indexed foreign key migrations on large production tables without rollback testing.
- **Upgrade-Aware Test Runner**: `UpgradeAwareTestRunner` (`GoSAdminPortal/test_runner.py`) escalates deprecation warnings to test failures to prepare for major Django upgrades.

---

## Pre-Merge CI Checks

A pull request must satisfy all automated checks:
1. All unit and integration tests pass (`python manage.py test --parallel`).
2. Type checking passes (`mypy --strict`).
3. Security scanning passes (`bandit -r .`).
4. Migration dry-run detects no uncreated migrations (`python manage.py makemigrations --check --dry-run`).
5. Frontend JavaScript tests pass (`npm run test:js`).
6. Zero debug/print statements in production code.

---

## Environment Variables

For the complete reference of all required and optional environment variables, see [`docs/environment-variables.md`](./environment-variables.md).
