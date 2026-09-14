from django import forms

from .models import CalendarEvent, CalendarFeed, CalendarFeedACL, EventException


class CalendarFeedForm(forms.ModelForm):
    class Meta:
        model = CalendarFeed
        fields = [
            "name",
            "description",
            "color",
            "visibility",
            "source_type",
            "program",
            "is_active",
        ]
        widgets = {
            "color": forms.TextInput(
                attrs={"type": "color", "class": "form-control form-control-color"}
            ),
            "description": forms.Textarea(attrs={"rows": 3}),
        }


DEFAULT_ACL_INITIAL = [
    {"role": "Mentor", "can_read": True, "can_write": True},
    {"role": "Parent", "can_read": True, "can_write": False},
    {"role": "Student", "can_read": True, "can_write": False},
    {"role": "Alumni", "can_read": False, "can_write": False},
]


CalendarFeedACLInlineFormSet = forms.inlineformset_factory(
    CalendarFeed,
    CalendarFeedACL,
    fields=["role", "can_read", "can_write"],
    extra=0,
    can_delete=True,
)


class CalendarEventForm(forms.ModelForm):
    class Meta:
        model = CalendarEvent
        fields = [
            "title",
            "description",
            "start_datetime",
            "end_datetime",
            "all_day",
            "location",
            "rrule_ical",
            "exdate_ical",
        ]
        widgets = {
            "start_datetime": forms.DateTimeInput(
                attrs={"type": "datetime-local", "class": "form-control"},
                format="%Y-%m-%dT%H:%M",
            ),
            "end_datetime": forms.DateTimeInput(
                attrs={"type": "datetime-local", "class": "form-control"},
                format="%Y-%m-%dT%H:%M",
            ),
            "description": forms.Textarea(attrs={"rows": 3}),
            "rrule_ical": forms.TextInput(
                attrs={
                    "class": "form-control",
                    "placeholder": "e.g. FREQ=WEEKLY;BYDAY=TU,TH;COUNT=20",
                }
            ),
            "exdate_ical": forms.TextInput(
                attrs={
                    "class": "form-control",
                    "placeholder": "e.g. 2026-09-15,2026-09-22",
                }
            ),
        }


class EventExceptionForm(forms.ModelForm):
    class Meta:
        model = EventException
        fields = [
            "original_date",
            "change_type",
            "new_start_datetime",
            "new_end_datetime",
            "new_title",
            "new_location",
            "new_description",
            "note",
        ]
        widgets = {
            "original_date": forms.DateInput(
                attrs={"type": "date", "class": "form-control"}
            ),
            "new_start_datetime": forms.DateTimeInput(
                attrs={"type": "datetime-local", "class": "form-control"},
                format="%Y-%m-%dT%H:%M",
            ),
            "new_end_datetime": forms.DateTimeInput(
                attrs={"type": "datetime-local", "class": "form-control"},
                format="%Y-%m-%dT%H:%M",
            ),
            "new_description": forms.Textarea(attrs={"rows": 3}),
        }

    def clean(self):
        cleaned_data = super().clean()
        change_type = cleaned_data.get("change_type")
        if change_type == EventException.CHANGE_MOVED:
            if not cleaned_data.get("new_start_datetime"):
                self.add_error("new_start_datetime", "Required when moving an event.")
            if not cleaned_data.get("new_end_datetime"):
                self.add_error("new_end_datetime", "Required when moving an event.")
        return cleaned_data
