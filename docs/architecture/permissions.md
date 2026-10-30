# Permissions, Roles, and Access Control

This document defines the roles, dynamic permissions system, and access control invariants across the portal.

## Roles Hierarchy & Resolution

User roles are evaluated dynamically by `get_user_role(user)` in `programs/permission_views.py`. 

The priority order is:
1. **`LeadMentor`**: Evaluated if the user has an active `UserOrganizationMembership` with role `LeadMentor`, is a Django superuser, or belongs to the `LeadMentor` group. Lead Mentors serve as organization administrators.
2. **`Mentor`**: An Adult record with `is_mentor=True` and active status.
3. **`Parent`**: An Adult record linked to at least one student via `AdultStudentRelationship` or with `is_parent=True`.
4. **`Alumni`**: An Adult record with `is_alumni=True`.
5. **`Student`**: A linked `Student` record.
6. **`Staff` / None**: Unassigned or base staff users.

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

---

## Object-Level Access Rules

When checking access for a specific instance, **always pass `obj`** to `can_user_read` and `can_user_write`.

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
