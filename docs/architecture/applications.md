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

Persisted normalization, the shared wizard step writer, applicant type/email,
program selection, parent handoff, and parent swapping reload and lock the
organization-bound application row before updating. These writers preserve
newer unrelated JSON and wizard progress rather than saving a stale instance.
Step 2 rechecks identity and status under the lock and rejects a request whose
applicant/email changed while the form was being submitted; changing the email
clears its verification and pending OTP. Program selection rechecks the
tenant's eligible programs and current status before updating the program,
progress, and program-bound custom-question snapshot together. When switching
programs, it archives the active snapshot by program ID and restores a saved
snapshot for the selected program; malformed, unbound, or ambiguous snapshots
are left unchanged and require staff assistance. Handoff also revalidates the
parent address and applicant status against the locked row before issuing a
token; the notification is sent after the transaction. Conversion extracts
historical answers from the same locked data before normalizing it.

Student steps 5–8 and mentor information/clearance writes also recheck the
locked row after form validation. They do not save when the application has
become terminal, browser or handoff authorization no longer applies, the
applicant type or program changed, or student-contact context used for parent
email validation has changed. Step 8 rechecks the primary/secondary contact
email-update opt-in against current data. Mentor clearance details are saved
only while current clearance interest remains affirmative, and clearance
interest edits reject a conflicting intervening change. Rejected forms are
rendered with their submitted values so applicants can review and resubmit.
Primary and secondary guardian forms reject addresses that match each other
after trimming surrounding whitespace and comparing case-insensitively. The
comparison is repeated against the locked application before saving, so an
intervening change cannot introduce a shared guardian address. Blank secondary
emails remain optional when email updates are not selected, and plus-tagged
addresses remain distinct. This is an application-form check, not a global
email uniqueness constraint; historical profile addresses are unchanged.

Student/parent and mentor confirmation also reload and lock the organization-
bound application after form validation. They recheck terminal status and the
current browser verification; student/parent submission additionally rechecks
parent handoff authorization, program selection, and the current parent email-
updates opt-in. The status transition is saved once, and applicant/lead notices
are sent only after the transaction, so stale or repeated confirmation posts
do not reset submission time or resend notifications.

Application JSON writers outside these coordinated paths may not all use the
same locking protocol, so this is not a complete concurrency guarantee; SQLite
does not provide row-level `select_for_update` locking. Step 6 freezes the
program's active question set and saves it with experience answers under the
application lock. A signed version rejects competing Step 6 answer edits, and
the locked confirmation path checks required answers again so direct submission
cannot bypass them.

---

## Staff Review & Student Conversion

Staff manage applications via `/applications/review/`:
- **Workflow States**: `draft` → `submitted` → `under_review` → `accepted` / `waitlisted` / `rejected`.
- **Required forms before acceptance**: Active required student/parent program documents must have tenant-owned uploaded submissions before the reviewer can accept an application. The locked decision rechecks current uploads; an applicant or staff upload does not itself accept a submitted application. New approvals with required documents use `APPROVED_SIGNED`; existing approved applications remain accessible so families and staff can finish their uploads without changing the prior decision.
- **Edit conflict protection**: Staff edit forms carry a signed version of the tenant-bound application state. The locked POST rejects missing, forged, or stale versions before saving and redisplays submitted values with a reload notice. GET first persists legacy step-key normalization and then signs the refreshed row so its timestamp matches the normalized data.
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
them only after validating their ownership. Conversion stops with an error if
an experience answer is malformed rather than discarding it during step-key
normalization.

Lead Mentors can configure organization-scoped, free-text questions for a
program, including active/inactive state, required/optional status, and display
order. When an applicant reaches Step 6, the application freezes the active
question set—including exact wording and required status—against its selected
program. Later configuration changes do not rewrite that application's frozen
questions. Switching programs archives the outgoing snapshot and restores a
saved snapshot for the target program; malformed, unbound, or ambiguous state
is retained for staff assistance rather than guessed or discarded.

Applicants answer the frozen questions alongside the existing experience
questions. Required answers are checked by the Step 6 form and again against the
freshly locked application during confirmation; direct submission cannot skip
an unfrozen set or submit an unanswered required question. The confirmation
page shows the saved wording and answers. Step 6 reads legacy fixed experience
answers through the existing key normalizer without rewriting legacy data just
because the page was viewed. New conversions preserve nonblank custom answers
with the exact wording shown, alongside the legacy experience answers.

Staff review renders custom snapshots and flags malformed data. Lead Mentors may
edit answers on submitted or approved applications; the wording and metadata are
retained, required answers remain required, and stale edits are rejected. Answer
edits use the existing application edit audit event. Staff edits remain
read-only for in-progress applications and after conversion to avoid diverging
from applicant answers or the student profile.

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

Step 10 displays and accepts documents for submitted student/parent applications before approval, as well as approved legacy applications, only when the application, program, document, and submission ownership are consistent with the request organization. New submissions explicitly inherit the application's organization. Existing mismatched submissions cannot be exposed or overwritten, and duplicate/withdraw deletion is blocked when it would cascade through an inconsistent submission. Applicant downloads and uploads require a fresh, rate-limited email code verified in the same browser session; the grant expires with the OTP lifetime and is bound to the tenant, application, and document recipient. For a student-started application, the recipient is the handoff parent, or the primary parent's saved email for a legacy application without a handoff address. For a parent-started application, it is the application email. After submission, a separate required-document notice and protected link are sent only to that same parent/guardian; the student is never used as a fallback recipient. A missing parent address requires staff assistance, not a fallback to the student. The document challenge is separate from application email verification, and a recipient change invalidates both pending codes and access grants. Staff use a separate download route protected by application-review permission and tenant scoping; staff uploads recheck the application and program under a row lock. Completing uploads while the application is still submitted does not auto-accept it. Previously approved applications with missing required documents retain their approval and can complete uploads; completion preserves the existing `APPROVED_SIGNED` transition.

Approved and later application states are read-only through earlier public wizard steps. An application ID alone is not proof of applicant identity. Other in-progress wizard routes continue to follow the existing email-verification and parent-handoff workflow; this Step 10 challenge is an additional control for signed-document access.

Student photos and signed application uploads are stored under organization-scoped random paths beneath the existing `MEDIA_ROOT`. The path is not an authorization control. The `/media/` handler blocks both these private paths and legacy `photos/students/` and `application_documents/` paths in debug and production, without database access. Existing files remain in place and are streamed from their stored paths only through authorized endpoints; no automatic move or backfill is performed. Deployment proxies, CDNs, and storage origins must not serve these prefixes directly.
