# Momentum (aka Girls of Steel Portal) — Agent Instructions

## Scope

This is a Django administrative portal for managing programs, students,
organizations, volunteers, attendance, applications, outreach, travel,
orders, finance, and calendar data.

Follow existing project conventions. Before adding a new pattern, inspect the
nearest comparable model, view, form, template, and test.

The project is being converted from a single team portal ("Girls of Steel 
Portal" or "GoS Admin Portal") to a multi-organizational portal ("Momentum"). 

## Working rules

- Do not modify files unless the user asks for an implementation or edit.
- Keep changes narrowly scoped to the request. Do not perform unrelated
  refactors, renames, formatting churn, or dependency upgrades.
- Use non-interactive commands only.
- Do not add secrets, credentials, API keys, real personal data, or production
  data to code, fixtures, tests, logs, or documentation.
- Prefer existing project helpers, mixins, managers, and utilities over new
  implementations.
- Do not silently weaken permissions, tenant isolation, audit behavior,
  validation, or encryption to make a test pass.

## Decision-making

1. Inspect relevant code and tests before editing.
2. Reuse the closest existing implementation where possible.
3. Make the smallest safe change.
4. Add or update proportionate automated tests.
5. Run relevant checks.
6. Report changed files, validation performed, and any checks not run.

Ask before proceeding only when the change is ambiguous in a way that affects
security, permissions, data migration, public behavior, or architecture.
Otherwise, use the established local pattern and state the assumption.

## Non-negotiable architecture rules

### Organization and tenant isolation

- Scope organization-owned querysets using the established organization-aware
  manager/queryset pattern.
- New organization-owned models must use the project’s standard organization
  foreign key, manager, and query scoping conventions.
- Forms, autocomplete endpoints, filters, exports, and object lookups must not
  expose records from another organization.
- Add tests proving cross-organization access is denied where applicable.
- Never replace scoped queries with unrestricted `Model.objects.all()` merely
  to simplify implementation.

### Permissions

- Use the project permission helpers and view mixins.
- For object-level checks, pass the relevant object; do not rely only on a
  general permission when record-specific access matters.
- Do not introduce direct/raw permission checks when an established project
  helper exists.
- New authenticated views require allowed and denied access coverage for the
  relevant roles and organization boundaries.

### Sensitive data, files, and audit behavior

- Reuse the existing encrypted-file, protected-download, and audit-log paths.
- Do not expose sensitive uploads through public or guessable media URLs.
- Treat student, health, financial, contact, and identity data as sensitive.
- Preserve existing audit behavior for sensitive or operationally important
  changes.
- Do not log secrets, decrypted values, access tokens, or unnecessary personal
  information.

### Request-path performance

- Static, media, health, and similarly lightweight routes must remain
  database-free.
- Avoid introducing per-row queries in list views, forms, exports, templates,
  middleware, or permissions checks.
- Use project-established eager-loading patterns when rendering related data.

## Testing workflow

### Behavior changes and bug fixes

Use test-driven development for logic and behavior changes:

1. Find the closest existing test.
2. Add or update a test that demonstrates the required behavior or regression.
3. Run it and confirm it fails for the expected reason when practical.
4. Implement the smallest change that passes.
5. Refactor only after tests are green.

For purely presentational changes, add automated coverage when practical and
perform focused template/UI validation.

### Required coverage

Add or update tests for:

- Bug fixes.
- New business logic.
- New permission-sensitive views or endpoints.
- Organization-scoped data access.
- Validation rules.
- Data imports, exports, or transformations.
- Security-sensitive file, email, or audit behavior.
- Migrations that transform existing data.

Do not remove, skip, weaken, or rewrite a test merely because the implementation
does not yet pass it.

## Validation

Run the smallest relevant checks while developing, then run appropriate final
validation before reporting completion.

| Change type | Minimum validation |
|---|---|
| Python logic, models, forms, views | Relevant Django test module(s) |
| Permission or tenant-scoping change | Allowed/denied and cross-organization tests |
| JavaScript or frontend behavior | Relevant JS test(s) and the project JS test command |
| Template-only change | Relevant view/template test when available; inspect rendered path |
| Model/schema change | Relevant tests plus migration checks |
| Data migration | Forward-path test and safe handling of existing/null/edge-case data |
| File, email, or sensitive-data change | Relevant security/access tests |

Use the repository’s documented canonical commands for tests, linting,
formatting, type checks, and JavaScript validation. Do not disable the
upgrade-aware test runner or bypass project checks without stating why.

## Database and migrations

- Do not edit migration files that have already been applied outside the
  current local change unless explicitly instructed.
- Generate migrations through the project’s normal workflow.
- Review generated migrations before accepting them.
- Prefer safe, incremental migrations for large tables or production data.
- Make data migrations idempotent where practical.
- Handle nulls, duplicates, missing related records, and existing production
  data explicitly.
- Do not combine an unrelated schema cleanup with a feature migration.

## Documentation and changelog

- Update focused documentation when a durable workflow, invariant, security
  model, or operational procedure changes.
- Update `CHANGELOG.md` only for user-visible behavior changes.
- Keep this file concise. Do not add detailed subsystem documentation here.
- Add detailed domain guidance to the relevant document under `docs/`.

## Area-specific references

Read the relevant document before making substantial changes in that area:

- Organization/tenant behavior: `docs/architecture/organizations.md`
- Permissions and roles: `docs/architecture/permissions.md`
- Attendance and kiosks: `docs/architecture/attendance.md`
- Email editor and delivery: `docs/architecture/email.md`
- Student merge, relationships, and exports: `docs/architecture/student-data.md`
- Program fees, payments, and sliding scale: `docs/architecture/financials.md`
- Purchase requests and vendor orders: `docs/architecture/orders.md`
- Outreach events and travel: `docs/architecture/outreach-travel.md`
- Calendars and iCal feeds: `docs/architecture/calendar.md`
- Applications wizard and review: `docs/architecture/applications.md`
- Mentor agreements and background clearances: `docs/architecture/agreements-clearances.md`
- Security, encryption, and audit: `docs/security.md`
- Deployment, CLI commands, and operations: `docs/operations.md`
- Environment variables and configuration: `docs/environment-variables.md`

If an area lacks focused documentation, follow the closest existing code and
tests. Add focused documentation only when the new behavior introduces a
lasting project-wide rule.

## Completion report

When implementation is complete, report:

- What changed.
- Important design choices or assumptions.
- Tests/checks run and their result.
- Checks not run, with a reason.
- Any follow-up risk, migration concern, or manual verification needed.
