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
