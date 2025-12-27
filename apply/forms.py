from django import forms

from programs.models import Program, School

from .models import StudentApplication


class ProgramSelectionForm(forms.Form):
    program = forms.ModelChoiceField(
        queryset=Program.objects.filter(active=True),
        widget=forms.RadioSelect,
        empty_label=None,
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from django.utils import timezone

        today = timezone.localdate()
        # Show future and current programs, exclude past programs
        # A program is past if end_date < today
        self.fields["program"].queryset = (
            Program.objects.filter(active=True)
            .exclude(end_date__lt=today)
            .order_by("start_date")
        )


class RoleSelectionForm(forms.Form):
    ROLE_CHOICES = [
        ("student", "I am a student filling out the application"),
        ("parent", "I am a parent/guardian filling out the application"),
    ]
    role = forms.ChoiceField(choices=ROLE_CHOICES, widget=forms.RadioSelect)


class StudentInfoForm(forms.ModelForm):
    class Meta:
        model = StudentApplication
        fields = [
            "preferred_first_name",
            "legal_first_name",
            "last_name",
            "date_of_birth",
            "address",
            "city",
            "state",
            "zip_code",
            "phone_number",
            "email",
            "school",
            "grade",
            "race_ethnicity",
            "tshirt_size",
            "allergies",
            "medical_conditions",
        ]
        widgets = {
            "date_of_birth": forms.DateInput(attrs={"type": "date"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["school"].queryset = School.objects.all().order_by("name")


class StudentEssayForm(forms.ModelForm):
    class Meta:
        model = StudentApplication
        fields = [
            "interest_reason",
            "hope_to_gain",
            "past_impact_student",
            "past_impact_team",
        ]


class ParentVerificationForm(forms.Form):
    email = forms.EmailField()
    otp = forms.CharField(
        max_length=6,
        required=False,
        help_text="Enter the 6-digit code sent to your email",
    )


class Parent1InfoForm(forms.ModelForm):
    class Meta:
        model = StudentApplication
        fields = [
            "parent1_preferred_first_name",
            "parent1_legal_first_name",
            "parent1_last_name",
            "parent1_phone_number",
            "parent1_email",
            "parent1_email_notices",
        ]


class Parent2InfoForm(forms.ModelForm):
    parent2_email_notices = forms.BooleanField(required=False)

    class Meta:
        model = StudentApplication
        fields = [
            "parent2_preferred_first_name",
            "parent2_legal_first_name",
            "parent2_last_name",
            "parent2_phone_number",
            "parent2_email",
            "parent2_email_notices",
        ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Make all fields optional for Parent 2
        for field in self.fields:
            self.fields[field].required = False
