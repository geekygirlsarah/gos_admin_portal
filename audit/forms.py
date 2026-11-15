from django import forms

from .events import AuditEvent
from .models import AuditLog


class OrganizationAuditFilterForm(forms.Form):
    DEFAULT_PAGE_SIZE = 50

    search = forms.CharField(
        label="Search",
        required=False,
        widget=forms.TextInput(
            attrs={"class": "form-control", "placeholder": "Person, record, or details"}
        ),
    )
    event = forms.ChoiceField(
        label="What happened",
        choices=[("", "All activities")] + list(AuditEvent.choices),
        required=False,
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    outcome = forms.ChoiceField(
        label="Result",
        choices=[("", "All results")] + AuditLog.OUTCOME_CHOICES,
        required=False,
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    date_from = forms.DateField(
        label="From date",
        required=False,
        widget=forms.DateInput(attrs={"type": "date", "class": "form-control"}),
    )
    date_to = forms.DateField(
        label="Through date",
        required=False,
        widget=forms.DateInput(attrs={"type": "date", "class": "form-control"}),
    )
    page_size = forms.TypedChoiceField(
        label="Actions per page",
        choices=[(size, str(size)) for size in (50, 100, 200, 500)],
        coerce=int,
        initial=DEFAULT_PAGE_SIZE,
        widget=forms.Select(attrs={"class": "form-select"}),
    )

    def clean(self):
        cleaned = super().clean()
        start = cleaned.get("date_from")
        end = cleaned.get("date_to")
        if start and end and start > end:
            self.add_error("date_to", "Choose a date on or after the from date.")
        return cleaned
