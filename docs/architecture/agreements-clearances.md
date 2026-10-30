# Mentor Agreements and Background Clearances

This document details the versioned mentor agreement acceptance system and background clearance checks.

## Mentor Agreements (`MentorAgreement` & `MentorAgreementAcceptance`)

Mentors must review and accept all active organizational agreements before gaining portal access.

### Versioning & Lifecycle
- Identified by a `slug` grouping versions of the same document, with sequential `version` numbers.
- Content can be Markdown (`content`), an uploaded PDF document (`document`), or both.
- **Auto-Versioning**: Editing content or uploading a new file automatically generates a new version and deactivates prior versions with the same slug.
- Metadata updates (title, `is_active`) update in place without bumping version.

### Middleware Enforcement (`MentorAgreementMiddleware`)
- Intercepts mentor requests and redirects mentors who have pending agreements to `/mentor-agreement/`.
- `Adult.has_accepted_current_agreement()` evaluates acceptance across all active agreements.
- Controlled globally by `MENTOR_AGREEMENT_ENABLED` (defaults to `not TESTING`).
- Seed agreements via `python manage.py seed_mentor_agreement`.

---

## Background Clearances (`BackgroundCheck` & `BackgroundCheckType`)

Tracks clearances for Students and Adults (parents, mentors, lead mentors, alumni).

### Configurable Clearance Types (`BackgroundCheckType`)
- **Validity Duration**: `validity_years` defines expiration (`expiration_date = obtained_date + validity_years`).
- **Role Requirements**: Flagged per role (students, mentors, lead mentors, parents, alumni).
- **Student Age Rules**: Configurable minimum age cutoff dates or age reached during active program enrollment.

### Dynamic Verification Helpers
- `requires_background_check(person)`: Determines if a person must provide clearances.
- `needs_background_check(person)`: Evaluates if required clearances are missing, un-cleared, or expired.
- Clearances are edited inline on student/adult profiles via `BackgroundChecksForm` / `BackgroundChecksInlineMixin` (gated on `can_user_write('background_checks', obj)`, Lead Mentors only by default).
