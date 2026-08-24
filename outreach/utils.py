"""Shared helpers for computing outreach stats for a student."""

from outreach.models import OutreachSignup


def compute_outreach_stats(signups):
    """Compute career-to-date outreach stats from a list/queryset of ``OutreachSignup``.

    Credits hours based on the specific shift signed up for (not the whole
    event's duration), splitting them into hours already completed (past
    shifts) and hours still pending (upcoming shifts).
    """
    past_signups = [s for s in signups if s.shift.is_past]
    upcoming_signups = [s for s in signups if not s.shift.is_past]

    return {
        "championed_count": len(
            set(s.shift.event_id for s in signups if s.role == OutreachSignup.CHAMPION)
        ),
        "total_outreach_hours": sum(s.shift.duration_hours for s in past_signups),
        "pending_outreach_hours": sum(s.shift.duration_hours for s in upcoming_signups),
    }


def get_student_outreach_stats(student, program):
    """Return career-to-date outreach stats for ``student`` within ``program``.

    See ``compute_outreach_stats`` for the keys returned.
    """
    signups = OutreachSignup.objects.filter(
        student=student, shift__event__program=program
    ).select_related("shift", "shift__event")
    return compute_outreach_stats(signups)
