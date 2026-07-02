# Changelog

All notable changes to this project will be documented in this file.

## 2026-07-02

### Added
- New application open and close dates for Programs, allowing applications to remain open after a program's start date.

### Changed
- Updated the application wizard to look at explicit application dates for program availability.
- Application open and close dates now default to the program's start and end dates.

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

## 2025-09-07
- Initial project creation with basic student, program, parent, and mentor data forms
