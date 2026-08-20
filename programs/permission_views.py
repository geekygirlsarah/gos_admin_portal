from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.shortcuts import redirect, render
from django.views import View

from programs.constants import TEAM_TYPES

from .models import (
    Adult,
    Crew,
    Enrollment,
    Fee,
    MentorAgreement,
    Payment,
    Program,
    RolePermission,
    SlidingScale,
    SlidingScaleSettings,
    Student,
    SubTeam,
    Team,
)

try:
    from attendance.models import KioskConfig
except ImportError:
    KioskConfig = None


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


def can_user_read(user, section, obj=None):
    role = get_user_role(user)
    if role == "LeadMentor":
        return True
    if role is None:
        return False

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
        can_read_section = perm.can_read if perm else default_read

    # Only Lead Mentors and Parents can view payments/fees/sliding scale
    if section in ["payments", "sliding_scale", "fees"]:
        if role != "LeadMentor" and not is_parent:
            return False

    if not can_read_section:
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
                # Parents cannot view programs directly
                return False
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
                # Parents cannot view programs directly
                return False
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
                # Students cannot view programs directly
                return False
        except (Student.DoesNotExist, AttributeError):
            return False
    elif role == "Mentor" and obj:
        if isinstance(obj, Program):
            # Mentors can only view active programs
            return obj.status == "Active"
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
    if role is None:
        return False

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


class MentorOrLeadMentorRequiredMixin(UserPassesTestMixin):
    def test_func(self):
        return get_user_role(self.request.user) in ("LeadMentor", "Mentor")

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


class PortalSettingsView(LoginRequiredMixin, LeadMentorRequiredMixin, View):
    template_name = "programs/settings.html"

    def get(self, request):
        sections = RolePermission.SECTION_CHOICES
        roles = RolePermission.ROLE_CHOICES

        # Ensure all combinations exist
        for role_code, role_name in roles:
            for section_code, section_name in sections:
                RolePermission.objects.get_or_create(
                    role=role_code, section=section_code
                )

        permissions = RolePermission.objects.all()

        # Group permissions by section for the new table layout
        grouped_permissions = []
        for section_code, section_name in sections:
            grouped_permissions.append(
                {
                    "name": section_name,
                    "mentor": permissions.filter(
                        section=section_code, role="Mentor"
                    ).first(),
                    "parent": permissions.filter(
                        section=section_code, role="Parent"
                    ).first(),
                    "student": permissions.filter(
                        section=section_code, role="Student"
                    ).first(),
                }
            )

        teams = Team.objects.all()
        team_types = TEAM_TYPES
        crews = Crew.objects.select_related("program").all()
        subteams = SubTeam.objects.select_related("program").all()
        programs = Program.objects.all().order_by("name")
        attendance_programs = [p for p in programs if p.has_feature("attendance")]

        kiosk_configs = None
        if KioskConfig:
            kiosk_configs = KioskConfig.objects.select_related("program").all()

        context = {
            "grouped_permissions": grouped_permissions,
            "teams": teams,
            "team_types": team_types,
            "crews": crews,
            "subteams": subteams,
            "programs": programs,
            "attendance_programs": attendance_programs,
            "kiosk_configs": kiosk_configs,
            "sliding_scale_settings": SlidingScaleSettings.get_solo(),
            "pending_sliding_scale_count": SlidingScale.objects.filter(
                status=SlidingScale.STATUS_PENDING
            ).count(),
            "mentor_agreements": MentorAgreement.objects.order_by("slug", "-version"),
            "mentor_agreement_slugs": MentorAgreement.objects.values_list(
                "slug", flat=True
            ).distinct(),
            "role": "LeadMentor",  # Required for base.html to show Nav correctly
            "active_tab": request.GET.get("tab", "permissions"),
            "sections": sections,
        }
        return render(request, self.template_name, context)


class PortalSlidingScaleSettingsView(LoginRequiredMixin, LeadMentorRequiredMixin, View):
    """Handles updates to the portal-wide sliding scale calculation settings."""

    def post(self, request):
        from decimal import Decimal, InvalidOperation

        settings_obj = SlidingScaleSettings.get_solo()
        fields = [
            "base_amount",
            "additional_member_amount",
            "low_multiplier",
            "high_multiplier",
        ]
        try:
            for field in fields:
                value = request.POST.get(field)
                setattr(settings_obj, field, Decimal(value))
        except (TypeError, InvalidOperation):
            messages.error(request, "Please enter valid numbers for all fields.")
            return redirect("/programs/settings/?tab=sliding_scale_settings")

        settings_obj.save()
        messages.success(request, "Sliding scale settings updated successfully.")
        return redirect("/programs/settings/?tab=sliding_scale_settings")


class PortalPermissionsUpdateView(LoginRequiredMixin, LeadMentorRequiredMixin, View):
    """Handles the 'update_permissions' action from the settings page."""

    def post(self, request):
        permissions = RolePermission.objects.all()
        for perm in permissions:
            read_key = f"read_{perm.id}"
            write_key = f"write_{perm.id}"

            perm.can_read = read_key in request.POST
            perm.can_write = write_key in request.POST
            perm.save()
        messages.success(request, "Permissions updated successfully.")
        return redirect("/programs/settings/?tab=permissions")


class PortalTeamView(LoginRequiredMixin, LeadMentorRequiredMixin, View):
    """Handles add/delete/update actions for Teams from the settings page."""

    def post(self, request):
        action = request.POST.get("action")

        if action == "add_team":
            team_type = request.POST.get("team_type")
            number = request.POST.get("number")
            name = request.POST.get("name")
            color = request.POST.get("color")
            if team_type and number:
                Team.objects.create(
                    team_type=team_type, number=number, name=name, color=color
                )
                messages.success(request, f"Team {team_type} {number} added.")
            return redirect("/programs/settings/?tab=teams")

        elif action == "delete_team":
            team_id = request.POST.get("team_id")
            if team_id:
                Team.objects.filter(id=team_id).delete()
                messages.success(request, "Team deleted.")
            return redirect("/programs/settings/?tab=teams")

        elif action == "update_team":
            team_id = request.POST.get("team_id")
            team_type = request.POST.get("team_type")
            number = request.POST.get("number")
            name = request.POST.get("name")
            color = request.POST.get("color")
            if team_id:
                team = Team.objects.filter(id=team_id).first()
                if team:
                    team.team_type = team_type
                    team.number = number
                    team.name = name
                    team.color = color
                    team.save()
                    messages.success(request, "Team updated.")
            return redirect("/programs/settings/?tab=teams")

        return redirect("/programs/settings/?tab=teams")


class PortalCrewView(LoginRequiredMixin, LeadMentorRequiredMixin, View):
    """Handles add/delete/update actions for Crews from the settings page."""

    def post(self, request):
        action = request.POST.get("action")

        if action == "add_crew":
            program_id = request.POST.get("program_id")
            name = request.POST.get("name")
            color = request.POST.get("color")
            if program_id and name:
                Crew.objects.create(program_id=program_id, name=name, color=color)
                messages.success(request, f"Crew {name} added.")
            return redirect("/programs/settings/?tab=crews")

        elif action == "delete_crew":
            crew_id = request.POST.get("crew_id")
            if crew_id:
                Crew.objects.filter(id=crew_id).delete()
                messages.success(request, "Crew deleted.")
            return redirect("/programs/settings/?tab=crews")

        elif action == "update_crew":
            crew_id = request.POST.get("crew_id")
            name = request.POST.get("name")
            color = request.POST.get("color")
            if crew_id:
                crew = Crew.objects.filter(id=crew_id).first()
                if crew:
                    crew.name = name
                    crew.color = color
                    crew.save()
                    messages.success(request, "Crew updated.")
            return redirect("/programs/settings/?tab=crews")

        return redirect("/programs/settings/?tab=crews")


class PortalSubteamView(LoginRequiredMixin, LeadMentorRequiredMixin, View):
    """Handles add/delete/update actions for SubTeams from the settings page."""

    def post(self, request):
        action = request.POST.get("action")

        if action == "add_subteam":
            program_id = request.POST.get("program_id")
            name = request.POST.get("name")
            color = request.POST.get("color")
            if program_id and name:
                SubTeam.objects.create(program_id=program_id, name=name, color=color)
                messages.success(request, f"SubTeam {name} added.")
            return redirect("/programs/settings/?tab=subteams")

        elif action == "delete_subteam":
            subteam_id = request.POST.get("subteam_id")
            if subteam_id:
                SubTeam.objects.filter(id=subteam_id).delete()
                messages.success(request, "SubTeam deleted.")
            return redirect("/programs/settings/?tab=subteams")

        elif action == "update_subteam":
            subteam_id = request.POST.get("subteam_id")
            name = request.POST.get("name")
            color = request.POST.get("color")
            if subteam_id:
                subteam = SubTeam.objects.filter(id=subteam_id).first()
                if subteam:
                    subteam.name = name
                    subteam.color = color
                    subteam.save()
                    messages.success(request, "SubTeam updated.")
            return redirect("/programs/settings/?tab=subteams")

        return redirect("portal_settings")


class PortalKioskView(LoginRequiredMixin, LeadMentorRequiredMixin, View):
    """Handles add/delete/toggle actions for Kiosks from the settings page."""

    def post(self, request):
        action = request.POST.get("action")

        if action == "add_kiosk_config" and KioskConfig:
            label = request.POST.get("label", "").strip()
            program_id = request.POST.get("program_id")
            if label and program_id:
                KioskConfig.objects.create(
                    label=label,
                    program_id=program_id,
                )
                messages.success(request, f"Kiosk '{label}' added.")
            return redirect("/programs/settings/?tab=kiosk_configs")

        elif action == "delete_kiosk_config" and KioskConfig:
            kiosk_config_id = request.POST.get("kiosk_config_id")
            if kiosk_config_id:
                KioskConfig.objects.filter(id=kiosk_config_id).delete()
                messages.success(request, "Kiosk configuration deleted.")
            return redirect("/programs/settings/?tab=kiosk_configs")

        elif action == "toggle_kiosk_config" and KioskConfig:
            kiosk_config_id = request.POST.get("kiosk_config_id")
            if kiosk_config_id:
                kiosk = KioskConfig.objects.filter(id=kiosk_config_id).first()
                if kiosk:
                    kiosk.is_active = not kiosk.is_active
                    kiosk.save(update_fields=["is_active"])
                    state = "activated" if kiosk.is_active else "deactivated"
                    messages.success(request, f"Kiosk '{kiosk.label}' {state}.")
            return redirect("/programs/settings/?tab=kiosk_configs")

        return redirect("/programs/settings/?tab=kiosk_configs")


class PortalAgreementView(LoginRequiredMixin, LeadMentorRequiredMixin, View):
    """Handles create / update / toggle / delete actions for Mentor Agreements."""

    def post(self, request):
        from datetime import date

        from .forms import MentorAgreementForm

        action = request.POST.get("action")

        if action == "add_agreement":
            form = MentorAgreementForm(request.POST, request.FILES)
            if form.is_valid():
                slug = form.cleaned_data["slug"]
                version = (
                    MentorAgreement.objects.filter(slug=slug)
                    .order_by("-version")
                    .values_list("version", flat=True)
                    .first()
                )
                version = (version or 0) + 1
                agreement = form.save(commit=False)
                agreement.version = version
                if not agreement.effective_date:
                    agreement.effective_date = date.today()
                agreement.save()
                messages.success(
                    request,
                    f"Agreement '{agreement.title}' created (version {version}).",
                )
            else:
                messages.error(request, "Please correct the errors below.")
            return redirect("/programs/settings/?tab=agreements")

        elif action == "update_agreement":
            agreement_id = request.POST.get("agreement_id")
            try:
                agreement = MentorAgreement.objects.get(pk=agreement_id)
            except MentorAgreement.DoesNotExist:
                messages.error(request, "Agreement not found.")
                return redirect("/programs/settings/?tab=agreements")

            old_content = agreement.content
            old_doc = agreement.document
            form = MentorAgreementForm(request.POST, request.FILES, instance=agreement)
            if form.is_valid():
                new_agreement = form.save(commit=False)
                content_changed = new_agreement.content != old_content
                doc_changed = (
                    "document" in form.changed_data
                    and new_agreement.document
                    and new_agreement.document != old_doc
                )
                if content_changed or doc_changed:
                    # Create new version
                    version = (
                        MentorAgreement.objects.filter(slug=agreement.slug)
                        .order_by("-version")
                        .values_list("version", flat=True)
                        .first()
                    )
                    new_agreement.version = (version or 0) + 1
                    new_agreement.pk = None
                    new_agreement.created_at = None
                    new_agreement.updated_at = None
                    if not new_agreement.effective_date:
                        new_agreement.effective_date = date.today()
                    new_agreement.save()
                    messages.success(
                        request,
                        f"Agreement '{new_agreement.title}' updated as version {new_agreement.version}.",
                    )
                else:
                    # No content change — just update metadata (title, is_active, etc.)
                    form.save()
                    messages.success(
                        request,
                        f"Agreement '{agreement.title}' metadata updated.",
                    )
            else:
                messages.error(request, "Please correct the errors below.")
            return redirect("/programs/settings/?tab=agreements")

        elif action == "toggle_agreement":
            agreement_id = request.POST.get("agreement_id")
            try:
                agreement = MentorAgreement.objects.get(pk=agreement_id)
                agreement.is_active = not agreement.is_active
                agreement.save(update_fields=["is_active"])
                state = "activated" if agreement.is_active else "deactivated"
                messages.success(request, f"Agreement '{agreement.title}' {state}.")
            except MentorAgreement.DoesNotExist:
                messages.error(request, "Agreement not found.")
            return redirect("/programs/settings/?tab=agreements")

        elif action == "delete_agreement":
            agreement_id = request.POST.get("agreement_id")
            try:
                agreement = MentorAgreement.objects.get(pk=agreement_id)
                title = agreement.title
                from programs.models import MentorAgreementAcceptance

                versions = MentorAgreement.objects.filter(slug=agreement.slug)
                MentorAgreementAcceptance.objects.filter(
                    agreement__in=versions
                ).delete()
                count = versions.count()
                versions.delete()
                messages.success(
                    request,
                    f"Agreement '{title}' and {count} version(s) deleted.",
                )
            except MentorAgreement.DoesNotExist:
                messages.error(request, "Agreement not found.")
            return redirect("/programs/settings/?tab=agreements")

        return redirect("/programs/settings/?tab=agreements")
