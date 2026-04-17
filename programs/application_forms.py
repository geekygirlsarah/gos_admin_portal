import random
import string
from django import forms
from django.conf import settings
from django.core.mail import send_mail
from django.utils import timezone
from .models import Application, DisclosureForm, ApplicationDisclosure, ApplicationFieldConfig, Program, Student, Adult, School, RaceEthnicity, RELATIONSHIP_CHOICES

def generate_application_code():
    return ''.join(random.choices(string.ascii_uppercase + string.digits, k=8))

def generate_otp():
    return ''.join(random.choices(string.digits, k=6))

class ProgramModelChoiceField(forms.ModelChoiceField):
    def label_from_instance(self, obj):
        if obj.start_date and obj.end_date:
            return f"{obj.name} ({obj.start_date} to {obj.end_date})"
        return obj.name

class ApplicationWizardForm(forms.Form):
    # This will be base for various steps
    pass

class RoleSelectionForm(forms.Form):
    ROLE_CHOICES = [
        ('student_parent', 'Student or Parent/Guardian'),
        ('mentor_volunteer', 'Mentor or Volunteer'),
    ]
    role_type = forms.ChoiceField(choices=ROLE_CHOICES, widget=forms.RadioSelect)
    program = ProgramModelChoiceField(
        queryset=Program.objects.filter(
            active=True, 
            start_date__gt=timezone.now().date()
        )
    )

class IdentityForm(forms.Form):
    email = forms.EmailField(label="Email Address")

class VerifyOTPForm(forms.Form):
    otp = forms.CharField(max_length=6, label="Enter 6-digit OTP")

class VerifyParentOTPForm(forms.Form):
    otp = forms.CharField(max_length=6, label="Enter 6-digit Parent OTP")

class StudentInfoForm(forms.Form):
    legal_first_name = forms.CharField(max_length=150)
    first_name = forms.CharField(max_length=150, required=False)
    last_name = forms.CharField(max_length=150)
    pronouns = forms.CharField(max_length=50, required=False)
    date_of_birth = forms.DateField(widget=forms.DateInput(attrs={'type': 'date'}), required=False)
    
    # Grade for graduation year calculation
    GRADE_CHOICES = [('', '—')] + [('0', 'K')] + [(str(i), str(i)) for i in range(1, 13)]
    grade = forms.ChoiceField(choices=GRADE_CHOICES, required=False)

    address = forms.CharField(max_length=255, required=False)
    city = forms.CharField(max_length=100, required=False)
    state = forms.CharField(max_length=50, required=False)
    zip_code = forms.CharField(max_length=20, required=False)
    cell_phone_number = forms.CharField(max_length=30, required=False)
    personal_email = forms.EmailField(required=False)
    
    school = forms.ModelChoiceField(queryset=School.objects.all(), required=False)
    tshirt_size = forms.CharField(max_length=10, required=False)
    
    # Handoff trigger
    parent_email = forms.EmailField(required=True, help_text="Required for all students to finish their application.")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Apply field configs
        configs = {c.field_name: c for c in ApplicationFieldConfig.objects.all()}
        for field_name, field in self.fields.items():
            if field_name in configs:
                config = configs[field_name]
                if not config.is_enabled:
                    # We might want to keep it in the form but hidden if we want to preserve data
                    field.widget = forms.HiddenInput()
                    field.required = False
                else:
                    field.required = config.is_required

class ParentInfoForm(forms.Form):
    # Primary Parent
    p1_first_name = forms.CharField(max_length=150, label="Primary Parent First Name")
    p1_last_name = forms.CharField(max_length=150, label="Primary Parent Last Name")
    p1_email = forms.EmailField(label="Primary Parent Email")
    p1_phone = forms.CharField(max_length=30, label="Primary Parent Phone")
    p1_relationship = forms.ChoiceField(choices=RELATIONSHIP_CHOICES, label="Relationship to Student")
    
    # Secondary Parent (Optional)
    p2_first_name = forms.CharField(max_length=150, required=False, label="Secondary Parent First Name")
    p2_last_name = forms.CharField(max_length=150, required=False, label="Secondary Parent Last Name")
    p2_email = forms.EmailField(required=False, label="Secondary Parent Email")
    p2_phone = forms.CharField(max_length=30, required=False, label="Secondary Parent Phone")
    p2_relationship = forms.ChoiceField(choices=RELATIONSHIP_CHOICES, required=False, label="Relationship to Student")

class MentorInfoForm(forms.Form):
    legal_first_name = forms.CharField(max_length=150)
    first_name = forms.CharField(max_length=150, required=False)
    last_name = forms.CharField(max_length=150)
    cell_phone_number = forms.CharField(max_length=30, required=False)
    
    # Clearances etc.
    has_paca_clearance = forms.BooleanField(required=False)
    has_patch_clearance = forms.BooleanField(required=False)
    has_fbi_clearance = forms.BooleanField(required=False)

class DisclosureUploadForm(forms.Form):
    def __init__(self, *args, application=None, **kwargs):
        super().__init__(*args, **kwargs)
        if application:
            # Add file fields for each required disclosure
            role = 'student' if application.role_type == 'student_parent' else 'mentor'
            # For student_parent, we might need both student and parent disclosures
            # Actually user role_type determines the set of disclosures
            # If student_parent and handoff, maybe both?
            
            # Simple approach: find all active disclosures for this role (or 'all')
            # and create a file field for each.
            role_filters = ['all']
            if application.role_type == 'student_parent':
                role_filters.extend(['student', 'parent'])
            else:
                role_filters.append('mentor')
                
            forms_needed = DisclosureForm.objects.filter(required_for_role__in=role_filters, is_active=True)
            for df in forms_needed:
                self.fields[f'disclosure_{df.pk}'] = forms.FileField(label=f"Upload signed: {df.name}")
