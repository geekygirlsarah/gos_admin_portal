from django import forms
from django.core.exceptions import ValidationError

from organizations.models import OrganizationDomain


class OrganizationDomainRequestForm(forms.Form):
    hostname = forms.CharField(
        max_length=253,
        label="Domain name",
        help_text="Enter the hostname only, such as portal.example.org.",
    )

    def __init__(self, *args, organization, **kwargs):
        self.organization = organization
        super().__init__(*args, **kwargs)

    def clean_hostname(self):
        hostname = self.cleaned_data["hostname"].strip()
        domain = OrganizationDomain(organization=self.organization, hostname=hostname)
        try:
            domain.full_clean()
        except ValidationError as exc:
            if hasattr(exc, "message_dict") and "hostname" in exc.message_dict:
                raise forms.ValidationError(exc.message_dict["hostname"])
            raise forms.ValidationError(exc.messages)
        return domain.hostname
