from django import forms
from django.db.models import Value
from django.db.models.functions import Coalesce, Lower, NullIf
from PIL import Image, UnidentifiedImageError

from programs.models import Adult, Student
from programs.utils import active_students_in_program
from travel.models import (
    TravelCostOption,
    TravelDeparture,
    TravelEvent,
    TravelMentorSignup,
    TravelParentChaperoneSignup,
    TravelSignup,
)


def _apply_bootstrap_classes(form):
    for field in form.fields.values():
        widget = field.widget
        if isinstance(widget, forms.Select):
            css_class = "form-select"
        elif isinstance(widget, forms.CheckboxInput):
            css_class = "form-check-input"
        else:
            css_class = "form-control"
        existing_classes = widget.attrs.get("class", "").split()
        if css_class not in existing_classes:
            existing_classes.append(css_class)
        widget.attrs["class"] = " ".join(existing_classes)


class TravelSignedPermissionFormUpload(forms.Form):
    file = forms.FileField()
    form_version = forms.UUIDField()

    def clean_file(self):
        uploaded = self.cleaned_data["file"]
        if uploaded.size > 10 * 1024 * 1024:
            raise forms.ValidationError("The signed form must be 10 MB or smaller.")
        extension = uploaded.name.rsplit(".", 1)[-1].lower()
        try:
            if extension == "pdf":
                valid = uploaded.read(5) == b"%PDF-"
            elif extension in ("jpg", "jpeg", "png"):
                image = Image.open(uploaded)
                valid = image.format == ("PNG" if extension == "png" else "JPEG")
                image.verify()
            else:
                valid = False
        except (OSError, ValueError, UnidentifiedImageError):
            valid = False
        finally:
            uploaded.seek(0)
        if not valid:
            raise forms.ValidationError("Upload a PDF, JPEG, or PNG signed form.")
        return uploaded


class TravelEventForm(forms.ModelForm):
    class Meta:
        model = TravelEvent
        fields = [
            "name",
            "start_date",
            "end_date",
            "requires_departure",
            "location_name",
            "location_address",
            "description",
            "permission_form",
        ]
        widgets = {
            "start_date": forms.DateInput(attrs={"type": "date"}),
            "end_date": forms.DateInput(attrs={"type": "date"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _apply_bootstrap_classes(self)


class TravelDepartureForm(forms.ModelForm):
    class Meta:
        model = TravelDeparture
        fields = ["label", "leave_date", "leave_time", "meeting_location", "notes"]
        widgets = {
            "leave_date": forms.DateInput(attrs={"type": "date"}),
            "leave_time": forms.TimeInput(attrs={"type": "time"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _apply_bootstrap_classes(self)


class TravelCostOptionForm(forms.ModelForm):
    class Meta:
        model = TravelCostOption
        fields = [
            "name",
            "amount",
            "is_required",
            "unit_label",
            "notes",
            "sort_order",
        ]
        widgets = {
            "amount": forms.NumberInput(attrs={"step": "0.01", "min": "0"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["unit_label"].required = False
        self.fields["sort_order"].required = False
        _apply_bootstrap_classes(self)

    def has_changed(self):
        if self.instance.pk or not super().has_changed():
            return super().has_changed()

        name = self.data.get(self.add_prefix("name"), "").strip()
        amount = self.data.get(self.add_prefix("amount"), "").strip()
        notes = self.data.get(self.add_prefix("notes"), "").strip()
        unit_label = self.data.get(self.add_prefix("unit_label"), "").strip()
        sort_order = self.data.get(self.add_prefix("sort_order"), "").strip()
        initial_unit_label = str(self.initial.get("unit_label") or "per person")
        initial_sort_order = str(self.initial.get("sort_order") or 0)
        return bool(
            name
            or amount
            or notes
            or (unit_label and unit_label != initial_unit_label)
            or (sort_order and sort_order != initial_sort_order)
        )


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
    extra=1,
    can_delete=True,
    min_num=0,
    validate_min=False,
)


class TravelSignupForm(forms.ModelForm):
    """Student signs up for a trip: optional departure pick + cost check-list.

    The view hides optional departure selectors when only one departure exists;
    required choices remain visible and all choice querysets are scoped.
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
        requires_departure = kwargs.pop("requires_departure", False)
        super().__init__(*args, **kwargs)
        self.requires_departure = requires_departure
        self.cost_options = (
            cost_options
            if cost_options is not None
            else TravelCostOption.objects.none()
        )
        available_departures = list(departures) if departures is not None else []
        available_cost_options = list(self.cost_options)
        self.fields["departure"].queryset = (
            departures if departures is not None else TravelDeparture.objects.none()
        )
        self.fields["cost_items"].queryset = self.cost_options
        self.fields["cost_items"].choices = [
            (option.pk, str(option)) for option in available_cost_options
        ]
        if self.instance.pk and not self.is_bound:
            selected_ids = {option.pk for option in self.instance.cost_items.all()}
            required_ids = {
                option.pk for option in available_cost_options if option.is_required
            }
            self.initial["cost_items"] = selected_ids | required_ids
        self.fields["departure"].required = requires_departure
        self.fields["departure"].empty_label = (
            "Choose a departure" if requires_departure else "No specific departure"
        )
        departure_choices = [("", self.fields["departure"].empty_label)]
        departure_choices.extend(
            (departure.pk, str(departure)) for departure in available_departures
        )
        self.fields["departure"].choices = departure_choices

    def clean_cost_items(self):
        selected = self.cleaned_data["cost_items"]
        required = self.cost_options.filter(is_required=True)
        return selected | required


class TravelStaffSignupForm(TravelSignupForm):
    """Lead mentors adding a student after a conversation. The student must be
    an active student of the program; the signup lands in 'interested' and the
    parents still have to approve."""

    student = forms.ModelChoiceField(queryset=Student.objects.none(), label="Student")

    def __init__(self, *args, **kwargs):
        program = kwargs.pop("program", None)
        super().__init__(*args, **kwargs)
        if program is not None:
            self.fields["student"].queryset = active_students_in_program(
                program
            ).order_by(
                Lower(
                    Coalesce(
                        NullIf("preferred_first_name", Value("")),
                        "legal_first_name",
                    )
                ),
                Lower("last_name"),
            )

    def clean_departure(self):
        departure = self.cleaned_data.get("departure")
        return departure


class TravelSignupDepartureForm(forms.ModelForm):
    class Meta:
        model = TravelSignup
        fields = ["departure"]

    def __init__(self, *args, **kwargs):
        departures = kwargs.pop("departures", None)
        requires_departure = kwargs.pop("requires_departure", False)
        super().__init__(*args, **kwargs)
        self.fields["departure"].queryset = (
            departures if departures is not None else TravelDeparture.objects.none()
        )
        self.fields["departure"].required = requires_departure
        self.fields["departure"].empty_label = (
            "Choose a departure" if requires_departure else "No specific departure"
        )


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


class TravelMentorStaffSignupForm(forms.ModelForm):
    """Lead Mentors add another active mentor to a trip."""

    class Meta:
        model = TravelMentorSignup
        fields = ["adult", "departure"]

    def __init__(self, *args, **kwargs):
        program = kwargs.pop("program", None)
        event = kwargs.pop("event", None)
        departures = kwargs.pop("departures", None)
        super().__init__(*args, **kwargs)

        self.fields["adult"].queryset = Adult.objects.none()
        self.fields["departure"].queryset = TravelDeparture.objects.none()
        if program is not None and program.organization_id:
            organization = program.organization
            mentors = Adult.objects.for_organization(organization).filter(
                organization=organization,
                is_mentor=True,
                mentor_active=True,
            )
            if event is not None:
                existing_adult_ids = (
                    TravelMentorSignup.objects.for_organization(organization)
                    .filter(event=event)
                    .values("adult_id")
                )
                mentors = mentors.exclude(pk__in=existing_adult_ids)
            self.fields["adult"].queryset = mentors.order_by(
                Lower(
                    Coalesce(
                        NullIf("preferred_first_name", Value("")),
                        "legal_first_name",
                    )
                ),
                Lower("last_name"),
            )
        if departures is not None:
            self.fields["departure"].queryset = departures

        self.fields["adult"].label = "Mentor"
        self.fields["adult"].empty_label = "-- Choose a mentor --"
        self.fields["departure"].required = False
        self.fields["departure"].empty_label = "-- Choose a departure --"
        _apply_bootstrap_classes(self)


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
