# Permissions, Roles, and Access Control

This document defines the roles, dynamic permissions system, and access control invariants across the portal.

## Roles Hierarchy & Resolution

User roles are evaluated dynamically by `get_user_role(user)` in `programs/permission_views.py`. 

For accounts with memberships, the priority order of active roles in the
requested organization is:
1. **`LeadMentor`**: An active `LeadMentor` membership in the organization, or a Django superuser. The legacy group is used only when the user has no membership rows. Lead Mentors serve as organization administrators.
2. **`Mentor`**: An active `Mentor` membership.
3. **`Parent`**: An active `Parent` membership.
4. **`Alumni`**: An active `Alumni` membership.
5. **`Student`**: An active `Student` membership.
6. **`Staff` / None**: Staff membership or no applicable role.

### Organization Scoping and the Transitional Legacy Fallback

Role and boolean-helper checks (`get_user_role`, `user_is_*`) resolve within an
organization: the `org` argument when passed, otherwise the default
organization (`DEFAULT_ORGANIZATION_SLUG`).

Scoping rules:

- **Superusers are org-agnostic.** The legacy `LeadMentor` group does not
  override an existing membership or grant a role in another organization.
- **Users with any `UserOrganizationMembership` rows are strictly scoped.**
  When they hold no active membership in the target organization, the role is
  `None` / the helper is `False` — never a fall-through to global profile or
  group flags. This is what enforces cross-organization isolation.
- **Users with no membership rows anywhere** (accounts predating membership
  sync) fall back to the legacy Adult/Student profile and role-group
  resolution in the helpers, including the legacy `LeadMentor` group. This
  fallback does **not** grant authenticated
  tenant-host access: the membership middleware requires an active membership
  even for legacy profiles and `LeadMentor` group members. It is
  **transitional**: once `python manage.py backfill_profile_memberships` has
  covered every profile, no-row users no longer exist and the fallback can be
  removed.

The tenant request guard checks active membership independently of role
resolution, after login enforcement and before mentor-agreement enforcement.
It allows platform superusers and the narrowly scoped onboarding and webhook
routes described in `docs/architecture/organizations.md`; it does not replace
view, object, or independently authenticated API permission checks. Revoking
the last active role immediately blocks subsequent tenant requests. Review
legacy, inactive, and group-only accounts before rollout to avoid lockouts.

### Membership Sync

`programs/signals.py` keeps `UserOrganizationMembership` rows in sync with
profiles, mirroring the backfill in organizations migration 0003:

- Saving an `Adult` creates `Mentor` (active follows `mentor_active`),
  `Parent`, and `Alumni` memberships in the Adult's organization (or the
  default organization when unset).
- Saving a `Student` creates a `Student` membership (active follows
  `graduated`).
- Flipping a flag **off** (e.g. `mentor_active`) deactivates the matching
  membership; profile saves **never re-activate** a deactivated membership.
  Reactivation is an explicit platform-admin or invitation action.
- Existing rows are otherwise untouched (idempotent `get_or_create`).

Profiles created before the sync existed can be backfilled with
`python manage.py backfill_profile_memberships` (idempotent; see
`docs/operations.md`).

### Multi-Role Helpers
Because an Adult can hold multiple responsibilities simultaneously (e.g., a parent who also mentors), `get_user_role` alone does not convey all capabilities. Always use the boolean helpers in `programs/permission_views.py`:
- `user_is_parent(user)`: True if the user has any linked students.
- `user_is_mentor(user)`: True if the user is a mentor.
- `user_is_alumni(user)`: True if the user is marked as alumni.
- `user_is_mentor_or_lead(user)`: True if the user is a Mentor or Lead Mentor.

Finance and payment views treat any user with parent status as a parent even if their primary resolved role is `Mentor` or `Alumni`.

### The LeadMentor Role
- Group name is strictly `"LeadMentor"` (singular, no space).
- Decoupled from Django `is_superuser` and `is_staff`, which are reserved for system administrators.
- Lead Mentors are managed via Portal Settings (`/programs/settings/?tab=lead_mentors`).

---

## Dynamic Permissions System (`RolePermission`)

Lead Mentors can customize read and write permissions for each role per organization using the `RolePermission` model (`programs/models/`).

### Permission Checking Helpers
Always check permissions using the core service functions:
- `can_user_read(user, section, obj=None, org=None)`
- `can_user_write(user, section, obj=None, org=None)`
- `can_user_delete(user, section, obj=None, org=None)`

When `org` is omitted, it is automatically inferred from `obj.organization` or `obj.program.organization`.

### Template Tags
Evaluate permissions inside Django templates using:
- `{% can_read user 'section' [obj] %}`
- `{% can_write user 'section' [obj] %}`
- `{% can_delete user 'section' [obj] %}`

---

## View Mixins (`programs/permission_views.py`)

Do **not** use raw Django `has_perm()` checks or decorator checks. Use the standard mixins:
- `LeadMentorRequiredMixin`: Restricts the view strictly to Lead Mentors.
- `DynamicReadPermissionMixin`: Gated on `can_user_read` for the specified `permission_section`.
- `DynamicWritePermissionMixin`: Gated on `can_user_write` for the specified `permission_section`.
- `LoginRequiredMixin`: Standard authentication check (enforced globally via middleware).

Application and guest-form review is restricted to a Lead Mentor in the
resolved organization or a platform superuser. Global Django reviewer
permissions do not grant access in another organization's tenant context; a
missing organization context is denied.

Badge prerequisite choices and rendered prerequisite links are restricted to
the resolved organization. Program alumni routes validate the program against
that same organization before including its name in the page.

Guest-form review requires both the submission and its linked form to belong
to the resolved organization. Tenant-owned forms with foreign-owned submissions
cannot be deleted until the inconsistent links are reviewed; displayed counts
include only local submissions.

---

## Object-Level Access Rules

When checking access for a specific instance, **always pass `obj`** to `can_user_read` and `can_user_write`.
If an explicit request organization is supplied, the object must belong to that same organization; mismatches are denied before role permissions are evaluated.

### Students
- Read-only access to their own profile, attendance, enrollments, and balances.
- Can create purchase requests (`orders-request`) and edit unassigned items they created.
- Can sign up as champions or helpers on outreach shifts and travel events.
- Write access to outreach events/shifts is restricted to shifts they champion.

### Parents
- Read access to their own linked students (`adult.students.all()`).
- Can apply for sliding-scale discounts and view student balance sheets.
- Can approve/decline travel signups only for their own students (`parent.students.filter(pk=...)`).
- Can self-register as parent chaperones for programs where they have active students.

### Mentors
- Read access to current and upcoming programs (`active=True` and status in `"Active"`, `"Upcoming"`). Past/ended programs remain visible on lists as plain text without active management links.
- Read access to parents (`Adult` with `is_parent=True`) who have children in active programs.
- Can send program-wide emails for programs they have permission to read.
- Read and write access to `attendance` (marking presence, operating sign-outs, closing stale sessions).
- Deletion of records is restricted (`can_user_delete` is Lead Mentor only).

### Order Request Granular Sections (`ORDERS_SECTIONS`)
Order management uses four fine-grained permission sections:
- `orders-view`: Master view toggle to see requested orders.
- `orders-request`: Permission to place new item requests and edit own unassigned items.
- `orders-manage`: Permission for mentors to group requests into vendor orders.
- `orders-shipping`: Permission to record shipping and carrier tracking information.
