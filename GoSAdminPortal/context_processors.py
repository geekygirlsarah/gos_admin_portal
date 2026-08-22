import re

from programs.models import Adult, Enrollment, Program, Student
from programs.permission_views import get_user_role


def wizard_context(request):
    """Provide wizard-step template defaults.

    ``applications/_wizard_base.html`` renders ``{% if warnings %}`` on every
    wizard step, but only the Step 5 view ever supplies ``warnings``. Supplying
    a falsy default here keeps every wizard page resolvable so Django doesn't
    log ``VariableDoesNotExist`` at DEBUG on each render. The Step 5 view's own
    context overrides this value.
    """
    return {"warnings": None}


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

    # Resolve the adult's role flags once per request. Django caches
    # ``user.adult_profile`` on the instance after the first lookup (including
    # the "no related object" case), and we fetch the user's group names in a
    # single query, so the navbar adds at most two queries no matter how many
    # flags the template reads. Superusers short-circuit ``get_user_role``
    # without touching the profile, so we always do the one profile lookup here.
    try:
        adult_flags = {
            "is_parent": request.user.adult_profile.is_parent,
            "is_mentor": request.user.adult_profile.is_mentor,
            "is_alumni": request.user.adult_profile.is_alumni,
        }
    except (Adult.DoesNotExist, AttributeError):
        adult_flags = {}

    if request.user.is_superuser:
        group_names = set()
    else:
        group_names = set(request.user.groups.values_list("name", flat=True))

    navbar_is_parent = bool(adult_flags.get("is_parent") or "Parent" in group_names)
    navbar_is_mentor = bool(adult_flags.get("is_mentor") or "Mentor" in group_names)
    navbar_is_alumni = bool(adult_flags.get("is_alumni") or "Alumni" in group_names)

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

    # Injects student_outreach_programs for the nav bar
    student_outreach_programs = []
    if role == "Student":
        try:
            student = request.user.student_profile
            active_enrollments = Enrollment.objects.filter(
                student=student, active=True
            ).select_related("program")
            for e in active_enrollments:
                if e.program.features.filter(key="outreach").exists():
                    student_outreach_programs.append(e.program)
        except (Student.DoesNotExist, AttributeError):
            pass

    return {
        "current_program": current_program,
        "navbar_role": role,
        "student_outreach_programs": student_outreach_programs,
        # Flag-style helpers: an Adult can hold several roles at once (e.g. a
        # parent who also mentors), so expose each independently rather than
        # relying on the single navbar_role string.
        "navbar_is_parent": navbar_is_parent,
        "navbar_is_mentor": navbar_is_mentor,
        "navbar_is_alumni": navbar_is_alumni,
    }
