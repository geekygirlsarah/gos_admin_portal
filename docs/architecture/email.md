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

Monthly balance emails are sent separately for each eligible program, with filters applied per program. Mismatched enrollments are excluded and logged without family details. Both balance-email flows use saved organization branding or a Momentum fallback rather than GoS-specific footer and contact defaults; existing GoS details must be saved explicitly to appear. Monthly payment guidance directs families to their program, not to an unverified payment destination. The parent Payments page separately supports organization-owned instructions and an optional validated HTTP(S) destination configured by Lead Mentors; blank instructions use the neutral contact-the-lead-mentors message.

Weekly student and parent digests select recipients and children only through ownership-consistent active enrollments and parent relationships. Outreach signups, shifts, events, travel signups, and required-document submissions are included only when their related ownership chain is local; open outreach capacity counts only valid local signups. The digest uses the tenant-safe balance calculator, and an inconsistent statement is shown as unavailable rather than as a zero or partial balance.

Fee-added and payment-recorded notices keep valid local parent delivery and
ownership checks, but use only the owning organization's validated payment
instructions and optional HTTP(S) destination; blank instructions use the
neutral contact-the-lead-mentors message. Their branding uses saved local
settings or a neutral Momentum fallback. Sliding-scale reviewer emails are
sent only to active local Lead Mentors with usable account email addresses and
contain a generic review notice, not student identity or household financial
details. Parent submission and decision notices retain their local content
and tenant branding.

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

Tenant delivery issues are visible only after a provider event is correlated with a trusted outgoing-message mapping. The organization identity is carried as an internal attribute on tenant-aware Django email messages; it is not read from a client header, hostname, subject, or recipient address. The `ToSendEmailBackend` records a mapping only after a provider-confirmed successful response and stores the recipient address encrypted with a keyed fingerprint for comparison.

The provider contract was checked against the official [send API](https://tosend.com/docs/api/introduction), [batch API](https://tosend.com/docs/api/batch-emails), and [webhook guide](https://tosend.com/docs/guide/webhooks):

- A successful single send returns a `message_id`, which the webhook repeats as `data.message_id`; bounce/complaint payloads include the actual bare recipient in `data.email`.
- A batch `207` response returns per-email results in request order, so only individual `status: success` items with distinct `message_id` values can be mapped. A multi-email `200` response does not document result ordering; those messages are deliberately left unattributed. A one-item batch has no ordering ambiguity.
- ToSend supports up to 50 `to`/`cc`/`bcc` recipients per message, but its send response does not document per-recipient acceptance. Multi-recipient messages therefore remain unmapped. A send with no provider ID, redirected recipient, or missing internal organization metadata is also untracked.
- Webhooks use `X-ToSend-Signature: sha256=<HMAC-SHA256 of the raw body>`. Provider events can be duplicated and are deduplicated by message ID, event type, and recipient. ToSend documents that failed/slow webhook deliveries are not retried; valid signed events are acknowledged after verification or secure quarantine. ToSend supports `Idempotency-Key` for batch requests only, but the backend does not automatically retry sends; network-ambiguous sends remain unattributable instead of guessing.

After signature verification, the handler matches both provider message ID and exact recipient fingerprint against one unambiguous mapping. Only then is an `EmailDeliveryEvent` stored as verified with that mapping. A message accepted by the provider but whose webhook arrives before the send mapping commits is first quarantined and reconciled after the mapping is persisted. Replays produce one verified event. A later-discovered provider-ID collision makes the mapping ambiguous and hides its events from tenant queries without deleting their history.

Profile and application resolution is limited to unique, ownership-consistent records in the verified organization; shared email addresses do not select a first match. Verified critical alerts go only to active local Lead Mentors with valid account email addresses and contain a generic notice, not recipient, profile, or provider diagnostic data. Audit records likewise omit addresses, payloads, and raw provider reasons.

Existing delivery events default to unverified and remain unchanged; they are hidden from tenant issue lists, profile badges, counts, resolution actions, and audit summaries until separately reviewed. New unmatched, ambiguous, malformed, or early events are retained in an encrypted quarantine available only to platform superusers on the root domain. Review is read-only. `cleanup_email_delivery_quarantine` is dry-run by default; `--execute` deletes only records received at least 30 days earlier. A separately approved scheduled execution is a release prerequisite; this implementation does not configure a scheduler, and retention is not operationally guaranteed until one is configured.
