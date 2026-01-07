from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.shortcuts import redirect, render
from django.views import View

from .models import Adult, Crew, Program, RolePermission, Student, Team

try:
    from api.models import ApiClientKey
except ImportError:
    ApiClientKey = None


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
        if adult.is_mentor:
            return "Mentor"
        if adult.is_parent:
            return "Parent"
    except (Adult.DoesNotExist, AttributeError):
        pass

    # Check if the user is linked to a Student profile
    try:
        student = user.student_profile
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


def can_user_read(user, section):
    role = get_user_role(user)
    if role == "LeadMentor":
        return True
    if role is None:
        return False

    perm = RolePermission.objects.filter(role=role, section=section).first()
    return perm.can_read if perm else True  # Default to True for read if not specified


def can_user_write(user, section, obj=None):
    role = get_user_role(user)
    if role == "LeadMentor":
        return True
    if role is None:
        return False

    # Section specific write permission
    perm = RolePermission.objects.filter(role=role, section=section).first()
    can_write_section = perm.can_write if perm else False

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
    elif role == "Student" and obj:
        try:
            student = user.student_profile
            if isinstance(obj, Student):
                return obj == student
        except (Student.DoesNotExist, AttributeError):
            return False

    return can_write_section


class LeadMentorRequiredMixin(UserPassesTestMixin):
    def test_func(self):
        return (
            self.request.user.is_superuser
            or self.request.user.groups.filter(name="LeadMentor").exists()
        )


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
        team_types = Team.TEAM_TYPES
        crews = Crew.objects.select_related("program").all()
        programs = Program.objects.all()

        api_keys = None
        if ApiClientKey and request.user.has_perm("api.view_apiclientkey"):
            api_keys = ApiClientKey.objects.all()

        context = {
            "grouped_permissions": grouped_permissions,
            "teams": teams,
            "team_types": team_types,
            "crews": crews,
            "programs": programs,
            "api_keys": api_keys,
            "role": "LeadMentor",  # Required for base.html to show Nav correctly
            "active_tab": request.GET.get("tab", "permissions"),
            "sections": sections,
        }
        return render(request, self.template_name, context)

    def post(self, request):
        action = request.POST.get("action")

        if action == "update_permissions":
            permissions = RolePermission.objects.all()
            for perm in permissions:
                read_key = f"read_{perm.id}"
                write_key = f"write_{perm.id}"

                perm.can_read = read_key in request.POST
                perm.can_write = write_key in request.POST
                perm.save()
            messages.success(request, "Permissions updated successfully.")
            return redirect("/programs/settings/?tab=permissions")

        elif action == "add_team":
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

        elif action == "add_crew":
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

        return redirect("portal_settings")
