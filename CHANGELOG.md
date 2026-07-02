# Changelog

All notable changes to this project will be documented in this file.

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

## Before 2026-06-28
- Initial project