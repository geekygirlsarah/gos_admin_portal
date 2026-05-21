"""Forms for the public application wizard (Steps 1-8)."""

from __future__ import annotations

from django import forms
from django.utils import timezone

from programs.models import (
    RELATIONSHIP_CHOICES,
    TSHIRT_SIZE_CHOICES,
    Program,
    School,
    Student,
)
from programs.validators import validate_phone_number

from .models import APP_ID_ALPHABET, APP_ID_LENGTH, Application
from .services import normalize_email


class ResumeApplicationForm(forms.Form):
    """Step 1: enter an existing application ID to resume."""

    application_id = forms.CharField(
        label="Application ID",
        min_length=APP_ID_LENGTH,
        max_length=APP_ID_LENGTH,
        widget=forms.TextInput(
            attrs={
                "class": "form-control text-uppercase",
                "autocomplete": "off",
                "placeholder": "e.g. AB23CDEF",
                "aria-describedby": "application_id_help",
            }
        ),
        help_text="Enter the 8-character code shown when you started.",
    )

    def clean_application_id(self):
        raw = (self.cleaned_data["application_id"] or "").strip().upper()
        if len(raw) != APP_ID_LENGTH:
            raise forms.ValidationError(
                f"Application IDs are exactly {APP_ID_LENGTH} characters long."
            )
        invalid = [c for c in raw if c not in APP_ID_ALPHABET]
        if invalid:
            raise forms.ValidationError(
                "Application IDs only contain letters and digits "
                "(no 0, O, 1, I or L). "
                f"Unexpected characters: {''.join(sorted(set(invalid)))}"
            )
        if not Application.objects.filter(application_id=raw).exists():
            raise forms.ValidationError(
                "We couldn't find an application with that ID. "
                "Please double-check, or start a new application."
            )
        return raw


class ApplicantTypeForm(forms.Form):
    """Step 2: who are you, and what's your email?"""

    applicant_type = forms.ChoiceField(
        label="I am applying as a…",
        choices=Application.Type.choices,
        widget=forms.RadioSelect(attrs={"class": "form-check-input"}),
    )
    email = forms.EmailField(
        label="Email address",
        required=False,
        widget=forms.EmailInput(
            attrs={
                "class": "form-control",
                "autocomplete": "email",
                "placeholder": "you@example.com",
            }
        ),
        help_text=(
            "Students: if you don't have your own email, leave this blank "
            "and a parent/guardian should fill out this application instead."
        ),
    )

    def clean(self):
        cleaned = super().clean()
        applicant_type = cleaned.get("applicant_type")
        email = cleaned.get("email")
        # Parents and mentors must have an email; students may not.
        if (
            applicant_type in (Application.Type.PARENT, Application.Type.MENTOR)
            and not email
        ):
            self.add_error(
                "email",
                "An email address is required for parents and mentors.",
            )
        if applicant_type == Application.Type.STUDENT and not email:
            # Not an error, but signal up to the view that the parent
            # should be the applicant.
            cleaned["needs_parent"] = True
        return cleaned


class ProgramSelectForm(forms.Form):
    """Step 3: pick an upcoming (future) program to apply to."""

    program = forms.ModelChoiceField(
        label="Program",
        queryset=Program.objects.none(),
        empty_label=None,
        widget=forms.RadioSelect(attrs={"class": "form-check-input"}),
    )

    def __init__(self, *args, future_programs=None, **kwargs):
        super().__init__(*args, **kwargs)
        if future_programs is not None:
            self.fields["program"].queryset = future_programs


class OtpVerifyForm(forms.Form):
    """Step 4: enter the 6-digit code emailed to the applicant."""

    code = forms.CharField(
        label="Verification code",
        min_length=6,
        max_length=6,
        widget=forms.TextInput(
            attrs={
                "class": "form-control form-control-lg text-center",
                "autocomplete": "one-time-code",
                "inputmode": "numeric",
                "pattern": "[0-9]*",
                "placeholder": "123456",
            }
        ),
    )

    def clean_code(self):
        code = (self.cleaned_data["code"] or "").strip()
        if not code.isdigit():
            raise forms.ValidationError("The code is 6 digits.")
        return code


# ---------------------------------------------------------------------------
# Step 5: student information
# ---------------------------------------------------------------------------


_text_attrs = {"class": "form-control"}
_select_attrs = {"class": "form-select"}


class ChooseExistingStudentForm(forms.Form):
    """Optional sub-form shown to a parent who has multiple existing children
    on file. Lets them pick which student this application is for.
    """

    student = forms.ModelChoiceField(
        label="Which student is this application for?",
        queryset=Student.objects.none(),
        empty_label="— Apply for a new student —",
        required=False,
        widget=forms.RadioSelect(attrs={"class": "form-check-input"}),
    )

    def __init__(self, *args, students=None, **kwargs):
        super().__init__(*args, **kwargs)
        if students is not None:
            self.fields["student"].queryset = students


class StudentInfoForm(forms.Form):
    """Step 5: blank or prefilled student information."""

    legal_first_name = forms.CharField(
        label="Legal first name",
        max_length=150,
        widget=forms.TextInput(attrs=_text_attrs),
    )
    first_name = forms.CharField(
        label="Preferred first name (if different)",
        max_length=150,
        required=False,
        widget=forms.TextInput(attrs=_text_attrs),
    )
    last_name = forms.CharField(
        label="Last name",
        max_length=150,
        widget=forms.TextInput(attrs=_text_attrs),
    )
    pronouns = forms.CharField(
        label="Pronouns",
        max_length=50,
        required=False,
        widget=forms.TextInput(attrs=_text_attrs),
    )
    date_of_birth = forms.DateField(
        label="Date of birth",
        required=False,
        widget=forms.DateInput(attrs={**_text_attrs, "type": "date"}),
    )
    personal_email = forms.EmailField(
        label="Student's personal email",
        required=False,
        widget=forms.EmailInput(attrs=_text_attrs),
    )
    cell_phone_number = forms.CharField(
        label="Student's cell phone",
        max_length=30,
        required=False,
        validators=[validate_phone_number],
        widget=forms.TextInput(attrs=_text_attrs),
    )
    school_name = forms.ChoiceField(
        label="School",
        required=False,
        widget=forms.Select(attrs=_select_attrs),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["school_name"].choices = [("", "---")] + [
            (s.name, s.name) for s in School.objects.all().order_by("name")
        ]
        current_year = timezone.now().year
        self.fields["graduation_year"].min_value = current_year
        # Also update the MinValueValidator limit to ensure the error message is correct
        for validator in self.fields["graduation_year"].validators:
            if hasattr(validator, "limit_value") and not hasattr(validator, "max_value"):
                # MaxValueValidator usually doesn't have min_value attribute, but they both have limit_value.
                # In Django, MinValueValidator and MaxValueValidator are subclasses of BaseValidator.
                # MinValueValidator has code 'min_value'
                # MaxValueValidator has code 'max_value'
                if getattr(validator, "code", None) == "min_value":
                    validator.limit_value = current_year

    graduation_year = forms.IntegerField(
        label="Expected graduation year",
        required=False,
        min_value=2000,
        max_value=2100,
        widget=forms.NumberInput(attrs=_text_attrs),
    )
    tshirt_size = forms.ChoiceField(
        label="T-shirt size",
        choices=[("", "---")] + TSHIRT_SIZE_CHOICES,
        required=False,
        widget=forms.Select(attrs=_select_attrs),
    )
    allergies = forms.CharField(
        label="Allergies",
        required=False,
        widget=forms.Textarea(attrs={**_text_attrs, "rows": 2}),
    )
    dietary_restrictions = forms.CharField(
        label="Dietary restrictions",
        required=False,
        widget=forms.Textarea(attrs={**_text_attrs, "rows": 2}),
    )
    medical_notes = forms.CharField(
        label="Other medical notes",
        required=False,
        widget=forms.Textarea(attrs={**_text_attrs, "rows": 2}),
    )


# ---------------------------------------------------------------------------
# Steps 6 & 7: parent / guardian information
# ---------------------------------------------------------------------------


class ParentInfoForm(forms.Form):
    """Step 6 (primary) and Step 7 (secondary) parent/guardian info."""

    first_name = forms.CharField(
        label="First name",
        max_length=150,
        widget=forms.TextInput(attrs=_text_attrs),
    )
    last_name = forms.CharField(
        label="Last name",
        max_length=150,
        widget=forms.TextInput(attrs=_text_attrs),
    )
    relationship_to_student = forms.ChoiceField(
        label="Relationship to student",
        choices=[("", "---")] + RELATIONSHIP_CHOICES,
        required=False,
        widget=forms.Select(attrs=_select_attrs),
    )
    email = forms.EmailField(
        label="Email address",
        widget=forms.EmailInput(attrs=_text_attrs),
    )
    cell_phone = forms.CharField(
        label="Cell phone",
        max_length=30,
        required=False,
        validators=[validate_phone_number],
        widget=forms.TextInput(attrs=_text_attrs),
    )
    home_phone = forms.CharField(
        label="Home phone",
        max_length=30,
        required=False,
        validators=[validate_phone_number],
        widget=forms.TextInput(attrs=_text_attrs),
    )

    def __init__(self, *args, require_email=True, student_emails=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.student_emails = [normalize_email(e) for e in (student_emails or []) if e]
        if not require_email:
            self.fields["email"].required = False

    def clean_email(self):
        email = self.cleaned_data.get("email")
        if not email:
            return email
        normalized = normalize_email(email)
        if normalized in self.student_emails:
            raise forms.ValidationError(
                "This email address is already used by the student."
            )
        return email


class ParentHandoffForm(forms.Form):
    """When a student started the application, prompt them for a parent
    email to hand the rest of the wizard off to (used if no primary parent
    is on file yet).
    """

    parent_email = forms.EmailField(
        label="Parent / guardian email",
        help_text=(
            "We'll email this address with your application ID so a "
            "parent or guardian can finish the rest of the application."
        ),
        widget=forms.EmailInput(attrs=_text_attrs),
    )

    def __init__(self, *args, student_emails=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.student_emails = [normalize_email(e) for e in (student_emails or []) if e]

    def clean_parent_email(self):
        email = self.cleaned_data.get("parent_email")
        if not email:
            return email
        normalized = normalize_email(email)
        if normalized in self.student_emails:
            raise forms.ValidationError(
                "You cannot use your own email address for your parent/guardian."
            )
        return email


# ---------------------------------------------------------------------------
# Step 8: final confirmation
# ---------------------------------------------------------------------------


class ConfirmSubmitForm(forms.Form):
    """Step 8: explicit confirmation checkbox before submitting."""

    confirm = forms.BooleanField(
        label=(
            "I confirm that the information above is accurate to the best "
            "of my knowledge."
        ),
        required=True,
        widget=forms.CheckboxInput(attrs={"class": "form-check-input"}),
    )


# ---------------------------------------------------------------------------
# Step 9: post-approval signed-document upload
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Mentor branch forms
# ---------------------------------------------------------------------------


class MentorInfoForm(forms.Form):
    """Mentor application: basic mentor information."""

    legal_first_name = forms.CharField(
        label="Legal first name",
        max_length=150,
        widget=forms.TextInput(attrs=_text_attrs),
    )
    first_name = forms.CharField(
        label="Preferred first name (if different)",
        max_length=150,
        required=False,
        widget=forms.TextInput(attrs=_text_attrs),
    )
    last_name = forms.CharField(
        label="Last name",
        max_length=150,
        widget=forms.TextInput(attrs=_text_attrs),
    )
    cell_phone = forms.CharField(
        label="Cell phone",
        max_length=30,
        required=False,
        validators=[validate_phone_number],
        widget=forms.TextInput(attrs=_text_attrs),
    )
    discord_username = forms.CharField(
        label="Discord username",
        max_length=100,
        required=False,
        widget=forms.TextInput(attrs=_text_attrs),
    )
    andrew_id = forms.CharField(
        label="Andrew ID (CMU affiliates only)",
        max_length=50,
        required=False,
        widget=forms.TextInput(attrs=_text_attrs),
    )
    employer = forms.CharField(
        label="Employer / affiliation",
        max_length=200,
        required=False,
        widget=forms.TextInput(attrs=_text_attrs),
    )
    notes = forms.CharField(
        label="Why are you interested in mentoring Girls of Steel?",
        required=False,
        widget=forms.Textarea(attrs={**_text_attrs, "rows": 4}),
    )


class MentorClearanceInterestForm(forms.Form):
    """Are you interested in getting PA child-protection clearances?"""

    INTEREST_CHOICES = [
        ("yes", "Yes, I want to start / complete clearances."),
        ("no", "No, not at this time."),
    ]
    interested = forms.ChoiceField(
        label="Are you interested in obtaining PA child-protection clearances?",
        choices=INTEREST_CHOICES,
        widget=forms.RadioSelect(attrs={"class": "form-check-input"}),
    )


class MentorClearanceDetailForm(forms.Form):
    """For each PA clearance, indicate whether you have it or still need it."""

    STATUS_CHOICES = [
        ("have", "I already have this clearance."),
        ("need", "I don't have this yet — I'll need to get it."),
    ]
    paca = forms.ChoiceField(
        label="PA Child Abuse Clearance (PACA)",
        choices=STATUS_CHOICES,
        widget=forms.RadioSelect(attrs={"class": "form-check-input"}),
    )
    patch = forms.ChoiceField(
        label="PA Criminal Record Clearance (PATCH)",
        choices=STATUS_CHOICES,
        widget=forms.RadioSelect(attrs={"class": "form-check-input"}),
    )
    fbi = forms.ChoiceField(
        label="FBI criminal fingerprint check",
        choices=STATUS_CHOICES,
        widget=forms.RadioSelect(attrs={"class": "form-check-input"}),
    )


class DocumentSubmissionForm(forms.Form):
    """Upload a single signed document for an approved application."""

    file = forms.FileField(
        label="Signed file",
        widget=forms.ClearableFileInput(
            attrs={"class": "form-control", "accept": ".pdf,.png,.jpg,.jpeg"}
        ),
        help_text="PDF preferred. Images (PNG/JPEG) are also accepted.",
    )
