from datetime import date

from django import forms
from django.utils import timezone

from programs.models import Program
from programs.utils import GroupedProgramChoiceField

from .models import CalendarEvent, CalendarFeed, CalendarFeedACL, EventException

RECURRENCE_CHOICES = [
    ("none", "Does not repeat"),
    ("daily", "Daily"),
    ("weekly", "Weekly"),
    ("monthly", "Monthly"),
    ("custom", "Custom RFC-5545 RRULE"),
]

REPEAT_END_CHOICES = [
    ("never", "Never"),
    ("until", "On date"),
    ("count", "After occurrences"),
]

DAY_CHOICES = [
    ("MO", "Mon"),
    ("TU", "Tue"),
    ("WE", "Wed"),
    ("TH", "Thu"),
    ("FR", "Fri"),
    ("SA", "Sat"),
    ("SU", "Sun"),
]


class CalendarFeedForm(forms.ModelForm):
    program = GroupedProgramChoiceField(
        queryset=Program.objects.all(),
        required=False,
        empty_label="Org-wide (no program)",
    )

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


class CalendarFeedACLForm(forms.ModelForm):
    class Meta:
        model = CalendarFeedACL
        fields = ["role", "can_read", "can_write"]
        widgets = {
            "role": forms.HiddenInput(),
            "can_read": forms.CheckboxInput(attrs={"class": "form-check-input"}),
            "can_write": forms.CheckboxInput(attrs={"class": "form-check-input"}),
        }


class BaseCalendarFeedACLInlineFormSet(forms.BaseInlineFormSet):
    """Inline formset that pre-populates all standard roles (Mentor, Parent, Student, Alumni)
    when creating a new feed or when editing a feed that is missing ACL rows for any roles.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if not self.is_bound:
            if self.instance and self.instance.pk:
                existing_roles = set(self.get_queryset().values_list("role", flat=True))
            else:
                existing_roles = set()
            self._missing_roles = [
                d for d in DEFAULT_ACL_INITIAL if d["role"] not in existing_roles
            ]
            self.extra = len(self._missing_roles)
        else:
            self._missing_roles = []

    def get_form_kwargs(self, index):
        kwargs = super().get_form_kwargs(index)
        if not self.is_bound and index is not None:
            initial_count = self.initial_form_count()
            if index >= initial_count:
                extra_idx = index - initial_count
                if extra_idx < len(self._missing_roles):
                    kwargs["initial"] = self._missing_roles[extra_idx]
        return kwargs


CalendarFeedACLInlineFormSet = forms.inlineformset_factory(
    CalendarFeed,
    CalendarFeedACL,
    form=CalendarFeedACLForm,
    formset=BaseCalendarFeedACLInlineFormSet,
    fields=["role", "can_read", "can_write"],
    extra=0,
    can_delete=False,
)


class CalendarEventForm(forms.ModelForm):
    recurrence_type = forms.ChoiceField(
        choices=RECURRENCE_CHOICES,
        required=False,
        initial="none",
        widget=forms.Select(attrs={"class": "form-select", "id": "id_recurrence_type"}),
        label="Repeats",
    )
    repeat_interval = forms.IntegerField(
        required=False,
        min_value=1,
        initial=1,
        widget=forms.NumberInput(
            attrs={
                "class": "form-control",
                "min": "1",
                "id": "id_repeat_interval",
            }
        ),
        label="Repeat every",
    )
    repeat_days = forms.MultipleChoiceField(
        choices=DAY_CHOICES,
        required=False,
        widget=forms.CheckboxSelectMultiple(attrs={"class": "form-check-input"}),
        label="Repeat on",
    )
    repeat_end_type = forms.ChoiceField(
        choices=REPEAT_END_CHOICES,
        required=False,
        initial="never",
        widget=forms.RadioSelect(
            attrs={"class": "form-check-input", "id": "id_repeat_end_type"}
        ),
        label="Ends",
    )
    repeat_until = forms.DateField(
        required=False,
        widget=forms.DateInput(
            attrs={
                "type": "date",
                "class": "form-control",
                "id": "id_repeat_until",
            }
        ),
        label="End date",
    )
    repeat_count = forms.IntegerField(
        required=False,
        min_value=1,
        widget=forms.NumberInput(
            attrs={
                "class": "form-control",
                "min": "1",
                "placeholder": "Occurrences",
                "id": "id_repeat_count",
            }
        ),
        label="Number of occurrences",
    )

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

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        rrule_str = self.initial.get("rrule_ical") or (
            self.instance.rrule_ical if self.instance and self.instance.pk else ""
        )
        if rrule_str:
            self._parse_rrule_to_initial(rrule_str)

    def _parse_rrule_to_initial(self, rrule_str):
        parts = {}
        for item in rrule_str.split(";"):
            if "=" in item:
                k, v = item.split("=", 1)
                parts[k.upper()] = v

        freq = parts.get("FREQ")
        if freq == "DAILY":
            self.initial["recurrence_type"] = "daily"
        elif freq == "WEEKLY":
            self.initial["recurrence_type"] = "weekly"
            if "BYDAY" in parts:
                self.initial["repeat_days"] = [
                    d.strip() for d in parts["BYDAY"].split(",") if d.strip()
                ]
        elif freq == "MONTHLY":
            self.initial["recurrence_type"] = "monthly"
        elif freq:
            self.initial["recurrence_type"] = "custom"
        else:
            self.initial["recurrence_type"] = "none"

        if "INTERVAL" in parts:
            try:
                self.initial["repeat_interval"] = int(parts["INTERVAL"])
            except ValueError:  # nosec B110
                pass

        if "UNTIL" in parts:
            self.initial["repeat_end_type"] = "until"
            until_str = parts["UNTIL"]
            try:
                if len(until_str) >= 8:
                    y = int(until_str[:4])
                    m = int(until_str[4:6])
                    d = int(until_str[6:8])
                    self.initial["repeat_until"] = date(y, m, d)
            except (ValueError, TypeError):  # nosec B110
                pass
        elif "COUNT" in parts:
            self.initial["repeat_end_type"] = "count"
            try:
                self.initial["repeat_count"] = int(parts["COUNT"])
            except ValueError:  # nosec B110
                pass
        else:
            self.initial["repeat_end_type"] = "never"

    def clean(self):
        cleaned_data = super().clean()
        rec_type = cleaned_data.get("recurrence_type")
        rrule_raw = cleaned_data.get("rrule_ical", "").strip()

        if not rec_type or rec_type == "none":
            cleaned_data["rrule_ical"] = ""
        elif rec_type == "custom":
            if rrule_raw:
                try:
                    from dateutil.rrule import rrulestr

                    start_dt = cleaned_data.get("start_datetime") or timezone.now()
                    rrulestr(rrule_raw, dtstart=start_dt)
                except Exception as e:
                    self.add_error("rrule_ical", f"Invalid RFC-5545 RRULE: {e}")
            cleaned_data["rrule_ical"] = rrule_raw
        elif rec_type in ("daily", "weekly", "monthly"):
            freq_map = {
                "daily": "DAILY",
                "weekly": "WEEKLY",
                "monthly": "MONTHLY",
            }
            freq = freq_map[rec_type]
            rrule_parts = [f"FREQ={freq}"]

            interval = cleaned_data.get("repeat_interval") or 1
            if interval > 1:
                rrule_parts.append(f"INTERVAL={interval}")

            if rec_type == "weekly":
                days = cleaned_data.get("repeat_days")
                if days:
                    rrule_parts.append(f"BYDAY={','.join(days)}")
                else:
                    start_dt = cleaned_data.get("start_datetime")
                    if start_dt:
                        weekday_names = [
                            "MO",
                            "TU",
                            "WE",
                            "TH",
                            "FR",
                            "SA",
                            "SU",
                        ]
                        rrule_parts.append(f"BYDAY={weekday_names[start_dt.weekday()]}")

            end_type = cleaned_data.get("repeat_end_type")
            if end_type == "until":
                until_date = cleaned_data.get("repeat_until")
                if until_date:
                    rrule_parts.append(f"UNTIL={until_date.strftime('%Y%m%d')}T235959Z")
                else:
                    self.add_error(
                        "repeat_until",
                        "Please specify an end date for the recurring event.",
                    )
            elif end_type == "count":
                count = cleaned_data.get("repeat_count")
                if count:
                    rrule_parts.append(f"COUNT={count}")
                else:
                    self.add_error(
                        "repeat_count",
                        "Please specify the number of occurrences.",
                    )

            cleaned_data["rrule_ical"] = ";".join(rrule_parts)

        return cleaned_data


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
