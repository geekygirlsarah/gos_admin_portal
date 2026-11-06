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

### Tenant isolation

Reviewer lists, filters, details, decisions, edits, conversions, uploads, and deletes require the resolved organization and only load applications owned by it. Applications linked to a program owned by another organization are treated as inconsistent and are not available in review. Program choices and application KPIs are tenant-scoped.

Stale cleanup only considers applications belonging to the current organization. Review pages read document submissions only when both the submission and its program are owned by that organization; individual deletes and cleanup leave applications with foreign-owned or mismatched document submissions untouched. Staff uploads explicitly assign the current organization and do not overwrite an inconsistently owned submission.

Public wizard routes require a resolved organization and load application IDs only within that organization. New applications are assigned to the request organization; program choices, duplicate-draft actions, and returning student/adult prefill are likewise organization-scoped. A missing tenant context fails closed. Lead notifications go only to active users with an active `LeadMentor` membership in the application organization; if there are no eligible email addresses, no lead notification is sent.

Step 10 displays and accepts documents only when the application, program, document, and submission ownership are consistent with the request organization. New submissions explicitly inherit the application's organization. Existing mismatched submissions cannot be exposed or overwritten, and duplicate/withdraw deletion is blocked when it would cascade through an inconsistent submission. Signed uploads are streamed through the tenant-scoped application document download route rather than linking to their raw media URL.

Tenant scoping is not applicant identity verification: the existing application-ID and handoff workflow remains unchanged. An application ID should not be treated as proof of the applicant's identity or as a replacement for the existing email-verification and handoff controls.
