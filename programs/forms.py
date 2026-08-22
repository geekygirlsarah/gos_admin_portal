import datetime
from decimal import Decimal

from django import forms
from django.conf import settings
from django.db.models import Value
from django.db.models.functions import Coalesce, Lower, NullIf

from programs.utils import (
    active_students,
    active_students_in_program,
    format_grade,
    get_academic_year_ending,
)

from .models import (
    Adult,
    BackgroundCheck,
    BackgroundCheckType,
    Fee,
    Payment,
    Program,
    School,
    SchoolDistrict,
    SlidingScale,
    Student,
)
from .widgets import DualListboxWidget


class StudentForm(forms.ModelForm):
    # Expose reverse M2M to Adults so edits on Student reflect on Adult.students
    parents = forms.ModelMultipleChoiceField(
        queryset=Adult.objects.filter(is_parent=True),
        required=False,
        help_text="Select the parents/guardians for this student.",
        widget=DualListboxWidget(
            available_label="Available Parents",
            selected_label="Selected Parents",
        ),
    )
    # Primary/secondary contacts are now relationship-row pointers on the
    # model; expose them as Adult pickers for backwards compatibility. The
    # model's property setters keep the through row and pointer in sync.
    primary_contact = forms.ModelChoiceField(
        queryset=Adult.objects.filter(is_parent=True),
        required=False,
        label="Primary contact",
        help_text="Primary parent/guardian, e.g. the one to contact first.",
    )
    secondary_contact = forms.ModelChoiceField(
        queryset=Adult.objects.filter(is_parent=True),
        required=False,
        label="Secondary contact",
        help_text="Secondary parent/guardian.",
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
        # Include all model fields except system-managed ones, M2M-through,
        # and the relationship-row pointers (rendered as Adult pickers above).
        fields = "__all__"
        exclude = [
            "programs",
            "created_at",
            "updated_at",
            "primary_contact_relationship",
            "secondary_contact_relationship",
        ]
        widgets = {
            "date_of_birth": forms.DateInput(attrs={"type": "date"}),
            "andrew_id_expiration": forms.DateInput(attrs={"type": "date"}),
            # Render as clear, clickable checkboxes (fixes empty button appearance)
            "race_ethnicities": forms.CheckboxSelectMultiple(),
            "directory_consent": forms.CheckboxInput(
                attrs={"class": "form-check-input"}
            ),
        }

    def __init__(self, *args, **kwargs):
        user = kwargs.pop("user", None)
        super().__init__(*args, **kwargs)

        # Protect user field from non-Lead Mentors/Admins to avoid accidental disconnection
        # Only apply protection if user is explicitly provided (e.g. from portal views)
        if user is not None:
            is_privileged = (
                user.is_superuser or user.groups.filter(name="LeadMentor").exists()
            )
            if not is_privileged:
                if "user" in self.fields:
                    del self.fields["user"]

        # Ensure sorted dropdowns for adult-related fields; limit to Adults marked as parents
        qs_adults = Adult.objects.filter(is_parent=True).order_by(
            Lower(Coalesce(NullIf("preferred_first_name", Value("")), "first_name")),
            Lower("last_name"),
        )
        # Parents (multi-select used for custom picker)
        self.fields["parents"].queryset = qs_adults
        # Primary/Secondary contact fields (FKs)
        if "primary_contact" in self.fields:
            self.fields["primary_contact"].queryset = qs_adults
        if "secondary_contact" in self.fields:
            self.fields["secondary_contact"].queryset = qs_adults

        # Sort Andrew ID sponsor by first name (Preferred, then legal if no preferred),
        # and limit to Mentors only.
        if "andrew_id_sponsor" in self.fields:
            self.fields["andrew_id_sponsor"].queryset = Adult.objects.filter(
                is_mentor=True
            ).order_by(
                Lower(
                    Coalesce(NullIf("preferred_first_name", Value("")), "first_name")
                ),
                Lower("last_name"),
            )

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
        # Wire primary/secondary contacts (declared form fields, not model
        # fields) onto the instance; the model setters keep the through row
        # and relationship pointer in sync.
        if hasattr(self, "cleaned_data"):
            instance.primary_contact = self.cleaned_data.get("primary_contact")
            instance.secondary_contact = self.cleaned_data.get("secondary_contact")
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
        # Exclude students already enrolled in this program, and keep inactive
        # (graduated) students out of the dropdown.
        # Also sort by first name (coalescing legal name) then last name
        self.fields["student"].queryset = (
            active_students()
            .exclude(id__in=program.students.values_list("id", flat=True))
            .order_by(
                Lower(Coalesce(NullIf("first_name", Value("")), "legal_first_name")),
                Lower("last_name"),
            )
        )


class QuickCreateStudentForm(forms.ModelForm):
    class Meta:
        model = Student
        fields = ["first_name", "last_name"]


class AdultForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        user = kwargs.pop("user", None)
        super().__init__(*args, **kwargs)

        # role has a model-level default so it should not be required in the form
        if "role" in self.fields:
            self.fields["role"].required = False

        # Protect role flags, active status, and students list from non-Lead Mentors/Admins
        # Only apply protection if user is explicitly provided (e.g. from portal views)
        if user is not None:
            is_privileged = (
                user.is_superuser or user.groups.filter(name="LeadMentor").exists()
            )

            if not is_privileged:
                protected_fields = [
                    "is_parent",
                    "is_mentor",
                    "is_alumni",
                    "login_enabled",
                    "mentor_active",
                    "students",
                ]
                for field in protected_fields:
                    if field in self.fields:
                        del self.fields[field]

        # Sort students by first name (Preferred, then legal if no preferred),
        # and exclude inactive (graduated) students.
        if "students" in self.fields:
            self.fields["students"].queryset = active_students().order_by(
                Lower(Coalesce(NullIf("first_name", Value("")), "legal_first_name")),
                Lower("last_name"),
            )

        # Sort Andrew ID sponsor by first name (Preferred, then legal if no preferred),
        # and limit to Mentors only.
        if "andrew_id_sponsor" in self.fields:
            self.fields["andrew_id_sponsor"].queryset = Adult.objects.filter(
                is_mentor=True
            ).order_by(
                Lower(
                    Coalesce(NullIf("preferred_first_name", Value("")), "first_name")
                ),
                Lower("last_name"),
            )

    def validate_unique(self):
        # personal_email is intentionally non-unique (two adults may share one
        # email, e.g. a mother and father). Skip the email uniqueness check.
        exclude = self._get_validation_exclusions()
        exclude.add("personal_email")
        try:
            self.instance.validate_unique(exclude=exclude)
        except forms.ValidationError as e:
            self._update_errors(e)

    class Meta:
        model = Adult
        fields = [
            "first_name",
            "preferred_first_name",
            "last_name",
            "pronouns",
            "personal_email",
            "phone_number",
            "phone_type",
            "can_receive_texts",
            "address",
            "city",
            "state",
            "zip_code",
            "email_updates",
            "is_parent",
            "is_mentor",
            "is_alumni",
            "students",
            "login_enabled",
            "mentor_active",
            # Mentor-specific
            "start_year",
            "role",
            "photo",
            "emergency_contact_name",
            "emergency_contact_phone",
            "on_discord",
            "discord_username",
            "has_cmu_id_card",
            "has_cmu_building_access",
            # Andrew ID
            "andrew_id",
            "andrew_email",
            "andrew_id_expiration",
            "andrew_id_sponsor",
            # Alumni-specific
            "college",
            "field_of_study",
            "employer",
            "job_title",
            "ok_to_contact",
            "notes",
        ]
        widgets = {
            "andrew_id_expiration": forms.DateInput(attrs={"type": "date"}),
            "students": DualListboxWidget(
                available_label="Available Students",
                selected_label="Selected Students",
            ),
        }


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
        # Restrict student choices to those actively enrolled in this program
        # (excludes inactive enrollments and graduated students)
        self.fields["student"].queryset = active_students_in_program(program).order_by(
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
            "family_size",
            "adjusted_gross_income",
            "percent",
            "date",
            "expiration_date",
            "notes",
        ]
        labels = {
            "family_size": "Household Size",
            "adjusted_gross_income": "Adjusted Gross Income (AGI)",
            "percent": "Discount percent",
            "date": "Effective date",
            "expiration_date": "Expiration date",
        }
        help_texts = {
            "family_size": "Total number of people in the student's household.",
            "adjusted_gross_income": "The household's adjusted gross income (AGI), as reported on their most recent tax return.",
            "percent": "Enter a value between 0 and 100. This percent will discount applicable fees across all of the student's programs.",
            "date": "Only fees on or after this date will be discounted. Leave blank to apply to all fees.",
            "expiration_date": "The discount stops applying after this date. Leave blank for no expiration.",
        }
        widgets = {
            "family_size": forms.NumberInput(attrs={"min": "1"}),
            "adjusted_gross_income": forms.NumberInput(
                attrs={"step": "0.01", "min": "0"}
            ),
            "date": forms.DateInput(attrs={"type": "date"}),
            "expiration_date": forms.DateInput(attrs={"type": "date"}),
        }

    def __init__(self, *args, program=None, **kwargs):
        super().__init__(*args, **kwargs)
        # Restrict to students in the program when reached from a specific
        # program's page; otherwise list all students (the sliding scale
        # itself applies across all of the student's programs). Inactive
        # (graduated) students are always excluded.
        if program is not None:
            students = active_students_in_program(program)
        else:
            students = active_students()
        self.fields["student"].queryset = students.order_by(
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


class MultipleFileInput(forms.ClearableFileInput):
    allow_multiple_selected = True


class MultipleFileField(forms.FileField):
    """A FileField that accepts multiple files, following Django's documented
    pattern for multi-file uploads (single ClearableFileInput doesn't support
    the `multiple` HTML attribute)."""

    def __init__(self, *args, **kwargs):
        kwargs.setdefault("widget", MultipleFileInput())
        super().__init__(*args, **kwargs)

    def clean(self, data, initial=None):
        single_file_clean = super().clean
        if isinstance(data, (list, tuple)):
            return [single_file_clean(d, initial) for d in data]
        return single_file_clean(data, initial)


class SlidingScaleApplicationForm(forms.Form):
    """Parent-facing questionnaire used to apply for the sliding scale."""

    family_size = forms.IntegerField(
        min_value=1,
        label="Household Size",
        help_text="Total number of people in your household.",
        widget=forms.NumberInput(attrs={"class": "form-control"}),
    )
    adjusted_gross_income = forms.DecimalField(
        min_value=Decimal("0"),
        max_digits=10,
        decimal_places=2,
        label="Adjusted Gross Income",
        help_text="Your household's adjusted gross income (AGI), as reported on your most recent tax return.",
        widget=forms.NumberInput(attrs={"class": "form-control", "step": "0.01"}),
    )
    documents = MultipleFileField(
        required=False,
        widget=MultipleFileInput(attrs={"multiple": True, "class": "form-control"}),
        help_text="Upload tax forms or other income documentation. We recommend the first page of the IRS 1040 form but can take other forms. Please block out social security numbers and birthdates. Uploads are kept private and encrypted, and are permanently deleted once your application is processed.",
    )
    notes = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"rows": 3, "class": "form-control"}),
        label="Additional Notes (optional)",
    )


class SchoolForm(forms.ModelForm):
    class Meta:
        model = School
        fields = ["name", "district", "street_address", "city", "state", "zip_code"]


class SchoolDistrictForm(forms.ModelForm):
    class Meta:
        model = SchoolDistrict
        fields = ["name"]


class SchoolMergeForm(forms.Form):
    """Merge one school into another.

    ``keep`` is the canonical school that survives. ``source`` is the school
    to fold into ``keep`` and delete. Both are chosen via radio buttons on a
    single table of schools so it's easy to tell which is which.
    """

    keep = forms.ModelChoiceField(
        queryset=School.objects.all(),
        widget=forms.RadioSelect,
    )
    source = forms.ModelChoiceField(
        queryset=School.objects.all(),
        widget=forms.RadioSelect,
    )

    def clean(self):
        cleaned = super().clean()
        keep = cleaned.get("keep")
        source = cleaned.get("source")
        if keep and source and keep.pk == source.pk:
            self.add_error("source", "Choose a different school to merge in.")
        return cleaned


class ParentMergeForm(forms.Form):
    """Merge two parent/adult records that represent the same person.

    ``keep`` is the surviving parent. ``source`` is the parent to fold into
    ``keep`` and delete. Both are chosen via radio buttons.
    """

    keep = forms.ModelChoiceField(
        queryset=Adult.objects.none(),
        widget=forms.RadioSelect,
    )
    source = forms.ModelChoiceField(
        queryset=Adult.objects.none(),
        widget=forms.RadioSelect,
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        qs_adults = Adult.objects.filter(is_parent=True).order_by(
            Lower(Coalesce(NullIf("preferred_first_name", Value("")), "first_name")),
            Lower("last_name"),
        )
        self.fields["keep"].queryset = qs_adults
        self.fields["source"].queryset = qs_adults

    def clean(self):
        cleaned = super().clean()
        keep = cleaned.get("keep")
        source = cleaned.get("source")
        if keep and source and keep.pk == source.pk:
            self.add_error("source", "Choose a different parent to merge in.")
        return cleaned


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
        from .models import Fee, Payment
        from .utils import get_active_sliding_scale
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

        sliding = get_active_sliding_scale(student)
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
            # Population and sorting for student field; only actively enrolled
            # (non-graduated) students are selectable.
            self.fields["student"].program = program
            self.fields["student"].queryset = active_students_in_program(
                program
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
        # Limit to actively enrolled students in the program, sorted by display
        # name then last name (case-insensitive; uses legal_first_name as
        # fallback). Inactive (graduated/dropped) students are excluded.
        self.fields["students"].queryset = active_students_in_program(program).order_by(
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

        # Only manage assignments for students selectable in this form; preserve
        # existing assignments to inactive (graduated/dropped) students.
        selectable_ids = list(
            self.fields["students"].queryset.values_list("id", flat=True)
        )
        # Delete assignments not in selection
        FeeAssignment.objects.filter(
            fee=self.fee, student_id__in=selectable_ids
        ).exclude(student__in=selected_students).delete()
        # Ensure assignments exist for selected
        for s in selected_students:
            FeeAssignment.objects.get_or_create(fee=self.fee, student=s)
        return self.fee


# ProgramApplySelectForm and StudentApplicationForm removed; the public
# application flow now lives in the `applications` app.


class FeeForm(forms.ModelForm):
    class Meta:
        model = Fee
        fields = ["program", "name", "amount", "effective_date", "due_date"]
        widgets = {
            "program": forms.HiddenInput(),
            "effective_date": forms.DateInput(attrs={"type": "date"}),
            "due_date": forms.DateInput(attrs={"type": "date"}),
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


class BackgroundChecksForm(forms.Form):
    """Inline editor for a holder's PA background clearances.

    Presents one row per clearance type (``cleared_<type>`` checkbox and
    ``obtained_<type>`` date). Saving creates/updates/deletes the matching
    ``BackgroundCheck`` rows for exactly one student or adult.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for check_type in BackgroundCheckType.values:
            self.fields[f"cleared_{check_type}"] = forms.BooleanField(
                required=False,
                label="Cleared",
                widget=forms.CheckboxInput(attrs={"class": "form-check-input"}),
            )
            self.fields[f"obtained_{check_type}"] = forms.DateField(
                required=False,
                label="Obtained",
                widget=forms.DateInput(attrs={"type": "date", "class": "form-control"}),
            )

    @property
    def rows(self):
        """Return per-type (label, cleared_field, obtained_field) tuples."""
        return [
            (
                BackgroundCheckType(check_type).label,
                self[f"cleared_{check_type}"],
                self[f"obtained_{check_type}"],
            )
            for check_type in BackgroundCheckType.values
        ]

    def _existing_rows(self, student, adult):
        return {
            bc.check_type: bc
            for bc in BackgroundCheck.objects.filter(
                student=student if student else None,
                adult=adult if adult else None,
            )
        }

    def initial_from_holder(self, student=None, adult=None):
        """Pre-populate the form from a holder's existing clearances."""
        for check_type, check in self._existing_rows(student, adult).items():
            self.fields[f"cleared_{check_type}"].initial = check.cleared
            self.fields[f"obtained_{check_type}"].initial = check.obtained_date

    def save(self, student=None, adult=None):
        if bool(student) == bool(adult):
            raise ValueError(
                "A background check form must target exactly one student or adult."
            )
        if not self.is_valid():
            raise ValueError("Cannot save an invalid background check form.")
        existing = self._existing_rows(student, adult)
        cleaned = self.cleaned_data
        for check_type in BackgroundCheckType.values:
            cleared = cleaned.get(f"cleared_{check_type}")
            obtained = cleaned.get(f"obtained_{check_type}")
            row = existing.get(check_type)
            if not cleared:
                if row is not None:
                    row.delete()
                continue
            if row is None:
                row = BackgroundCheck(
                    student=student if student else None,
                    adult=adult if adult else None,
                    check_type=check_type,
                )
            row.cleared = True
            row.obtained_date = obtained
            row.save()


class MentorAgreementForm(forms.ModelForm):
    """Form for creating / editing a Mentor Agreement from Portal Settings.

    Supports both markdown content and document (PDF) uploads.  When editing an
    existing agreement the version is auto-incremented in the view; the form
    does **not** manage version numbers directly.
    """

    class Meta:
        from .models import MentorAgreement as _MA

        model = _MA
        fields = [
            "slug",
            "title",
            "content",
            "document",
            "effective_date",
            "is_active",
        ]
        widgets = {
            "slug": forms.TextInput(
                attrs={
                    "class": "form-control",
                    "placeholder": "e.g. data-access-policy",
                }
            ),
            "title": forms.TextInput(attrs={"class": "form-control"}),
            "content": forms.Textarea(attrs={"class": "form-control", "rows": 10}),
            "document": forms.ClearableFileInput(attrs={"class": "form-control"}),
            "effective_date": forms.DateInput(
                attrs={"class": "form-control", "type": "date"}
            ),
            "is_active": forms.CheckboxInput(attrs={"class": "form-check-input"}),
        }
