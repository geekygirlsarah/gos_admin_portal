import re

from programs.models import Enrollment, Program
from programs.permission_views import get_user_role


def navbar_context(request):
    """
    Injects navbar-related context into every template:
    - current_program: the Program object if the current URL is scoped to a program
    - navbar_role: the user's role string
    - navbar_program_students: students enrolled in the current program (for scoped nav)
    - navbar_program_parents: adults who are parents of enrolled students (for scoped nav)
    """
    if not request.user.is_authenticated:
        return {}

    role = get_user_role(request.user)

    # Try to extract a program pk from the URL path, e.g. /programs/42/...
    current_program = None
    match = re.match(r"^/programs/(\d+)/", request.path)
    if match:
        program_pk = int(match.group(1))
        try:
            current_program = Program.objects.get(pk=program_pk)
        except Program.DoesNotExist:
            current_program = None

    # If no program in URL, try to auto-select for Students/Parents who only have one
    if current_program is None and role in ("Student", "Parent", "Mentor", "Alumni"):
        try:
            if role == "Student":
                student = request.user.student_profile
                enrollments = Enrollment.objects.filter(student=student).select_related(
                    "program"
                )
                programs = [e.program for e in enrollments]
            elif role in ("Parent", "Alumni", "Mentor"):
                adult = request.user.adult_profile
                if role == "Parent":
                    students = adult.all_students()
                    programs = list(
                        Program.objects.filter(
                            enrollment__student__in=students
                        ).distinct()
                    )
                elif role == "Alumni" and adult.student_record:
                    enrollments = Enrollment.objects.filter(
                        student=adult.student_record
                    ).select_related("program")
                    programs = [e.program for e in enrollments]
                else:
                    programs = []
            else:
                programs = []

            if len(programs) == 1:
                current_program = programs[0]
        except (AttributeError, Exception):
            pass

    return {
        "current_program": current_program,
        "navbar_role": role,
    }
