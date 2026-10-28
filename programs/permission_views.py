from django.contrib import messages
from django.contrib.auth.mixins import UserPassesTestMixin
from django.shortcuts import redirect

from .models import (
    Adult,
    Enrollment,
    Fee,
    Payment,
    Program,
    RolePermission,
    SlidingScale,
    Student,
)

# Granular order permissions surfaced on the Portal Settings page. Each action
# section uses one column: ``orders-view`` keys off ``can_read`` (seeing the
# order pages); the others key off ``can_write`` (doing the action).
ORDERS_SECTIONS = {
    "orders-view": "view",
    "orders-request": "request",
    "orders-manage": "manage",
    "orders-shipping": "shipping",
}


def get_user_role(user):
    """
    Determines the role of a user for permission purposes.
    Returns 'LeadMentor', 'Mentor', 'Parent', 'Student', or None.
    """
    if user.is_superuser or user.groups.filter(name="LeadMentor").exists():
        return "LeadMentor"

    # Check if the user is linked to an Adult profile
    try:
        adult = user.adult_profile
        if adult.mentor_active and adult.is_mentor:
            return "Mentor"
        if adult.is_parent:
            return "Parent"
        if adult.is_alumni:
            return "Alumni"
    except (Adult.DoesNotExist, AttributeError):
        pass

    # Check if the user is linked to a Student profile.
    # The profile is accessed without assignment intentionally: if the attribute
    # exists the reverse accessor succeeds and we return "Student"; if not,
    # it raises DoesNotExist (or AttributeError for anonymous users) and we fall
    # through to the group-based fallback below.
    try:
        user.student_profile
        return "Student"
    except (Student.DoesNotExist, AttributeError):
        pass

    # Check groups if profile link is missing or doesn't specify
    if user.groups.filter(name="Mentor").exists():
        return "Mentor"
    if user.groups.filter(name="Parent").exists():
        return "Parent"
    if user.groups.filter(name="Student").exists():
        return "Student"

    return None


def _user_adult_flag(user, field, group_name):
    """True if the user's Adult profile has ``field`` set (or the legacy
    ``group_name`` group as a fallback). Unlike ``get_user_role`` this ignores
    role priority, so an Adult who is flagged as several roles at once (e.g. a
    parent who also mentors) is still recognized for each role they hold."""
    if user is None or not getattr(user, "is_authenticated", False):
        return False
    try:
        adult = user.adult_profile
        if getattr(adult, field):
            # If checking mentor status, also require the mentor to be active
            if field == "is_mentor" and not adult.mentor_active:
                return False
            return True
    except (Adult.DoesNotExist, AttributeError):
        pass
    return user.groups.filter(name=group_name).exists()


def user_is_parent(user):
    """True if the user is a parent/guardian, regardless of other Adult flags.

    ``get_user_role`` collapses a multi-role Adult to a single role with Mentor
    taking priority, so parent-only features (Payments page, balance sheets)
    must check this helper rather than the role string.
    """
    return _user_adult_flag(user, "is_parent", "Parent")


def user_is_mentor(user):
    """True if the user serves as a mentor/volunteer, regardless of other
    Adult flags."""
    return _user_adult_flag(user, "is_mentor", "Mentor")


def user_is_alumni(user):
    """True if the user is a program alumni, regardless of other Adult flags."""
    return _user_adult_flag(user, "is_alumni", "Alumni")


def user_is_mentor_or_lead(user):
    """True if the user is a Lead Mentor (superuser or LeadMentor group) or an
    active mentor. Convenience helper for ``user_is_mentor(user) or
    get_user_role(user) == "LeadMentor"`` checks."""
    if user is None:
        return False
    if user.is_superuser or user.groups.filter(name="LeadMentor").exists():
        return True
    return user_is_mentor(user)


def can_user_read(user, section, obj=None):
    role = get_user_role(user)
    if role == "LeadMentor":
        return True
    if role is None:
        return False

    # Order requests: students and mentors read by default; the 'orders-view'
    # RolePermission row (configured from Portal Settings) lets Lead Mentors
    # revoke read access per role. Parents and alumni never see orders. The
    # action sections ('orders-request'/'-manage'/'-shipping') are write-gated;
    # page access is governed by 'orders-view'. Without the program's 'orders'
    # feature the views 404 first anyway.
    if section in ORDERS_SECTIONS:
        if role not in ("Student", "Mentor"):
            return False
        if section == "orders-view":
            perm = RolePermission.objects.filter(
                role=role, section="orders-view"
            ).first()
            return perm.can_read if perm else True
        return True

    # Always allow reading own profile and children
    if obj:
        if isinstance(obj, Student):
            # Own student profile
            if hasattr(user, "student_profile") and obj == user.student_profile:
                return True
            # Own child
            try:
                if user.adult_profile.students.filter(pk=obj.pk).exists():
                    return True
            except (Adult.DoesNotExist, AttributeError):
                pass
        if isinstance(obj, Adult):
            # Own adult profile
            if hasattr(user, "adult_profile") and obj == user.adult_profile:
                return True

    is_parent = user_is_parent(user)

    # Finance sections use the Parent role's permission config for anyone who
    # is a parent, even if they also hold mentor/alumni flags (get_user_role
    # would report those roles instead).
    if is_parent and section in ["payments", "sliding_scale", "fees"]:
        perm = RolePermission.objects.filter(role="Parent", section=section).first()
        can_read_section = perm.can_read if perm else True
    else:
        perm = RolePermission.objects.filter(role=role, section=section).first()
        # Default to True for read, except for attendance for mentors
        default_read = True
        if role == "Mentor" and section == "attendance":
            default_read = False
        if role == "Mentor" and section == "team_assignments":
            default_read = False
        if role == "Mentor" and section in ("badge_award", "badge_manage"):
            default_read = False
        if role == "Student" and section == "outreach":
            default_read = True
        if role == "Alumni" and section == "calendar":
            default_read = False
        can_read_section = perm.can_read if perm else default_read

    # Only Lead Mentors and Parents can view payments/fees/sliding scale
    if section in ["payments", "sliding_scale", "fees"]:
        if role != "LeadMentor" and not is_parent:
            return False

    if not can_read_section:
        return False

    # Team assignments are mentor-only (besides LeadMentor) and program-scoped
    if section == "team_assignments":
        if role not in ("Mentor", "LeadMentor"):
            return False
        if role == "Mentor" and obj and isinstance(obj, Program):
            if obj.status not in ("Active", "Upcoming"):
                return False

    if section in ("badge_award", "badge_manage"):
        if role not in ("Mentor", "LeadMentor"):
            return False

    # Object-level restriction for Parents — including parents who also carry
    # the mentor/alumni flags — on finance sections. The single-role branches
    # below don't cover dual-role adults, so finance access must always be
    # scoped to the parent's own students.
    if is_parent and obj and section in ["payments", "sliding_scale", "fees"]:
        try:
            adult = user.adult_profile
            if isinstance(obj, Student):
                # Parents can only read their own students
                return obj in adult.students.all()
            if isinstance(obj, Adult):
                # Parents can only read their own profile
                return obj == adult
            if isinstance(obj, (Payment, SlidingScale)):
                # Parents can only read their own students' payments/sliding scale
                return obj.student in adult.students.all()
            if isinstance(obj, Fee):
                # Parents can see fees for programs their students are enrolled in
                return Enrollment.objects.filter(
                    student__adults=adult, program=obj.program
                ).exists()
            if isinstance(obj, Program):
                # Parents can see programs their students are enrolled in
                return obj.enrollment_set.filter(student__adults=adult).exists()
        except (Adult.DoesNotExist, AttributeError):
            return False

    # Object-level restriction for Parents, Alumni, and Students
    if role == "Parent" and obj:
        try:
            adult = user.adult_profile
            if isinstance(obj, Student):
                # Parents can only read their own students
                return obj in adult.students.all()
            if isinstance(obj, Adult):
                # Parents can only read their own profile
                return obj == adult
            if isinstance(obj, (Payment, SlidingScale)):
                # Parents can only read their own students' payments/sliding scale
                return obj.student in adult.students.all()
            if isinstance(obj, Fee):
                # Parents can see fees for programs their students are enrolled in
                return Enrollment.objects.filter(
                    student__adults=adult, program=obj.program
                ).exists()
            if isinstance(obj, Program):
                # Parents can see programs their students are enrolled in
                return obj.enrollment_set.filter(student__adults=adult).exists()
        except (Adult.DoesNotExist, AttributeError):
            return False
    elif role == "Alumni" and obj:
        try:
            adult = user.adult_profile
            if isinstance(obj, Adult):
                return obj == adult
            if isinstance(obj, Student):
                # Alumni can see their own student record
                return adult.student_record == obj
            if isinstance(obj, Program):
                # Alumni cannot view programs directly
                return False
        except (Adult.DoesNotExist, AttributeError):
            return False
    elif role == "Student" and obj:
        try:
            student = user.student_profile
            if isinstance(obj, Student):
                # Students can only read their own profile
                return obj == student
            if isinstance(obj, Adult):
                # TODO: Design decision — students cannot view adult profiles
                # directly (even their own parents') to keep adult contact
                # information private. Revisit if a "view my guardians" feature
                # is ever added.
                return False
            if isinstance(obj, Program):
                # Students can see programs they are enrolled in
                return obj.enrollment_set.filter(student=student).exists()
        except (Student.DoesNotExist, AttributeError):
            return False
    elif role == "Mentor" and obj:
        if isinstance(obj, Program):
            # Mentors can view current and upcoming programs (e.g. to review
            # rosters before a program starts); past/inactive stay hidden.
            return obj.status in ("Active", "Upcoming")
        if isinstance(obj, Adult):
            # Mentors can only view Parents with a student in an active program
            if not obj.is_parent:
                return False
            return obj.students.filter(enrollment__program__active=True).exists()

    return can_read_section


def can_user_write(user, section, obj=None):
    role = get_user_role(user)
    if role == "LeadMentor":
        return True
    if role == "Student" and section == "outreach":
        if obj:
            from outreach.models import OutreachEvent, OutreachShift, OutreachSignup

            # A user with the Student group but no linked student profile (e.g.
            # a wiped/never-restored link) can never be a champion. Guard the
            # reverse OneToOne accessor — it raises instead of returning None.
            try:
                student = user.student_profile
            except Student.DoesNotExist:
                student = None
            if isinstance(obj, OutreachEvent):
                return (
                    student is not None
                    and OutreachSignup.objects.filter(
                        shift__event=obj,
                        student=student,
                        role=OutreachSignup.CHAMPION,
                    ).exists()
                )
            if isinstance(obj, OutreachShift):
                return (
                    student is not None
                    and OutreachSignup.objects.filter(
                        shift=obj,
                        student=student,
                        role=OutreachSignup.CHAMPION,
                    ).exists()
                )
        return True
    if role == "Mentor" and section == "outreach":
        return True
    if role is None:
        return False

    # Travel: students and mentors may sign up (mentors to go along, students
    # as interested passengers); a parent may only act on their own children's
    # signups (approve/decline/undo). Parents never create trips and alumni
    # never write travel at all.
    if role == "Student" and section == "travel":
        return True
    if role == "Mentor" and section == "travel":
        return True
    if role == "Parent" and section == "travel":
        if obj:
            from travel.models import TravelParentChaperoneSignup, TravelSignup

            if isinstance(obj, TravelSignup):
                try:
                    adult = user.adult_profile
                except Adult.DoesNotExist:
                    return False
                return adult.students.filter(pk=obj.student_id).exists()
            if isinstance(obj, TravelParentChaperoneSignup):
                try:
                    adult = user.adult_profile
                except Adult.DoesNotExist:
                    return False
                return obj.adult_id == adult.pk
        return False
    if role == "Alumni" and section == "travel":
        return False

    # Order requests: students and mentors may create requests (items) and edit
    # their own items while they are still unassigned to an order; once an item
    # is grouped into an order it can only be changed by a Lead Mentor.
    # Mentors may additionally create and manage their own pending orders
    # (groupings), and - with the 'orders-shipping' toggle - add shipping to
    # placed orders; students never manage orders or add shipping. Parents/
    # alumni never write orders. Each granular RolePermission section (Portal
    # Settings) acts as a master switch for the corresponding action; the
    # per-object ownership rules below still gate individual records.
    if section in ORDERS_SECTIONS:
        if role in ("Parent", "Alumni"):
            return False
        if role not in ("Student", "Mentor"):
            return False
        if role == "Student" and section in (
            "orders-view",
            "orders-manage",
            "orders-shipping",
        ):
            # Students only place item requests; they never manage grouped
            # orders, add shipping, or use the (view-only) section's write
            # column.
            return False
        perm = RolePermission.objects.filter(role=role, section=section).first()
        if section == "orders-view":
            # Viewing only; the write column mirrors view permission.
            return perm.can_write if perm else True
        if not (perm.can_write if perm else True):
            return False
        if obj:
            from orders.models import Order, OrderItem

            if isinstance(obj, OrderItem):
                if section == "orders-shipping":
                    return False
                if section == "orders-request":
                    return obj.requested_by_id == user.pk and obj.order_id is None
                # orders-manage acts on the item's order

                if obj.order_id:
                    return (
                        obj.order.created_by_id == user.pk
                        and obj.order.status == Order.STATUS_PENDING
                    )
                return False
            if isinstance(obj, Order):
                if section == "orders-request":
                    return False
                if section == "orders-shipping":
                    return True
                # orders-manage
                return (
                    obj.created_by_id == user.pk and obj.status == Order.STATUS_PENDING
                )
        return True

    # Background checks are read-only for every role except Lead Mentors/admins.
    # This must run before the "own profile and children" shortcut below, which
    # would otherwise let parents/mentors/students edit their own clearances.
    if section == "background_checks":
        return False

    # Always allow writing own profile and children
    if obj:
        if isinstance(obj, Student):
            # Own student profile
            if hasattr(user, "student_profile") and obj == user.student_profile:
                return True
            # Own child
            try:
                if user.adult_profile.students.filter(pk=obj.pk).exists():
                    return True
            except (Adult.DoesNotExist, AttributeError):
                pass
        if isinstance(obj, Adult):
            # Own adult profile
            if hasattr(user, "adult_profile") and obj == user.adult_profile:
                return True

    # Section specific write permission
    perm = RolePermission.objects.filter(role=role, section=section).first()
    can_write_section = perm.can_write if perm else False

    # Only Lead Mentors can write to payments/fees/sliding scale
    if section in ["payments", "sliding_scale", "fees"]:
        if role != "LeadMentor":
            return False

    if role == "Mentor" and section == "student_info":
        return False

    if not can_write_section:
        return False

    # Team assignments are mentor-only (besides LeadMentor) and program-scoped
    if section == "team_assignments":
        if role not in ("Mentor", "LeadMentor"):
            return False
        if role == "Mentor" and obj and isinstance(obj, Program):
            if obj.status not in ("Active", "Upcoming"):
                return False

    if section in ("badge_award", "badge_manage"):
        if role not in ("Mentor", "LeadMentor"):
            return False

    # Object-level restriction for Parents and Students
    if role == "Parent" and obj:
        try:
            adult = user.adult_profile
            if isinstance(obj, Student):
                return obj in adult.students.all()
            if isinstance(obj, Adult):
                return obj == adult
        except (Adult.DoesNotExist, AttributeError):
            return False
    elif role == "Alumni" and obj:
        try:
            adult = user.adult_profile
            if isinstance(obj, Adult):
                return obj == adult
            # Optionally allow alumni to see their own student record
            if isinstance(obj, Student):
                return adult.student_record == obj
        except (Adult.DoesNotExist, AttributeError):
            return False
    elif role == "Student" and obj:
        try:
            student = user.student_profile
            if isinstance(obj, Student):
                return obj == student
        except (Student.DoesNotExist, AttributeError):
            return False

    return can_write_section


def can_user_delete(user, section, obj=None):
    """
    Returns True if the user may delete records in the given section.

    Deletion rules are stricter than write rules:
    - LeadMentors can always delete.
    - Mentors are explicitly blocked from deleting attendance records, even if
      they have write (add/edit) access.
    - All other roles follow the same object-level write restrictions but
      deletion is denied if the role cannot write to the section at all.
    """
    role = get_user_role(user)
    if role == "LeadMentor":
        return True
    if role is None:
        return False

    # Mentors can add/edit attendance but never delete it
    if role == "Mentor" and section == "attendance":
        return False

    # Students can never delete outreach events
    if role == "Student" and section == "outreach":
        return False

    # Only Lead Mentors can delete travel trips
    if section == "travel":
        return False

    # Order requests can only be deleted by Lead Mentors
    if section in ORDERS_SECTIONS:
        return False

    # For all other sections/roles, delete tracks write permission
    return can_user_write(user, section, obj=obj)


class LeadMentorRequiredMixin(UserPassesTestMixin):
    def test_func(self):
        return (
            self.request.user.is_superuser
            or self.request.user.groups.filter(name="LeadMentor").exists()
        )

    def handle_no_permission(self):
        if self.request.user.is_authenticated:
            messages.error(
                self.request, "You do not have permission to access that section."
            )
            return redirect("home")
        return super().handle_no_permission()


class TeamAssignmentPermissionMixin(UserPassesTestMixin):
    """Allows LeadMentors always, and Mentors only for active/upcoming programs
    when they have ``team_assignments`` write permission."""

    def test_func(self):
        user = self.request.user
        # Need program pk from URL kwargs (``pk``)
        pk = self.kwargs.get("pk") or self.kwargs.get("program_id")
        if pk is None:
            # Fallback: deny if we cannot resolve program context
            return False
        from programs.models import Program

        try:
            program = Program.objects.get(pk=pk)
        except Program.DoesNotExist:
            return False
        return can_user_write(user, "team_assignments", program)

    def handle_no_permission(self):
        if self.request.user.is_authenticated:
            messages.error(
                self.request, "You do not have permission to access that section."
            )
            return redirect("home")
        return super().handle_no_permission()


class MentorOrLeadMentorRequiredMixin(UserPassesTestMixin):
    def test_func(self):
        return user_is_mentor_or_lead(self.request.user)

    def handle_no_permission(self):
        if self.request.user.is_authenticated:
            messages.error(
                self.request, "You do not have permission to access that section."
            )
            return redirect("home")
        return super().handle_no_permission()


class PassUserToFormMixin:
    """
    Mixin to pass the current user to the form's kwargs.
    Used by AdultForm to restrict field access.
    """

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["user"] = self.request.user
        return kwargs


def __getattr__(name):
    if name in (
        "PortalAgreementView",
        "PortalCrewView",
        "PortalEmailSettingsView",
        "PortalKioskView",
        "PortalPermissionsUpdateView",
        "PortalSettingsView",
        "PortalSlidingScaleSettingsView",
        "PortalSubteamView",
        "PortalTeamView",
    ):
        from .views import portal_settings

        return getattr(portal_settings, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
