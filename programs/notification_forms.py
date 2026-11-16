from django import forms
from django.utils import timezone

from programs.models import Notification

TENANT_AUDIENCE_CHOICES = [
    ("all", "All active members of this organization"),
    ("student", "Students"),
    ("parent", "Parents"),
    ("mentor", "Mentors"),
    ("lead_mentor", "Lead mentors"),
    ("alumni", "Alumni"),
]

PLATFORM_AUDIENCE_CHOICES = [
    ("all", "All active portal members"),
    ("admin", "Platform administrators"),
    ("student", "Students"),
    ("parent", "Parents"),
    ("mentor", "Mentors"),
    ("lead_mentor", "Lead mentors"),
    ("alumni", "Alumni"),
]


class NotificationForm(forms.Form):
    title = forms.CharField(max_length=255, label="Title")
    message = forms.CharField(
        label="Message",
        widget=forms.Textarea(attrs={"rows": 6}),
    )
    notification_type = forms.ChoiceField(
        choices=Notification.TYPE_CHOICES,
        initial="info",
        label="Update type",
    )
    audience = forms.MultipleChoiceField(
        choices=TENANT_AUDIENCE_CHOICES,
        label="Audience",
        widget=forms.CheckboxSelectMultiple,
    )
    expires_at = forms.DateTimeField(
        required=False,
        label="Optional expiration",
        input_formats=["%Y-%m-%dT%H:%M"],
        widget=forms.DateTimeInput(
            format="%Y-%m-%dT%H:%M",
            attrs={"type": "datetime-local"},
        ),
    )

    def __init__(self, *args, scope="tenant", **kwargs):
        super().__init__(*args, **kwargs)
        self.scope = scope
        if scope == "platform":
            self.fields["audience"].choices = PLATFORM_AUDIENCE_CHOICES
        for name, field in self.fields.items():
            if name != "audience":
                field.widget.attrs.setdefault("class", "form-control")
        self.fields["notification_type"].widget.attrs["class"] = "form-select"
        self.fields["expires_at"].widget.attrs["class"] = "form-control"

    def clean_audience(self):
        audience = self.cleaned_data["audience"]
        if "all" in audience and len(audience) > 1:
            raise forms.ValidationError(
                "Choose all members or specific roles, not both."
            )
        return audience

    def clean_expires_at(self):
        expires_at = self.cleaned_data["expires_at"]
        if expires_at and expires_at <= timezone.now():
            raise forms.ValidationError("The expiration must be in the future.")
        return expires_at

    def save(self, *, organization, created_by):
        audience = self.cleaned_data["audience"]
        target_roles = [] if "all" in audience else audience
        return Notification.objects.create(
            organization=organization,
            is_platform_wide=organization is None,
            created_by=created_by,
            title=self.cleaned_data["title"],
            message=self.cleaned_data["message"],
            notification_type=self.cleaned_data["notification_type"],
            target_roles=target_roles,
            expires_at=self.cleaned_data["expires_at"],
        )
