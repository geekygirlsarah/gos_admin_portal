from django.urls import resolve
from django.utils.deprecation import MiddlewareMixin


class ActiveProgramMiddleware(MiddlewareMixin):
    def process_view(self, request, view_func, view_args, view_kwargs):
        if not request.user.is_authenticated:
            return None

        # If we are visiting a program detail page, set it as active in session
        try:
            match = resolve(request.path)
            if (
                match.app_name == "programs" or "programs" in match.view_name
            ) and "pk" in view_kwargs:
                # We only want to set it if the pk likely refers to a program
                # Common views where pk is program_id: program_detail, program_edit, etc.
                if any(
                    x in match.view_name
                    for x in [
                        "program_detail",
                        "program_edit",
                        "program_email",
                        "program_dues",
                        "program_student",
                        "program_assignment",
                        "program_schools",
                    ]
                ):
                    request.session["active_program_id"] = str(view_kwargs["pk"])
            elif "program_id" in view_kwargs:
                request.session["active_program_id"] = str(view_kwargs["program_id"])
        except Exception:  # nosec B110
            pass

        # 2. Auto-select if not set
        if "active_program_id" not in request.session:
            from programs.models import Enrollment, Program
            from programs.permission_views import get_user_role

            role = get_user_role(request.user)
            program = None
            if role == "Parent":
                enrollments = Enrollment.objects.filter(
                    student__adults__user=request.user
                ).distinct()
                programs = Program.objects.filter(enrollment__in=enrollments).distinct()
                if programs.count() == 1:
                    program = programs.first()
            elif role == "Student":
                programs = Program.objects.filter(
                    enrollment__student__user=request.user
                ).distinct()
                if programs.count() == 1:
                    program = programs.first()
            elif role == "LeadMentor":
                active_programs = Program.objects.filter(active=True)
                if active_programs.count() == 1:
                    program = active_programs.first()

            if program:
                request.session["active_program_id"] = str(program.id)

        return None
