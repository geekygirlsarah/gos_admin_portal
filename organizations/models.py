import secrets
import uuid
from decimal import Decimal

from django.apps import apps
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models.functions import Lower, Trim
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
    "org_account_enabled": True,
    "org_account_name": "Andrew ID",
    "org_email_domain": "andrew.cmu.edu",
    "force_mentor_org_email": True,
    "force_student_org_email": False,
    "force_parent_org_email": False,
    "force_alumni_org_email": False,
}


def generate_domain_verification_token():
    return secrets.token_urlsafe(32)


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

    ``for_organization`` returns only rows belonging to the given organization.
    Unscoped rows (organization__isnull=True) are not visible to any tenant;
    migration 0006 backfills them to the GoS org. ``organization=None``
    (superuser / root-domain requests) returns all records.
    """

    def for_organization(self, organization):
        if organization is None:
            return self
        return self.filter(organization=organization)


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

    def refresh_from_db(self, using=None, fields=None, **kwargs):
        super().refresh_from_db(using=using, fields=fields, **kwargs)
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

    def is_org_email(self, email: str) -> bool:
        """Check if the given email belongs to this organization's institutional domain."""
        if not email or "@" not in email:
            return False
        if not self.get_setting("org_account_enabled", True):
            return False
        domain_setting = self.get_setting("org_email_domain", "andrew.cmu.edu") or ""
        if not domain_setting:
            return False
        email_domain = email.lower().split("@")[-1].strip()
        allowed_domains = [
            d.strip().lower().lstrip("@")
            for d in domain_setting.replace(";", ",").split(",")
            if d.strip()
        ]
        return any(
            email_domain == d or email_domain.endswith(f".{d}") for d in allowed_domains
        )

    def is_role_forced_org_email(self, role: str) -> bool:
        """Check if members of a specific role are required to use their org email."""
        if not self.get_setting("org_account_enabled", True):
            return False
        if not self.get_setting("org_email_domain", ""):
            return False
        role_lower = (role or "").strip().lower()
        if role_lower in ("mentor", "lead_mentor", "lead mentor"):
            return bool(self.get_setting("force_mentor_org_email", True))
        elif role_lower == "student":
            return bool(self.get_setting("force_student_org_email", False))
        elif role_lower == "parent":
            return bool(self.get_setting("force_parent_org_email", False))
        elif role_lower == "alumni":
            return bool(self.get_setting("force_alumni_org_email", False))
        return False


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

    @property
    def user_display_name(self):
        """Fetch the best display name for the associated user across Adult/Student profiles or User fields."""
        if not self.user:
            return ""
        if hasattr(self.user, "adult_profile") and self.user.adult_profile:
            return self.user.adult_profile.display_name
        if hasattr(self.user, "student_profile") and self.user.student_profile:
            return self.user.student_profile.display_name
        full_name = self.user.get_full_name()
        if full_name:
            return full_name
        return self.user.email or self.user.username


class SupportImpersonationHandoff(models.Model):
    """One-use, short-lived bridge from a platform session to a tenant session."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    initiated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="support_impersonation_handoffs_initiated",
    )
    initiator_session_hash = models.CharField(max_length=64)
    target_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="support_impersonation_handoffs_received",
    )
    organization = models.ForeignKey(
        Organization,
        on_delete=models.SET_NULL,
        null=True,
        related_name="support_impersonation_handoffs",
    )
    tenant_hostname = models.CharField(max_length=253)
    token_hash = models.CharField(max_length=64, unique=True)
    tenant_session_hash = models.CharField(max_length=64, blank=True)
    tenant_challenge_hash = models.CharField(max_length=64, blank=True)
    platform_token_hash = models.CharField(max_length=64, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    redeemed_at = models.DateTimeField(null=True, blank=True)
    platform_confirmed_at = models.DateTimeField(null=True, blank=True)
    platform_returned_at = models.DateTimeField(null=True, blank=True)
    confirmed_at = models.DateTimeField(null=True, blank=True)
    ended_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["expires_at", "redeemed_at"])]


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

    def matches_user(self, user):
        """Check if the given user matches the invitation's recipient email."""
        if not user or not getattr(user, "is_authenticated", False):
            return False

        target_email = (self.email or "").strip().lower()
        if not target_email:
            return False

        return target_email in self._candidate_user_emails(user)

    def _candidate_user_emails(self, user):
        """Collect all known email addresses associated with a user account."""
        emails = set()
        if user.email:
            emails.add(user.email.strip().lower())

        if apps.is_installed("allauth.account"):
            from allauth.account.models import EmailAddress

            for addr in EmailAddress.objects.filter(user=user).values_list(
                "email", flat=True
            ):
                if addr:
                    emails.add(addr.strip().lower())

        for profile_attr in ("adult_profile", "student_profile"):
            profile = getattr(user, profile_attr, None)
            if profile:
                for email_attr in ("personal_email", "andrew_email"):
                    val = getattr(profile, email_attr, None)
                    if val:
                        emails.add(val.strip().lower())

        return emails

    def accept(self, user):
        if not self.is_valid():
            raise ValueError("Invitation is no longer valid or has expired.")
        if not self.matches_user(user):
            user_identifier = (
                getattr(user, "email", None)
                or getattr(getattr(user, "adult_profile", None), "personal_email", None)
                or getattr(user, "username", "another user")
            )
            raise ValueError(
                f"This invitation was sent to '{self.email}'. You are currently signed in as '{user_identifier}'."
            )
        membership, _ = UserOrganizationMembership.objects.get_or_create(
            user=user,
            organization=self.organization,
            role=self.role,
            defaults={"is_active": True, "invited_by": self.invited_by},
        )
        if not membership.is_active:
            membership.is_active = True
            membership.invited_by = self.invited_by
            membership.save(update_fields=["is_active", "invited_by", "updated_at"])
        if (
            self.role == "LeadMentor"
            and hasattr(user, "adult_profile")
            and user.adult_profile
        ):
            adult = user.adult_profile
            if not adult.is_mentor:
                adult.is_mentor = True
                adult.save(update_fields=["is_mentor"])
        self.status = self.STATUS_ACCEPTED
        self.accepted_at = timezone.now()
        self.accepted_by = user
        self.save(update_fields=["status", "accepted_at", "accepted_by"])
        return membership


class OrganizationDomain(models.Model):
    """A tenant-owned hostname with explicit DNS and operator activation states."""

    STATUS_PENDING_VERIFICATION = "pending_verification"
    STATUS_DNS_VERIFIED = "dns_verified"
    STATUS_ACTIVE = "active"
    STATUS_DISABLED = "disabled"
    STATUS_CHOICES = [
        (STATUS_PENDING_VERIFICATION, "Waiting for DNS verification"),
        (STATUS_DNS_VERIFIED, "DNS verified; waiting for setup"),
        (STATUS_ACTIVE, "Active"),
        (STATUS_DISABLED, "Disabled"),
    ]

    organization = models.ForeignKey(
        Organization,
        on_delete=models.PROTECT,
        related_name="domains",
    )
    hostname = models.CharField(max_length=253, unique=True)
    verification_token = models.CharField(
        max_length=64,
        default=generate_domain_verification_token,
        editable=False,
    )
    status = models.CharField(
        max_length=32,
        choices=STATUS_CHOICES,
        default=STATUS_PENDING_VERIFICATION,
    )
    dns_verified_at = models.DateTimeField(null=True, blank=True)
    activated_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="requested_organization_domains",
    )
    activated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="activated_organization_domains",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = OrganizationManager()

    class Meta:
        ordering = ["hostname"]
        constraints = [
            models.UniqueConstraint(
                Lower("hostname"), name="org_domain_hostname_ci_unique"
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(hostname=Lower(Trim("hostname")))
                    & ~models.Q(hostname__endswith=".")
                ),
                name="org_domain_hostname_canonical",
            ),
            models.CheckConstraint(
                condition=(
                    ~models.Q(status="active") | models.Q(dns_verified_at__isnull=False)
                ),
                name="org_domain_active_requires_dns",
            ),
        ]

    def clean(self):
        from organizations.domain_utils import (
            LEGACY_ORGANIZATION_HOSTNAMES,
            get_legacy_custom_hostname,
            is_momentum_hostname,
            normalize_domain_hostname,
        )

        errors = {}
        try:
            self.hostname = normalize_domain_hostname(self.hostname)
        except ValidationError as exc:
            errors["hostname"] = exc.messages
        else:
            previous_identity = (
                OrganizationDomain.objects.filter(pk=self.pk)
                .values_list("hostname", "organization_id")
                .first()
                if self.pk
                else None
            )
            if previous_identity and previous_identity != (
                self.hostname,
                self.organization_id,
            ):
                errors["hostname"] = (
                    "A domain's hostname and organization cannot be changed after it is requested."
                )
            elif previous_identity is None:
                if is_momentum_hostname(self.hostname):
                    errors["hostname"] = (
                        "Use the assigned Momentum address, not a subdomain."
                    )
                elif self.hostname in LEGACY_ORGANIZATION_HOSTNAMES and not (
                    self.organization_id and self.organization.slug.lower() == "gos"
                ):
                    errors["hostname"] = (
                        "This hostname is reserved for an existing portal."
                    )
                elif self.organization_id:
                    for organization in Organization.objects.exclude(
                        pk=self.organization_id
                    ).only("id", "settings"):
                        if (
                            get_legacy_custom_hostname(organization.settings)
                            == self.hostname
                        ):
                            errors["hostname"] = (
                                "This hostname is already configured for another organization."
                            )
                            break

        if self.status == self.STATUS_ACTIVE and not self.dns_verified_at:
            errors["status"] = "A domain must pass DNS verification before activation."
        elif self.status == self.STATUS_ACTIVE and (
            not self.activated_at or not self.activated_by_id
        ):
            errors["status"] = "An operator must confirm activation of a domain."
        if self.status == self.STATUS_DNS_VERIFIED and not self.dns_verified_at:
            errors["status"] = "A domain must pass DNS verification first."
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    @property
    def verification_txt_name(self):
        from organizations.domain_utils import DOMAIN_TXT_PREFIX

        return f"{DOMAIN_TXT_PREFIX}.{self.hostname}"

    @property
    def verification_txt_value(self):
        return f"momentum-domain-verification={self.verification_token}"

    def __str__(self):
        return self.hostname


class OrganizationDomainRecovery(models.Model):
    STATUS_PENDING = "pending"
    STATUS_REVOKED = "revoked"
    STATUS_APPROVED = "approved"
    STATUS_CHOICES = [
        (STATUS_PENDING, "Pending"),
        (STATUS_REVOKED, "Revoked"),
        (STATUS_APPROVED, "Approved"),
    ]

    organization = models.ForeignKey(
        Organization, on_delete=models.PROTECT, related_name="domain_recoveries"
    )
    domain = models.ForeignKey(
        OrganizationDomain, on_delete=models.PROTECT, related_name="recoveries"
    )
    original_organization = models.ForeignKey(
        Organization,
        on_delete=models.PROTECT,
        related_name="original_domain_recoveries",
    )
    hostname = models.CharField(max_length=253)
    original_token_digest = models.CharField(max_length=64, editable=False)
    verification_token = models.CharField(
        max_length=64, default=generate_domain_verification_token, editable=False
    )
    status = models.CharField(
        max_length=16, choices=STATUS_CHOICES, default=STATUS_PENDING
    )
    dns_verified_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="requested_domain_recoveries",
    )
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="approved_domain_recoveries",
    )
    approved_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = OrganizationManager()

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "domain"],
                condition=models.Q(status="pending"),
                name="org_domain_recovery_pending_unique",
            )
        ]

    @property
    def verification_txt_name(self):
        from organizations.domain_utils import DOMAIN_TXT_PREFIX

        return f"{DOMAIN_TXT_PREFIX}.{self.hostname}"

    @property
    def verification_txt_value(self):
        return f"momentum-domain-verification={self.verification_token}"

    def __str__(self):
        return self.hostname


from contextlib import suppress

from django.core.cache import cache
from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver


@receiver([post_save, post_delete], sender=Organization)
def clear_organization_cache(sender, **kwargs):
    with suppress(Exception):
        from GoSAdminPortal.middleware import _default_organization

        _default_organization.cache_clear()
    with suppress(Exception):
        cache.clear()
