```md
# Momentum (formerly Girls of Steel Portal) — Agent Instructions

## Scope

Momentum is a Django administrative portal for managing programs, students,
organizations, volunteers, attendance, applications, outreach, travel, orders,
finance, and calendar data.

The project is transitioning from a single-organization portal to a
multi-organizational portal. Preserve organization isolation and established
access rules in all changes.

Before adding a new pattern, inspect the nearest comparable model, view, form,
template, and test. Reuse existing project helpers, mixins, managers, and
utilities where possible.

## Approval required before implementation

Stop and request explicit approval before:

- Adding, removing, or upgrading a dependency.
- Modifying CI, deployment, security scanning, test-runner, coverage, lint,
  formatter, type-checker, or pre-commit configuration.
- Adding a security suppression, test skip, exclusion, or ignore rule.
- Changing authentication, permissions, roles, organization isolation, access
  policy, encryption, key handling, sensitive-data retention, or audit logs.
- Creating or modifying data migrations that may run against production-like
  data.
- Running migrations, management commands, imports, exports, email delivery,
  or other write operations outside local or test environments.
- Deleting data, files, migrations, tests, public endpoints, or user-facing
  functionality.
- Making a broad refactor that affects multiple subsystems.
- Committing, amending, rebasing, merging, pushing, tagging, creating a pull
  request, or otherwise altering Git history.

Agents may prepare and validate migrations locally, but must not run
production, staging, shared-environment, or real-data operations.

## Change integrity

The goal is to fix the underlying defect—not merely make a check stop failing.

Without explicit approval, never:

- Disable, skip, xfail, delete, weaken, or narrow a test to make a suite pass.
- Add or broaden test exclusions, ignore patterns, or coverage exclusions.
- Add `# nosec`, `# noqa`, `# type: ignore`, `# pragma: no cover`, or an
  equivalent suppression.
- Change Bandit, lint, formatter, type-checker, test-runner, coverage, CI, or
  pre-commit configuration to suppress a finding.
- Lower security settings, remove validation, relax permissions, or bypass
  organization scoping.
- Modify snapshots, fixtures, mocks, or expected values solely to match broken
  output.
- Use destructive Git commands, force pushes, or bulk deletion commands.

If an exception, suppression, skipped test, or configuration change may be
necessary:

1. Stop and identify the specific failure.
2. Explain why a root-cause fix is not appropriate or feasible.
3. Propose the smallest possible exception.
4. State the security, correctness, and maintenance consequences.
5. Wait for explicit approval before applying it.

A request to “make checks pass” does not authorize bypassing checks.

## Working rules

- Do not modify files unless the user requests an implementation or edit.
- Keep changes narrowly scoped to the request and required tests.
- Do not perform unrelated refactors, renames, formatting churn, cleanup, or
  dependency upgrades.
- Use non-interactive commands only.
- Do not add secrets, credentials, API keys, real personal data, or production
  data to code, fixtures, tests, logs, or documentation.
- Do not silently weaken permissions, organization isolation, audit behavior,
  validation, encryption, or test quality.
- Do not alter generated files unless they are an expected output of the
  approved workflow.
- If a necessary fix expands beyond the request, stop and explain the added
  scope before proceeding.
- Suggestions for unrelated improvements are welcome in the final report, but
  do not implement them without approval.

## Decision-making workflow

1. Inspect relevant code, documentation, and tests before editing.
2. Reuse the closest safe existing implementation where possible.
3. Add or update a test that demonstrates the required behavior or regression.
4. Implement the smallest safe change.
5. Refactor only after relevant tests pass.
6. Run proportionate validation.
7. Review the diff and report evidence.

Ask before proceeding only when an ambiguity affects security, permissions, data
migration, public behavior, or architecture. Otherwise, follow the established
local pattern and state any important assumption.

Do not copy an existing pattern that appears to weaken security, organization
isolation, validation, data integrity, or test quality. Flag the concern and
propose a safer compatible approach.

## External side effects

Without explicit approval, do not:

- Send email, SMS, notifications, webhooks, or invitations to real recipients.
- Call payment, accounting, identity, background-check, or other production
  third-party services.
- Upload, delete, overwrite, or move real files in remote or shared storage.
- Run bulk updates, imports, exports, backfills, cleanup scripts, or management
  commands against shared, staging, or production-like data.
- Change environment variables, secrets, deployment settings, cloud resources,
  DNS, or third-party-service configuration.

Use local/test doubles, sandbox environments, or dry-run behavior when
available. State any command with external side effects before running it.

## Non-negotiable architecture rules

### Organization and tenant isolation

- Scope organization-owned querysets using the established organization-aware
  manager and queryset pattern.
- New organization-owned models must use the standard organization foreign key,
  manager, and query-scoping conventions.
- Forms, autocomplete endpoints, filters, exports, and object lookups must not
  expose records from another organization.
- Add cross-organization denial tests where applicable.
- Never replace scoped queries with unrestricted `Model.objects.all()` merely
  to simplify implementation.

### Permissions

- Use established project permission helpers and view mixins.
- For object-level checks, pass the relevant object when record-specific access
  matters.
- Do not introduce direct/raw permission checks when an established project
  helper exists.
- New authenticated views require allowed and denied access coverage for
  relevant roles and organization boundaries.

### Sensitive data, files, and audit behavior

- Reuse existing encrypted-file, protected-download, and audit-log paths.
- Do not expose sensitive uploads through public or guessable media URLs.
- Treat student, health, financial, contact, and identity data as sensitive.
- Preserve existing audit behavior for sensitive or operationally important
  changes.
- Do not log secrets, decrypted values, access tokens, or unnecessary personal
  information.

### Request-path performance

- Static, media, health, and similarly lightweight routes must remain
  database-free.
- Avoid per-row queries in list views, forms, exports, templates, middleware,
  and permission checks.
- Use established eager-loading patterns when rendering related data.

## Testing and validation

### Canonical commands

Use the commands documented in `docs/operations.md`.

- Targeted Django tests:
  `python manage.py test --parallel [testnames]`
- Full Django suite:
  `python manage.py test --parallel`
- JavaScript tests:
  `npm run test:js`
- Linting:
  `flake8 . --select=E9,F63,F7,F82 --show-source --statistics`
- Formatting checks:
  `black --check .` and `isort --check-only --profile black .`
- Apply formatting when needed:
  `black .` and `isort --profile black .`
- Missing-migration check:
  `python manage.py makemigrations --check`
- Migration-state check:
  `python manage.py migrate --check --plan`
- Security scan:
  `bandit -r . -x ./venv,./.venv`

Please don't test without the parallel flag in order to help speed up the process. 
There are over 3000 tests, and can take nearly 10 minutes to run without the flag.

### Required workflow

For logic and behavior changes:

1. Find the closest existing test.
2. Add or update a test for the requested behavior or regression.
3. Run it and confirm it fails for the expected reason when practical.
4. Implement the smallest change that passes.
5. Refactor only after relevant tests are green.
6. Run appropriate final validation.

For purely presentational changes, add automated coverage when practical and
perform focused template or UI validation.

### Required coverage

Add or update tests for:

- Bug fixes and new business logic.
- Permission-sensitive views or endpoints.
- Organization-scoped data access.
- Validation rules.
- Imports, exports, or data transformations.
- Security-sensitive file, email, or audit behavior.
- Migrations that transform existing data.

Treat failing tests, security findings, lint errors, type errors, and coverage
regressions as evidence of a defect, an intentional behavior change requiring
review, or an incorrect test—not as obstacles to suppress.

When a check fails:

1. Read the full failure output and identify the owning code path.
2. Determine whether production code, test expectations, test setup, or tool
   configuration is wrong.
3. Prefer a root-cause production-code fix.
4. Change a test only when its asserted behavior is demonstrably obsolete,
   incorrect, or inconsistent with an explicitly requested behavior change.
5. Explain every changed test expectation and the product behavior it reflects.
6. Re-run the smallest relevant check after each meaningful change.

### Minimum validation

| Change type | Minimum validation |
|---|---|
| Python logic, models, forms, views | Relevant Django test module(s) |
| Permission or organization-scoping change | Allowed, denied, and cross-organization tests |
| JavaScript or frontend behavior | Relevant JS tests and project JS test command |
| Template-only change | Relevant view/template test when available; inspect rendered path |
| Model/schema change | Relevant tests plus migration checks |
| Data migration | Forward-path test and handling of existing, null, and edge-case data |
| File, email, or sensitive-data change | Relevant security and access tests |

## Database and migrations

- Do not edit migrations already applied outside the current local change unless
  explicitly instructed.
- Generate migrations through the normal project workflow and review them before
  accepting them.
- Do not combine unrelated schema cleanup with a feature migration.
- Prefer safe, incremental migrations for large tables or production data.
- Handle nulls, duplicates, missing related records, and existing data
  explicitly.
- Make data migrations idempotent where practical.
- Read `docs/operations.md` before substantial schema or data-migration work.

## Documentation

- Update focused documentation when a durable workflow, invariant, security
  model, or operational procedure changes.
- Update `CHANGELOG.md` only for user-visible behavior changes.
- Keep this file focused on project-wide operating rules.
- Put subsystem-specific implementation detail in focused documentation under
  `docs/`.

## Area-specific references

For substantial work in an area, read its focused documentation if present. If
no focused document exists, inspect the nearest implementation and tests.

- Organization and tenant behavior: `docs/architecture/organizations.md`
- Permissions and roles: `docs/architecture/permissions.md`
- Attendance and kiosks: `docs/architecture/attendance.md`
- Email editor and delivery: `docs/architecture/email.md`
- Student merge, relationships, and exports: `docs/architecture/student-data.md`
- Program fees, payments, and sliding scale: `docs/architecture/financials.md`
- Purchase requests and vendor orders: `docs/architecture/orders.md`
- Outreach events and travel: `docs/architecture/outreach-travel.md`
- Calendars and iCal feeds: `docs/architecture/calendar.md`
- Applications wizard and review: `docs/architecture/applications.md`
- Mentor agreements and background clearances:
  `docs/architecture/agreements-clearances.md`
- Security, encryption, and audit: `docs/security.md`
- Deployment, CLI commands, migrations, and operations: `docs/operations.md`
- Environment variables and configuration: `docs/environment-variables.md`

Add focused documentation only when new behavior introduces a lasting,
project-wide rule or subsystem invariant.

## Review-ready completion report

Before reporting completion:

- Review the diff for unrelated changes, debug code, generated artifacts,
  accidental secrets, and unnecessary configuration changes.
- Confirm changed tests still assert meaningful behavior.
- Confirm no test, security control, lint rule, type check, coverage rule, or
  quality tool was bypassed or weakened.
- Review migrations, permissions, organization isolation, and sensitive-data
  handling when relevant.

Report:

- Files changed and the reason for each.
- The root cause addressed.
- Important assumptions or design choices.
- Tests and checks run, with results.
- Checks not run, with a reason.
- Any changed test expectation and the requested behavior change it represents.
- Any manual verification, deployment step, migration concern, or follow-up
  risk.
```