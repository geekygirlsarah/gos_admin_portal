import secrets
from decimal import Decimal

from django.conf import settings
from django.db import models
from django.utils import timezone

DEFAULT_ORGANIZATION_SETTINGS = {
    "champion_label": "Champion",
    "helper_label": "Helper",
    "lead_mentor_label": "Lead Mentor",
    "mentor_label": "Mentor",
    "parent_label": "Parent",
    "student_label": "Student",
    "clearance_types": [
        "PA State Police Criminal Record Check (PATCH)",
        "PA Child Abuse History Clearance (PACA)",
        "FBI Fingerprinting / Affidavit",
    ],
    "sliding_scale": {
        "base_amount": "10150.00",
        "additional_member_amount": "5500.00",
        "low_multiplier": "1.50",
        "high_multiplier": "4.00",
    },
    "sliding_scale_base_amount": "10150.00",
    "sliding_scale_additional_member_amount": "5500.00",
    "sliding_scale_low_multiplier": "1.50",
    "sliding_scale_high_multiplier": "4.00",
    "feature_flags": {
        "outreach": True,
        "orders": True,
        "travel": True,
        "badges": True,
        "signout-sheet": True,
    },
    "email_settings": {},
    "portal_title": "Girls of Steel Portal",
    "footer_tagline": "is an outreach program of Carnegie Mellon University.",
}


class SettingsDict(dict):
    """Dictionary wrapper for organization settings with default fallback and dot-attribute access."""

    def __init__(self, data=None, defaults=None, **kwargs):
        self._defaults = (
            defaults if defaults is not None else DEFAULT_ORGANIZATION_SETTINGS
        )
        base = dict(data or {})
        base.update(kwargs)
        super().__init__(base)

    def __getitem__(self, key):
        if dict.__contains__(self, key):
            return super().__getitem__(key)
        if key in self._defaults:
            return self._defaults[key]
        raise KeyError(key)

    def get(self, key, default=None):
        if dict.__contains__(self, key):
            val = super().get(key)
            if val is not None:
                return val
        if key in self._defaults:
            return self._defaults[key]
        return default

    def __getattr__(self, name):
        if name.startswith("_"):
            return super().__getattribute__(name)
        if dict.__contains__(self, name):
            return self[name]
        if name in self._defaults:
            return self._defaults[name]
        raise AttributeError(f"'SettingsDict' object has no attribute '{name}'")

    def __contains__(self, key):
        return dict.__contains__(self, key) or key in self._defaults


class OrganizationQuerySet(models.QuerySet):
    """Queryset helpers for organization (multi-tenant) scoping.

    ``for_organization`` filters rows belonging to the given organization.
    While records may still be unscoped during transitions (organization__isnull=True),
    it keeps them visible alongside the matching org, or returns all records if
    organization is None.
    """

    def for_organization(self, organization):
        if organization is None:
            return self
        return self.filter(
            models.Q(organization=organization) | models.Q(organization__isnull=True)
        )


class OrganizationManager(models.Manager.from_queryset(OrganizationQuerySet)):
    """Manager providing multi-tenant organization scoping."""

    pass


class Organization(models.Model):
    """A tenant/organization that owns portal data.

    Phase 1 of the multi-tenant effort: there is a single organization
    (Girls of Steel, slug ``gos``) and core models carry a *nullable*
    ``organization`` FK. Nothing enforces scoping yet — the FK is just the
    schema groundwork the later phases will build on.
    """

    name = models.CharField(
        max_length=200,
        help_text="Display name of the organization.",
    )
    slug = models.SlugField(
        max_length=100,
        unique=True,
        help_text="URL-safe identifier for this organization (e.g. 'gos').",
    )
    subdomain = models.SlugField(
        max_length=100,
        unique=True,
        null=True,
        blank=True,
        help_text="Optional subdomain this organization is served under (reserved for future routing).",
    )
    is_active = models.BooleanField(
        default=True,
        help_text="Inactive organizations are hidden from routing once scoping is enabled.",
    )
    settings = models.JSONField(
        default=dict,
        blank=True,
        help_text="Free-form key/value settings for this organization.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if not isinstance(self.settings, SettingsDict):
            self.settings = SettingsDict(self.settings)

    @classmethod
    def from_db(cls, db, field_names, values, **kwargs):
        instance = super().from_db(db, field_names, values, **kwargs)
        if not isinstance(instance.settings, SettingsDict):
            instance.settings = SettingsDict(instance.settings)
        return instance

    def refresh_from_db(self, using=None, fields=None):
        super().refresh_from_db(using=using, fields=fields)
        if not isinstance(self.settings, SettingsDict):
            self.settings = SettingsDict(self.settings)

    def __str__(self):
        return self.name

    def get_setting(self, key, default=None):
        """Retrieve a setting for this organization, falling back to defaults."""
        return self.settings.get(key, default)

    def set_setting(self, key, value, save=True):
        """Set a setting value on this organization and optionally save."""
        self.settings[key] = value
        if save:
            self.save(update_fields=["settings", "updated_at"])


class OrganizationSettings(models.Model):
    """Structured settings scoped to an organization.

    ``Organization.settings`` is good for quick free-form keys; this model is
    reserved for typed settings as multi-tenancy grows (branding, defaults,
    feature flags, etc.). Phase 1 ships a OneToOne row per organization.
    """

    organization = models.OneToOneField(
        Organization,
        on_delete=models.CASCADE,
        related_name="organization_settings",
    )
    data = models.JSONField(
        default=dict,
        blank=True,
        help_text="Key/value settings for the organization.",
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Organization Settings"
        verbose_name_plural = "Organization Settings"

    def __str__(self):
        return f"{self.organization} settings"

    def get_setting(self, key, default=None):
        """Retrieve setting from data, or organization.settings, or DEFAULT_ORGANIZATION_SETTINGS."""
        if isinstance(self.data, dict) and key in self.data:
            return self.data[key]
        if self.organization:
            val = self.organization.settings.get(key, None)
            if val is not None:
                return val
        return DEFAULT_ORGANIZATION_SETTINGS.get(key, default)

    def set_setting(self, key, value, save=True):
        """Set a setting in self.data and optionally save."""
        if not isinstance(self.data, dict):
            self.data = {}
        self.data[key] = value
        if save:
            self.save(update_fields=["data", "updated_at"])

    @property
    def champion_label(self):
        return self.get_setting("champion_label", "Champion")

    @property
    def helper_label(self):
        return self.get_setting("helper_label", "Helper")

    @property
    def lead_mentor_label(self):
        return self.get_setting("lead_mentor_label", "Lead Mentor")

    @property
    def mentor_label(self):
        return self.get_setting("mentor_label", "Mentor")

    @property
    def parent_label(self):
        return self.get_setting("parent_label", "Parent")

    @property
    def student_label(self):
        return self.get_setting("student_label", "Student")

    @property
    def clearance_types(self):
        return self.get_setting(
            "clearance_types", DEFAULT_ORGANIZATION_SETTINGS["clearance_types"]
        )

    @property
    def feature_flags(self):
        return self.get_setting(
            "feature_flags", DEFAULT_ORGANIZATION_SETTINGS["feature_flags"]
        )

    @property
    def sliding_scale_config(self):
        return self.get_setting(
            "sliding_scale", DEFAULT_ORGANIZATION_SETTINGS["sliding_scale"]
        )

    @property
    def sliding_scale_base_amount(self):
        cfg = self.sliding_scale_config
        val = (
            cfg.get("base_amount")
            if isinstance(cfg, dict)
            else self.get_setting("sliding_scale_base_amount")
        )
        return Decimal(str(val)) if val is not None else Decimal("10150.00")

    @property
    def sliding_scale_additional_member_amount(self):
        cfg = self.sliding_scale_config
        val = (
            cfg.get("additional_member_amount")
            if isinstance(cfg, dict)
            else self.get_setting("sliding_scale_additional_member_amount")
        )
        return Decimal(str(val)) if val is not None else Decimal("5500.00")

    @property
    def sliding_scale_low_multiplier(self):
        cfg = self.sliding_scale_config
        val = (
            cfg.get("low_multiplier")
            if isinstance(cfg, dict)
            else self.get_setting("sliding_scale_low_multiplier")
        )
        return Decimal(str(val)) if val is not None else Decimal("1.50")

    @property
    def sliding_scale_high_multiplier(self):
        cfg = self.sliding_scale_config
        val = (
            cfg.get("high_multiplier")
            if isinstance(cfg, dict)
            else self.get_setting("sliding_scale_high_multiplier")
        )
        return Decimal(str(val)) if val is not None else Decimal("4.00")

    @property
    def email_settings(self):
        return self.get_setting("email_settings", {})


class UserOrganizationMembership(models.Model):
    """Association linking a User to an Organization with a specific Role."""

    ROLE_CHOICES = [
        ("LeadMentor", "Lead Mentor"),
        ("Mentor", "Mentor"),
        ("Parent", "Parent"),
        ("Student", "Student"),
        ("Alumni", "Alumni"),
        ("Staff", "Staff"),
    ]

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="organization_memberships",
        help_text="User belonging to the organization.",
    )
    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name="memberships",
        help_text="Organization the user is a member of.",
    )
    role = models.CharField(
        max_length=50,
        choices=ROLE_CHOICES,
        default="Student",
        help_text="User's role within this organization.",
    )
    is_active = models.BooleanField(
        default=True,
        help_text="Whether this membership is currently active.",
    )
    invited_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="invited_memberships",
        help_text="User who invited or created this membership.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = OrganizationManager.from_queryset(OrganizationQuerySet)()

    class Meta:
        unique_together = ("user", "organization", "role")
        verbose_name = "User Organization Membership"
        verbose_name_plural = "User Organization Memberships"
        ordering = ["organization", "user", "role"]

    def __str__(self):
        return f"{self.user} - {self.organization} ({self.get_role_display()})"


class OrganizationInvitation(models.Model):
    """Invitation for a user to join an organization with a pre-assigned role."""

    STATUS_PENDING = "pending"
    STATUS_ACCEPTED = "accepted"
    STATUS_DECLINED = "declined"
    STATUS_EXPIRED = "expired"

    STATUS_CHOICES = [
        (STATUS_PENDING, "Pending"),
        (STATUS_ACCEPTED, "Accepted"),
        (STATUS_DECLINED, "Declined"),
        (STATUS_EXPIRED, "Expired"),
    ]

    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name="invitations",
        help_text="Organization the user is invited to.",
    )
    email = models.EmailField(
        help_text="Email address of the invited user.",
    )
    role = models.CharField(
        max_length=50,
        choices=UserOrganizationMembership.ROLE_CHOICES,
        default="Mentor",
        help_text="Role to be granted upon accepting the invitation.",
    )
    token = models.CharField(
        max_length=64,
        unique=True,
        editable=False,
        help_text="Unique secret token used to accept the invitation.",
    )
    invited_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="sent_organization_invitations",
    )
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default=STATUS_PENDING,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(
        help_text="Date and time when this invitation expires.",
    )
    accepted_at = models.DateTimeField(null=True, blank=True)
    accepted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="accepted_organization_invitations",
    )

    objects = OrganizationManager.from_queryset(OrganizationQuerySet)()

    class Meta:
        verbose_name = "Organization Invitation"
        verbose_name_plural = "Organization Invitations"
        ordering = ["-created_at"]

    def __str__(self):
        return f"Invitation for {self.email} to {self.organization} ({self.role})"

    def save(self, *args, **kwargs):
        if not self.token:
            self.token = secrets.token_urlsafe(32)
        if not self.expires_at:
            self.expires_at = timezone.now() + timezone.timedelta(days=14)
        super().save(*args, **kwargs)

    def is_valid(self):
        if self.status != self.STATUS_PENDING:
            return False
        return timezone.now() <= self.expires_at

    def accept(self, user):
        if not self.is_valid():
            raise ValueError("Invitation is no longer valid or has expired.")
        membership, _ = UserOrganizationMembership.objects.get_or_create(
            user=user,
            organization=self.organization,
            role=self.role,
            defaults={"is_active": True, "invited_by": self.invited_by},
        )
        if not membership.is_active:
            membership.is_active = True
            membership.save(update_fields=["is_active", "updated_at"])
        self.status = self.STATUS_ACCEPTED
        self.accepted_at = timezone.now()
        self.accepted_by = user
        self.save(update_fields=["status", "accepted_at", "accepted_by"])
        return membership
