import datetime
from decimal import Decimal

from django import forms
from django.conf import settings
from django.db.models import Value
from django.db.models.functions import Coalesce, Lower, NullIf

from programs.utils import format_grade, get_academic_year_ending

from .models import Adult, Fee, Payment, Program, School, SlidingScale, Student


class StudentForm(forms.ModelForm):
    # Expose reverse M2M to Adults so edits on Student reflect on Adult.students
    parents = forms.ModelMultipleChoiceField(
        queryset=Adult.objects.filter(is_parent=True),
        required=False,
        help_text="Select the parents/guardians for this student.",
    )
    # Non-model field used to pick K–12 and auto-calc graduation year
    GRADE_CHOICES = [(0, "K")] + [(i, str(i)) for i in range(1, 13)]
    grade_selector = forms.ChoiceField(
        choices=[("", "—")] + [(str(v), label) for v, label in GRADE_CHOICES],
        required=False,
        label="Grade (K–12)",
    )

    class Meta:
        model = Student
        # Include all model fields except system-managed ones and M2M-through
        fields = "__all__"
        exclude = ["programs", "created_at", "updated_at"]
        widgets = {
            "date_of_birth": forms.DateInput(attrs={"type": "date"}),
            "clearances_expiration_date": forms.DateInput(attrs={"type": "date"}),
            # Render as clear, clickable checkboxes (fixes empty button appearance)
            "race_ethnicities": forms.CheckboxSelectMultiple(),
            "directory_consent": forms.CheckboxInput(
                attrs={"class": "form-check-input"}
            ),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Ensure sorted dropdowns for adult-related fields; limit to Adults marked as parents
        qs_adults = Adult.objects.filter(is_parent=True).order_by(
            "first_name", "last_name"
        )
        # Parents (multi-select used for custom picker)
        self.fields["parents"].queryset = qs_adults
        # Primary/Secondary contact fields (FKs)
        if "primary_contact" in self.fields:
            self.fields["primary_contact"].queryset = qs_adults
        if "secondary_contact" in self.fields:
            self.fields["secondary_contact"].queryset = qs_adults

        # When editing, pre-populate parents from the reverse relation
        instance = getattr(self, "instance", None)
        if instance and instance.pk:
            # Start with existing adults
            initial_set = set(instance.adults.all())
            # ALSO include primary/secondary in the initial parents
            if instance.primary_contact:
                initial_set.add(instance.primary_contact)
            if instance.secondary_contact:
                initial_set.add(instance.secondary_contact)
            self.fields["parents"].initial = list(initial_set)
        # Initialize grade_selector from graduation_year if available
        gy = self.instance.graduation_year if instance else None
        if gy:
            # infer grade from graduation year based on current academic year
            today = datetime.date.today()
            end_year = today.year + (1 if today.month >= 7 else 0)
            # years remaining from current school year end to graduation
            years_remaining = gy - end_year
            # Map back to grade: 12 - years_remaining; for K we consider 13 remaining
            if years_remaining == 13:
                grade_str = "0"
            else:
                grade = 12 - years_remaining
                if 0 <= grade <= 12:
                    grade_str = str(grade)
                else:
                    grade_str = ""
            if grade_str:
                self.fields["grade_selector"].initial = grade_str
        # Add help text to graduation_year
        if "graduation_year" in self.fields:
            self.fields["graduation_year"].help_text = (
                "Auto-calculated from Grade, but you may override if needed."
            )

    def clean(self):
        cleaned = super().clean()
        p = cleaned.get("primary_contact")
        s = cleaned.get("secondary_contact")
        if p and s and p == s:
            self.add_error(
                "secondary_contact",
                "Secondary contact must be different from Primary contact.",
            )
        return cleaned

    def save(self, commit=True):
        # Compute graduation_year from grade_selector when provided
        grade_val = (
            self.cleaned_data.get("grade_selector")
            if hasattr(self, "cleaned_data")
            else None
        )
        if grade_val not in (None, "", "None"):
            try:
                g = int(grade_val)

                self.instance.graduation_year = get_academic_year_ending() + (12 - g)
            except (ValueError, TypeError):
                pass
        # Save base fields first
        instance = super().save(commit=False)
        if commit:
            instance.save()
        # After instance exists, sync the reverse M2M to Parents ensuring Primary/Secondary are included
        if hasattr(self, "cleaned_data") and "parents" in self.cleaned_data:
            selected = set(self.cleaned_data.get("parents", []))
            for p in (instance.primary_contact, instance.secondary_contact):
                if p:
                    selected.add(p)
            # Ensure instance has a PK in case commit=False was used
            if not instance.pk:
                instance.save()
            instance.adults.set(selected)
        # Return the instance
        return instance


class AddExistingStudentToProgramForm(forms.Form):
    student = forms.ModelChoiceField(
        queryset=Student.objects.all(),
        required=True,
        label="Select student to add",
    )

    def __init__(self, *args, program: Program, **kwargs):
        super().__init__(*args, **kwargs)
        # Exclude students already enrolled in this program
        # Also sort by first name (coalescing legal name) then last name
        self.fields["student"].queryset = Student.objects.exclude(
            id__in=program.students.values_list("id", flat=True)
        ).order_by(
            Lower(Coalesce(NullIf("first_name", Value("")), "legal_first_name")),
            Lower("last_name"),
        )


class QuickCreateStudentForm(forms.ModelForm):
    class Meta:
        model = Student
        fields = ["first_name", "last_name"]


class ParentForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        user = kwargs.pop("user", None)
        super().__init__(*args, **kwargs)

        # Protect students list and active status from non-Lead Mentors/Admins
        is_privileged = user and (
            user.is_superuser or user.groups.filter(name="LeadMentor").exists()
        )

        if not is_privileged:
            protected_fields = ["active", "students"]
            for field in protected_fields:
                if field in self.fields:
                    del self.fields[field]

    class Meta:
        model = Adult
        # Exclude flags (is_parent, is_mentor, is_alumni, active)
        # to ensure they are preserved if not explicitly rendered in the template.
        fields = [
            "first_name",
            "preferred_first_name",
            "last_name",
            "personal_email",
            "phone_number",
            "email_updates",
            "students",
        ]


class AdultForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        user = kwargs.pop("user", None)
        super().__init__(*args, **kwargs)

        # Protect role flags, active status, and students list from non-Lead Mentors/Admins
        is_privileged = user and (
            user.is_superuser or user.groups.filter(name="LeadMentor").exists()
        )

        if not is_privileged:
            protected_fields = [
                "is_parent",
                "is_mentor",
                "is_alumni",
                "active",
                "students",
            ]
            for field in protected_fields:
                if field in self.fields:
                    del self.fields[field]

    class Meta:
        model = Adult
        fields = [
            "first_name",
            "preferred_first_name",
            "last_name",
            "pronouns",
            "personal_email",
            "phone_number",
            "cell_phone",
            "home_phone",
            "address",
            "city",
            "state",
            "zip_code",
            "email_updates",
            "is_parent",
            "is_mentor",
            "is_alumni",
            "students",
            "active",
            "andrew_id",
            "andrew_email",
            "andrew_id_expiration",
            "andrew_id_sponsor",
            "college",
            "field_of_study",
            "employer",
            "job_title",
            "ok_to_contact",
            "notes",
        ]


class PaymentForm(forms.ModelForm):
    class Meta:
        model = Payment
        fields = [
            "student",
            "amount",
            "paid_on",
            "paid_via",
            "check_number",
            "camp_hours",
            "notes",
        ]
        widgets = {
            "paid_on": forms.DateInput(attrs={"type": "date"}),
        }

    def __init__(self, *args, program: Program, **kwargs):
        super().__init__(*args, **kwargs)
        # Restrict student choices to those enrolled in this program
        self.fields["student"].queryset = Student.objects.filter(
            programs=program
        ).order_by(
            Lower(Coalesce(NullIf("first_name", Value("")), "legal_first_name")),
            Lower("last_name"),
        )
        # Store program for use in the view when saving
        self._program = program


class SlidingScaleForm(forms.ModelForm):
    class Meta:
        model = SlidingScale
        fields = [
            "student",
            "percent",
            "date",
            "family_size",
            "adjusted_gross_income",
            "is_pending",
            "notes",
        ]
        labels = {
            "percent": "Discount percent",
            "date": "Effective date",
        }
        help_texts = {
            "percent": "Enter a value between 0 and 100. This percent will discount applicable fees.",
            "date": "Only fees on or after this date will be discounted. Leave blank to apply to all program fees.",
        }
        widgets = {
            "date": forms.DateInput(attrs={"type": "date"}),
        }

    def __init__(self, *args, program: Program, **kwargs):
        super().__init__(*args, **kwargs)
        # Restrict to students in this program
        self.fields["student"].queryset = Student.objects.filter(
            programs=program
        ).order_by(
            Lower(Coalesce(NullIf("first_name", Value("")), "legal_first_name")),
            Lower("last_name"),
        )

    def clean_percent(self):
        p = self.cleaned_data.get("percent")
        if p is None:
            return p
        if p < 0 or p > 100:
            raise forms.ValidationError("Percent must be between 0 and 100.")
        return p


class SchoolForm(forms.ModelForm):
    class Meta:
        model = School
        fields = ["name", "district", "street_address", "city", "state", "zip_code"]


class ProgramForm(forms.ModelForm):
    class Meta:
        model = Program
        fields = [
            "name",
            "description",
            "start_date",
            "end_date",
            "applications_open",
            "applications_close",
            "grade_range_start",
            "grade_range_end",
            "cost",
            "active",
            "features",
        ]
        widgets = {
            "start_date": forms.DateInput(attrs={"type": "date"}),
            "end_date": forms.DateInput(attrs={"type": "date"}),
            "applications_open": forms.DateInput(attrs={"type": "date"}),
            "applications_close": forms.DateInput(attrs={"type": "date"}),
            "grade_range_start": forms.Select(
                choices=[("", "---")] + [(i, format_grade(i)) for i in range(13)]
            ),
            "grade_range_end": forms.Select(
                choices=[("", "---")] + [(i, format_grade(i)) for i in range(13)]
            ),
            "features": forms.CheckboxSelectMultiple(),
        }


class ProgramEmailForm(forms.Form):
    program = forms.ModelChoiceField(
        queryset=Program.objects.all(),
        required=False,
        help_text="Select the program whose contacts you want to email.",
    )
    recipient_groups = forms.MultipleChoiceField(
        required=True,
        choices=[
            ("students", "Students"),
            ("parents", "Parents/Guardians"),
            ("mentors", "Mentors"),
        ],
        widget=forms.CheckboxSelectMultiple(),
        help_text="Choose one or more groups to email.",
    )
    subject = forms.CharField(max_length=255)
    body = forms.CharField(
        widget=forms.Textarea(attrs={"rows": 12}),
        help_text="Rich text is supported. Paste content or use the editor.",
    )
    test_email = forms.EmailField(
        required=False, help_text="Optional: send only to this address for testing."
    )

    def __init__(self, *args, **kwargs):
        # Allow passing a fixed program via kwarg program
        program = kwargs.pop("program", None)
        super().__init__(*args, **kwargs)
        # Build sender choices from settings
        accounts = getattr(settings, "EMAIL_SENDER_ACCOUNTS", []) or []
        choices = []
        initial_value = None
        if accounts:
            for acc in accounts:
                email = acc.get("email") or ""
                display = acc.get("display_name") or email or "Sender"
                value = acc.get("key") or email
                label = f"{display} <{email}>" if email else display
                choices.append((value, label))
            if choices:
                initial_value = choices[0][0]
        else:
            default_email = getattr(settings, "DEFAULT_FROM_EMAIL", "")
            default_name = getattr(settings, "DEFAULT_FROM_NAME", None)
            if default_name:
                label = (
                    f"Default ({default_name} <{default_email}>)"
                    if default_email
                    else f"Default ({default_name})"
                )
            else:
                label = (
                    f"Default ({default_email})"
                    if default_email
                    else "Default configured sender"
                )

            choices = [
                (
                    "DEFAULT",
                    label,
                )
            ]
            initial_value = "DEFAULT"
        self.fields["from_account"] = forms.ChoiceField(
            choices=choices, initial=initial_value, label="Send from"
        )
        if program is not None:
            self.fields["program"].initial = program
            self.fields["program"].widget = forms.HiddenInput()
            self.fields["program"].required = True

    def clean(self):
        cleaned = super().clean()
        prog = cleaned.get("program")
        if self.fields["program"].widget.__class__ is forms.HiddenInput and not prog:
            raise forms.ValidationError("Program is required.")
        return cleaned


class StudentBalanceModelChoiceField(forms.ModelChoiceField):
    def __init__(self, *args, **kwargs):
        self.program = kwargs.pop("program", None)
        super().__init__(*args, **kwargs)

    def label_from_instance(self, obj):
        from .models import Fee, Payment, SlidingScale
        from .views import compute_sliding_discount_rounded

        student = obj
        program = self.program

        # Calculation logic mirror from views.py
        total_fees = Decimal("0")
        fees = Fee.objects.filter(program=program)
        for fee in fees:
            is_assigned = fee.assignments.exists()
            if not is_assigned or fee.assignments.filter(student=student).exists():
                total_fees += fee.amount

        sliding = SlidingScale.objects.filter(student=student, program=program).first()
        total_sliding = Decimal("0")
        if sliding and sliding.percent is not None:
            total_sliding = compute_sliding_discount_rounded(
                total_fees, sliding.percent
            )

        total_payments = sum(
            Payment.objects.filter(student=student, program=program).values_list(
                "amount", flat=True
            ),
            Decimal("0"),
        )

        balance = total_fees - total_sliding - total_payments
        return f"{student.first_name or student.legal_first_name} {student.last_name} (${balance:,.2f})"


class ProgramEmailBalancesForm(forms.Form):
    program = forms.ModelChoiceField(queryset=Program.objects.all(), required=False)
    subject = forms.CharField(
        max_length=255, help_text="Subject for the email to each family/student."
    )
    default_message = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"rows": 6}),
        help_text="Optional message that will appear above the balance sheet.",
    )
    recipient_filter = forms.ChoiceField(
        choices=[
            ("all", "Send to everyone (in the program)"),
            ("non_zero", "Send to everyone with a non-zero balance"),
            ("positive", "Send to everyone with a positive non-zero balance"),
            ("individual", "Send to an individual:"),
        ],
        initial="all",
        label="Recipient Filter",
        widget=forms.Select(attrs={"id": "id_recipient_filter"}),
    )
    student = StudentBalanceModelChoiceField(
        queryset=Student.objects.none(),
        required=False,
        label="Student",
        help_text="Only used if 'Send to an individual' is selected.",
        widget=forms.Select(attrs={"id": "id_student"}),
    )
    test_email = forms.EmailField(
        required=False, help_text="Optional: send a single sample to this address."
    )

    def __init__(self, *args, **kwargs):
        program = kwargs.pop("program", None)
        super().__init__(*args, **kwargs)
        # Sender choices from settings
        accounts = getattr(settings, "EMAIL_SENDER_ACCOUNTS", []) or []
        choices = []
        initial_value = None
        if accounts:
            for acc in accounts:
                email = acc.get("email") or ""
                display = acc.get("display_name") or email or "Sender"
                value = acc.get("key") or email
                label = f"{display} <{email}>" if email else display
                choices.append((value, label))
            if choices:
                initial_value = choices[0][0]
        else:
            default_email = getattr(settings, "DEFAULT_FROM_EMAIL", "")
            default_name = getattr(settings, "DEFAULT_FROM_NAME", None)
            if default_name:
                label = (
                    f"Default ({default_name} <{default_email}>)"
                    if default_email
                    else f"Default ({default_name})"
                )
            else:
                label = (
                    f"Default ({default_email})"
                    if default_email
                    else "Default configured sender"
                )

            choices = [
                (
                    "DEFAULT",
                    label,
                )
            ]
            initial_value = "DEFAULT"
        self.fields["from_account"] = forms.ChoiceField(
            choices=choices, initial=initial_value, label="Send from"
        )
        if program is not None:
            self.fields["program"].initial = program
            self.fields["program"].widget = forms.HiddenInput()
            self.fields["program"].required = True
            # Population and sorting for student field
            self.fields["student"].program = program
            self.fields["student"].queryset = Student.objects.filter(
                enrollment__program=program
            ).order_by(
                Lower(Coalesce(NullIf("first_name", Value("")), "legal_first_name")),
                Lower("last_name"),
            )

    def clean(self):
        cleaned = super().clean()
        prog = cleaned.get("program")
        if self.fields["program"].widget.__class__ is forms.HiddenInput and not prog:
            raise forms.ValidationError("Program is required.")
        return cleaned


class FeeAssignmentEditForm(forms.Form):
    students = forms.ModelMultipleChoiceField(
        queryset=Student.objects.none(),
        required=False,
        help_text="Choose which students this fee applies to. Leave empty to apply to every student in the program.",
        widget=forms.SelectMultiple(attrs={"size": 15}),
    )

    def __init__(self, *args, program: Program, fee: Fee, **kwargs):
        super().__init__(*args, **kwargs)
        self.program = program
        self.fee = fee
        # Limit to students enrolled in the program, sorted by display name then last name
        # (case-insensitive; uses legal_first_name as fallback)
        self.fields["students"].queryset = Student.objects.filter(
            programs=program
        ).order_by(
            Lower(Coalesce(NullIf("first_name", Value("")), "legal_first_name")),
            Lower("last_name"),
        )
        # Preselect currently assigned students (if any)
        self.fields["students"].initial = fee.assignments.values_list(
            "student_id", flat=True
        )

    def save(self):
        selected_students = list(self.cleaned_data.get("students", []))
        # Clearing assignments means fee applies to everyone
        from .models import FeeAssignment

        # Delete assignments not in selection
        FeeAssignment.objects.filter(fee=self.fee).exclude(
            student__in=selected_students
        ).delete()
        # Ensure assignments exist for selected
        for s in selected_students:
            FeeAssignment.objects.get_or_create(fee=self.fee, student=s)
        return self.fee


# ProgramApplySelectForm and StudentApplicationForm removed; the public
# application flow now lives in the `applications` app.


class FeeForm(forms.ModelForm):
    class Meta:
        model = Fee
        fields = ["program", "name", "amount", "date"]
        widgets = {
            "program": forms.HiddenInput(),
            "date": forms.DateInput(attrs={"type": "date"}),
        }

    def __init__(self, *args, program: Program = None, **kwargs):
        super().__init__(*args, **kwargs)
        if program is not None:
            self.fields["program"].initial = program
            self.fields["program"].required = True


class ProgramDocumentForm(forms.ModelForm):
    """Form for adding/editing a blank document attached to a Program.

    Used on the Program detail/settings page to let lead mentors manage the
    list of forms approved applicants must download, sign, and re-upload
    (Step 9 of the application wizard).
    """

    class Meta:
        from .models import ProgramDocument as _PD

        model = _PD
        fields = [
            "program",
            "name",
            "description",
            "file",
            "is_required",
            "display_order",
            "is_active",
        ]
        widgets = {
            "program": forms.HiddenInput(),
            "name": forms.TextInput(attrs={"class": "form-control"}),
            "description": forms.Textarea(attrs={"class": "form-control", "rows": 3}),
            "file": forms.ClearableFileInput(attrs={"class": "form-control"}),
            "is_required": forms.CheckboxInput(attrs={"class": "form-check-input"}),
            "display_order": forms.NumberInput(
                attrs={"class": "form-control", "min": 0}
            ),
            "is_active": forms.CheckboxInput(attrs={"class": "form-check-input"}),
        }

    def __init__(self, *args, program: Program = None, **kwargs):
        super().__init__(*args, **kwargs)
        if program is not None:
            self.fields["program"].initial = program
            self.fields["program"].required = True
