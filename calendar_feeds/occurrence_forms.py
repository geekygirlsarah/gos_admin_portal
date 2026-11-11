from django import forms


class OccurrenceForm(forms.Form):
    title = forms.CharField(max_length=300)
    location = forms.CharField(max_length=500, required=False)
    description = forms.CharField(required=False, widget=forms.Textarea)
    start_datetime = forms.DateTimeField(
        widget=forms.DateTimeInput(
            attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M"
        )
    )
    end_datetime = forms.DateTimeField(
        widget=forms.DateTimeInput(
            attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M"
        )
    )

    def __init__(self, *args, event, **kwargs):
        super().__init__(*args, **kwargs)
        self.event = event

    def clean(self):
        cleaned = super().clean()
        start, end = cleaned.get("start_datetime"), cleaned.get("end_datetime")
        if start and end and end <= start:
            self.add_error("end_datetime", "End time must be after start time.")
        for field in ("location", "description"):
            if getattr(self.event, field) and field in cleaned and not cleaned[field]:
                self.add_error(
                    field, "Inherited text cannot be cleared for a single occurrence."
                )
        return cleaned
