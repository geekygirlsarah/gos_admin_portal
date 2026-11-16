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

Persisted normalization reloads and locks the application row before merging,
so it does not overwrite newer JSON with a stale instance. Conversion extracts
historical answers from that locked data before normalizing it. Other JSON
writers do not yet all use this locking protocol, so this is not a complete
concurrency guarantee; SQLite does not provide row-level `select_for_update`
locking.

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

### Historical student application answers

`StudentApplicationAnswer` stores the question text and response for each
application and linked student. Lead Mentors and platform superusers can see
these answers on the student profile under the same tenant restrictions as
application review. Ordinary student-profile access does not grant application
review access. Program information and the application ID identify the source;
different applications never overwrite one another.
The card is inside the collapsed “View Contact & Personal Details” section and
shows an empty state when no linked answers are visible to the reviewer.
It also shows nonblank responses already stored in the four fixed student
profile fields, separately because their source application is not recorded.

The backfill copies the four existing experience questions from approved,
approved-and-signed, and converted student/parent applications only when
`converted_student` is present and the student, application, and any program
belong to the same organization. It does not infer a student from an email or
an unconfirmed picker selection. Both old and current experience-step keys are
examined separately; identical answers are de-duplicated, while conflicting
answers are retained with their source step. Empty answers are not copied.
Original application data and existing fixed Student fields remain unchanged.
New conversions also preserve these answers before step-key normalization.
For historical answers, question text is reconstructed from the current fixed
wizard labels; earlier wording was not stored in the application data.

Malformed answers, missing student links, and inconsistent ownership require
manual review; see `report_application_answer_backfill` in `docs/operations.md`.
Unlinked approved applications are expected until conversion. The backfill
cannot recover data already lost before it ran. Answer rows follow the source
application's and student's deletion lifecycle, and student merges transfer
them only after validating their ownership. Question configuration and the
replacement application wizard are not part of this phase.
Conversion stops with an error if an experience answer is malformed rather than
discarding it during step-key normalization.

Conversion also recognizes a `step6-custom` snapshot: a list of objects with
`question_id` (UUID), `question_text`, and `answer` (strings). It preserves each
nonblank answer with the exact question text shown, alongside legacy answers.
Invalid snapshots or repeated question IDs stop conversion without changing the
source data. Snapshot creation is not yet connected to applicant forms, and
application-answer editing is not yet implemented. Lead Mentors can prepare
free-text questions in program settings,
mark them required or optional, set their order, and deactivate them. These
definitions are organization-scoped and do not yet change the public wizard.
Each definition has a stable UUID intended for future answer snapshots. Existing
applications and their answers are unaffected by configuring questions.
Snapshot-building and validation helpers select active questions from the same
program and organization, retain the wording and required flag, and reject
malformed or unanswered required entries. A form can render and validate that
frozen set. A persistence helper can freeze a program-bound snapshot while
locking and reloading the application; it rejects stale programs, inconsistent
ownership, completed applications, and malformed or unbound snapshots instead
of overwriting them. It is not yet wired to the wizard: other application
writers must be coordinated before it can safely be used publicly. Staff review
renders existing custom snapshots as questions and answers and flags malformed
snapshots. Lead Mentors may edit answers on submitted or approved applications;
the question text and metadata are retained, required answers remain required,
and stale edits are rejected. Answer edits use the existing application edit
audit event. Custom answers are read-only during an in-progress application
until concurrent applicant writes are coordinated, and after conversion to
avoid diverging from the student profile.

Conversion matches existing students and adults only within the application's
organization and explicitly assigns that organization to new profiles,
relationships, and enrollments. A selected foreign student, foreign program, or
inconsistent relationship, enrollment, or signed-document link stops conversion
without reassigning or deleting the existing records. `School` remains a shared
catalog and is not tenant-filtered.
If an application document is missing or cannot be copied, conversion fails and
the database changes are rolled back; the reviewer must resolve the document
before retrying. Storage writes are not transactional, so files copied before a
later conversion error may remain in storage.

### Tenant isolation

Reviewer lists, filters, details, decisions, edits, conversions, uploads, and deletes require the resolved organization and only load applications owned by it. Review authorization requires a Lead Mentor in that organization or a platform superuser; a global Django reviewer permission does not override tenant roles. Applications linked to a program owned by another organization are treated as inconsistent and are not available in review. Program choices and application KPIs are tenant-scoped.

Stale cleanup only considers applications belonging to the current organization. Review pages read document submissions only when both the submission and its program are owned by that organization; individual deletes and cleanup leave applications with foreign-owned or mismatched document submissions untouched. Staff uploads explicitly assign the current organization and do not overwrite an inconsistently owned submission.

Public wizard routes require a resolved organization and load application IDs only within that organization. New applications are assigned to the request organization; program choices, duplicate-draft actions, and returning student/adult prefill are likewise organization-scoped. A missing tenant context fails closed. Lead notifications go only to active users with an active `LeadMentor` membership in the application organization; if there are no eligible email addresses, no lead notification is sent.

Step 10 displays and accepts documents only when the application, program, document, and submission ownership are consistent with the request organization. New submissions explicitly inherit the application's organization. Existing mismatched submissions cannot be exposed or overwritten, and duplicate/withdraw deletion is blocked when it would cascade through an inconsistent submission. Applicant downloads and uploads require a fresh, rate-limited email code verified in the same browser session; the grant expires with the OTP lifetime and is bound to the tenant, application, and document recipient. For a student-started application, the recipient is the handoff parent, or the primary parent's saved email for a legacy application without a handoff address. For a parent-started application, it is the application email. Document notices use that same recipient. A missing parent address requires staff assistance, not a fallback to the student. The document challenge is separate from application email verification, and a recipient change invalidates both pending codes and access grants. Staff use a separate download route protected by application-review permission and tenant scoping.

Approved and later application states are read-only through earlier public wizard steps. An application ID alone is not proof of applicant identity. Other in-progress wizard routes continue to follow the existing email-verification and parent-handoff workflow; this Step 10 challenge is an additional control for signed-document access.

Student photos and signed application uploads are stored under organization-scoped random paths beneath the existing `MEDIA_ROOT`. The path is not an authorization control. The `/media/` handler blocks both these private paths and legacy `photos/students/` and `application_documents/` paths in debug and production, without database access. Existing files remain in place and are streamed from their stored paths only through authorized endpoints; no automatic move or backfill is performed. Deployment proxies, CDNs, and storage origins must not serve these prefixes directly.
