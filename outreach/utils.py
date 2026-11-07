"""Shared helpers for computing outreach stats and check-in access."""

from collections.abc import Iterable
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


def _merge_intervals(
    intervals: Iterable[tuple[datetime | None, datetime | None]],
) -> list[tuple[datetime, datetime]]:
    valid_intervals: list[tuple[datetime, datetime]] = []
    for start, end in intervals:
        if start is None or end is None or end <= start:
            continue
        valid_intervals.append((start, end))
    valid_intervals.sort()
    merged: list[tuple[datetime, datetime]] = []
    for start, end in valid_intervals:
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def compute_attendance_overlap_hours(signups, attendance_sessions, now=None):
    """Return finalized outreach time that overlaps kiosk attendance time.

    Overlapping intervals on either side are merged before calculating the
    intersection, so the same elapsed time is only counted once. Open kiosk
    sessions are treated as ending at ``now`` (or the current time).
    """
    now = now or timezone.now()
    outreach_intervals = _merge_intervals(
        (signup.checked_in_at, signup.checked_out_at)
        for signup in signups
        if signup.is_finalized
    )
    attendance_intervals = _merge_intervals(
        (session.check_in, session.check_out or now) for session in attendance_sessions
    )

    outreach_index = 0
    attendance_index = 0
    overlap_seconds = 0
    while outreach_index < len(outreach_intervals) and attendance_index < len(
        attendance_intervals
    ):
        outreach_start, outreach_end = outreach_intervals[outreach_index]
        attendance_start, attendance_end = attendance_intervals[attendance_index]
        overlap = min(outreach_end, attendance_end) - max(
            outreach_start, attendance_start
        )
        overlap_seconds += max(0, overlap.total_seconds())

        if outreach_end <= attendance_end:
            outreach_index += 1
        if attendance_end <= outreach_end:
            attendance_index += 1

    return overlap_seconds / 3600.0


def get_student_outreach_stats(student, program):
    """Return career-to-date outreach stats for ``student`` within ``program``.

    See ``compute_outreach_stats`` for the keys returned.
    """
    signups = OutreachSignup.objects.filter(
        student=student, shift__event__program=program
    ).select_related("shift", "shift__event")
    return compute_outreach_stats(signups)


def can_view_checkin(user, shift, organization=None):
    """Who may open (even read-only) the check-in page for ``shift``.

    Mentors/Lead Mentors always; a student only if they champion the shift.
    """
    from programs.permission_views import can_user_write, user_is_mentor_or_lead

    organization = (
        organization
        or getattr(shift, "organization", None)
        or shift.event.program.organization
    )
    if user_is_mentor_or_lead(user, org=organization):
        return True
    return can_user_write(user, "outreach", shift, org=organization)


def can_operate_checkin(user, shift, organization=None):
    """Who may tap students in/out on the check-in page for ``shift``.

    Mentors/Lead Mentors always. A shift's champion may operate it until
    *every* signup has both a check-in and check-out, or until the grace
    window after the shift ends — whichever comes first.
    """
    from programs.permission_views import can_user_write, user_is_mentor_or_lead

    organization = (
        organization
        or getattr(shift, "organization", None)
        or shift.event.program.organization
    )
    if user_is_mentor_or_lead(user, org=organization):
        return True
    if not can_user_write(user, "outreach", shift, org=organization):
        return False
    if timezone.now() > shift_end_datetime(shift) + CHECKIN_GRACE:
        return False
    signups = list(shift.signups.all())
    if signups and all(s.checked_in_at and s.checked_out_at for s in signups):
        return False
    return True
