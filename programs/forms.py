from django import forms
from django.conf import settings
from django.db.models.functions import Coalesce, Lower
from django.utils.safestring import mark_safe

from .models import (
    Adult,
    Enrollment,
    Fee,
    Payment,
    Program,
    School,
    SlidingScale,
    Student,
    StudentApplication,
)


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
            import datetime

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
            except (TypeError, ValueError):
                g = None
            if g is not None and 0 <= g <= 12:
                import datetime

                today = datetime.date.today()
                end_year = today.year + (1 if today.month >= 7 else 0)
                if g == 0:
                    grad_year = end_year + 13
                else:
                    grad_year = end_year + max(0, 12 - g)
                # assign to instance via cleaned_data for model save
                self.cleaned_data["graduation_year"] = grad_year
                if "graduation_year" in self.fields:
                    self.instance.graduation_year = grad_year
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
        self.fields["student"].queryset = Student.objects.exclude(
            id__in=program.students.values_list("id", flat=True)
        )


class QuickCreateStudentForm(forms.ModelForm):
    class Meta:
        model = Student
        fields = ["first_name", "last_name"]


class ParentForm(forms.ModelForm):
    class Meta:
        model = Adult
        # Exclude mentor-specific "role" so the Parent form doesn't require it (not rendered in the UI).
        # The model default ('mentor') is irrelevant for parents and caused validation to fail when missing.
        fields = "__all__"
        exclude = ["role"]


class AdultForm(forms.ModelForm):
    class Meta:
        model = Adult
        fields = [
            "first_name",
            "preferred_first_name",
            "last_name",
            "pronouns",
            "email",
            "personal_email",
            "phone_number",
            "cell_phone",
            "home_phone",
            "email_updates",
            "is_parent",
            "is_mentor",
            "is_alumni",
            "students",
            "active",
            "alumni_email",
            "college",
            "field_of_study",
            "employer",
            "job_title",
            "ok_to_contact",
            "notes",
        ]


class ApplicationParentProfileForm(forms.ModelForm):
    class Meta:
        model = Adult
        fields = [
            "first_name",
            "preferred_first_name",
            "last_name",
            "pronouns",
            "email",
            "personal_email",
            "phone_number",
            "cell_phone",
            "home_phone",
            "email_updates",
        ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name, field in self.fields.items():
            if name == "email_updates":
                field.widget.attrs.update({"class": "form-check-input"})
            else:
                field.widget.attrs.update({"class": "form-control"})

        # Default email_updates to True for new forms
        if not self.instance or not self.instance.pk:
            self.fields["email_updates"].initial = True

        # Disable personal_email if it's prefilled (verified)
        if self.initial.get("personal_email") or (
            self.instance and self.instance.personal_email
        ):
            self.fields["personal_email"].widget.attrs["readonly"] = True
            self.fields["personal_email"].disabled = True

    def clean_personal_email(self):
        email = self.cleaned_data.get("personal_email")
        if self.instance and self.instance.pk and self.instance.personal_email:
            if email != self.instance.personal_email:
                return self.instance.personal_email
        return email

    def save(self, commit=True):
        instance = super().save(commit=False)
        instance.is_parent = True
        if commit:
            instance.save()
        return instance


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
            "fee",
        ]
        widgets = {
            "paid_on": forms.DateInput(attrs={"type": "date"}),
        }

    def __init__(self, *args, program: Program, **kwargs):
        super().__init__(*args, **kwargs)
        # Restrict student choices to those enrolled in this program
        self.fields["student"].queryset = Student.objects.filter(programs=program)
        # Fee is optional; restrict choices to this program when present
        if "fee" in self.fields:
            self.fields["fee"].required = False
            self.fields["fee"].queryset = Fee.objects.filter(program=program)
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
        }
        help_texts = {
            "percent": "Enter a value between 0 and 100. This percent will discount the total fees for the full program year.",
        }
        widgets = {
            "date": forms.DateInput(attrs={"type": "date"}),
        }

    def __init__(self, *args, program: Program, **kwargs):
        super().__init__(*args, **kwargs)
        # Restrict to students in this program
        self.fields["student"].queryset = Student.objects.filter(programs=program)

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
            "year",
            "start_date",
            "end_date",
            "active",
            "features",
        ]
        widgets = {
            "start_date": forms.DateInput(attrs={"type": "date"}),
            "end_date": forms.DateInput(attrs={"type": "date"}),
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
            choices = [
                (
                    "DEFAULT",
                    (
                        f"Default ({default_email})"
                        if default_email
                        else "Default configured sender"
                    ),
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
    include_zero_balances = forms.BooleanField(
        required=False, initial=False, label="Email families with a $0.00 balance"
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
            choices = [
                (
                    "DEFAULT",
                    (
                        f"Default ({default_email})"
                        if default_email
                        else "Default configured sender"
                    ),
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
        # Limit to students enrolled in the program (sorted by displayed first name then last name, case-insensitive; use legal_first_name fallback)
        self.fields["students"].queryset = Student.objects.filter(
            programs=program
        ).order_by(
            Lower(Coalesce("first_name", "legal_first_name")), Lower("last_name")
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


class ProgramApplySelectForm(forms.Form):
    program = forms.ModelChoiceField(
        queryset=Program.objects.filter(active=True),
        label="Select a Program",
        widget=forms.RadioSelect(attrs={"class": "form-check-input"}),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Show all active programs so they can be grouped by status in the template
        self.fields["program"].queryset = Program.objects.filter(active=True).order_by(
            "start_date"
        )


class ApplicationRoleForm(forms.Form):
    ROLE_CHOICES = [
        ("parent", "I am a Parent/Guardian applying for a student"),
        ("student", "I am a Student applying for myself"),
    ]
    role = forms.ChoiceField(
        choices=ROLE_CHOICES,
        widget=forms.RadioSelect(attrs={"class": "form-check-input"}),
        label="Who are you?",
    )


class ApplicationIdentityForm(forms.Form):
    email = forms.EmailField(
        label="Email Address",
        widget=forms.EmailInput(
            attrs={"class": "form-control", "placeholder": "Enter your email"}
        ),
    )


class ApplicationStudentIdentityForm(forms.Form):
    legal_first_name = forms.CharField(max_length=150, label="Legal First Name")
    last_name = forms.CharField(max_length=150, label="Last Name")
    parent_email = forms.EmailField(label="Parent Email", required=False)

    def __init__(self, *args, **kwargs):
        show_parent_email = kwargs.pop("show_parent_email", True)
        super().__init__(*args, **kwargs)
        if not show_parent_email:
            self.fields["parent_email"].widget = forms.HiddenInput()
            self.fields["parent_email"].required = False
        else:
            self.fields["parent_email"].required = True


class ApplicationOTPForm(forms.Form):
    otp = forms.CharField(
        label="Verification Code",
        max_length=6,
        widget=forms.TextInput(
            attrs={"class": "form-control", "placeholder": "6-digit code"}
        ),
    )


class ApplicationStudentSelectionForm(forms.Form):
    student = forms.ChoiceField(
        label="Select Student",
        widget=forms.RadioSelect(attrs={"class": "form-check-input"}),
    )

    def __init__(self, *args, parent=None, program=None, **kwargs):
        super().__init__(*args, **kwargs)
        choices = []
        if parent:
            for student in parent.students.all():
                # Check if already applied or enrolled
                is_enrolled = Enrollment.objects.filter(
                    student=student, program=program
                ).exists()
                is_applied = (
                    StudentApplication.objects.filter(
                        program=program,
                        legal_first_name=student.legal_first_name,
                        last_name=student.last_name,
                        parent_email=parent.email,
                    )
                    .exclude(status="draft")
                    .exists()
                )

                disabled = is_enrolled or is_applied
                label = f"{student.legal_first_name} {student.last_name}"
                if is_enrolled:
                    label += " (Already Enrolled)"
                elif is_applied:
                    label += " (Application Pending)"

                choices.append((student.id, label))

        choices.append(("new", "Add New Student"))
        self.fields["student"].choices = choices


class ApplicationHouseholdForm(forms.ModelForm):
    class Meta:
        model = Adult
        fields = [
            "first_name",
            "preferred_first_name",
            "last_name",
            "pronouns",
            "email",
            "personal_email",
            "phone_number",
            "cell_phone",
            "home_phone",
            "email_updates",
        ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name, field in self.fields.items():
            if name == "email_updates":
                field.widget.attrs.update({"class": "form-check-input"})
            else:
                field.widget.attrs.update({"class": "form-control"})

        # Default email_updates to True for new forms
        if not self.instance or not self.instance.pk:
            self.fields["email_updates"].initial = True

    def save(self, commit=True):
        instance = super().save(commit=False)
        instance.is_parent = True
        if commit:
            instance.save()
        return instance


class StudentApplicationForm(forms.ModelForm):
    # grade selector to compute graduation_year like StudentForm
    GRADE_CHOICES = [(0, "K")] + [(i, str(i)) for i in range(1, 13)]
    grade_selector = forms.ChoiceField(
        choices=[("", "—")] + [(str(v), label) for v, label in GRADE_CHOICES],
        required=False,
        label="Grade (K–12)",
    )

    class Meta:
        model = StudentApplication
        fields = [
            "program",
            "first_name",
            "pronouns",
            "date_of_birth",
            "address",
            "city",
            "state",
            "zip_code",
            "cell_phone_number",
            "personal_email",
            "school",
            "grade_selector",
            "graduation_year",
            "race_ethnicity",
            "tshirt_size",
            "on_discord",
            "discord_handle",
        ]
        widgets = {
            "program": forms.HiddenInput(),
            "date_of_birth": forms.DateInput(attrs={"type": "date"}),
        }

    def __init__(self, *args, **kwargs):
        is_parent_flow = kwargs.pop("is_parent_flow", False)
        super().__init__(*args, **kwargs)

        if is_parent_flow:
            # In parent flow, we might be editing an existing student,
            # so we should show legal_first_name and last_name if they are new,
            # but maybe the user wants them gone from the form ENTIRELY.
            # If so, I need to collect them in a step before 4_parent_student_details if "new".
            pass

        # Prefill grade_selector from grad year
        gy = self.instance.graduation_year if getattr(self, "instance", None) else None
        if gy:
            import datetime

            today = datetime.date.today()
            end_year = today.year + (1 if today.month >= 7 else 0)
            years_remaining = gy - end_year
            if years_remaining == 13:
                grade_str = "0"
            else:
                grade = 12 - years_remaining
                grade_str = str(grade) if 0 <= grade <= 12 else ""
            if grade_str:
                self.fields["grade_selector"].initial = grade_str
        if "graduation_year" in self.fields:
            self.fields["graduation_year"].help_text = (
                "Auto-calculated from Grade, but you may override if needed."
            )

    def clean(self):
        cleaned = super().clean()

        # Student email recommended for older grades but not required for younger ones
        grade_val = cleaned.get("grade_selector")
        personal_email = cleaned.get("personal_email")
        if grade_val:
            try:
                g = int(grade_val)
                if g >= 6 and not personal_email:
                    # Not raising error, just note it if we had a way to show recommendations
                    # For now, just follow "never Required for younger ones"
                    pass
            except ValueError:
                pass

        # Basic validation to ensure contact info present
        if (
            not cleaned.get("personal_email")
            and not cleaned.get("cell_phone_number")
            and not self.instance.pk
        ):
            # For students, we might not have these yet if they are young
            pass

        return cleaned

    def save(self, commit=True):
        grade_val = (
            self.cleaned_data.get("grade_selector")
            if hasattr(self, "cleaned_data")
            else None
        )
        if grade_val not in (None, "", "None"):
            try:
                g = int(grade_val)
            except (TypeError, ValueError):
                g = None
            if g is not None and 0 <= g <= 12:
                import datetime

                today = datetime.date.today()
                end_year = today.year + (1 if today.month >= 7 else 0)
                grad_year = end_year + (13 if g == 0 else max(0, 12 - g))
                self.cleaned_data["graduation_year"] = grad_year
                if "graduation_year" in self.fields:
                    self.instance.graduation_year = grad_year
        return super().save(commit=commit)


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
