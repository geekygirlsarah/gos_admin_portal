# Security, Encryption, and Audit Architecture

This document defines the security architecture and operational requirements for
encryption, protected-file delivery, request handling, rate limiting, and audit
logging.

Security controls must not be bypassed to make tests, linters, or security scans
pass. Any exception requires explicit approval, a narrow scope, and documented
justification.

## Encryption at Rest and Key Rotation

Sensitive files and fields use application-level symmetric encryption via
`EncryptedFileField`, `EncryptedCharField`, and `EncryptedTextField`.

Protected data includes:

- Student medical data
- Signed waivers in `StudentDocument.file`
- Digital sign-out signatures in `DigitalSignout.signature`
- Tax form uploads in `TaxForm.file`

Encryption protects stored field and file contents. It does not necessarily
protect metadata such as object identifiers, timestamps, filenames, audit-event
types, or access patterns.

### Encryption Settings

- `FILE_ENCRYPTION_KEY`: The active Fernet encryption key for encrypted files.
- `SECRET_KEY_FALLBACKS`: Populated only when `OLD_SECRET_KEY` is configured.
  It remains empty otherwise so Django security checks pass.

Encryption keys and prior keys are secrets. They must never be committed to
source control or included in application logs, audit events, test fixtures,
error messages, screenshots, tickets, or chat transcripts.

### Key Rotation Procedure

Before rotating an encryption key:

1. Confirm that a tested backup exists.
2. Confirm the current application version can decrypt existing encrypted data.
3. Deploy configuration that makes the replacement key available securely.
4. Run the re-encryption command in dry-run mode.
5. Review the dry-run output without exposing key material.
6. Run the re-encryption command.
7. Validate that representative protected records can be read successfully.
8. Retain the old key only for the approved rollback or transition period.
9. Remove the old key only after validation and approval.

```bash
python manage.py reencrypt_encrypted_data --old-file-key "<old_key>" --dry-run
python manage.py reencrypt_encrypted_data --old-file-key "<old_key>"
```

- Use secure operational procedures when supplying key material; command-line
  arguments may be retained in shell history or process listings.
- `--new-file-key` supports pre-encryption to an upcoming key.
- Rotation covers Student medical fields, `TaxForm.file`,
  `StudentDocument.file`, `DigitalSignout.signature`, and all
  `Encrypted*Field` subclasses.
- A targeted command exists for Student medical data:

```bash
python manage.py reencrypt_student_medical
```

Key rotation is a security-sensitive production operation. Do not alter,
replace, or retire keys without an approved recovery plan.

---

## Protected Media and Zero-Database Request Invariant

### Protected File Delivery

Sensitive uploads must not be publicly accessible through direct media URLs.

Protected files are delivered through authorized application endpoints:

- `student_photo` (`/programs/students/<pk>/photo/`)
- `student_document_download`
  (`/programs/documents/<pk>/download/`)
- `program_digital_signout_signature`
  (`/attendance/signout/<pk>/signature/`)
- `apply_step10_document_download` and
  `application_review_document_download` for signed application submissions.

Every protected-file endpoint must enforce:

- Authentication
- Object-level authorization
- Organization and program tenant scoping
- Authorization before returning file content or revealing sensitive metadata

Storage, reverse-proxy, CDN, and deployment configuration must not expose the
underlying paths of protected uploads publicly.

The application's `/media/` handler rejects the sensitive legacy photo and
application-document directories and all `private/` paths in both debug and
production configurations. This guard is database-free; private files are
streamed only by the authorized endpoints above. New student photos and signed
application uploads use organization-scoped random storage paths under the
existing `MEDIA_ROOT`; these paths are not authorization tokens. Existing files
are not moved automatically, and their direct legacy URLs remain blocked.

Replaced private student-photo files are not deleted during upload. The
`cleanup_student_photos` operator command tracks unreferenced private student
photos for at least 90 days from their first explicit inventory, verifies file
identity, and rechecks current file-field references and student/adult history
before deletion. History paths remain available for auditing but do not by
themselves keep a file active. The command does not touch legacy photo paths,
signed application documents, or backup copies; backups follow their separately
approved retention schedule. Destructive execution requires verified database
and media backups and paused writers.

The opt-in `transition_private_media` management command can copy existing
student photos and signed application submissions into those private paths. It
defaults to a read-only inventory, checks tenant ownership and verifies each
copy before updating its record, and retains every original. Random filenames
and organization directories are organizational safeguards only; authorized
delivery checks remain mandatory before and after a transition.

Applicant access to Step 10 requires a fresh email OTP. Successful verification
is bound to the tenant, application, current email, browser session, and OTP
expiry; requesting and verifying codes use the existing rate limits. Staff
downloads require the tenant-scoped application review permission. Approved,
submitted, converted, and declined applications cannot be changed through the
earlier public wizard steps.

Any proxy, CDN, or storage origin that serves media without passing through the
Django handler must independently deny the protected legacy and private
prefixes. Cached copies of files previously served through public URLs require
separate operational review.

### Zero-Database Request Rule for Static and Media Paths

Static, media, and health-check requests must not require database access.
This prevents database connection exhaustion during high-volume asset requests
or database outages.

- `LoginRequiredMiddleware` and `MentorAgreementMiddleware` must check
  `EXEMPT_PATH_PREFIXES` before evaluating `request.user` or `request.session`.
- Evaluating `request.user` or `request.session` may trigger lazy session or
  user loading and database access.
- Static, media, and health-check routes must execute with zero database
  queries. `MediaRequestDbFreeTests` enforces this using `assertNumQueries(0)`.
- `templates/500.html` is rendered with `render_to_string` without request
  context processors and does not extend `base.html`, avoiding request-context
  database access during error handling.

Changes to middleware ordering, exempt paths, authentication behavior, static
delivery, media delivery, or error templates require regression tests for this
invariant.

---

## Rate Limiting and Throttling

Rate limiting for the public application wizard is implemented in
`applications/rate_limiting.py`.

The `/apply/` flow is login-exempt and uses tiered protections:

| Control | Default limit | Scope |
|---|---:|---|
| IP POST limit | 10 requests per 60 seconds | Source IP |
| OTP send limit | 5 requests per hour | Email address |
| OTP verification limit | 10 attempts per hour | Application |

Relevant settings:

- `APPLY_IP_POST_LIMIT`
- `APPLY_IP_POST_WINDOW_SECONDS`
- `APPLY_OTP_SEND_LIMIT`
- `APPLY_OTP_VERIFY_LIMIT`
- `APPLY_RATE_LIMIT_ENABLED`

Exceeded limits return HTTP `429`, include a `Retry-After` header, and render
`templates/429.html`.

`APPLY_RATE_LIMIT_ENABLED` is disabled by default in the test suite only.
Production environments must enable rate limiting.

Rate-limit storage must be shared and atomic across application instances in
production. Limits must not rely on per-process memory when the application can
run on multiple workers or servers.

When requests pass through a reverse proxy or load balancer, the deployment must
define which proxy headers are trusted for client-IP determination. Do not trust
client-supplied forwarding headers unless the proxy configuration strips and
sets them safely.

---

## Audit Logging and Data Sanitization

The audit system provides an application-level append-only history of critical
operations. Application behavior must create audit records without modifying or
deleting prior records.

Database permissions and operational access controls must also prevent routine
application users and services from updating or deleting audit rows directly.

### Core Components

- **`AuditLog` model:** Records event type (`AuditEvent`), acting user,
  timestamp, target-object representation, and sanitized before/after JSON
  snapshots.
- **Service helpers:** `log_event()` and `log_deletion()`.
- **`AuditedDeleteMixin`:** Appears before `DeleteView` in the MRO. It captures
  `audit_snapshot(obj)` before deletion and logs `RECORD_DELETED`.
- **`SensitiveDataViewMixin`:** Records access to sensitive views with
  program-scoped query logging.
- **`AuditContextMiddleware`:** Provides the active request to audit signal
  handlers through thread-local context in `audit/context.py`.

### Audit Security Requirements

Audit records must:

- Be tenant-scoped where applicable.
- Be readable only by authorized users with a legitimate administrative or
  compliance need.
- Avoid storing plaintext secrets, credentials, OTPs, encryption keys, or raw
  protected medical data.
- Preserve enough context to identify the actor, action, target, and time of a
  security-relevant event.
- Be retained and protected according to the project’s approved retention and
  backup policy.

Audit logging is not a substitute for authorization checks. A valid audit event
must never make an otherwise unauthorized action permissible.

### Data Sanitization

`sanitize_audit_data` in `audit/service.py` recursively removes sensitive values
from before/after snapshots before they are persisted to `AuditLog`.

Redacted values include:

- Passwords
- OTP values
- API keys
- Secret tokens
- Encryption keys
- Raw encrypted medical fields

When adding a sensitive field, token, credential, or protected-data type, update
the sanitizer and add a regression test proving it cannot be persisted to audit
snapshots.

---

## Security Change Requirements

Any change affecting encryption, authentication, authorization, tenant scoping,
rate limiting, audit logging, protected-file delivery, middleware exemptions, or
error handling must include:

1. A regression test proving the intended security boundary.
2. A review of cross-organization and cross-program access paths.
3. Validation that secrets and plaintext sensitive data do not enter logs,
   audit records, error output, or test artifacts.
4. Documentation updates when architecture, settings, or operational procedures
   change.
5. Review-ready reporting of tests and security checks run.

Do not add `# nosec`, `# noqa`, skipped tests, weakened authorization checks,
broadened exempt paths, or reduced tenant isolation without explicit approval.