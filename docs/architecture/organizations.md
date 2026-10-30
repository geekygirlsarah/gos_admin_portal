# Multi-Tenant Organization Architecture

This document describes the multi-tenant architecture and tenant-isolation invariants in the Momentum / Girls of Steel Admin Portal.

## Overview

The multi-tenant architecture supports multiple organizations (teams/programs) within a single database instance while guaranteeing strict data isolation, configurable settings, per-tenant permissions, and independent branding.

The multi-tenant implementation spans seven structured phases:
1. **Foundation Models**: `Organization` and `OrganizationSettings`.
2. **Query Scoping & Managers**: `OrganizationQuerySet` and `OrganizationManager`.
3. **View & Form Integration**: `OrganizationScopeMixin` and `OrganizationFormMixin`.
4. **Per-Tenant Configuration**: Dynamic `OrganizationSettings` with dot-access.
5. **Scoped Permissions**: Tenant-aware `RolePermission` and permission helpers.
6. **Membership & Routing**: `UserOrganizationMembership`, `OrganizationInvitation`, and dynamic domain routing.
7. **Platform Administration**: `SuperuserRequiredMixin`, Organization Health Diagnostics, Platform Metrics, User Support, Impersonation, and Cross-Tenant Audit Logging.

---

## Core Models (`organizations/models.py`)

### `Organization`
Represents an independent tenant in the platform.
- `name`: Organization display name (e.g., "Girls of Steel Robotics").
- `slug`: Unique slug identifier (e.g., `"gos"`).
- `subdomain`: Optional unique subdomain for tenant routing (e.g., `gos.momentumteam.app`).
- `is_active`: Boolean flag indicating if the organization is active.
- `settings`: JSONField storing organization-specific configuration overrides.

A default Girls of Steel organization with slug `gos` is seeded via data migration `0002_seed_gos_organization`.

### `OrganizationSettings`
A OneToOne singleton per organization containing structured settings.
- Stored as JSON data (`data` field) with dictionary dot-attribute access via `request.organization.settings.<key>`.
- Defaults are defined in `DEFAULT_ORGANIZATION_SETTINGS` (org name, custom role labels like "Champion" or "Lead Mentor", clearance types, sliding-scale parameters, email templates, and feature flags).

### `UserOrganizationMembership`
Links users to organizations with specific tenant roles:
- `user`: ForeignKey to `settings.AUTH_USER_MODEL`.
- `organization`: ForeignKey to `Organization`.
- `role`: Role string (`LeadMentor`, `Mentor`, `Parent`, `Student`, `Alumni`, `Staff`).
- `is_active`: Status flag for membership.

### `OrganizationInvitation`
Manages onboarding workflows for new members:
- Uses secure, tokenized invitation links.
- Associates invited users with the appropriate organization and role upon acceptance.

---

## Model Multi-Tenancy & Query Scoping

All organization-owned models across apps (`programs`, `applications`, `attendance`, `outreach`, `orders`, `travel`, `calendar_feeds`) include:
1. A nullable ForeignKey to `Organization`:
   ```python
   organization = models.ForeignKey(
       "organizations.Organization",
       on_delete=models.SET_NULL,
       null=True,
       blank=True,
       related_name="%(class)ss",
   )
   ```
2. The custom manager:
   ```python
   objects = OrganizationManager.from_queryset(OrganizationQuerySet)()
   ```

### Scoping Rules (`.for_organization(org)`)
- `Model.objects.for_organization(None)`: Returns all rows (unfiltered, used by superusers/system admin).
- `Model.objects.for_organization(org)`: Returns records where `organization == org` **plus** legacy unscoped rows (`organization__isnull=True`) to maintain visibility during multi-tenant transitions.

---

## Request Routing & Middleware

`OrganizationMiddleware` (`GoSAdminPortal/middleware.py`) attaches the resolved tenant to `request.organization` on every request.

Resolution occurs in priority order:
1. **HTTP Header**: `X-Organization` header (used by API clients and integration tests).
2. **Subdomain Routing**: `<subdomain>.momentumteam.app` (where root domain defaults to `MOMENTUM_ROOT_DOMAIN = "momentumteam.app"`).
3. **Custom Domains**: Matches configured custom domain aliases.
4. **Session**: User session preferences.
5. **Default Fallback**: The seeded Girls of Steel (`gos`) organization.

---

## Views and Forms Conventions

### Class-Based Views
Views handling organization-scoped models inherit `OrganizationScopeMixin` (from `organizations.mixins` or `programs.views.mixins`):
- Automatically scopes `get_queryset()` to `request.organization`.
- Automatically assigns `form.instance.organization = request.organization` on creation in `form_valid()`.

### Forms
Forms dealing with organization-scoped selections:
- Inherit `OrganizationFormMixin` (from `organizations.forms` or `programs.forms`).
- Or explicitly limit choice querysets using `limit_choices_to_organization(form, request.organization)`.

---

## Platform Administration & Superuser Tools (`organizations/platform_views.py`)

Dedicated platform administration endpoints (`/platform-admin/`) are strictly restricted to active superusers via `SuperuserRequiredMixin`:
- **Overview Dashboard (`/platform-admin/`)**: Aggregates tenant counts, user totals, active program metrics, health summaries, and recent cross-tenant audit activity.
- **Organization Health & Readiness**: Automated health checks evaluate lead mentor staffing, active program availability, and mentor compliance rates (background checks and agreements).
- **Tenant Management**: Interface to create, configure, update, activate/deactivate, and switch active organization context.
- **Platform Usage Metrics (`/platform-admin/metrics/`)**: Multi-tenant metrics for attendance sessions/hours, application pipeline status, purchase orders, outreach, and role distribution.
- **System & Isolation Diagnostics (`/platform-admin/health/`)**: Scans database tables across all domain apps to detect unscoped (`organization__isnull=True`) records, verify database connectivity, and report compliance bottlenecks.
- **User Support Directory (`/platform-admin/users/`)**: Global search across all tenants by name, username, email, phone, or Andrew ID, with account status toggling.
- **Support Impersonation (`/platform-admin/impersonate/<id>/`)**: Enables superusers to log into any regular user account for troubleshooting without knowing their credentials. Safeguards prevent impersonating other superusers, record audit events (`USER_IMPERSONATED` and `IMPERSONATION_ENDED`), display persistent warning banners, and support one-click termination (`/platform-admin/impersonate/stop/`).
- **Cross-Tenant Audit Log Explorer (`/platform-admin/audit/`)**: Searchable and filterable history of security events, administrative updates, and access records.

---

## Architecture Invariants

- **No Cross-Tenant Data Leaks**: Autocomplete endpoints, dropdowns, searches, and exports must strictly filter by `request.organization`.
- **Eager Loading & FK Integrity**: Deleting an organization sets foreign keys to `NULL` (`on_delete=SET_NULL`), preventing cascade deletion of historical records.
- **New Models Requirement**: Any new domain model representing student, program, financial, or operational data must include the nullable `organization` FK and use `OrganizationManager`.
