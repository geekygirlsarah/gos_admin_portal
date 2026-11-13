import logging
import re
from datetime import date

from django.conf import settings
from django.db.models import Prefetch

from organizations.models import UserOrganizationMembership
from programs.models import (
    AdultStudentRelationship,
    Enrollment,
    Program,
    ProgramFeature,
    Student,
)

logger = logging.getLogger(__name__)


def wizard_context(request):
    """Provide wizard-step template defaults.

    ``applications/_wizard_base.html`` renders ``{% if warnings %}`` on every
    wizard step, but only the Step 5 view ever supplies ``warnings``. Supplying
    a falsy default here keeps every wizard page resolvable so Django doesn't
    log ``VariableDoesNotExist`` at DEBUG on each render. The Step 5 view's own
    context overrides this value.
    """
    return {"warnings": None}


def _navbar_profile(request, name):
    org = getattr(request, "organization", None)
    profile = getattr(request.user, name, None) if org else None
    return profile if profile and profile.organization_id == org.pk else None


def _navbar_children(request):
    org = getattr(request, "organization", None)
    adult = _navbar_profile(request, "adult_profile")
    if not org or not adult:
        return Student.objects.none()
    relationships = AdultStudentRelationship.objects.for_organization(org).filter(
        adult=adult, student__organization=org
    )
    return Student.objects.for_organization(org).filter(
        pk__in=relationships.values("student_id")
    )


def _navbar_enrollments(request):
    org = getattr(request, "organization", None)
    if not org:
        return Enrollment.objects.none()
    return Enrollment.objects.for_organization(org).filter(
        student__organization=org, program__organization=org
    )


def _navbar_outreach_and_carpool_programs(request, role, navbar_is_parent):
    """Resolve student_outreach_programs / carpool_map_programs / travel nav
    lists for the nav bar. carpool_map_programs feeds the standalone "Carpool
    Map" dropdown: only the Student's/Parent's children's currently-active (not
    past, not inactive) enrollments, never every program ever joined. Also
    resolves student_orders_programs / student_travel_programs — the active
    programs with the 'orders' / 'travel' features enabled — and
    parent_travel_programs for parents (their children's active travel-enabled
    programs).
    """
    student_outreach_programs = []
    student_orders_programs = []
    student_travel_programs = []
    parent_travel_programs = []
    carpool_map_programs = []
    org = getattr(request, "organization", None)
    if role == "Student" and org:
        student = _navbar_profile(request, "student_profile")
        if student:
            active_enrollments = (
                _navbar_enrollments(request)
                .filter(student=student, active=True)
                .select_related("program")
                .prefetch_related(
                    Prefetch(
                        "program__features",
                        queryset=ProgramFeature.objects.for_organization(org),
                        to_attr="navbar_features",
                    )
                )
                .order_by("-program__start_date", "-program__end_date", "-program__id")
            )
            for e in active_enrollments:
                carpool_map_programs.append(e.program)
                feature_keys = {feature.key for feature in e.program.navbar_features}
                if "outreach" in feature_keys:
                    student_outreach_programs.append(e.program)
                if "orders" in feature_keys:
                    student_orders_programs.append(e.program)
                if "travel" in feature_keys:
                    student_travel_programs.append(e.program)
    elif navbar_is_parent and org:
        enrollments = _navbar_enrollments(request).filter(
            student__in=_navbar_children(request), active=True
        )
        programs = Program.objects.for_organization(org).filter(
            pk__in=enrollments.values("program_id")
        )
        carpool_map_programs = list(programs)
        parent_travel_programs = list(
            programs.filter(
                features__key="travel", features__organization=org
            ).distinct()
        )

    return (
        student_outreach_programs,
        student_orders_programs,
        student_travel_programs,
        parent_travel_programs,
        carpool_map_programs,
    )


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

    org = getattr(request, "organization", None)
    if not org:
        return badge_count, has_any_badge_program

    if role == "Student":
        student = _navbar_profile(request, "student_profile")
        if not student:
            return badge_count, has_any_badge_program
        children = Student.objects.for_organization(org).filter(pk=student.pk)
    elif navbar_is_parent:
        children = _navbar_children(request)
    else:
        return badge_count, has_any_badge_program

    badge_count = (
        StudentBadge.objects.for_organization(org)
        .filter(student__in=children, badge__organization=org)
        .count()
    )
    has_any_badge_program = (
        _navbar_enrollments(request)
        .filter(
            student__in=children,
            active=True,
            program__features__key="badges",
            program__features__organization=org,
        )
        .exists()
    )

    return badge_count, has_any_badge_program


def navbar_context(request):
    """
    Injects navbar-related context into every template:
    - current_program: the Program object if the current URL is scoped to a program
    - navbar_role: the user's role string
    - navbar_program_students: students enrolled in the current program (for scoped nav)
    - navbar_program_parents: adults who are parents of enrolled students (for scoped nav)
    - is_platform_admin: boolean indicating if current URL is under /platform-admin/
    - site_base_url: primary domain base URL (e.g. https://momentumteam.app)
    """
    try:
        from organizations.utils import get_site_base_url

        site_base_url = get_site_base_url()
    except Exception:
        site_base_url = ""

    is_platform_admin = bool(
        (
            request.resolver_match
            and getattr(request.resolver_match, "app_name", None) == "platform_admin"
        )
        or request.path.startswith("/platform-admin/")
    )

    if not hasattr(request, "user") or not request.user.is_authenticated:
        return {
            "site_base_url": site_base_url,
            "is_platform_admin": is_platform_admin,
        }

    org = getattr(request, "organization", None)
    roles = set()
    if org:
        roles = set(
            UserOrganizationMembership.objects.filter(
                user=request.user, organization=org, is_active=True
            ).values_list("role", flat=True)
        )
    # Match get_user_role's membership priority without its legacy fallback.
    role = (
        "LeadMentor"
        if request.user.is_superuser
        else next(
            (
                candidate
                for candidate in (
                    "LeadMentor",
                    "Mentor",
                    "Parent",
                    "Alumni",
                    "Student",
                    "Staff",
                )
                if candidate in roles
            ),
            None,
        )
    )

    adult = (
        _navbar_profile(request, "adult_profile")
        if role in {"Parent", "Alumni"}
        else None
    )
    navbar_is_parent = "Parent" in roles
    navbar_is_mentor = "Mentor" in roles
    navbar_is_alumni = "Alumni" in roles
    # Students resolve their profile from the role rather than hitting
    # ``user.student_profile`` here (and in templates), which would otherwise
    # cost a query on every page for users who aren't students.
    navbar_is_student = role == "Student"

    # Try to extract a program pk from the URL path, e.g. /programs/42/...
    current_program = None
    match = re.match(r"^/programs/(\d+)/", request.path)
    if match and org:
        program_pk = int(match.group(1))
        current_program = (
            Program.objects.for_organization(org).filter(pk=program_pk).first()
        )

    # If no program in URL, try to auto-select for Students/Parents who only have one
    if current_program is None and org:
        enrollments = _navbar_enrollments(request)
        if role == "Student":
            student = _navbar_profile(request, "student_profile")
            enrollments = (
                enrollments.filter(student=student) if student else enrollments.none()
            )
        elif role == "Parent" and adult:
            enrollments = enrollments.filter(student__in=_navbar_children(request))
        elif role == "Alumni" and adult and adult.student_record_id:
            enrollments = enrollments.filter(student_id=adult.student_record_id)
        else:
            enrollments = enrollments.none()

        programs = list(
            Program.objects.for_organization(org).filter(
                pk__in=enrollments.values("program_id")
            )[:2]
        )
        if len(programs) == 1:
            current_program = programs[0]

    # Resolve the enabled feature keys once per request so template checks
    # like "has badges/outreach nav links" don't re-query per condition.
    # This runs *after* the auto-select above so an auto-selected program's
    # features (e.g. badges/outreach) are reflected in the nav too.
    if current_program is not None:
        program_feature_keys = set(
            current_program.features.filter(organization=org).values_list(
                "key", flat=True
            )
        )
    else:
        program_feature_keys = set()

    (
        student_outreach_programs,
        student_orders_programs,
        student_travel_programs,
        parent_travel_programs,
        carpool_map_programs,
    ) = _navbar_outreach_and_carpool_programs(request, role, navbar_is_parent)

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
        "current_program_has_travel": "travel" in program_feature_keys,
        "navbar_role": role,
        "student_outreach_programs": student_outreach_programs,
        "student_orders_programs": student_orders_programs,
        "student_travel_programs": student_travel_programs,
        "parent_travel_programs": parent_travel_programs,
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
        "is_platform_admin": is_platform_admin,
        "site_base_url": site_base_url,
    }


def impersonation_context(request):
    """Provide impersonation state and the original superuser to all templates."""
    if not hasattr(request, "session"):
        return {"is_impersonating": False, "impersonator": None}

    impersonator_id = request.session.get("impersonator_id")
    if impersonator_id:
        from django.contrib.auth.models import User

        impersonator = None
        try:
            impersonator = User.objects.filter(pk=impersonator_id).first()
        except Exception as e:
            logger.debug(
                "Failed to resolve impersonator user %s: %s", impersonator_id, e
            )
        return {"is_impersonating": True, "impersonator": impersonator}
    return {"is_impersonating": False, "impersonator": None}


def organization_context(request):
    """Provide organization settings and branding variables to templates."""
    try:
        from organizations.utils import (
            get_organization_email_settings,
            get_organization_setting,
        )

        org = getattr(request, "organization", None)
    except Exception:
        org = None

    email_cfg = {}
    if org:
        try:
            email_cfg = get_organization_email_settings(org)
        except Exception as e:
            logger.debug("Failed to resolve organization email settings: %s", e)

    org_account_enabled = (
        get_organization_setting(org, "org_account_enabled", False) if org else False
    )
    org_account_name = (
        get_organization_setting(org, "org_account_name", "") if org else ""
    )
    org_email_domain = (
        get_organization_setting(org, "org_email_domain", "") if org else ""
    )
    force_mentor_org_email = (
        get_organization_setting(org, "force_mentor_org_email", False) if org else False
    )
    force_student_org_email = (
        get_organization_setting(org, "force_student_org_email", False)
        if org
        else False
    )
    force_parent_org_email = (
        get_organization_setting(org, "force_parent_org_email", False) if org else False
    )
    force_alumni_org_email = (
        get_organization_setting(org, "force_alumni_org_email", False) if org else False
    )

    org_contact_email = (
        email_cfg.get("contact_email")
        or email_cfg.get("reply_to_email")
        or getattr(settings, "DEFAULT_REPLY_TO_EMAIL", "")
    )
    organization_footer_tagline = (
        get_organization_setting(org, "footer_tagline", "") if org else ""
    )

    return {
        "organization": org,
        "organization_name": org.name if org else "Momentum",
        "organization_email_name": email_cfg.get("organization_name", "Momentum"),
        "organization_logo_url": email_cfg.get("logo_url", ""),
        "organization_address_line1": email_cfg.get("address_line1", ""),
        "organization_address_line2": email_cfg.get("address_line2", ""),
        "organization_tax_id": email_cfg.get("tax_id", ""),
        "organization_website": email_cfg.get("website", ""),
        "organization_footer_tagline": organization_footer_tagline,
        "org_account_enabled": org_account_enabled,
        "org_account_name": org_account_name,
        "org_email_domain": org_email_domain,
        "force_mentor_org_email": force_mentor_org_email,
        "force_student_org_email": force_student_org_email,
        "force_parent_org_email": force_parent_org_email,
        "force_alumni_org_email": force_alumni_org_email,
        "org_contact_email": org_contact_email,
    }
