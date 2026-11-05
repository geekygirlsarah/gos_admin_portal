# Email Architecture, Editor Pipeline, and Delivery Tracking

This document outlines the email dispatch mechanisms, development safety safeguards, the rich-text Tiptap editor bundle, HTML normalization, and delivery webhook processing.

## Email Safety & Development Configuration

To eliminate accidental emails to real parents and students in non-production environments:
- **Development Default (`DEBUG=True`)**: Outgoing emails default to the console backend (`django.core.mail.backends.console.EmailBackend`).
- **Production (`DEBUG=False`)**: Uses the toSend REST API backend (`ToSendEmailBackend` via `TOSEND_API_KEY` or `EMAIL_BACKEND="tosend"`) or standard SMTP.
- **Developer Redirection (`EMAIL_REDIRECT_TO`)**: Staging/testing environments can reroute all outgoing emails to a safe inbox (e.g., `EMAIL_REDIRECT_TO="dev-team@example.com"`). The original recipients are annotated in the subject line.
- **Global Disable (`EMAILS_ENABLED=False` or `DISABLE_OUTGOING_EMAILS=True`)**: Reroutes all outgoing mail to `dummy.EmailBackend`.
- **Database Scrubbing**: In non-production database copies, scrub real email addresses across Students, Adults, Users, and Applications using:
  ```bash
  python manage.py anonymize_emails --domain example.com --force
  ```

---

## Portal Addresses in Email

Login branding and application links use the organization's canonical portal
address: an active, unambiguously routed custom domain, otherwise an
unambiguous Momentum subdomain. Legacy email portal URL overrides remain in
storage but are not used. If no safe organization address is available,
application resume links are omitted instead of using another tenant's host.
Portal hostnames are separate from email sending domains and do not configure
the sending provider, DNS, or HTTPS.

---

## Bulk Email Pipeline & Background Dispatch

Bulk email views (`ProgramEmailView`, `ApplicationEmailView`, `ProgramEmailBalancesView`) avoid request timeouts and spam filter penalties:
- **Individual Dispatch**: Constructs individual messages per recipient instead of large BCC lists.
- **Threaded Background Execution**: Uses `send_messages_in_background()` to dispatch batches asynchronously via an in-process thread pool.

Program Messaging scopes program and subgroup choices, sender identities, enrolled students, mentors, and parent relationships to the request organization. Applicant bulk messaging likewise scopes program choices and recipients to applications owned by the request organization, excluding applications linked to a foreign-organization program. Both compose views use the request organization's sender identities and branding for previews without a selected program; requests without a tenant are rejected. Manual and scheduled balance statements scope programs, students, guardians, sender identities, and branding to the relevant organization; inconsistent financial records make affected program statements unavailable rather than generating partial totals. Other bulk email views still require their own tenant-isolation review.

Monthly balance emails are sent separately for each eligible program, with filters applied per program. Mismatched enrollments are excluded and logged without family details. Both balance-email flows use saved organization branding or a Momentum fallback rather than GoS-specific footer and contact defaults; existing GoS details must be saved explicitly to appear. Monthly payment guidance directs families to their program, not to an unverified payment destination. Configurable payment instructions and platform-wide branding defaults are separate follow-up work.

The separate fee-added and payment-recorded email templates still contain GoS/CMU payment instructions and are not covered by this balance-statement isolation work.

Program Messaging offers the deployment-configured default sender and named identities saved by the current organization, not the legacy global named-account fallback. An organization's notification-from override replaces the default; otherwise `NOTIFICATION_FROM_EMAIL` or `DEFAULT_FROM_EMAIL` supplies it. Email Settings shows the saved override separately from the effective address and does not pin the fallback when saving other settings. Existing saved overrides are not changed automatically.

The operator must configure a verified shared Momentum sender with the delivery provider and DNS before relying on it; changing an address here does not configure or verify delivery. Existing deployment defaults may still be GoS-specific and should be reviewed during that transition.

---

## Email Compose Pipeline & Tiptap Editor

The compose interface uses a self-hosted Tiptap editor bundled via esbuild (`frontend/email-editor.js` built to `programs/static_built/email-editor.js`).

### Editor Initialization & Form Synchronization
- Compose templates provide `<textarea id="id_body" data-email-editor>`.
- The bundle auto-initializes Tiptap over matching textareas.
- On every editor `update` event, the serialized HTML is synced to `textarea.value` and the form is marked with `data-touched` for unsaved-change warnings.

### Paste Handling (Word / Google Docs / External)
Pasting rich text undergoes double-sanitization:
1. **Client-Side Rewrite**: `wordListHtmlToHtml()` in `frontend/email-editor.js` converts Microsoft Word `mso-list` markup into semantic HTML `<ul>` and `<li>` elements during `editorProps.handlePaste` before ProseMirror strips the styling.
2. **Server-Side Sanitization**: `normalize_email_html()` in `programs/utils/email_html.py` cleans incoming HTML via `nh3`, unrolls spacer paragraphs, unwraps `<li><p>` blocks, and applies standard table styling.

### Embedded Image Pipeline
Pasted images are converted to `data:` URLs by `@tiptap/extension-image` and processed securely on submission:
- Server-side parser temporarily replaces `data:` image `src` attributes with `#gos-inline-img-N` sentinels prior to `nh3` cleaning (preventing `data:` scheme stripping).
- Re-encodes image bytes using Pillow:
  - Max dimension: `EMAIL_EMBEDDED_IMAGE_MAX_DIMENSION = 1600` px.
  - JPEG Quality: `EMAIL_EMBEDDED_IMAGE_QUALITY = 82`.
  - Max size: `EMAIL_EMBEDDED_IMAGE_MAX_BYTES = 2.5` MB.
  - Image limit: `EMAIL_MAX_EMBEDDED_IMAGES = 5`.
- Unembeddable, oversized, or invalid images receive the `#gos-inline-img-drop` sentinel and are cleanly stripped without broken placeholder graphics.

### CSS Styling Single Source of Truth
- `EMAIL_BASE_CSS` in `programs/utils/email_html.py` defines canonical styling for rendered emails.
- `programs/static/css/email_editor.css` mirrors `EMAIL_BASE_CSS` for in-browser editing.
- The editor canvas is bounded (`max-height: 60vh; overflow-y: auto`) with a sticky toolbar (`position: sticky; top: 0`).

---

## Email Delivery Tracking (`EmailDeliveryEvent`)

The application processes bounce and complaint webhooks from toSend:
- **Webhook Endpoint**: `/api/webhooks/tosend/` and `/programs/webhooks/tosend/`.
- **Model**: `EmailDeliveryEvent` logs `bounced`, `complaint`, and `unsubscribed` statuses, diagnostic error codes, and message IDs.
- **Entity Resolution**: Automatically links matching `Student`, `Adult`, and `Application` records by email address.
- **Lead Mentor Alerts**: Hard bounces and spam complaints trigger immediate notification emails to Lead Mentors.
- **UI Indicators**: Delivery issue badges and warning banners are displayed on student/adult directory views, profiles, and the Lead Mentor Command Center.
