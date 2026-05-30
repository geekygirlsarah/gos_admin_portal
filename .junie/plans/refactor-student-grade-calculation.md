---
sessionId: session-260526-172340-1xjr
isActive: true
---

# Requirements

### Overview & Goals
Format the program start date in the Step 5 application wizard to adhere to the server's locale setting (e.g., MM/DD/YYYY for `en-us`) instead of the default ISO YYYY-MM-DD format.

### Key Decisions
- Use `django.utils.formats.date_format` to format the date object. This respects the `LANGUAGE_CODE` setting in `settings.py`.
- Apply this formatting directly in `StudentInfoForm.__init__` where the label is constructed.


# Technical Design

### Proposed Changes
- **`applications/forms.py`**:
    - Update the import to include `from django.utils.formats import date_format`.
    - Modify `StudentInfoForm.__init__` to format `program_start_date` using `SHORT_DATE_FORMAT` before interpolating it into the `grade` field label.

### File Structure
- Modified: `applications/forms.py`


# Delivery Steps

###   Step 1: Format program date in StudentInfoForm label
Update `StudentInfoForm` in `applications/forms.py` to format the `program_start_date` label using the server's locale.
- Import `date_format` from `django.utils.formats`.
- In `StudentInfoForm.__init__`, update the `grade` field label to use `date_format(program_start_date, 'SHORT_DATE_FORMAT')`.

###   Step 2: Verify and finalize application wizard template
Verify and finalize the application wizard template.
- Ensure `templates/applications/step5_student_info.html` correctly displays the label provided by `StudentInfoForm`.
- Verify the JavaScript in `step5_student_info.html` is correctly calculating the graduation year.