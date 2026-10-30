# Environment Variables and Configuration

This document catalogues all environment variables supported by Momentum (Girls of Steel Admin Portal), specifying their purpose, requirements, default values, and operational guidelines.

---

## Quick Reference Summary

| Variable | Required? | Default | Purpose |
|---|---|---|---|
| `SECRET_KEY` | **Required in Prod** | Dev insecure key | Cryptographic signing key (sessions, tokens, cookies) |
| `FILE_ENCRYPTION_KEY` | **Required in Prod** | None | Fernet symmetric key for at-rest encrypted fields |
| `ALLOWED_HOSTS` | **Required in Prod** | `localhost`, `127.0.0.1`, `testserver`, `.momentumteam.app`, `.girlsofsteelrobotics.org`, `.gosadmin.com` | Permitted HTTP host/domain headers |
| `DATABASE_URL` | Optional (Prod standard) | SQLite (`db.sqlite3`) | Database connection URL |
| `DEBUG` | Optional | `True` | Toggles development vs. production mode |
| `EMAIL_HOST_USER` | **Required in Prod** | `""` | SMTP authentication username (required for OTP login) |
| `EMAIL_HOST_PASSWORD` | **Required in Prod** | `""` | SMTP authentication password (required for OTP login) |
| `TOSEND_API_KEY` | Optional | `""` | API key for toSend REST email backend |
| `MOMENTUM_ROOT_DOMAIN` | Optional | `momentumteam.app` | Base domain for tenant subdomain routing |
| `MENTOR_AGREEMENT_ENABLED` | Optional | `True` | Enforces mentor agreement signing before access |
| `LOG_LEVEL` | Optional | `INFO` | Root logging level |

---

## 1. Security & Core Django Settings

### `SECRET_KEY`
- **Requirement**: **Required in production**. Optional in local development (uses an insecure development default).
- **Default**: `"django-insecure-1oqwv49tbxn_x5f=mvb^)_r7g#l!@#*3r1sijxyvpv^424%5qd"`
- **Purpose**: Used for Django cryptographic signing, session hashing, CSRF tokens, and password reset tokens.
- **Notes**: In production (`DEBUG=False`), starting the app with the default value or an empty string raises `ImproperlyConfigured`.
- **Generation**:
  ```bash
  python -c "import secrets; print(secrets.token_urlsafe(50))"
  ```

### `FILE_ENCRYPTION_KEY`
- **Requirement**: **Required in production**. Optional in local development (displays warning if missing).
- **Default**: `None`
- **Purpose**: Fernet symmetric encryption key used by `EncryptedFileField`, `EncryptedTextField`, and `EncryptedCharField` to encrypt sensitive data at rest (medical notes, allergies, dietary restrictions, signatures, background check files).
- **Notes**: In production (`DEBUG=False`), starting the app without this key raises `ImproperlyConfigured`. Must be a 32-byte URL-safe base64-encoded string.
- **Generation**:
  ```bash
  python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
  ```

### `OLD_SECRET_KEY`
- **Requirement**: Optional.
- **Default**: `None`
- **Purpose**: Secret key used prior to a key rotation. If supplied, Django appends it to `SECRET_KEY_FALLBACKS` so existing sessions and signed tokens remain valid during rotation.

### `DEBUG`
- **Requirement**: Optional.
- **Default**: `True`
- **Values**: `1`, `true`, `yes` (case-insensitive) enables debug; `0`, `false`, `no` enables production mode.
- **Purpose**: Controls Django debug mode, detailed error pages, static file handling, and email backend selection.

### `ALLOWED_HOSTS`
- **Requirement**: **Required in production**.
- **Default**: Includes `localhost`, `127.0.0.1`, `testserver`, `.momentumteam.app`, `.girlsofsteelrobotics.org`, `.gosadmin.com`.
- **Format**: Comma-separated domain list, e.g., `portal.example.org,admin.example.org`.
- **Purpose**: Protects against HTTP Host header attacks.

### Production SSL & Header Controls
These settings take effect when `DEBUG=False`:

| Variable | Default | Type | Purpose |
|---|---|---|---|
| `SECURE_SSL_REDIRECT` | `True` | Boolean | Redirects all HTTP requests to HTTPS. |
| `SESSION_COOKIE_SECURE` | `True` | Boolean | Flags session cookies as HTTPS-only. |
| `CSRF_COOKIE_SECURE` | `True` | Boolean | Flags CSRF cookies as HTTPS-only. |
| `SECURE_HSTS_SECONDS` | `31536000` (1 yr) | Integer | Sets `Strict-Transport-Security` header max-age. |
| `SECURE_HSTS_INCLUDE_SUBDOMAINS` | `True` | Boolean | Extends HSTS policy to all subdomains. |
| `SECURE_HSTS_PRELOAD` | `True` | Boolean | Allows browser HSTS preload list inclusion. |
| `SECURE_REFERRER_POLICY` | `"same-origin"` | String | Configures `Referrer-Policy` header. |

---

## 2. Multi-Tenancy & Domains

### `MOMENTUM_ROOT_DOMAIN`
- **Requirement**: Optional.
- **Default**: `"momentumteam.app"`
- **Purpose**: The base root domain used by `OrganizationMiddleware` to extract tenant subdomains (e.g. `gos.momentumteam.app` resolves tenant slug `"gos"`).

### `SITE_URL` / `BASE_URL` / `CANONICAL_DOMAIN`
- **Requirement**: Optional.
- **Default**: Resolved dynamically from request headers, Render environment, or `ALLOWED_HOSTS`.
- **Purpose**: Canonical public URL prefix (e.g. `https://portal.girlsofsteelrobotics.org`) used in notification emails, password reset links, and ICS calendar subscription URLs.

### `RENDER_EXTERNAL_URL` / `RENDER_EXTERNAL_HOSTNAME`
- **Requirement**: Optional (provided automatically on Render hosting).
- **Purpose**: Fallback for site URL resolution and staging environment detection.

---

## 3. Database

### `DATABASE_URL`
- **Requirement**: Optional in local development; standard in production.
- **Default**: `sqlite:///db.sqlite3`
- **Format**: `postgres://USER:PASSWORD@HOST:PORT/NAME` or standard connection string.
- **Purpose**: Configures the primary database connection using `dj_database_url`. Includes automatic connection health checks and connection pooling (`conn_max_age=600`).

---

## 4. Email & Notification Services

### SMTP Configuration

| Variable | Required? | Default | Purpose |
|---|---|---|---|
| `EMAIL_HOST_USER` | **Required in Prod** | `""` | SMTP authentication username (required for OTP login). |
| `EMAIL_HOST_PASSWORD` | **Required in Prod** | `""` | SMTP authentication password. |
| `EMAIL_HOST` | Optional | `"smtp.fastmail.com"` | Outgoing SMTP server hostname. |
| `EMAIL_PORT` | Optional | `465` | SMTP port (typically `465` for SSL, `587` for STARTTLS). |
| `EMAIL_USE_SSL` | Optional | `True` | Connect using implicit SSL/TLS. |
| `EMAIL_USE_TLS` | Optional | `False` | Connect using explicit STARTTLS. |
| `EMAIL_TIMEOUT` | Optional | `30` | Socket timeout in seconds for SMTP operations. |

### Email Routing & Safety Controls

| Variable | Default | Purpose |
|---|---|---|
| `EMAIL_BACKEND` | Auto-detected | Explicitly selects backend: `console`, `smtp`, `dummy`, `locmem`, `filebased`, `redirect`, `tosend`, or a full Python path. |
| `EMAILS_ENABLED` / `DISABLE_OUTGOING_EMAILS` / `EMAIL_DISABLED` | `True` (enabled) | Setting `EMAILS_ENABLED=False` or `DISABLE_OUTGOING_EMAILS=True` silently discards all outgoing emails via `dummy.EmailBackend`. |
| `EMAIL_REDIRECT_TO` | `None` | Intercepts and reroutes all outgoing emails to a safe address (e.g. `dev@example.com`). Original recipients are prepended to the subject line. |
| `EMAIL_FILE_PATH` | `BASE_DIR/tmp/app-emails` | Directory path for the `filebased` email backend. |

### Sender Identities & Addresses

| Variable | Default | Purpose |
|---|---|---|
| `DEFAULT_FROM_EMAIL` | `"notifications@girlsofsteelrobotics.org"` | Default `From:` header for automated system notifications. |
| `DEFAULT_FROM_NAME` | `"Girls of Steel Notifications"` | Display name attached to default outgoing messages. |
| `NOTIFICATION_FROM_EMAIL` | `None` (uses `DEFAULT_FROM_EMAIL`) | Override sender address for notifications. |
| `DEFAULT_REPLY_TO_EMAIL` | `"leads@girlsofsteelrobotics.org"` | Default `Reply-To:` header for emails sent to members. |
| `SERVER_EMAIL` | `DEFAULT_FROM_EMAIL` | Sender address for error reports sent to `ADMINS`. |
| `ADMIN_EMAILS` | `""` | Comma-separated list of admin emails to receive error alerts on HTTP 500 exceptions. |
| `EMAIL_SENDER_ACCOUNTS_JSON` | `""` | JSON array of secondary sender accounts for the compose dropdown (see example below). |

#### `EMAIL_SENDER_ACCOUNTS_JSON` Example
```json
[
  {
    "key": "leads",
    "display_name": "Team Leadership",
    "email": "leads@example.org",
    "username": "leads@example.org",
    "password": "smtp-app-password"
  },
  {
    "key": "finance",
    "display_name": "Finance Committee",
    "email": "finance@example.org",
    "username": "finance@example.org",
    "password": "smtp-app-password"
  }
]
```

### toSend REST API Backend

| Variable | Default | Purpose |
|---|---|---|
| `TOSEND_API_KEY` | `""` | API key for toSend REST API v2 delivery (`https://api.tosend.com`). |
| `TOSEND_API_URL` | `"https://api.tosend.com"` | Base URL for toSend REST endpoints. |
| `TOSEND_TIMEOUT` | `30` | Request timeout in seconds for toSend HTTP requests. |
| `TOSEND_WEBHOOK_SECRET` | `""` | Shared secret to verify incoming delivery/bounce webhook payloads. |

### Health Check Throttling

| Variable | Default | Purpose |
|---|---|---|
| `HEALTH_SMTP_CHECK_INTERVAL` | `300` (5 min) | Seconds between live SMTP connection tests on `/health` probes. |
| `HEALTH_SMTP_FAILURE_COOLDOWN` | `30` | Shorter interval used after an SMTP probe failure to detect recovery quickly. |

---

## 5. Cache & Rate Limiting

### `CACHE_BACKEND` & `CACHE_LOCATION`
- **Default Backend**: `django.core.cache.backends.locmem.LocMemCache`
- **Default Location**: `gos-admin-portal`
- **Production Recommendation**: Use Redis across multiple application workers:
  ```env
  CACHE_BACKEND=django.core.cache.backends.redis.RedisCache
  CACHE_LOCATION=redis://127.0.0.1:6379/1
  ```

### Application Wizard Rate Limiting (`/apply/*`)

| Variable | Default | Purpose |
|---|---|---|
| `APPLY_RATE_LIMIT_ENABLED` | `True` | Master toggle for `/apply/*` rate limiting (auto-disabled during test runs). |
| `APPLY_IP_POST_LIMIT` | `10` | Max POST submissions per client IP within the window. |
| `APPLY_IP_POST_WINDOW_SECONDS` | `60` (1 min) | Sliding window duration for IP rate limiting. |
| `APPLY_OTP_SEND_LIMIT` | `5` | Max OTP codes sent to a single email address per window. |
| `APPLY_OTP_VERIFY_LIMIT` | `10` | Max verification attempts per application per window. |
| `APPLY_OTP_WINDOW_SECONDS` | `3600` (1 hr) | Sliding window duration for OTP code generation/verification. |

---

## 6. Feature Toggles & Development Utilities

### `MENTOR_AGREEMENT_ENABLED`
- **Default**: `True` (auto-disabled during test suite runs).
- **Purpose**: Enforces that users with `is_mentor=True` or `LeadMentor` roles sign the active `MentorAgreement` before accessing portal features.

### `PRINT_LOGIN_CODE_ALWAYS`
- **Default**: `False`
- **Values**: `1`, `true`, `yes`.
- **Purpose**: When enabled, logs all generated OTP login codes directly to `stdout`. Useful during local testing and development without email access.

---

## 7. Geocoding & Mapping Services

| Variable | Default | Purpose |
|---|---|---|
| `GEOCODING_BACKENDS` | `programs.utils.geocoding.MapboxBackend,programs.utils.geocoding.NominatimBackend` | Comma-separated list of backend classes in priority order. |
| `MAPBOX_ACCESS_TOKEN` | `""` | Public access token for Mapbox geocoding and map tile rendering. |
| `GEOCODING_URL` | `https://nominatim.openstreetmap.org/search` | Search endpoint URL for OpenStreetMap / Nominatim. |
| `GEOCODING_USER_AGENT` | `GoSAdminPortal/1.0` | Custom `User-Agent` header for Nominatim requests. |
| `GEOCODING_TIMEOUT` | `10` | HTTP timeout in seconds for geocoding requests. |
| `GEOCODING_DELAY_SECONDS` | `1.0` | Delay between consecutive geocoding calls to observe Nominatim usage policies. |

---

## 8. Logging & Diagnostics

| Variable | Default | Values | Purpose |
|---|---|---|---|
| `LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL` | Root application logging level. |
| `DJANGO_LOG_LEVEL` | `INFO` (inherits `LOG_LEVEL`) | `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL` | Framework logging level for `django.*` loggers. |

---

## 9. Encryption Key Rotation & Maintenance

These variables are used by the `reencrypt_encrypted_data` management command when rotating encryption keys:

| Variable | Purpose |
|---|---|
| `NEW_FILE_ENCRYPTION_KEY` | Target key to encrypt records with when rotating from an older `FILE_ENCRYPTION_KEY`. |
| `OLD_FILE_ENCRYPTION_KEY` | Previous Fernet key used as a fallback to decrypt legacy records during migration. |
| `OLD_SECRET_KEY` | Legacy secret key used to decrypt historical records that predated dedicated Fernet keys. |

Example key rotation invocation:
```bash
python manage.py reencrypt_encrypted_data \
  --new-file-key "$NEW_FILE_ENCRYPTION_KEY" \
  --old-file-key "$OLD_FILE_ENCRYPTION_KEY"
```
