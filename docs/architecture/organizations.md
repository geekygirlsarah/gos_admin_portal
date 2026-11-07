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

### `OrganizationDomain`
Stores custom portal hostnames separately from free-form organization settings:
- Hostnames are normalized and unique across organizations.
- New requests begin in `pending_verification`; the organization proves DNS control with the displayed TXT challenge.
- DNS proof moves a domain to `dns_verified`, but only an operator may activate it after confirming DNS routing, HTTPS, and trusted host admission.
- Only active registry records are used for newly registered custom-domain routing and canonical portal links. Disabling an active record takes effect on the next request, including requests that have a cached hostname mapping.
- Existing GoS aliases and previously configured free-form custom domains remain as temporary compatibility routes; they are not automatically copied into or marked active in the registry. A pending registry request does not interrupt an existing legacy route, but disabling its registry record blocks that route. Inventory, verify, and transition these hosts before removing the compatibility paths.
- Requests reserve their hostname even before DNS verification and after disablement. There is no automatic expiry. Do not treat an unverified reservation as proof of ownership.
- A domain record's hostname and organization are immutable through ordinary model saves. The only approved identity change is the locked, audited recovery transition below; bulk database updates outside that service bypass validation and must not change those fields.

### Recovery of an unverified reservation

1. A lead mentor on the requesting tenant's portal requests recovery of the reserved hostname. Only another tenant's never-verified, never-activated pending reservation is eligible; verified, active, disabled and legacy alias claims are not transferred. Existing free-form custom-domain claims also block recovery.
2. The requesting tenant publishes its independent TXT challenge and checks it in portal settings. Challenges and recovery records are scoped to that tenant. A revoked request cannot be approved; a new request gets a new challenge. Competing tenants may each request recovery, but cannot reuse one another's proof.
3. A root-domain platform superuser reviews the request on the requesting organization's platform detail page. Approval rechecks the reservation identity, status, legacy claims, and the fresh TXT proof while holding the registry lock. Failure or a DNS error leaves ownership unchanged. A competing claim, changed original token, or original verification makes older requests ineligible; revoke and request again if appropriate.
4. Successful approval records the old and new organization IDs in the audit log and retains recovery history and original reservation attribution. The registry row becomes DNS-verified but **not active**. The operator must separately confirm routing, HTTPS, and trusted host admission, and activate it using the existing activation workflow. Until then the recovered domain does not route to the new tenant.

Recovery actions roll back if their audit event cannot be written. Registry transitions lock the hostname row; the database enforces at most one pending recovery per tenant and hostname. SQLite has database-level rather than row-level locking and bounded approval retries. On PostgreSQL, approval takes a share lock on the organization table as well as row locks so legacy settings cannot be inserted or changed during its check; this can briefly delay organization writes while DNS is queried. Other database backends fail closed for recovery approval. Legacy settings remain outside the registry, so routing and activation must continue to reject conflicting claims introduced after approval.

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
- `Model.objects.for_organization(org)`: Returns only records where `organization == org`. Unscoped rows (`organization__isnull=True`) are hidden from every tenant (migration `organizations/0006` backfills existing ones to GoS). `for_organization(None)` (superusers / root domain) returns everything. Under the test runner, new instances created without an organization are assigned the seeded `gos` org by a transitional shim (`GoSAdminPortal/test_runner.py`); new tests should set `organization=` explicitly.

---

## Request Routing & Middleware

`OrganizationMiddleware` (`GoSAdminPortal/middleware.py`) attaches the resolved tenant to `request.organization` on every request.

Resolution uses the validated request hostname only; `HttpRequest.get_host()` enforces Django's `ALLOWED_HOSTS` before routing:
1. **Root Marketing Domain**: The configured `MOMENTUM_ROOT_DOMAIN` (default `localhost` in settings) and its `www` host, plus the Momentum marketing root, have no organization context and serve the platform's public pages.
2. **Legacy Aliases**: Known GoS hosts (such as `portal.girlsofsteelrobotics.org` and `gosadmin.com`) resolve to the active `gos` organization.
3. **Registered Custom Domains**: An exact hostname match against an active `OrganizationDomain` record in the `active` state resolves that tenant. New records require an exact DNS TXT challenge and explicit operator activation after HTTPS and host admission are ready. The hostname must also pass Django `ALLOWED_HOSTS` or a trusted, validated ingress boundary.
4. **Tenant Subdomains**: One tenant label beneath `MOMENTUM_ROOT_DOMAIN` or `.localhost` resolves by matching the organization's `subdomain` or `slug`.
5. **No Match**: An unresolved host has no organization. Middleware returns a direct 404 for non-platform hosts rather than falling back to a session or `DEFAULT_ORGANIZATION_SLUG`. Static and media paths remain exempt; Django's synthetic `testserver` host uses the configured default organization only while tests run.

Existing free-form custom-domain settings and GoS aliases remain temporary compatibility routes during transition; they are not treated as verified registry records. Do not migrate them into the registry as active without inventory and verification. Organization-routing headers are ignored. Do not rely on the hostname as a substitute for membership and object-level authorization; tenant views must still enforce their established access rules.

### Authenticated tenant membership guard

After login enforcement and before mentor-agreement enforcement,
`TenantMembershipGuardMiddleware` returns a direct 403 on a tenant host when an
authenticated non-superuser has no active `UserOrganizationMembership` for the
resolved organization. Any active role is sufficient; a membership in another
organization, an inactive membership, or a legacy role group alone is not.
Membership is checked on each request, so revocation takes effect on the next
request. Platform superusers remain exempt; view permissions and object scoping
still apply to everyone else.

The guard permits the named login, logout, signup, email-confirmation and login
recovery routes, invitation acceptance, the audited stop-impersonation route,
and the toSend webhooks, which retain their existing signature-verification
policy. It does not exempt mixed
public/private prefixes such as `/apply/`, `/guest/`, or `/calendar/`:
anonymous requests retain their existing handling, but authenticated nonmembers
must sign out to use public tenant pages. Static, media and `/health` paths
bypass the guard before session-user resolution; this does not change media
serving or the health view's existing database check.
Root and unknown hosts remain governed by the root-domain guard.

This is a session-user guard, not a replacement for authentication and tenant
authorization in endpoints using kiosk/sign-out cookies, tokens, or other
credentials after middleware runs. Anonymous API requests still reach their
existing endpoint checks; authenticated nonmember sessions are denied there
(apart from the named webhooks). Review independent credential paths separately
for organization isolation. Kiosk routes independently require an active
organization, scope kiosk/program/person/RFID/session records to that tenant,
and use signed, expiring kiosk-and-organization-bound unlock cookies. OTP
issuance and redemption require an eligible active account and use throttles in
the shared cache; production multi-worker deployments must configure a shared
cache backend with atomic `add` and `incr` operations and no premature eviction
of unexpired keys. The default local-memory cache is not sufficient. Existing
kiosk stations must be unlocked again after this change. Digital sign-out
stations remain a separate access-control path and require their own review.
Before deployment, inventory and backfill missing or inactive memberships under the normal
approved operational process: existing legacy-profile and group-only accounts
without an active membership will lose tenant access, including during
impersonation. No automatic backfill occurs.

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

Portal Settings team, crew, subteam, and kiosk configuration lists and mutations are scoped to the resolved organization. Crew, subteam, and kiosk creation only accepts programs owned by that organization. Deleting a team, crew, or subteam is blocked if it would change an enrollment owned by another organization; settings actions fail closed without tenant context.

---

## Platform Administration & Superuser Tools (`organizations/platform_views.py`)

Dedicated platform administration endpoints (`/platform-admin/`) are strictly restricted to active superusers via `SuperuserRequiredMixin`:
- **Domain-Level Isolation**: Platform admin routes are strictly prohibited from being served through tenant subdomains (e.g. `team-name.momentumteam.app`), tenant custom domains, or tenant routing headers. Requests originating from tenant contexts raise `Http404` to maintain boundary separation between the global platform and individual team portals.
- **Dedicated Navigation**: When browsing `/platform-admin/` pages, the navbar switches context to display platform administration actions (Overview, Organizations, Usage Metrics, System Health, User Support, Audit Logs, and Django Admin) and a "Return to Team Portal" action, replacing team-specific dropdowns even for users who hold team leadership roles (e.g. Lead Mentor).
- **Overview Dashboard (`/platform-admin/`)**: Aggregates tenant counts, user totals, active program metrics, health summaries, and recent cross-tenant audit activity.
- **Organization Health & Readiness**: Automated health checks evaluate lead mentor staffing, active program availability, and mentor compliance rates (background checks and agreements).
- **Tenant Management**: Interface to create, configure, update, activate/deactivate, and switch active organization context.
- **Platform Usage Metrics (`/platform-admin/metrics/`)**: Multi-tenant metrics for attendance sessions/hours, application pipeline status, purchase orders, outreach, and role distribution.
- **System & Isolation Diagnostics (`/platform-admin/health/`)**: Scans database tables across all domain apps to detect unscoped (`organization__isnull=True`) records, verify database connectivity, and report compliance bottlenecks.
- **User Support Directory (`/platform-admin/users/`)**: Global search across all tenants by name, username, email, phone, or Andrew ID, with account status toggling.
- **Support Impersonation (`/platform-admin/impersonate/<id>/`)**: Active platform superusers select a specific active organization membership. The platform creates a five-minute, one-use POST handoff to that tenant's canonical HTTPS host. A narrowly CSRF-exempt, read-only bootstrap validates the high-entropy token issued only after a CSRF-protected platform request; a same-origin, CSRF-protected POST then consumes it and binds it to the existing tenant session. The browser returns to the platform, where the original superuser session is rechecked and issues a second one-use proof; a CSRF-protected tenant POST accepts that proof only in the bound tenant session. These steps preserve existing tenant cookies even for custom domains on another site. A separate CSRF-protected confirmation is required before replacing any existing tenant login. The platform session remains a superuser session only on the platform host; the tenant session authenticates as the selected member and cannot elevate to superuser. Every stage rechecks the selected host, account states, and membership; audit failure blocks starting access. Abandoned pending handoffs are closed when replaced. The tenant displays an impersonation banner and ends the tenant session on exit, while the separate platform session remains intact. Audit events record start and end (`USER_IMPERSONATED` and `IMPERSONATION_ENDED`).
- **Cross-Tenant Audit Log Explorer (`/platform-admin/audit/`)**: Searchable and filterable history of security events, administrative updates, and access records.

---

## Architecture Invariants

- **No Cross-Tenant Data Leaks**: Autocomplete endpoints, dropdowns, searches, and exports must strictly filter by `request.organization`.
- **Eager Loading & FK Integrity**: Deleting an organization sets foreign keys to `NULL` (`on_delete=SET_NULL`), preventing cascade deletion of historical records.
- **New Models Requirement**: Any new domain model representing student, program, financial, or operational data must include the nullable `organization` FK and use `OrganizationManager`.
