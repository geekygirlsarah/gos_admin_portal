# Operations, CLI Commands, and Reliability

This document defines local development workflows, management commands,
deployment safety requirements, CI validation, and database-maintenance rules.

## Operational Safety Rules

Commands that send messages, alter records, merge accounts, backfill data,
anonymize data, rotate encryption, or otherwise modify production state are
operationally sensitive.

Before running a state-changing command in production:

1. Confirm the target environment and organization scope.
2. Run a supported `--dry-run` mode first.
3. Review affected-record counts and sample output.
4. Confirm backup, rollback, and recovery procedures where applicable.
5. Obtain required approval for destructive, irreversible, or bulk operations.
6. Record the command, operator, date, outcome, and exceptions.

Never run development seed commands or test-data anonymization against production
data. Do not use `--fix`, `--execute`, or `--force` until the proposed changes
have been reviewed.

## Local Development Commands

| Task                     | Command                                                                      | Scope / safety                                                |
|--------------------------|------------------------------------------------------------------------------|---------------------------------------------------------------|
| Install dependencies     | `pip install -r requirements.txt` then `pip install -r requirements-dev.txt` | Local development and CI                                      |
| Run migrations           | `python manage.py migrate`                                                   | Local development; follow deployment procedures in production |
| Create superuser         | `python manage.py createsuperuser`                                           | Local development unless explicitly approved                  |
| Seed development data    | `python manage.py seed_db`                                                   | Development only; must remain idempotent                      |
| Seed mentor agreement    | `python manage.py seed_mentor_agreement`                                     | Development/test only unless explicitly approved              |
| Start development server | `python manage.py runserver`                                                 | Development only                                              |
| Start production server  | `./start.sh`                                                                 | Deployment-managed environments only                          |
| Build frontend bundle    | `npm ci && npm run build`                                                    | Local development and CI                                      |
| Run frontend tests       | `npm run test:js`                                                            | Local development and CI                                      |
| Run backend tests        | `python manage.py test --parallel`                                           | Local development and CI                                      |
| Run CI suite             | `.\run_ci.ps1` or `./run_ci.sh`                                              | Run before review                                             |
| Coverage report          | `coverage run manage.py test --parallel; coverage combine; coverage report`  | Local development and CI                                      |

## Background Digests and Scheduled Tasks

Scheduled commands may send external communications. Manual production runs must
use `--dry-run` first when the command supports it.

| Task               | Command                                                   | Notes                                                   |
|--------------------|-----------------------------------------------------------|---------------------------------------------------------|
| Weekly Digest      | `python manage.py send_weekly_digest --days-ahead 7`      | Supports `--role`, `--date`, and `--dry-run`            |
| Outreach Reminders | `python manage.py send_outreach_reminders --days-ahead 1` | Supports `--date` and `--dry-run`                       |
| Monthly Balances   | `python manage.py send_monthly_balances --all-balances`   | Supports `--days-back`, `--student-id`, and `--dry-run` |
| Calendar Reminders | `python manage.py send_calendar_reminders --days-ahead 3` | Supports `--dry-run`                                    |
| Audit Digest       | `python manage.py audit_digest --days 7`                  | Generates a summary of critical audit events            |

Scheduled commands must be safe against accidental duplicate execution where
practicable. Changes to recipient selection, message templates, scheduling, or
delivery behavior require regression tests.

## Data Management and Reconciliation Commands

These commands may inspect, alter, merge, or anonymize application data. Run
their read-only or `--dry-run` form first whenever available.

### Find Disconnected Accounts

```bash
python manage.py find_disconnected_accounts [--fix]
```

Audits unlinked Adult and Student users and re-links unambiguous matches.
Review all proposed changes before using `--fix`.

### Find Duplicate Parents

```bash
python manage.py find_duplicate_parents [--fix]
```

Identifies and safely merges duplicate parent records sharing name/email or
name/phone. It must never auto-merge ambiguous candidates or records with
independent logins.

### Find Disassociated Parents

```bash
python manage.py find_disassociated_parents [--fix] [--all]
```

Re-links orphaned parent records into empty contact slots on converted student
applications. Review proposed matches before using `--fix`.

### Merge User Accounts

```bash
python manage.py merge_user_accounts --source <pk> --target <pk>
python manage.py merge_user_accounts --source <pk> --target <pk> --execute
```

Transfers emails, memberships, permissions, and related records from the source
account to the target account.

Before using `--execute`, confirm both account identities, organization
memberships, permissions, and linked records. Treat account merges as
potentially irreversible.

### Anonymize Emails

```bash
python manage.py anonymize_emails --domain example.com --dry-run
python manage.py anonymize_emails --domain example.com --force
```

Scrubs emails across Students, Adults, Users, and Applications for test-data
handling. This command is intended for non-production databases only.

### Pre-populate Geocoding Cache

```bash
python manage.py geocode_student_addresses [--retry-missing]
```

Review provider configuration, request volume, and applicable usage limits
before bulk execution.

### Backfill Profile Memberships

```bash
python manage.py backfill_profile_memberships
```

Idempotently creates missing `UserOrganizationMembership` rows for legacy Adult
and Student profiles so organization-scoped permission checks resolve correctly.

This command must never modify or reactivate existing membership rows. Accounts
without membership rows retain legacy group/profile access until backfilled. See
`docs/architecture/permissions.md`.

## Database Safety and Migration Invariants

- **Idempotency:** `seed_db` and `seed_mentor_agreement` must be strictly
  idempotent.
- **Data migrations:** Custom data migrations require a valid `reverse_code` or
  an explicit, documented reason why reversal is unsafe or impossible.
- **Large tables:** Do not deploy locking table rewrites, unindexed foreign-key
  changes, or large synchronous backfills without a deployment plan.
- **Deployment plans:** High-risk migrations must document lock risk, expected
  runtime, rollout sequence, rollback strategy, validation queries, and any
  required maintenance window.
- **Backfills:** Prefer batched, resumable, observable backfills over one large
  transaction.
- **Upgrade awareness:** `UpgradeAwareTestRunner`
  (`GoSAdminPortal/test_runner.py`) escalates deprecation warnings to test
  failures to prepare for major Django upgrades.

## Pre-Merge Validation

A pull request must pass all applicable checks.

### Django Tests

```bash
python manage.py test --parallel
```

### Frontend Tests

```bash
npm run test:js
```

### Critical Lint Checks

```bash
flake8 . --select=E9,F63,F7,F82 --show-source --statistics
```

### Formatting Checks

```bash
black --check .
isort --check-only --profile black .
```

Use the following only when intentionally applying formatting changes:

```bash
black .
isort --profile black .
```

### Migration Checks

```bash
python manage.py makemigrations --check
python manage.py migrate --check --plan
```

### Security Scan

```bash
bandit -r . -x ./venv,./.venv
```

### Type Checking

```bash
mypy --strict
```

Do not bypass failures by skipping tests, adding suppression comments, weakening
checks, narrowing scan scope, or changing CI configuration without explicit
approval and documented rationale.

## Deployment Requirements

Before deployment:

1. Run the applicable pre-merge validation checks.
2. Confirm environment variables and external-service configuration.
3. Review pending migrations and their deployment plan.
4. Confirm backups and rollback steps for state-changing releases.
5. Validate health checks and critical workflows after deployment.
6. Record deployment outcome and any follow-up work.

For encryption-key rotation and other protected-data operations, follow
`docs/security.md`.

## Environment Variables

For the complete reference of required and optional environment variables, see
[`docs/environment-variables.md`](./environment-variables.md).