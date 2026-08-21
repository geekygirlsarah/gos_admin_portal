from django import forms

from outreach.models import OutreachEvent, OutreachSignup
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
            "start_date",
            "start_time",
            "end_date",
            "end_time",
            "description",
            "max_champions",
            "max_helpers",
        ]
        widgets = {
            "start_date": forms.DateInput(attrs={"type": "date"}),
            "end_date": forms.DateInput(attrs={"type": "date"}),
            "start_time": forms.TimeInput(attrs={"type": "time"}),
            "end_time": forms.TimeInput(attrs={"type": "time"}),
        }


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
        self.event = kwargs.pop("event")
        super().__init__(*args, **kwargs)
        active_students = active_students_in_program(self.event.program)
        self.fields["champions"].queryset = active_students
        self.fields["helpers"].queryset = active_students

        # Set initial values
        self.fields["champions"].initial = self.event.champions.values_list(
            "student_id", flat=True
        )
        self.fields["helpers"].initial = self.event.helpers.values_list(
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
        if champions and champions.count() > self.event.max_champions:
            raise forms.ValidationError(
                f"Maximum number of champions is {self.event.max_champions}."
            )
        if helpers and helpers.count() > self.event.max_helpers:
            raise forms.ValidationError(
                f"Maximum number of helpers is {self.event.max_helpers}."
            )

        return cleaned_data

    def save(self):
        champions = self.cleaned_data["champions"]
        helpers = self.cleaned_data["helpers"]

        # Remove old signups not in the new lists
        all_new_student_ids = [s.id for s in champions] + [s.id for s in helpers]
        self.event.signups.exclude(student_id__in=all_new_student_ids).delete()

        # Update or create champions
        for student in champions:
            OutreachSignup.objects.update_or_create(
                event=self.event,
                student=student,
                defaults={"role": OutreachSignup.CHAMPION},
            )

        # Update or create helpers
        for student in helpers:
            OutreachSignup.objects.update_or_create(
                event=self.event,
                student=student,
                defaults={"role": OutreachSignup.HELPER},
            )
