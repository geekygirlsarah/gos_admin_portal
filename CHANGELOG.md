# Changelog

All notable changes to this project will be documented in this file.

## 2026-07-21

### Added
- Implemented automatic email notifications for applicants (students and parents) when their application is converted to a program enrollment.
- Created new email templates for conversion notifications, informing them of their enrollment and that further program information will follow soon.
- Added a "Resend Conversion Email" button to the application review screen for lead mentors to resend enrollment notifications.

## 2026-07-20

### Changed
- Darkened the Mentor dashboard card colors from "info" (light blue) to "primary" (dark blue) to improve visibility against the white background.
- Improved Mentor dashboard by replacing the generic "View Programs" button with a list of currently active programs, providing direct access to rosters and details.
- Improved Student and Parent dashboards to show a "Withdrawn" status for students who are no longer active in a program.
- Updated `DashboardView` to move withdrawn enrollments to the "Past & Upcoming Programs" section to reduce clutter while maintaining visibility.
- Improved Student and Parent dashboards by grouping inactive and upcoming programs into collapsible Bootstrap accordions, reducing clutter while keeping program history accessible.
- Updated `DashboardView` to provide pre-grouped enrollment data for optimized dashboard rendering.
- Updated the Student and Parent dashboards to conditionally show program details:
  - Active programs are now expanded to show balance info, attendance tracking, and outreach sign-ups.
  - Inactive and Upcoming programs are now collapsed, showing only their name and a status badge.
- Added a new `status` property to programs and an "Upcoming" badge for programs starting in the future.

### Fixed
- Resolved Bandit security findings and cleaned up unused `# nosec` suppressions:
  - Fixed hardcoded password in `programs/tests/test_list_sorting.py` by adding appropriate suppression for test code.
  - Resolved `mark_safe` warnings in `programs/templatetags/sorting_tags.py` for static HTML entities.
  - Refactored `programs/templatetags/form_tags.py` to use `format_html` instead of `mark_safe`, improving security and eliminating redundant suppressions.
  - Cleaned up unnecessary `# nosec` comments in `programs/tests/test_balance_sheet.py`.

### Added
- Implemented automatic email notifications for parents when important financial actions occur:
  - Notifications sent when a new fee is added to a program (respecting individual fee assignments).
  - Notifications sent when a payment is recorded, including the payment details and the student's updated remaining balance.
  - Notifications sent when a sliding scale discount is assigned to a student.
- Created a standardized, responsive HTML email template system for these notifications, ensuring a professional look with consistent branding.
- Centralized student balance calculation logic into reusable utility functions to ensure consistency across the portal and email notifications.

## 2026-07-12

### Fixed
- Fixed a bug where the "Resend Parent Handoff" email was sent to the student's email address instead of the parent's email. It now correctly uses the parent email provided during the application process (Step 7).
- Fixed GitHub Actions CI failure in `test_student_login_provisioning.py` where `contextvars.Token` was incorrectly used as a context manager. Switched to `allauth.core.context.request_context(request)`.
- Fixed GitHub Actions `safety` check failure caused by `requirements.txt` being in UTF-16 encoding (often caused by `pip freeze` on Windows). Added an automatic conversion step to the CI workflow.

### Added
- Added a "Communications" section to the application review detail page, allowing lead mentors to resend system emails:
  - Resend OTP/Verification email (for resuming applications).
  - Resend Parent Handoff email (for students handing off to parents).
  - Resend Submission Confirmation email.
  - Resend Approval and Decline emails.
- This helps resolve issues where applicants miss or lose their application wizard emails.

## 2026-07-08

### Added
- Duplicate application detection in the application wizard:
  - When an applicant verifies their email, the system now checks for existing draft applications with the same email.
  - If a duplicate is found, the user is presented with a choice to resume the previous application or start over with a fresh one.
  - Choosing to resume deletes the current temporary application and redirects the user to their previous progress.
  - Choosing to start over deletes the previous draft application(s) and continues with the current one.
  - This prevents students from inadvertently creating multiple duplicate applications.

## 2026-07-07

### Added
- Implemented custom error handling for 403 (Forbidden), 400 (Bad Request), and 500 (Internal Server Error):
  - Added dedicated error pages (`403.html`, `400.html`, `500.html`) with consistent site branding and helpful messages.
  - Configured custom handlers in `views.py` and registered them in `urls.py`.
- Implemented custom 404 error handling:
  - Users visiting non-existent pages are now redirected to the home page with a "that page doesn't exist" message.
  - Visitors to the application wizard who encounter a 404 (e.g., due to an expired session or invalid application ID) are redirected back to the main `/apply/` page with a specific "that application doesn't exist or timed out" message.
- Configured Django message tags to map to Bootstrap 5 alert classes (e.g., `error` maps to `danger`), ensuring error messages appear in the appropriate red "error boxes."

### Changed
- Updated `LoginRequiredMiddleware` to allow 404 errors to pass through to the custom handler even for unauthenticated users, ensuring consistent redirection behavior across the site.

## 2026-07-05

### Fixed
- Fixed a bug where Students and Parents could not log in via the OTP email code, even if their email was on file. The login system now correctly creates an account for them on first login, instead of saying "no account found."
- Resolved "disconnected student info" issue:
  - Implemented automatic name synchronization between `Student`/`Adult` profiles and their linked `User` accounts. Profiles are now the authoritative source for names.
  - Protected the `user` field in student edit forms to prevent accidental disconnection or unauthorized changes by non-admins.
- Fixed a crash in Django Admin when editing Student profiles (KeyError 'user').

## 2026-07-04

### Fixed
- Resolved login issues for Students and Mentors converted from applications. Verified emails are now correctly saved to Student/Adult records even if the form fields were left blank in the application wizard.
- Fixed a bug where mentor applications were incorrectly processed as student applications during conversion. Mentors now correctly result in an `Adult` record with the mentor flag set.
- Fixed several field name and name handling bugs in the application conversion service (`preferred_name` instead of `preferred_first_name`, handling of `legal_first_name` and `andrew_id` for mentors).
- Relaxed the mentor login policy to allow any email ending in `@andrew.cmu.edu` if it belongs to the mentor's record, supporting tagged email addresses (e.g., `name+tag@andrew.cmu.edu`) for testing and flexibility.
- Fixed a bug in `AccountAdapter.send_mail` where the `PRINT_LOGIN_CODE_ALWAYS` environment variable was incorrectly interpreted.
- Resolved Django Admin error when editing a Student: removed a stale `active` field reference from `StudentAdmin`.

### Added
- New env var `PRINT_LOGIN_CODE_ALWAYS` to aid debugging OTP logins. When set (e.g., `1`/`true`), the adapter logs an INFO line with the login code (or `(none)`) and email for all login email attempts, including the `unknown_account` path. Existing behavior for `DEBUG`/staging remains unchanged.

### Changed
- Login policy updated to enable anyone with a modeled role to sign in via OTP with role-specific identifiers:
  - Students: may sign in with their Andrew email or personal email.
  - Parents: may sign in with their personal email only (Andrew email not accepted).
  - Mentors (including Lead Mentors): may sign in with their Andrew email only (personal email not accepted).
  - Alumni: may sign in with their personal email only (Andrew email not accepted).
- The authentication adapter now enforces these rules and still auto-provisions a `User` account and `EmailAddress` when a matching Student/Adult exists but no user has been linked yet.

## 2026-07-02

### Added
- New application open and close dates for Programs, allowing applications to remain open after a program's start date.

### Changed
- Updated the application wizard to look at explicit application dates for program availability.
- Application open and close dates now default to the program's start and end dates.
- Application wizard Step 4 UI: Program blurbs are now inside collapsible accordions so applicants can quickly scan programs and expand for more details.

## 2026-07-01

### Changed
- Unified the layout and styling of login and account management pages (Login, Sign In, Verify Identity, and Sign Out).
- Updated all authentication pages to use a consistent "Girls of Steel Portal" branding and wider card layout (720px).
- Added error message displays and support contact information to all authentication screens for better user assistance.

## 2026-06-29

### Fixed
- Fixed bug on Edit Parent screen where role flags (e.g., `is_parent`) were reset to False when saving.
- Fixed field name mismatch in Parent edit form that prevented saving the email address.
- Fixed data loss on Edit Adult screen where address and Andrew ID info were reset to empty on save.
- Fixed security vulnerability where non-Lead Mentors could edit their own role flags (e.g., `is_mentor`).

### Added
- Audit logging for user login, logout, and failed login attempts.
- Audit logging for sensitive data access (Student and Adult profiles) by Mentors and Lead Mentors.
- `SensitiveDataViewMixin` for consistent logging across sensitive detail and update views.

### Changed
- Improved `ParentForm` to explicitly include only relevant fields and preserve existing role flags.
- Updated Adult edit templates to include missing fields like address, Andrew ID, and CMU access details.

## 2026-06-16

### Changed
- Security: Updated dependencies and addressed Bandit security findings.
- Improved formatting and fixed broken unit tests.

## 2026-06-15

### Added
- Student and adult login functionality, with improved email handling.

## 2026-06-10

### Changed
- UI: Replaced navbar with context-aware navbar.

## 2026-06-08

### Fixed
- Fixed migration issue with duplicate email addresses.

## 2026-05
### Added
- Added grade management for programs and grade confirmation in the application wizard.
- Implemented sliding scale information display in the application wizard.
- Added support for non-destructive data imports.
### Changed
- Security: Upgraded Django and dependencies to address vulnerabilities.
- UI improvements across the application, including help text and form formatting.
### Fixed
- Bugs in application wizard data consistency.
- Test failures after Django upgrade, and OSV scanner/scheduler issues.

## 2026-04
### Added
- Enhanced viewing options for sliding scale balance sheets.
- Enabled emailing balance sheets to individual students.
### Changed
- Updated requirements per OSV security scans.
- Improved linting, formatting, and Bandit compliance.

## 2026-03
### Added
- Additional files for team/role assignments.
### Changed
- Extensive cleanup of false-positive security scan results (Semgrep, GitLeaks).
### Fixed
- Fixed sliding scale application logic for student fees.

## 2026-01 — 2026-02
### Added
- Integrated comprehensive security scanning (CodeQL, GitLeaks, Semgrep, Trivy, OSV).
- Added team and crew management features.
- Implemented global settings page.

## 2025-12
### Added
- Implemented one-time password (OTP) authentication, replacing Google/regular auth.
- Added role permission settings and logging for sensitive operations.
### Changed
- Improved CI/CD pipeline with GitHub Actions and pip caching.

## 2025-10 — 2025-11
### Added
- Attendance tracking features and API key management.
### Changed
- Improved balance sheet email templates and student list sorting.
- Enhanced student profile management (cropping, attendance tweaks).

## 2025-09-07
- Initial project creation with basic student, program, parent, and mentor data forms
