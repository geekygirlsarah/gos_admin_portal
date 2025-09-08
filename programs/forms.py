from django import forms
from .models import Student, Program, Parent


class StudentForm(forms.ModelForm):
    class Meta:
        model = Student
        # Include all model fields except system-managed ones and M2M-through
        fields = '__all__'
        exclude = ['programs', 'created_at', 'updated_at']


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
