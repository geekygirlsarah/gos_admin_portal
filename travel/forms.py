from django import forms

from programs.models import Student
from programs.utils import active_students_in_program
from travel.models import (
    TravelCostOption,
    TravelDeparture,
    TravelEvent,
    TravelParentChaperoneSignup,
    TravelSignup,
)


class TravelEventForm(forms.ModelForm):
    class Meta:
        model = TravelEvent
        fields = [
            "name",
            "start_date",
            "end_date",
            "location_name",
            "location_address",
            "description",
            "permission_form",
        ]
        widgets = {
            "start_date": forms.DateInput(attrs={"type": "date"}),
            "end_date": forms.DateInput(attrs={"type": "date"}),
        }


class TravelDepartureForm(forms.ModelForm):
    class Meta:
        model = TravelDeparture
        fields = ["label", "leave_date", "leave_time", "meeting_location", "notes"]
        widgets = {
            "leave_date": forms.DateInput(attrs={"type": "date"}),
            "leave_time": forms.TimeInput(attrs={"type": "time"}),
        }


class TravelCostOptionForm(forms.ModelForm):
    class Meta:
        model = TravelCostOption
        fields = ["name", "amount", "unit_label", "notes", "sort_order"]
        widgets = {
            "amount": forms.NumberInput(attrs={"step": "0.01", "min": "0"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["unit_label"].required = False
        self.fields["sort_order"].required = False


TravelDepartureFormSet = forms.inlineformset_factory(
    TravelEvent,
    TravelDeparture,
    form=TravelDepartureForm,
    extra=1,
    can_delete=True,
    min_num=0,
    validate_min=False,
)

TravelCostOptionFormSet = forms.inlineformset_factory(
    TravelEvent,
    TravelCostOption,
    form=TravelCostOptionForm,
    extra=2,
    can_delete=True,
    min_num=0,
    validate_min=False,
)


class TravelSignupForm(forms.ModelForm):
    """Student signs up for a trip: optional departure pick + cost check-list.

    The departure field is only shown when the trip has more than one departure
    (the view hides it otherwise); the queryset is scoped by the view.
    """

    class Meta:
        model = TravelSignup
        fields = ["departure", "cost_items"]
        widgets = {
            "cost_items": forms.CheckboxSelectMultiple,
        }

    def __init__(self, *args, **kwargs):
        departures = kwargs.pop("departures", None)
        cost_options = kwargs.pop("cost_options", None)
        super().__init__(*args, **kwargs)
        if departures is not None:
            self.fields["departure"].queryset = departures
        if cost_options is not None:
            self.fields["cost_items"].queryset = cost_options
        self.fields["departure"].required = False
        self.fields["departure"].empty_label = "No specific departure"


class TravelStaffSignupForm(TravelSignupForm):
    """Lead mentors adding a student after a conversation. The student must be
    an active student of the program; the signup lands in 'interested' and the
    parents still have to approve."""

    student = forms.ModelChoiceField(queryset=Student.objects.none(), label="Student")

    def __init__(self, *args, **kwargs):
        program = kwargs.pop("program", None)
        super().__init__(*args, **kwargs)
        if program is not None:
            self.fields["student"].queryset = active_students_in_program(program)

    def clean_departure(self):
        departure = self.cleaned_data.get("departure")
        return departure


class TravelParentChaperoneStaffForm(forms.ModelForm):
    """Lead Mentors add a parent as a chaperone. Only parents with at least one
    active student in the program are eligible."""

    class Meta:
        model = TravelParentChaperoneSignup
        fields = ["adult"]

    def __init__(self, *args, **kwargs):
        program = kwargs.pop("program", None)
        super().__init__(*args, **kwargs)
        if program is not None:
            self.fields["adult"].queryset = TravelParentChaperoneSignup.eligible_adults(
                program
            )
            self.fields["adult"].label = "Parent"
            self.fields["adult"].empty_label = "-- Choose a parent --"


class TravelApprovalForm(forms.Form):
    """Parent approval. When the trip has an uploaded permission form, the
    parent must acknowledge it online before they can approve."""

    permission_acknowledged = forms.BooleanField(
        required=False,
        label="I confirm I have reviewed the attached permission form",
    )

    def __init__(self, *args, **kwargs):
        self.requires_form = kwargs.pop("requires_form", False)
        super().__init__(*args, **kwargs)
        if self.requires_form:
            self.fields["permission_acknowledged"].required = True

    def clean(self):
        cleaned_data = super().clean()
        if self.requires_form and not cleaned_data.get("permission_acknowledged"):
            self.add_error(
                "permission_acknowledged",
                "This trip has a permission form that must be acknowledged before approving.",
            )
        return cleaned_data
