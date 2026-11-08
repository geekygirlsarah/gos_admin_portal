# Public Applications Wizard & Staff Review Workflow

This document details the public student application wizard (`applications/`), multi-step state management, step-key normalization, and staff conversion to active student profiles.

## Public Application Wizard (`/apply/`)

The public application wizard replaces the legacy `StudentApplication` model.
- **Login-Exempt & Throttled**: Accessible publicly with OTP email verification. Rate limits are enforced via `ApplyRateLimitMiddleware` and `applications/rate_limiting.py`.
- **Resumable State**: Application answers are persisted in the `data` JSONField across wizard steps.

---

## Step Key Normalization Invariant

Legacy applications stored responses under older keys (`step5`, `step6`, `step7`, `step8`, `step6_handoff`). Modern applications use dashed keys:
- `step5-student`
- `step6-experience`
- `step7-primaryparent`
- `step8-secondaryparent`
- `step7_handoff`

### Normalization Rule
Never write code that reads or writes the legacy key names directly. Always call `normalize_step_keys()` (module-level helper in `applications/models.py`) or `Application.normalize_step_data(save=True)` before inspecting or converting application data.

---

## Staff Review & Student Conversion

Staff manage applications via `/applications/review/`:
- **Workflow States**: `draft` → `submitted` → `under_review` → `accepted` / `waitlisted` / `rejected`.
- **Conversion Workflow (`convert_application_to_student`)**:
  - Automatically provisions or links the `Student` profile.
  - Provisions or links primary and secondary parent `Adult` profiles.
  - Creates the program `Enrollment` record.
  - Links application documents (`ApplicationDocumentSubmission`) into `StudentDocument` records.
  - Logs `AuditEvent.APPLICATION_CONVERTED`.

Conversion matches existing students and adults only within the application's
organization and explicitly assigns that organization to new profiles,
relationships, and enrollments. A selected foreign student, foreign program, or
inconsistent relationship, enrollment, or signed-document link stops conversion
without reassigning or deleting the existing records. `School` remains a shared
catalog and is not tenant-filtered.

### Tenant isolation

Reviewer lists, filters, details, decisions, edits, conversions, uploads, and deletes require the resolved organization and only load applications owned by it. Review authorization requires a Lead Mentor in that organization or a platform superuser; a global Django reviewer permission does not override tenant roles. Applications linked to a program owned by another organization are treated as inconsistent and are not available in review. Program choices and application KPIs are tenant-scoped.

Stale cleanup only considers applications belonging to the current organization. Review pages read document submissions only when both the submission and its program are owned by that organization; individual deletes and cleanup leave applications with foreign-owned or mismatched document submissions untouched. Staff uploads explicitly assign the current organization and do not overwrite an inconsistently owned submission.

Public wizard routes require a resolved organization and load application IDs only within that organization. New applications are assigned to the request organization; program choices, duplicate-draft actions, and returning student/adult prefill are likewise organization-scoped. A missing tenant context fails closed. Lead notifications go only to active users with an active `LeadMentor` membership in the application organization; if there are no eligible email addresses, no lead notification is sent.

Step 10 displays and accepts documents only when the application, program, document, and submission ownership are consistent with the request organization. New submissions explicitly inherit the application's organization. Existing mismatched submissions cannot be exposed or overwritten, and duplicate/withdraw deletion is blocked when it would cascade through an inconsistent submission. Applicant downloads and uploads require a fresh, rate-limited email code verified in the same browser session; the grant expires with the OTP lifetime and is bound to the tenant, application, and current email. Staff use a separate download route protected by application-review permission and tenant scoping.

Approved and later application states are read-only through earlier public wizard steps. An application ID alone is not proof of applicant identity. Other in-progress wizard routes continue to follow the existing email-verification and parent-handoff workflow; this Step 10 challenge is an additional control for signed-document access.

Student photos and signed application uploads are stored under organization-scoped random paths beneath the existing `MEDIA_ROOT`. The path is not an authorization control. The `/media/` handler blocks both these private paths and legacy `photos/students/` and `application_documents/` paths in debug and production, without database access. Existing files remain in place and are streamed from their stored paths only through authorized endpoints; no automatic move or backfill is performed. Deployment proxies, CDNs, and storage origins must not serve these prefixes directly.
