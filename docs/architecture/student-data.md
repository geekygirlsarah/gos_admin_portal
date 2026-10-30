# Student and Adult Data, Relationships, Merging, and Exports

This document details models and workflows for Students, Adults, parent-student relationships, merging records, active student scoping, and exports.

## Core Models & Naming Conventions

### Student Model (`programs/models/student.py`)
- **Name Fields**: `legal_first_name` (required), `preferred_first_name` (optional), `last_name`.
- **Display Properties**:
  - `display_name`: Returns `preferred_first_name or legal_first_name` + `last_name`. **Use `display_name` everywhere for UI presentation.**
  - `full_name`: Backward-compatible alias for `display_name`.
- **Linked User Account**:
  - The public portal `StudentForm` never includes the `user` ForeignKey (to prevent accidental nullification upon form save).
  - Linked accounts can only be managed via Django Admin using `AdminStudentForm`.
- **Encrypted Medical Fields**:
  - `allergies`, `dietary_restrictions`, `medical_notes` use symmetric encryption.
  - Medical data is gated under the `"health_medical"` permission section (`ProgramMedicalInfoView` lists active students with non-empty medical info).

### Adult Model (`programs/models/adult.py`)
- Unified model for Parents, Mentors, Volunteers, and Alumni.
- Shares the same name fields (`legal_first_name`, `preferred_first_name`, `last_name`) and `display_name` property.
- Roles are designated by boolean flags (`is_parent`, `is_mentor`, `is_alumni`, `is_volunteer`).

---

## Student ↔ Adult Relationships & Contact Properties

Student-Adult relationships are stored in the through-model `AdultStudentRelationship` (`Student.adults` / `Adult.students`).

- **Primary & Secondary Contacts**:
  - Stored as ForeignKeys to `AdultStudentRelationship`: `primary_contact_relationship` and `secondary_contact_relationship`.
  - `Student.primary_contact`, `Student.secondary_contact`, `Student.primary_contact_id`, and `Student.secondary_contact_id` are **compatibility properties with setters** that synchronize the through-record pointers.
  - **Query Invariant**: When filtering querysets by primary/secondary guardian, always filter via the relationship FK (e.g. `primary_contact_relationship__adult=...`), never the compat property name.

---

## Active vs. Inactive Students Scoping

A student is considered inactive if marked `graduated=True`, or within a program if their `Enrollment.active` is `False`.

### Standard Query Helpers (`programs/utils.py`)
Do not construct inline `graduated=False` filters. Use the standard helpers:
- `active_students()`: Global list of non-graduated students.
- `active_students_in_program(program)`: Active enrollments (`Enrollment.active=True`) and non-graduated students within a specific program.
- `students_in_running_programs()`: Students with active enrollments in any currently running program (`Program.status == "Active"`).

### Deactivation vs. Deletion
On program detail rosters, student membership is toggled rather than deleted:
- "Mark Inactive": `ProgramStudentDeactivateView` (`program_student_deactivate`) sets `Enrollment.active = False`.
- "Reactivate": `ProgramStudentActivateView` (`program_student_activate`) sets `Enrollment.active = True` (hidden for graduated students).

---

## Merging Duplicate Records

Merging helpers in `programs/utils/merges.py` handle deduplication atomically while preserving historical links and audit trails.

### Student Merge (`perform_student_merge`, `student_merge` view)
Merges duplicate student records into a target record:
1. Re-links all `AdultStudentRelationship` rows and preserves primary/secondary contact assignments.
2. Fills missing profile, demographic, contact, and medical information using `carry_over_missing_fields`.
3. Transfers linked user accounts via `transfer_user_account()` (`programs/utils/accounts.py`).
4. Moves all related data: enrollments, fee assignments, payments, signed documents, clearances (`BackgroundCheck`), sliding-scale applications, RFID cards, attendance sessions/events, daily presence records, digital sign-outs, applications, outreach signups, and badges.
5. Deletes the source student and logs `AuditEvent.RECORDS_MERGED`.

### Parent / Adult Merge (`perform_parent_merge`, `parent_merge` view)
Merges duplicate adult records:
1. Transfers student relationships and emergency contacts.
2. Fills missing profile and contact fields (preferred name, college/employer, mentor Andrew ID, photo).
3. Merges role flags (`is_parent`, `is_mentor`, `is_alumni`, `is_volunteer`).
4. Transfers user accounts and moves background checks, mentor agreement acceptances, outreach mentor signups, RFID cards, and attendance sessions.
5. Deletes the source adult and logs `AuditEvent.RECORDS_MERGED`.

---

## Roster Exports (`ProgramStudentExportView`)

Active enrolled students can be exported to Excel (`.xlsx`) or CSV (`.csv`) via `/programs/<id>/roster/export/`:
- **Default Export**: Standard name and grade level only.
- **Lead Mentor Optional Column Groups** (passed via `?groups=` query parameters):
  - `contact`: Student email, phone, school, graduation year.
  - `parents`: Primary/secondary guardian names, relationships, phone numbers, emails, and update permissions.
  - `balance`: Total fees, discounts, payments, and net balance due.
- Non-Lead Mentor users requesting optional groups are silently restricted to the default export.
- Implementation uses `export_csv_response()` and `export_xlsx_response()` from `programs/utils/spreadsheets.py`.

---

## School Districts & Carpool Geocoding

### SchoolDistrict
- Canonical school districts (e.g., "North Allegheny School District").
- `School.district` is a nullable ForeignKey (`on_delete=SET_NULL`), allowing districts to be deleted without removing schools.
- Managed by Lead Mentors under `/schools/districts/`.

### Address Geocoding (`AddressGeocode`)
- Normalized address coordinates are cached in `AddressGeocode` (`programs/utils/geocoding.py`).
- Program Carpool Map (`ProgramStudentMapView`) displays markers for students with `directory_consent=True`.
- Pre-populate cache via `python manage.py geocode_student_addresses --retry-missing`.
