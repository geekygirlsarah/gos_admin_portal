"""Forms for Platform Administration."""

from django import forms
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError

from audit.events import AuditEvent
from organizations.models import (
    Organization,
    OrganizationSettings,
    UserOrganizationMembership,
)
from programs.models import ProgramFeature


class PlatformOrganizationForm(forms.ModelForm):
    """Form to create and edit organizations along with their OrganizationSettings."""

    # OrganizationSettings fields
    portal_title = forms.CharField(
        max_length=150,
        required=False,
        help_text="Custom title for this organization's portal (e.g. 'Girls of Steel Robotics').",
    )
    footer_tagline = forms.CharField(
        max_length=255,
        required=False,
        help_text="Custom footer text (e.g. 'is an outreach program of CMU').",
    )
    contact_email = forms.EmailField(
        required=False,
        help_text="Public contact email for inquiries and support.",
    )
    phone = forms.CharField(
        max_length=30,
        required=False,
        help_text="Contact telephone number.",
    )
    time_zone = forms.CharField(
        max_length=50,
        initial="America/New_York",
        help_text="Primary time zone (e.g. 'America/New_York', 'America/Chicago').",
    )
    theme_color = forms.CharField(
        max_length=20,
        initial="#0056b3",
        widget=forms.TextInput(attrs={"type": "color"}),
        help_text="Primary branding/theme color.",
    )
    allowed_domains = forms.CharField(
        widget=forms.Textarea(attrs={"rows": 2}),
        required=False,
        help_text="Allowed email domains for auto-matching, one per line or comma-separated.",
    )
    org_account_enabled = forms.BooleanField(
        required=False,
        initial=True,
        help_text="Enable organization/school email accounts and identifiers.",
    )
    org_account_name = forms.CharField(
        max_length=100,
        required=False,
        initial="Andrew ID",
        help_text="Display name for org/school accounts (e.g. 'Andrew ID', 'Sciencey SSO').",
    )
    org_email_domain = forms.CharField(
        max_length=255,
        required=False,
        initial="andrew.cmu.edu",
        help_text="Primary email domain(s) for organization accounts (e.g. 'andrew.cmu.edu').",
    )
    force_mentor_org_email = forms.BooleanField(
        required=False,
        initial=True,
        help_text="Require mentors to log in using an email matching the organization domain.",
    )
    force_student_org_email = forms.BooleanField(
        required=False,
        initial=False,
        help_text="Require students to log in using an email matching the organization domain.",
    )
    force_parent_org_email = forms.BooleanField(
        required=False,
        initial=False,
        help_text="Require parents to log in using an email matching the organization domain.",
    )
    force_alumni_org_email = forms.BooleanField(
        required=False,
        initial=False,
        help_text="Require alumni to log in using an email matching the organization domain.",
    )

    # Initial Lead Mentor (Creation only)
    initial_lead_mentor_email = forms.EmailField(
        required=False,
        help_text="Email of user to assign/invite as initial Lead Mentor for this organization.",
    )

    class Meta:
        model = Organization
        fields = ["name", "slug", "subdomain", "is_active"]
        widgets = {
            "name": forms.TextInput(attrs={"placeholder": "e.g. Steel City Robotics"}),
            "slug": forms.TextInput(attrs={"placeholder": "e.g. steel-city"}),
            "subdomain": forms.TextInput(attrs={"placeholder": "e.g. steelcity"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance and self.instance.pk:
            # Populate settings fields from instance.settings
            settings = self.instance.settings or {}
            self.fields["portal_title"].initial = settings.get("portal_title", "")
            self.fields["footer_tagline"].initial = settings.get("footer_tagline", "")
            self.fields["contact_email"].initial = settings.get("contact_email", "")
            self.fields["phone"].initial = settings.get("phone", "")
            self.fields["time_zone"].initial = settings.get(
                "time_zone", "America/New_York"
            )
            self.fields["theme_color"].initial = settings.get("theme_color", "#0056b3")
            self.fields["allowed_domains"].initial = settings.get("allowed_domains", "")
            self.fields["org_account_enabled"].initial = settings.get(
                "org_account_enabled", True
            )
            self.fields["org_account_name"].initial = settings.get(
                "org_account_name", "Andrew ID"
            )
            self.fields["org_email_domain"].initial = settings.get(
                "org_email_domain", "andrew.cmu.edu"
            )
            self.fields["force_mentor_org_email"].initial = settings.get(
                "force_mentor_org_email", True
            )
            self.fields["force_student_org_email"].initial = settings.get(
                "force_student_org_email", False
            )
            self.fields["force_parent_org_email"].initial = settings.get(
                "force_parent_org_email", False
            )
            self.fields["force_alumni_org_email"].initial = settings.get(
                "force_alumni_org_email", False
            )
            # Hide initial lead mentor field on edit
            self.fields.pop("initial_lead_mentor_email", None)

    def clean_slug(self):
        slug = (self.cleaned_data.get("slug") or "").strip().lower()
        if not slug:
            raise ValidationError("Organization slug is required.")
        qs = Organization.objects.filter(slug=slug)
        if self.instance and self.instance.pk:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise ValidationError("An organization with this slug already exists.")
        return slug

    def clean_subdomain(self):
        subdomain = (self.cleaned_data.get("subdomain") or "").strip().lower()
        if not subdomain:
            return None
        qs = Organization.objects.filter(subdomain=subdomain)
        if self.instance and self.instance.pk:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise ValidationError("An organization with this subdomain already exists.")
        return subdomain

    def save(self, commit=True):
        org = super().save(commit=False)
        settings_dict = dict(org.settings) if isinstance(org.settings, dict) else {}
        for key in [
            "portal_title",
            "footer_tagline",
            "contact_email",
            "phone",
            "time_zone",
            "theme_color",
            "allowed_domains",
            "org_account_enabled",
            "org_account_name",
            "org_email_domain",
            "force_mentor_org_email",
            "force_student_org_email",
            "force_parent_org_email",
            "force_alumni_org_email",
        ]:
            val = self.cleaned_data.get(key)
            if val is not None:
                settings_dict[key] = val
        org.settings = settings_dict
        if commit:
            org.save()
            settings_obj, _ = OrganizationSettings.objects.get_or_create(
                organization=org
            )
            settings_obj.data = settings_dict
            settings_obj.save()
        return org


class PlatformMemberForm(forms.ModelForm):
    """Form to manage a user's membership and role in an organization."""

    user_identifier = forms.CharField(
        label="User (Username or Email)",
        help_text="Search existing user by username or email address.",
        widget=forms.TextInput(attrs={"placeholder": "e.g. mentor@example.com"}),
    )

    class Meta:
        model = UserOrganizationMembership
        fields = ["role", "is_active"]

    def __init__(self, *args, organization=None, **kwargs):
        self.organization = organization
        super().__init__(*args, **kwargs)
        if self.instance and self.instance.pk:
            self.fields["user_identifier"].initial = (
                self.instance.user.email or self.instance.user.username
            )
            self.fields["user_identifier"].disabled = True

    def clean_user_identifier(self):
        ident = self.cleaned_data.get("user_identifier", "").strip()
        if self.instance and self.instance.pk:
            return self.instance.user

        user = (
            User.objects.filter(email__iexact=ident).first()
            or User.objects.filter(username__iexact=ident).first()
        )
        if not user:
            raise ValidationError(f"No user found matching '{ident}'.")

        if self.organization:
            existing = UserOrganizationMembership.objects.filter(
                user=user, organization=self.organization
            ).exists()
            if existing:
                raise ValidationError(
                    f"User '{user.username}' already has a membership in this organization."
                )
        return user


class PlatformUserSearchForm(forms.Form):
    """Search and filter form for Global User Support."""

    q = forms.CharField(
        required=False,
        widget=forms.TextInput(
            attrs={
                "placeholder": "Search by name, email, username, phone, or Andrew ID...",
                "class": "form-control",
            }
        ),
    )
    role = forms.ChoiceField(
        required=False,
        choices=[("", "All Roles")] + list(UserOrganizationMembership.ROLE_CHOICES),
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    organization = forms.ModelChoiceField(
        required=False,
        queryset=Organization.objects.all().order_by("name"),
        empty_label="All Organizations",
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    status = forms.ChoiceField(
        required=False,
        choices=[
            ("", "All Statuses"),
            ("active", "Active Users"),
            ("inactive", "Inactive Users"),
        ],
        widget=forms.Select(attrs={"class": "form-select"}),
    )


class PlatformAuditLogFilterForm(forms.Form):
    """Filter form for Cross-Tenant Audit Log Explorer."""

    event = forms.ChoiceField(
        required=False,
        choices=[("", "All Events")] + list(AuditEvent.choices),
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    search = forms.CharField(
        required=False,
        widget=forms.TextInput(
            attrs={
                "placeholder": "Search actor, target, notes, or IP...",
                "class": "form-control",
            }
        ),
    )
    outcome = forms.ChoiceField(
        required=False,
        choices=[
            ("", "All Outcomes"),
            ("success", "Success Only"),
            ("failure", "Failure Only"),
        ],
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    date_from = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs={"type": "date", "class": "form-control"}),
    )
    date_to = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs={"type": "date", "class": "form-control"}),
    )
