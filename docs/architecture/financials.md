# Program Fees, Payments, and Sliding-Scale Financials

This document details the financial architecture including program fees, payment tracking, balance calculations, fee assignments, and sliding-scale fee assistance.

## Financial Models & Core Relationships

### `Program`
The central operational entity that defines active terms, fee schedules, feature flags, and student enrollments.

### `Fee` (`programs/models/fee.py`)
Represents a cost associated with a program (e.g., registration fee, kit fee, competition fee).
- **Applicability & Assignment**: Applicability to students is managed directly on the fee create/edit form (`FeeForm` in `programs/forms.py`) via the `students` multi-select field (restricted to `active_students_in_program`).
  - **All Students**: Leaving the `students` selection empty means the fee applies to all active students in the program.
  - **Specific Students**: Selecting students creates `FeeAssignment` rows that restrict the fee only to those assigned students.
- **Parent Notifications**:
  - Controlled by `notify_fee_parents()` in `programs/signals.py`.
  - When creating a fee via `ProgramFeeCreateView`, notifications are sent only to parents of the fee's applicable active students (signals are temporarily suppressed during save and `notify_fee_parents()` is invoked once after fee assignments are created).

### `Payment` (`programs/models/payment.py`)
Records a payment made by or on behalf of a student against a specific program fee:
- Linked to `Student` and `Fee`.
- Stores amount, payment date, method, notes, and recorded-by metadata.

---

## Sliding-Scale Fee Assistance (`SlidingScale`)

`SlidingScale` provides percentage-based financial discounts tied to a **Student** (not a single program).

### Multi-Program Scope
- An approved, active sliding scale record (`status="approved"`, non-expired) automatically applies to all fees across **all programs** in which the student participates.
- Balance calculation helpers in `programs/utils/balances.py` (`get_active_sliding_scale()`, `get_sliding_scale_for_program()`, `get_student_balance_data()`) factor the discount into total dues across enrollments.

### Workflow & Review
1. **Application**: Parents apply from the Payments portal page (`sliding_scale_apply`), providing household size, AGI data, and optional `TaxForm` documentation.
2. **Review & Decision**: Lead Mentors review submissions via `sliding_scale_review_list` / `sliding_scale_review_decide`.
3. **Document Auto-Deletion**: Uploaded `TaxForm` files are automatically deleted upon decision to protect sensitive financial records.
4. **Configuration**: Sliding-scale recommendation formulas (base income thresholds and household multipliers) are configured in the singleton `SlidingScaleSettings` model via Portal Settings → Sliding Scale tab.

---

## Balance Calculations & Exports

### Monthly Balances & Background Statements
- Balances are computed per student by summing applicable fees minus active sliding-scale discounts minus recorded payments.
- Statement generation and emailing:
  ```bash
  python manage.py send_monthly_balances --all-balances --days-back 30
  ```
- Balance sheets can be exported and emailed directly by Lead Mentors via `ProgramEmailBalancesView`.
