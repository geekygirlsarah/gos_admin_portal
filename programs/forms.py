from django import forms
from django.utils.safestring import mark_safe
from .models import Student, Program, Parent, Fee, Payment, SlidingScale, School, Mentor


class StudentForm(forms.ModelForm):
    # Expose reverse M2M to Parents so edits on Student reflect on Parent.students
    parents = forms.ModelMultipleChoiceField(
        queryset=Parent.objects.all(),
        required=False,
        help_text='Select the parents/guardians for this student.'
    )

    class Meta:
        model = Student
        # Include all model fields except system-managed ones and M2M-through
        fields = '__all__'
        exclude = ['programs', 'created_at', 'updated_at']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # When editing, pre-populate parents from the reverse relation
        instance = getattr(self, 'instance', None)
        if instance and instance.pk:
            self.fields['parents'].initial = instance.parents.all()

    def save(self, commit=True):
        # Save base fields first
        instance = super().save(commit=False)
        if commit:
            instance.save()
        # After instance exists, sync the reverse M2M to Parents
        if 'parents' in self.cleaned_data:
            # Ensure instance has a PK in case commit=False was used
            if not instance.pk:
                instance.save()
            instance.parents.set(self.cleaned_data['parents'])
        # Return the instance
        return instance


class AddExistingStudentToProgramForm(forms.Form):
    student = forms.ModelChoiceField(
        queryset=Student.objects.all(),
        required=True,
        label='Select student to add',
    )

    def __init__(self, *args, program: Program, **kwargs):
        super().__init__(*args, **kwargs)
        # Exclude students already enrolled in this program
        self.fields['student'].queryset = Student.objects.exclude(
            id__in=program.students.values_list('id', flat=True)
        )


class QuickCreateStudentForm(forms.ModelForm):
    class Meta:
        model = Student
        fields = ['first_name', 'last_name']


class ParentForm(forms.ModelForm):
    class Meta:
        model = Parent
        fields = '__all__'
        exclude = []


class PaymentForm(forms.ModelForm):
    class Meta:
        model = Payment
        fields = ['student', 'fee', 'amount', 'paid_at', 'paid_via', 'check_number', 'camp_hours', 'notes']

    def __init__(self, *args, program: Program, **kwargs):
        super().__init__(*args, **kwargs)
        # Restrict student choices to those enrolled in this program
        self.fields['student'].queryset = Student.objects.filter(programs=program)
        # Restrict fee choices to fees for this program
        self.fields['fee'].queryset = Fee.objects.filter(program=program)


class SlidingScaleForm(forms.ModelForm):
    class Meta:
        model = SlidingScale
        fields = ['student', 'percent', 'family_size', 'adjusted_gross_income', 'is_pending', 'notes']
        labels = {
            'percent': 'Discount percent',
        }
        help_texts = {
            'percent': 'Enter a value between 0 and 100. This percent will discount the total fees for the full program year.',
        }

    def __init__(self, *args, program: Program, **kwargs):
        super().__init__(*args, **kwargs)
        # Restrict to students in this program
        self.fields['student'].queryset = Student.objects.filter(programs=program)

    def clean_percent(self):
        p = self.cleaned_data.get('percent')
        if p is None:
            return p
        if p < 0 or p > 100:
            raise forms.ValidationError('Percent must be between 0 and 100.')
        return p



class SchoolForm(forms.ModelForm):
    class Meta:
        model = School
        fields = ['name']


class MentorForm(forms.ModelForm):
    class Meta:
        model = Mentor
        fields = '__all__'
        exclude = []


class ProgramEmailForm(forms.Form):
    program = forms.ModelChoiceField(queryset=Program.objects.all(), required=False, help_text='Select the program whose contacts you want to email.')
    recipient_groups = forms.MultipleChoiceField(
        required=True,
        choices=[
            ('students', 'Students'),
            ('parents', 'Parents/Guardians'),
            ('mentors', 'Mentors'),
        ],
        widget=forms.CheckboxSelectMultiple,
        help_text=mark_safe('Choose one or more groups to email.'),
    )
    subject = forms.CharField(max_length=255)
    body = forms.CharField(widget=forms.Textarea(attrs={'rows': 12}), help_text='Rich text is supported. Paste content or use the editor.')
    test_email = forms.EmailField(required=False, help_text='Optional: send only to this address for testing.')

    def __init__(self, *args, **kwargs):
        # Allow passing a fixed program via kwarg program
        program = kwargs.pop('program', None)
        super().__init__(*args, **kwargs)
        if program is not None:
            self.fields['program'].initial = program
            self.fields['program'].widget = forms.HiddenInput()
            self.fields['program'].required = True

    def clean(self):
        cleaned = super().clean()
        prog = cleaned.get('program')
        if self.fields['program'].widget.__class__ is forms.HiddenInput and not prog:
            raise forms.ValidationError('Program is required.')
        return cleaned
