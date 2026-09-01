from django import forms

from outreach.models import OutreachEvent, OutreachShift, OutreachSignup
from programs.models import Student
from programs.utils import active_students_in_program
from programs.widgets import DualListboxWidget


class OutreachEventForm(forms.ModelForm):
    class Meta:
        model = OutreachEvent
        fields = [
            "name",
            "location_name",
            "location_address",
            "description",
        ]


class OutreachShiftForm(forms.ModelForm):
    class Meta:
        model = OutreachShift
        fields = ["date", "start_time", "end_time", "max_champions", "max_helpers"]
        widgets = {
            "date": forms.DateInput(attrs={"type": "date"}),
            "start_time": forms.TimeInput(attrs={"type": "time"}),
            "end_time": forms.TimeInput(attrs={"type": "time"}),
        }

    def clean(self):
        cleaned_data = super().clean()
        date = cleaned_data.get("date")
        start_time = cleaned_data.get("start_time")
        end_time = cleaned_data.get("end_time")

        if date and start_time and end_time and end_time <= start_time:
            raise forms.ValidationError("End time must be after start time.")

        return cleaned_data


class OutreachSetTimesForm(forms.Form):
    """Correct a signup's check-in/out times after the fact.

    Lets event staff backdate attendance a student forgot to record, without
    needing Django admin. Both fields are optional datetime-local inputs; a
    check-out on its own is disallowed (a student can't leave before arriving),
    and check-out must not be before check-in.
    """

    checked_in_at = forms.DateTimeField(
        required=False,
        widget=forms.DateTimeInput(
            attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M"
        ),
    )
    checked_out_at = forms.DateTimeField(
        required=False,
        widget=forms.DateTimeInput(
            attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M"
        ),
    )

    def clean(self):
        cleaned_data = super().clean()
        check_in = cleaned_data.get("checked_in_at")
        check_out = cleaned_data.get("checked_out_at")

        if check_out and not check_in:
            self.add_error("checked_out_at", "A check-out requires a check-in time.")
        if check_in and check_out and check_out < check_in:
            self.add_error("checked_out_at", "Check-out can't be before check-in.")

        return cleaned_data


OutreachShiftFormSet = forms.inlineformset_factory(
    OutreachEvent,
    OutreachShift,
    form=OutreachShiftForm,
    extra=1,
    can_delete=True,
    min_num=1,
    validate_min=True,
)


class OutreachManageSignupsForm(forms.Form):
    champions = forms.ModelMultipleChoiceField(
        queryset=Student.objects.none(),
        required=False,
        widget=DualListboxWidget(
            available_label="All Active Students", selected_label="Champions"
        ),
    )
    helpers = forms.ModelMultipleChoiceField(
        queryset=Student.objects.none(),
        required=False,
        widget=DualListboxWidget(
            available_label="All Active Students", selected_label="Helpers"
        ),
    )

    def __init__(self, *args, **kwargs):
        self.shift = kwargs.pop("shift")
        super().__init__(*args, **kwargs)
        active_students = active_students_in_program(self.shift.event.program)
        self.fields["champions"].queryset = active_students
        self.fields["helpers"].queryset = active_students

        # Set initial values
        self.fields["champions"].initial = self.shift.champions.values_list(
            "student_id", flat=True
        )
        self.fields["helpers"].initial = self.shift.helpers.values_list(
            "student_id", flat=True
        )

    def clean(self):
        cleaned_data = super().clean()
        champions = cleaned_data.get("champions")
        helpers = cleaned_data.get("helpers")

        if champions and helpers:
            overlap = set(champions) & set(helpers)
            if overlap:
                names = ", ".join([s.full_name for s in overlap])
                raise forms.ValidationError(
                    f"Students cannot be both champions and helpers: {names}"
                )

        # Check limits
        if champions and champions.count() > self.shift.max_champions:
            raise forms.ValidationError(
                f"Maximum number of champions is {self.shift.max_champions}."
            )
        if helpers and helpers.count() > self.shift.max_helpers:
            raise forms.ValidationError(
                f"Maximum number of helpers is {self.shift.max_helpers}."
            )

        return cleaned_data

    def save(self):
        champions = self.cleaned_data["champions"]
        helpers = self.cleaned_data["helpers"]

        # Remove old signups not in the new lists
        all_new_student_ids = [s.id for s in champions] + [s.id for s in helpers]
        self.shift.signups.exclude(student_id__in=all_new_student_ids).delete()

        # Update or create champions
        for student in champions:
            OutreachSignup.objects.update_or_create(
                shift=self.shift,
                student=student,
                defaults={"role": OutreachSignup.CHAMPION},
            )

        # Update or create helpers
        for student in helpers:
            OutreachSignup.objects.update_or_create(
                shift=self.shift,
                student=student,
                defaults={"role": OutreachSignup.HELPER},
            )
