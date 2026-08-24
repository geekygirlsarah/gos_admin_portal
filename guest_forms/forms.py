"""Forms for guest forms."""

from __future__ import annotations

from django import forms

from .models import (
    EmergencyContactRelationship,
    GuestForm,
    GuestFormSubmission,
    ParticipantType,
)


class GuestFormForm(forms.ModelForm):
    """Form for adding/editing a guest form."""

    class Meta:
        model = GuestForm
        fields = [
            "slug",
            "name",
            "description",
            "file",
            "is_required",
            "display_order",
            "is_active",
            "legal_notices_url",
            "safety_guidelines_url",
        ]
        widgets = {
            "slug": forms.TextInput(
                attrs={
                    "class": "form-control",
                    "placeholder": "auto-generated from name",
                }
            ),
            "form_type": forms.Select(attrs={"class": "form-select"}),
            "name": forms.TextInput(attrs={"class": "form-control"}),
            "description": forms.Textarea(attrs={"class": "form-control", "rows": 3}),
            "file": forms.ClearableFileInput(attrs={"class": "form-control"}),
            "is_required": forms.CheckboxInput(attrs={"class": "form-check-input"}),
            "display_order": forms.NumberInput(
                attrs={"class": "form-control", "min": 0}
            ),
            "is_active": forms.CheckboxInput(attrs={"class": "form-check-input"}),
            "legal_notices_url": forms.URLInput(attrs={"class": "form-control"}),
            "safety_guidelines_url": forms.URLInput(attrs={"class": "form-control"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Slug is auto-generated but can be overridden
        self.fields["slug"].required = False
        self.fields["slug"].help_text = (
            "Auto-generated from name if left blank. Must be unique."
        )


class GuestFormSubmissionForm(forms.ModelForm):
    """Form for guest form submission (used for both student and adult)."""

    class Meta:
        model = GuestFormSubmission
        fields = [
            "participant_type",
            "participant_first_name",
            "participant_last_name",
            "email",
            "phone_number",
            "phone_type",
            "team_number",
            "emergency_contact_name",
            "emergency_contact_phone",
            "emergency_contact_relationship",
            "emergency_contact_other",
            "agreed_legal_notices",
            "agreed_safety_guidelines",
            "file",
        ]
        widgets = {
            "participant_type": forms.Select(attrs={"class": "form-select"}),
            "participant_first_name": forms.TextInput(
                attrs={"class": "form-control", "placeholder": "First Name"}
            ),
            "participant_last_name": forms.TextInput(
                attrs={"class": "form-control", "placeholder": "Last Name"}
            ),
            "email": forms.EmailInput(
                attrs={"class": "form-control", "placeholder": "Email Address"}
            ),
            "phone_number": forms.TextInput(
                attrs={"class": "form-control", "placeholder": "Phone Number"}
            ),
            "phone_type": forms.Select(attrs={"class": "form-select"}),
            "team_number": forms.TextInput(
                attrs={
                    "class": "form-control",
                    "placeholder": "Team Number (e.g., FRC 3504)",
                }
            ),
            "emergency_contact_name": forms.TextInput(
                attrs={"class": "form-control", "placeholder": "Emergency Contact Name"}
            ),
            "emergency_contact_phone": forms.TextInput(
                attrs={
                    "class": "form-control",
                    "placeholder": "Emergency Contact Phone",
                }
            ),
            "emergency_contact_relationship": forms.Select(
                attrs={"class": "form-select"}
            ),
            "emergency_contact_other": forms.TextInput(
                attrs={"class": "form-control", "placeholder": "Specify relationship"}
            ),
            "agreed_legal_notices": forms.CheckboxInput(
                attrs={"class": "form-check-input"}
            ),
            "agreed_safety_guidelines": forms.CheckboxInput(
                attrs={"class": "form-check-input"}
            ),
            "file": forms.ClearableFileInput(attrs={"class": "form-control"}),
        }
        labels = {
            "participant_type": "I am filling this out as a",
            "participant_first_name": "Participant First Name",
            "participant_last_name": "Participant Last Name",
            "email": "Email Address",
            "phone_number": "Phone Number",
            "phone_type": "Phone Type",
            "team_number": "Team Number (optional)",
            "emergency_contact_name": "Emergency Contact Name",
            "emergency_contact_phone": "Emergency Contact Phone",
            "emergency_contact_relationship": "Relationship to Participant",
            "emergency_contact_other": "If Other, please specify",
            "agreed_legal_notices": (
                "I have read and reviewed the notices at "
                "<a href='https://www.cmu.edu/legal/' target='_blank'>CMU Legal</a>"
            ),
            "agreed_safety_guidelines": (
                "I have read and reviewed the "
                "<a href='https://www.cmu.edu/legal/' target='_blank'>Practice Field Safety and Use Guidelines</a>"
            ),
            "file": "Signed Form (PDF, JPG, PNG)",
        }

    def __init__(self, *args, guest_form=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.guest_form = guest_form

        # Make agreement fields required
        self.fields["agreed_legal_notices"].required = True
        self.fields["agreed_safety_guidelines"].required = True

        # Customize emergency contact relationship field
        self.fields["emergency_contact_other"].required = False

        # If guest_form has safety_guidelines_url, use it in label
        if guest_form and guest_form.safety_guidelines_url:
            self.fields["agreed_safety_guidelines"].label = (
                f"I have read and reviewed the Practice Field Safety and Use Guidelines "
                f"(<a href='{guest_form.safety_guidelines_url}' target='_blank'>view</a>)"
            )

        # Always required fields
        for field_name in [
            "participant_first_name",
            "participant_last_name",
            "email",
            "phone_number",
            "emergency_contact_name",
            "emergency_contact_phone",
        ]:
            self.fields[field_name].required = True

    def clean(self):
        cleaned_data = super().clean()

        # If emergency contact relationship is Other, require the other field
        relationship = cleaned_data.get("emergency_contact_relationship")
        other = cleaned_data.get("emergency_contact_other")
        if relationship == EmergencyContactRelationship.OTHER and not other:
            self.add_error(
                "emergency_contact_other", "Please specify the relationship."
            )

        # Validate agreements
        if not cleaned_data.get("agreed_legal_notices"):
            self.add_error(
                "agreed_legal_notices", "You must agree to the legal notices."
            )

        if not cleaned_data.get("agreed_safety_guidelines"):
            self.add_error(
                "agreed_safety_guidelines", "You must agree to the safety guidelines."
            )

        return cleaned_data
