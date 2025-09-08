from django import forms
from .models import Student, Program, Parent, Fee, Payment, SlidingScale, School


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
        fields = ['student', 'amount', 'notes']

    def __init__(self, *args, program: Program, **kwargs):
        super().__init__(*args, **kwargs)
        # Restrict to students in this program
        self.fields['student'].queryset = Student.objects.filter(programs=program)



class SchoolForm(forms.ModelForm):
    class Meta:
        model = School
        fields = ['name']
