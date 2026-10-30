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

## Bulk Email Pipeline & Background Dispatch

Bulk email views (`ProgramEmailView`, `ApplicationEmailView`, `ProgramEmailBalancesView`) avoid request timeouts and spam filter penalties:
- **Individual Dispatch**: Constructs individual messages per recipient instead of large BCC lists.
- **Threaded Background Execution**: Uses `send_messages_in_background()` to dispatch batches asynchronously via an in-process thread pool.

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
