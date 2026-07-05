# Changelog

All notable changes to this project will be documented in this file.

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
