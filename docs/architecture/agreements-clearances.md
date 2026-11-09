# Mentor Agreements and Background Clearances

This document details the versioned mentor agreement acceptance system and background clearance checks.

## Mentor Agreements (`MentorAgreement` & `MentorAgreementAcceptance`)

Mentors must review and accept all active agreements belonging to the current
organization before gaining portal access. Agreement management, versioning,
acceptances, and uploads are scoped to that organization.

### Versioning & Lifecycle
- Identified by a `slug` grouping versions of the same document, with sequential `version` numbers.
- Content can be Markdown (`content`), an uploaded PDF document (`document`), or both.
- **Auto-Versioning**: Editing content or uploading a new file automatically generates a new version and deactivates prior versions with the same slug.
- Metadata updates (title, `is_active`) update in place without bumping version.

### Middleware Enforcement (`MentorAgreementMiddleware`)
- Intercepts mentor requests and redirects mentors who have pending agreements to `/mentor-agreement/`.
- `Adult.has_accepted_current_agreement()` evaluates active agreements and
  acceptances for the adult's organization only.
- Controlled globally by `MENTOR_AGREEMENT_ENABLED` (defaults to `not TESTING`).
- Seed agreements via `python manage.py seed_mentor_agreement`.

The agreement page remains publicly readable on a tenant hostname, including
its agreement content and linked documents. Authenticated users without an
active membership are denied by the tenant membership guard. An adult profile
currently belongs to one organization: a mentor with a profile from another
organization cannot accept or upload an agreement for this tenant. An
acceptance whose organization disagrees with its agreement is not honored and
requires administrative review; it is not automatically reassigned. Deletion
is blocked if an associated acceptance or submission has a mismatched owner or
adult profile, so it cannot cascade into another organization's records.

Signed submissions remain in `agreement_submissions/`, but direct public media
requests are denied. Downloads use the authenticated mentor-agreement route and
require the request organization to match the submission, adult, and agreement.
Only the uploading adult, a Lead Mentor in that organization, or a platform
superuser can download; successful downloads are recorded as
`SENSITIVE_DATA_VIEW` audit events. Files are streamed as attachments with
private, non-cacheable response headers.

Agreement-related digest action items are tenant-scoped. Other digest data,
including outreach, attendance, travel, and calendar items, requires separate
tenant-isolation review.

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
