import re
from datetime import date

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


def _navbar_outreach_and_carpool_programs(request, role, navbar_is_parent):
    """Resolve student_outreach_programs / carpool_map_programs for the nav
    bar. carpool_map_programs feeds the standalone "Carpool Map" dropdown:
    only the Student's/Parent's children's currently-active (not past, not
    inactive) enrollments, never every program ever joined. Also resolves
    student_orders_programs — the active programs with the 'orders' feature
    enabled, which drive the student "Order Requests" nav item.
    """
    student_outreach_programs = []
    student_orders_programs = []
    carpool_map_programs = []
    if role == "Student":
        try:
            student = request.user.student_profile
            active_enrollments = (
                Enrollment.objects.filter(student=student, active=True)
                .select_related("program")
                .order_by("-program__start_date", "-program__end_date", "-program__id")
            )
            for e in active_enrollments:
                carpool_map_programs.append(e.program)
                if e.program.features.filter(key="outreach").exists():
                    student_outreach_programs.append(e.program)
                if e.program.features.filter(key="orders").exists():
                    student_orders_programs.append(e.program)
        except (Student.DoesNotExist, AttributeError):
            pass
    elif navbar_is_parent:
        try:
            adult = request.user.adult_profile
            students = adult.all_students()
            carpool_map_programs = list(
                Program.objects.filter(
                    enrollment__student__in=students, enrollment__active=True
                ).distinct()
            )
        except (Adult.DoesNotExist, AttributeError):
            pass

    return student_outreach_programs, student_orders_programs, carpool_map_programs


def _navbar_badge_data(request, role, navbar_is_parent):
    """Resolve badge count and badge-program membership for the navbar.

    Returns ``(badge_count, has_any_badge_program)`` where:
    * ``badge_count`` is the number of ``StudentBadge`` records for the user
      (students) or their children (parents).  Zero for other roles.
    * ``has_any_badge_program`` is ``True`` when the user (or their children)
      is enrolled in at least one program with the ``badges`` feature enabled.
    """
    badge_count = 0
    has_any_badge_program = False

    try:
        from badges.models import StudentBadge
    except ImportError:
        return badge_count, has_any_badge_program

    if role == "Student":
        try:
            student = request.user.student_profile
        except (Student.DoesNotExist, AttributeError):
            return badge_count, has_any_badge_program
        badge_count = StudentBadge.objects.filter(student=student).count()
        has_any_badge_program = Enrollment.objects.filter(
            student=student,
            active=True,
            program__features__key="badges",
        ).exists()
    elif navbar_is_parent:
        try:
            adult = request.user.adult_profile
            children = adult.all_students()
        except (Adult.DoesNotExist, AttributeError):
            return badge_count, has_any_badge_program
        badge_count = StudentBadge.objects.filter(student__in=children).count()
        has_any_badge_program = Enrollment.objects.filter(
            student__in=children,
            active=True,
            program__features__key="badges",
        ).exists()

    return badge_count, has_any_badge_program


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
    # Students resolve their profile from the role rather than hitting
    # ``user.student_profile`` here (and in templates), which would otherwise
    # cost a query on every page for users who aren't students.
    navbar_is_student = role == "Student"

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

    # Resolve the enabled feature keys once per request so template checks
    # like "has badges/outreach nav links" don't re-query per condition.
    # This runs *after* the auto-select above so an auto-selected program's
    # features (e.g. badges/outreach) are reflected in the nav too.
    if current_program is not None:
        program_feature_keys = set(
            current_program.features.values_list("key", flat=True)
        )
    else:
        program_feature_keys = set()

    student_outreach_programs, student_orders_programs, carpool_map_programs = (
        _navbar_outreach_and_carpool_programs(request, role, navbar_is_parent)
    )

    navbar_badge_count, user_has_any_badge_program = _navbar_badge_data(
        request, role, navbar_is_parent
    )

    # Student program navigation: active programs the student is enrolled in,
    # used for the "My Program(s)" navbar dropdown. Most recent program first
    # (by start_date desc, then end_date desc) so students see their current
    # program at the top.
    navbar_student_programs = carpool_map_programs if role == "Student" else []
    if navbar_student_programs:
        navbar_student_programs = sorted(
            navbar_student_programs,
            key=lambda p: (
                p.start_date or date.min,
                p.end_date or date.min,
                p.pk,
            ),
            reverse=True,
        )

    return {
        "current_program": current_program,
        "current_program_has_badges": "badges" in program_feature_keys,
        "current_program_has_outreach": "outreach" in program_feature_keys,
        "current_program_has_orders": "orders" in program_feature_keys,
        "navbar_role": role,
        "student_outreach_programs": student_outreach_programs,
        "student_orders_programs": student_orders_programs,
        "carpool_map_programs": carpool_map_programs,
        "navbar_student_programs": navbar_student_programs,
        "navbar_badge_count": navbar_badge_count,
        "user_has_any_badge_program": user_has_any_badge_program,
        # Flag-style helpers: an Adult can hold several roles at once (e.g. a
        # parent who also mentors), so expose each independently rather than
        # relying on the single navbar_role string.
        "navbar_is_parent": navbar_is_parent,
        "navbar_is_mentor": navbar_is_mentor,
        "navbar_is_alumni": navbar_is_alumni,
        "navbar_is_student": navbar_is_student,
    }
