# Security, Encryption, and Audit Architecture

This document details encryption at rest, key rotation procedures, zero-database static/media request rules, rate limiting, and audit logging.

## Encryption at Rest & Key Rotation

Sensitive files and fields (Student medical data, signed waivers in `StudentDocument.file`, digital sign-out signatures in `DigitalSignout.signature`, and `TaxForm.file` uploads) use symmetric encryption via `EncryptedFileField` and `EncryptedCharField`/`EncryptedTextField`.

### Encryption Settings
- `FILE_ENCRYPTION_KEY`: Current active Fernet encryption key.
- `SECRET_KEY_FALLBACKS`: Populated only when `OLD_SECRET_KEY` is provided (kept empty otherwise to satisfy Django security checks).

### Key Rotation Management Command
When rotating encryption keys, re-encrypt existing records using:
```bash
python manage.py reencrypt_encrypted_data --old-file-key "<old_key>" --dry-run
python manage.py reencrypt_encrypted_data --old-file-key "<old_key>"
```
- Supports optional `--new-file-key` to pre-encrypt to an upcoming key.
- Specifically covers Student medical fields, `TaxForm.file`, `StudentDocument.file`, `DigitalSignout.signature`, and all `Encrypted*Field` subclasses.
- A targeted variant exists: `python manage.py reencrypt_student_medical`.

---

## Protected Media & Zero-DB Request Invariant

### Authenticated Endpoints
Sensitive uploads are never served from public media URLs:
- `student_document_download` (`/programs/documents/<pk>/download/`)
- `program_digital_signout_signature` (`/attendance/signout/<pk>/signature/`)

### Zero-DB Request Rule for Static & Media
To prevent database connection exhaustion under heavy static or media asset requests:
- **Middleware Gating**: `LoginRequiredMiddleware` and `MentorAgreementMiddleware` must check `EXEMPT_PATH_PREFIXES` **before** any evaluation of `request.user` or `request.session`.
- Reading `request.user` triggers lazy session loading from the database.
- Static, media, and health check routes must execute with zero database queries (`assertNumQueries(0)` enforced by `MediaRequestDbFreeTests`).
- 500 error pages (`templates/500.html`) are rendered via `render_to_string` without request context processors and do not extend `base.html` so error pages display reliably during database outages.

---

## Rate Limiting & Throttling (`applications/rate_limiting.py`)

The public application wizard (`/apply/`) is login-exempt and protected by tiered rate limits:
- **IP Limiting**: `ApplyRateLimitMiddleware` limits requests to `APPLY_IP_POST_LIMIT` (default 10) POSTs per `APPLY_IP_POST_WINDOW_SECONDS` (default 60s).
- **OTP Send Limiting**: `APPLY_OTP_SEND_LIMIT` (default 5 code requests per email per hour).
- **OTP Verification Limiting**: `APPLY_OTP_VERIFY_LIMIT` (default 10 verification attempts per application per hour).
- Exceeded limits return HTTP 429 with a `Retry-After` header and render `templates/429.html`.
- Controlled in tests via `APPLY_RATE_LIMIT_ENABLED` (disabled by default in test suite).

---

## Audit Logging & Data Sanitization (`audit/`)

The audit log provides an immutable, append-only history of critical system operations.

### Core Audit Components
- **`AuditLog` Model**: Immutable log capturing event type (`AuditEvent`), acting user, timestamp, target object representation, and before/after JSON snapshots.
- **Service Helpers**: `log_event()`, `log_deletion()`.
- **View Mixins**:
  - `AuditedDeleteMixin`: Placed before `DeleteView` in MRO; takes an `audit_snapshot(obj)` before database row removal and logs `RECORD_DELETED`.
  - `SensitiveDataViewMixin`: Tracks access to sensitive views with program-scoped query logging.
- **Context Middleware**: `AuditContextMiddleware` provides the active request to signal handlers via thread-local storage (`audit/context.py`).

### Data Sanitization (`sanitize_audit_data`)
`audit/service.py` recursively scrubs sensitive keys from before/after snapshots before persisting to `AuditLog`. Redacted keys include passwords, OTP tokens, API keys, secret tokens, and raw encrypted medical fields.
