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

## Temporary Maintenance Mode

Set `MAINTENANCE_MODE=true` on each web service instance and restart or redeploy
the processes to return a non-cacheable `503 Service Unavailable` page for
application requests. All visitors, including signed-in users and platform
superusers, see the same page. Set the variable to `false` or remove it, then
restart or redeploy the processes to restore normal access.

Static assets served by WhiteNoise and the real `/health` endpoint remain
available; health probes continue to report actual database and email status.
APIs and webhooks routed through Django also receive `503`; confirm that their
callers retry as appropriate or pause external delivery. Maintenance middleware
runs only for web requests, so it does not stop background workers, scheduled
jobs, or other external services. It also cannot help if the application fails
before it can start. Confirm the deployment platform's health-check and restart
behavior before relying on this switch during an incident.

## Protected Media Files

For a directory-by-directory map and a safe review process for older uploads,
see [`media-directory.md`](./media-directory.md).

Student photos and signed application submissions are stored beneath the
existing `MEDIA_ROOT`, in organization-scoped random paths under `private/`.
The application denies direct `/media/` requests for those paths and for the
legacy `photos/students/` and `application_documents/` directories; authorized
student-photo, applicant, and staff endpoints stream the file from its stored
path. Existing uploads are not moved or deleted automatically.

The current application setting uses the relative path `media/`. Before a
deployment, confirm that this resolves inside the existing persistent Render
disk mount and is not an ephemeral service-filesystem location. Do not change
the mount or bulk-move files as part of this application change. Any future
copy or cleanup requires a separately reviewed backup, inventory, validation,
and approval plan. A proxy, CDN, or storage origin that bypasses Django must
also deny the sensitive legacy and `private/` prefixes; review any cached
copies of previously public URLs separately.

After deploying the protection, verify that anonymous direct requests to both
legacy and new sensitive media paths are denied, authorized same-tenant photo
and document endpoints still work, and foreign-tenant requests are denied.
Perform these checks through the actual proxy/CDN path as well as Django when
those services can serve files independently.

### Transitioning existing uploads

`programs.management.commands.transition_private_media` inventories legacy
student photos and signed application submissions. It is read-only by default:

```bash
python manage.py transition_private_media
```

Review the aggregate counts and any manual-review reasons before an approved
execution:

```bash
python manage.py transition_private_media --execute
```

The command only accepts the known `photos/students/` and
`application_documents/` source prefixes and the filesystem storage rooted at
`MEDIA_ROOT`. It checks tenant ownership, copies each eligible file to its
model-generated random private path, verifies the copied bytes with SHA-256,
and updates the reference in a per-record transaction only if the stored path
is unchanged. Already-private files are validated and skipped. A nonzero result
means at least one record needs manual review; successful records from that run
may already have been committed, so inspect the summary and rerun after review.

Before any real execution, make and verify a coordinated database and complete
`MEDIA_ROOT` backup, confirm `MEDIA_ROOT` is on the persistent disk, and ensure
the Django server and any proxy, CDN, or storage origin deny direct access to
the sensitive legacy paths. The command never deletes or overwrites source
files. A rollback may restore a database reference to its retained source only
while that source remains present; new private copies left behind by a failed
or rolled-back attempt are orphans and must be inventoried separately, not
automatically deleted. Keep legacy originals until a separate reviewed cleanup
and approval; a successful transition does not purge previously cached copies.

### Retaining replaced private student photos

Replacing a student's photo does not immediately delete the previous file. Its
historical path remains in the tracked student record, and the old private file
can be retained for a 90-day recovery window before an operator removes it. Use
the photo-only `cleanup_student_photos` command:

```bash
python manage.py cleanup_student_photos
python manage.py cleanup_student_photos --inventory
python manage.py cleanup_student_photos --execute --confirm-no-writers --confirm-backup
```

The default is a read-only preview. `--inventory` records the first observed
time, file size, and SHA-256 identity of each unreferenced file and does not
delete files. The 90 days begin at that inventory time; an orphan first
discovered later receives a new 90-day window. If file contents change or
student/adult history shows the path was reused after inventory, run inventory
again to restart its retention clock. Current references in any file field,
including references in another organization, prevent deletion. Historical
paths alone are not active references and remain in history after the physical
file is removed.

Before `--execute`, make and verify database and complete `MEDIA_ROOT` backups,
pause web and background writers, review a fresh preview, and supply both
confirmation flags. The command rechecks references, history, and file identity
before removing eligible files; if a run reports an error, some earlier eligible
files may already have been deleted. Restore, if needed, from the verified
backup; there is no in-app rollback action. Backup snapshots may retain photo
copies according to their own retention schedule.

This command only scans `private/student_photos/`. It does not remove legacy
`photos/students/` files, signed application documents, or other media. Review
those separately; do not broaden this command's scope as part of a routine run.

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