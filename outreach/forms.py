from django import forms
from outreach.models import OutreachEvent

class OutreachEventForm(forms.ModelForm):
    class Meta:
        model = OutreachEvent
        fields = [
            'name',
            'location_name',
            'location_address',
            'start_date',
            'start_time',
            'end_date',
            'end_time',
            'description',
            'max_champions',
            'max_helpers',
        ]
        widgets = {
            'start_date': forms.DateInput(attrs={'type': 'date'}),
            'end_date': forms.DateInput(attrs={'type': 'date'}),
            'start_time': forms.TimeInput(attrs={'type': 'time'}),
            'end_time': forms.TimeInput(attrs={'type': 'time'}),
        }
