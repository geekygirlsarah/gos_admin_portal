from django.db import models


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

    def __str__(self):
        return self.name


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
