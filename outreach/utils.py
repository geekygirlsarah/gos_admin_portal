"""Shared helpers for computing outreach stats and check-in access."""

from datetime import datetime, timedelta

from django.utils import timezone

from outreach.models import OutreachSignup

#: After a shift ends, its champion may keep operating the check-in/out page
#: for this long so stragglers can be tapped out. Mentors can always edit.
CHECKIN_GRACE_HOURS = 4
CHECKIN_GRACE = timedelta(hours=CHECKIN_GRACE_HOURS)


def shift_end_datetime(shift):
    """Aware datetime for when ``shift`` is scheduled to end."""
    end_dt = datetime.combine(shift.date, shift.end_time)
    if timezone.is_naive(end_dt):
        end_dt = timezone.make_aware(end_dt)
    return end_dt


def compute_outreach_stats(signups):
    """Compute career-to-date outreach stats from a list/queryset of ``OutreachSignup``.

    Completed hours come from the *actual* attended time on past shifts that
    have been finalized via check-in/check-out. Pending hours are the
    scheduled hours of upcoming shifts plus shifts that are over but haven't
    had attendance recorded yet (so a student can't bank hours for a past
    event nobody stamped).
    """
    championed_event_ids = set()
    completed_hours = 0.0
    pending_hours = 0.0
    unconfirmed_count = 0

    for signup in signups:
        if signup.role == OutreachSignup.CHAMPION:
            championed_event_ids.add(signup.shift.event_id)
        if signup.shift.is_past:
            if signup.is_finalized:
                completed_hours += signup.attended_hours or signup.shift.duration_hours
            else:
                unconfirmed_count += 1
                pending_hours += signup.shift.duration_hours
        else:
            pending_hours += signup.shift.duration_hours

    return {
        "championed_count": len(championed_event_ids),
        "total_outreach_hours": completed_hours,
        "pending_outreach_hours": pending_hours,
        "unconfirmed_count": unconfirmed_count,
    }


def get_student_outreach_stats(student, program):
    """Return career-to-date outreach stats for ``student`` within ``program``.

    See ``compute_outreach_stats`` for the keys returned.
    """
    signups = OutreachSignup.objects.filter(
        student=student, shift__event__program=program
    ).select_related("shift", "shift__event")
    return compute_outreach_stats(signups)


def can_view_checkin(user, shift):
    """Who may open (even read-only) the check-in page for ``shift``.

    Mentors/Lead Mentors always; a student only if they champion the shift.
    """
    from programs.permission_views import can_user_write, user_is_mentor

    if user_is_mentor(user):
        return True
    return can_user_write(user, "outreach", shift)


def can_operate_checkin(user, shift):
    """Who may tap students in/out on the check-in page for ``shift``.

    Mentors/Lead Mentors always. A shift's champion may operate it until
    *every* signup has both a check-in and check-out, or until the grace
    window after the shift ends — whichever comes first.
    """
    from programs.permission_views import can_user_write, user_is_mentor

    if user_is_mentor(user):
        return True
    if not can_user_write(user, "outreach", shift):
        return False
    if timezone.now() > shift_end_datetime(shift) + CHECKIN_GRACE:
        return False
    signups = list(shift.signups.all())
    if signups and all(s.checked_in_at and s.checked_out_at for s in signups):
        return False
    return True
