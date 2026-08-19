from audit.events import AuditEvent
from audit.service import log_event
from programs.permission_views import get_user_role


def _build_scope_notes(user, obj):
    """Build program-scope context for SENSITIVE_DATA_VIEW audit notes.

    Uses prefetched data where available to avoid extra queries.
    """
    from programs.models import Student

    if not isinstance(obj, Student):
        return ""

    student_programs = sorted(
        {
            e.program.name
            for e in obj.enrollment_set.all()
            if getattr(e, "active", True) and e.program_id and e.program
        }
    )
    if not student_programs:
        return ""

    # Find the actor's Adult record to get their program affiliations
    try:
        adult = user.adult_profile
    except Exception:  # nosec B110
        return ""

    from programs.models import AdultStudentRelationship

    actor_programs = sorted(
        set(
            AdultStudentRelationship.objects.filter(
                adult=adult,
                student__enrollment__active=True,
            ).values_list("student__enrollment__program__name", flat=True)
        )
    )

    if not actor_programs:
        scope = "no-match"
    elif set(student_programs) <= set(actor_programs):
        scope = "full-match"
    else:
        scope = "partial-match"

    return (
        f" Student programs: [{', '.join(student_programs)}]."
        f" Actor programs: [{', '.join(actor_programs)}]."
        f" SCOPE: {scope}"
    )


class SensitiveDataViewMixin:
    """
    Mixin to log when sensitive data is viewed by a mentor.
    Should be added to DetailViews or UpdateViews of sensitive models (Student, Adult).
    """

    def get(self, request, *args, **kwargs):
        response = super().get(request, *args, **kwargs)

        # We only log if the user is a mentor (including lead mentors)
        role = get_user_role(request.user)
        if role in ["Mentor", "LeadMentor"]:
            # The object being viewed
            obj = getattr(self, "object", None)
            if not obj and hasattr(self, "get_object"):
                try:
                    obj = self.get_object()
                except Exception:  # nosec B110
                    pass

            if obj:
                scope_notes = _build_scope_notes(request.user, obj)
                log_event(
                    event=AuditEvent.SENSITIVE_DATA_VIEW,
                    resource=obj,
                    request=request,
                    notes=(
                        f"Mentor {request.user.email} viewed "
                        f"{type(obj).__name__} data.{scope_notes}"
                    ),
                )

        return response
