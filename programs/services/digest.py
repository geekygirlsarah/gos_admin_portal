"""Weekly digest service for students, parents, and mentors."""

import datetime
from typing import Any, Dict, List, Optional

from django.utils import timezone

from attendance.services import get_attendance_stats, get_student_attendance_stats
from outreach.models import (
    OutreachEvent,
    OutreachMentorSignup,
    OutreachShift,
    OutreachSignup,
)
from programs.models import (
    Adult,
    Enrollment,
    Program,
    Student,
)
from programs.utils import active_mentors, active_students


def collect_student_digest_data(
    student: Student,
    days_ahead: int = 7,
    reference_date: Optional[datetime.date] = None,
) -> Dict[str, Any]:
    """Collect upcoming commitments, open outreach opportunities, and attendance for a student."""
    ref_date = reference_date or timezone.localdate()
    end_date = ref_date + datetime.timedelta(days=days_ahead)

    active_enrollments = list(
        Enrollment.objects.filter(
            student=student, active=True, program__active=True
        ).select_related("program")
    )
    active_programs = [e.program for e in active_enrollments]

    # 1. Confirmed outreach shifts
    confirmed_signups = list(
        OutreachSignup.objects.filter(
            student=student,
            shift__date__gte=ref_date,
            shift__date__lte=end_date,
        )
        .select_related("shift", "shift__event", "shift__event__program")
        .order_by("shift__date", "shift__start_time")
    )

    confirmed_shifts = []
    signed_up_shift_ids = set()
    for s in confirmed_signups:
        signed_up_shift_ids.add(s.shift_id)
        confirmed_shifts.append(
            {
                "event_name": s.shift.event.name,
                "program_name": (
                    s.shift.event.program.name if s.shift.event.program else ""
                ),
                "date": s.shift.date,
                "start_time": s.shift.start_time,
                "end_time": s.shift.end_time,
                "role": s.role,
                "role_display": s.get_role_display(),
                "location_name": s.shift.event.location_name,
                "location_address": s.shift.event.location_address,
                "description": s.shift.event.description,
            }
        )

    # 2. Open outreach opportunities in student's active programs
    open_opportunities = []
    if active_programs:
        shifts = list(
            OutreachShift.objects.filter(
                event__program__in=active_programs,
                date__gte=ref_date,
                date__lte=end_date,
            )
            .exclude(id__in=signed_up_shift_ids)
            .select_related("event", "event__program")
            .prefetch_related("signups")
            .order_by("date", "start_time")
        )

        for sh in shifts:
            signups = list(sh.signups.all())
            n_champions = sum(1 for su in signups if su.role == OutreachSignup.CHAMPION)
            n_helpers = sum(1 for su in signups if su.role == OutreachSignup.HELPER)

            needs_champ = n_champions < sh.max_champions
            needs_help = n_helpers < sh.max_helpers
            if needs_champ or needs_help:
                open_opportunities.append(
                    {
                        "event_name": sh.event.name,
                        "program_name": (
                            sh.event.program.name if sh.event.program else ""
                        ),
                        "date": sh.date,
                        "start_time": sh.start_time,
                        "end_time": sh.end_time,
                        "location_name": sh.event.location_name,
                        "location_address": sh.event.location_address,
                        "needs_champions": needs_champ,
                        "open_champion_slots": max(0, sh.max_champions - n_champions),
                        "needs_helpers": needs_help,
                        "open_helper_slots": max(0, sh.max_helpers - n_helpers),
                    }
                )

    # Cap open opportunities to top 5
    open_opportunities = open_opportunities[:5]

    # 3. Attendance snapshot
    total_hours = 0.0
    week_hours = 0.0
    program_attendance = []
    for prog in active_programs:
        if prog.has_feature("attendance"):
            stats = get_student_attendance_stats(student, prog)
            total_hours += stats.get("total_hours", 0)
            week_hours += stats.get("week_hours", 0)
            program_attendance.append(
                {
                    "program": prog,
                    "total_hours": stats.get("total_hours", 0),
                    "week_hours": stats.get("week_hours", 0),
                }
            )

    attendance_stats = {
        "total_hours": round(total_hours, 1),
        "week_hours": round(week_hours, 1),
        "by_program": program_attendance,
    }

    # 4. Travel highlights (if travel app installed & feature active)
    travel_highlights = []
    try:
        from travel.models import TravelSignup

        travel_signups = list(
            TravelSignup.objects.filter(
                student=student,
                event__end_date__gte=ref_date,
                event__start_date__lte=end_date + datetime.timedelta(days=14),
            ).select_related("event")
        )
        for ts in travel_signups:
            travel_highlights.append(
                {
                    "event_name": ts.event.name,
                    "start_date": ts.event.start_date,
                    "end_date": ts.event.end_date,
                    "status": ts.get_status_display(),
                    "requires_permission_form": ts.event.requires_permission_form,
                }
            )
    except Exception:
        travel_highlights = []

    has_content = bool(
        confirmed_shifts
        or open_opportunities
        or attendance_stats["total_hours"] > 0
        or travel_highlights
    )

    return {
        "student": student,
        "days_ahead": days_ahead,
        "reference_date": ref_date,
        "end_date": end_date,
        "confirmed_shifts": confirmed_shifts,
        "open_opportunities": open_opportunities,
        "attendance_stats": attendance_stats,
        "travel_highlights": travel_highlights,
        "has_content": has_content,
    }


def collect_parent_digest_data(
    adult: Adult,
    days_ahead: int = 7,
    reference_date: Optional[datetime.date] = None,
) -> Dict[str, Any]:
    """Collect commitments and updates for all active enrolled students of a parent."""
    ref_date = reference_date or timezone.localdate()
    end_date = ref_date + datetime.timedelta(days=days_ahead)

    linked_students = [s for s in adult.all_students() if not s.graduated]
    students_data = []

    for s in linked_students:
        s_data = collect_student_digest_data(
            s, days_ahead=days_ahead, reference_date=ref_date
        )
        students_data.append(s_data)

    has_content = any(sd["has_content"] for sd in students_data)

    return {
        "adult": adult,
        "days_ahead": days_ahead,
        "reference_date": ref_date,
        "end_date": end_date,
        "students_data": students_data,
        "has_content": has_content,
    }


def collect_mentor_digest_data(
    adult: Adult,
    days_ahead: int = 7,
    reference_date: Optional[datetime.date] = None,
) -> Dict[str, Any]:
    """Collect mentor commitments, shifts needing support, and attendance stats."""
    ref_date = reference_date or timezone.localdate()
    end_date = ref_date + datetime.timedelta(days=days_ahead)

    # 1. Confirmed mentor signups
    confirmed_signups = list(
        OutreachMentorSignup.objects.filter(
            adult=adult,
            shift__date__gte=ref_date,
            shift__date__lte=end_date,
        )
        .select_related("shift", "shift__event", "shift__event__program")
        .order_by("shift__date", "shift__start_time")
    )

    confirmed_shifts = []
    signed_up_shift_ids = set()
    for s in confirmed_signups:
        signed_up_shift_ids.add(s.shift_id)
        confirmed_shifts.append(
            {
                "event_name": s.shift.event.name,
                "program_name": (
                    s.shift.event.program.name if s.shift.event.program else ""
                ),
                "date": s.shift.date,
                "start_time": s.shift.start_time,
                "end_time": s.shift.end_time,
                "location_name": s.shift.event.location_name,
                "location_address": s.shift.event.location_address,
                "description": s.shift.event.description,
            }
        )

    # 2. Needs support shifts across active programs
    upcoming_shifts = list(
        OutreachShift.objects.filter(
            event__program__active=True,
            date__gte=ref_date,
            date__lte=end_date,
        )
        .select_related("event", "event__program")
        .prefetch_related("signups", "mentor_signups")
        .order_by("date", "start_time")
    )

    needs_support_shifts = []
    for sh in upcoming_shifts:
        signups = list(sh.signups.all())
        mentor_signups = list(sh.mentor_signups.all())
        n_champions = sum(1 for su in signups if su.role == OutreachSignup.CHAMPION)
        n_helpers = sum(1 for su in signups if su.role == OutreachSignup.HELPER)
        n_mentors = len(mentor_signups)

        needs_champ = n_champions < sh.max_champions
        needs_help = n_helpers < sh.max_helpers
        needs_mentor = n_mentors == 0

        if needs_mentor or needs_champ or needs_help:
            needs_support_shifts.append(
                {
                    "event_name": sh.event.name,
                    "program_name": (sh.event.program.name if sh.event.program else ""),
                    "date": sh.date,
                    "start_time": sh.start_time,
                    "end_time": sh.end_time,
                    "location_name": sh.event.location_name,
                    "location_address": sh.event.location_address,
                    "needs_mentors": needs_mentor,
                    "mentor_count": n_mentors,
                    "needs_champions": needs_champ,
                    "needs_helpers": needs_help,
                }
            )

    needs_support_shifts = needs_support_shifts[:8]

    # 3. Mentor attendance hours
    total_hours = 0.0
    week_hours = 0.0
    for prog in Program.objects.filter(active=True):
        if prog.has_feature("attendance"):
            stats = get_attendance_stats(prog, adult=adult)
            total_hours += stats.get("total_hours", 0)
            week_hours += stats.get("week_hours", 0)

    attendance_stats = {
        "total_hours": round(total_hours, 1),
        "week_hours": round(week_hours, 1),
    }

    has_content = bool(
        confirmed_shifts or needs_support_shifts or attendance_stats["total_hours"] > 0
    )

    return {
        "adult": adult,
        "days_ahead": days_ahead,
        "reference_date": ref_date,
        "end_date": end_date,
        "confirmed_shifts": confirmed_shifts,
        "needs_support_shifts": needs_support_shifts,
        "attendance_stats": attendance_stats,
        "has_content": has_content,
    }


def get_weekly_digest_recipients(
    role: str = "all",
    days_ahead: int = 7,
    reference_date: Optional[datetime.date] = None,
) -> List[Dict[str, Any]]:
    """Determine all recipient targets and build their digest data."""
    ref_date = reference_date or timezone.localdate()
    targets = []

    # 1. Students
    if role in ("all", "student"):
        active_student_qs = (
            active_students()
            .filter(enrollment__active=True, enrollment__program__active=True)
            .distinct()
        )

        for st in active_student_qs:
            email = (
                st.personal_email
                or st.andrew_email
                or (st.user.email if st.user else None)
            )
            if email and email.strip():
                data = collect_student_digest_data(
                    st, days_ahead=days_ahead, reference_date=ref_date
                )
                targets.append(
                    {
                        "role": "student",
                        "user": st.user,
                        "recipient_obj": st,
                        "email": email.strip(),
                        "data": data,
                    }
                )

    # 2. Parents
    if role in ("all", "parent"):
        # Resolve active enrolled students
        active_student_qs = (
            active_students()
            .filter(enrollment__active=True, enrollment__program__active=True)
            .distinct()
        )

        parent_candidates = {}
        for st in active_student_qs:
            parents = st.all_parents
            opted_in = [
                p
                for p in parents
                if p.email_updates and getattr(p, "login_enabled", True)
            ]
            if not opted_in:
                # Fallback: at least one parent (primary or first available) receives the digest
                fallback = st.primary_contact or (parents[0] if parents else None)
                if fallback and getattr(fallback, "login_enabled", True):
                    opted_in = [fallback]

            for p in opted_in:
                email = (
                    p.personal_email
                    or p.andrew_email
                    or (p.user.email if p.user else None)
                )
                if email and email.strip():
                    parent_candidates[p.pk] = (p, email.strip())

        for adult_pk, (adult, email) in parent_candidates.items():
            data = collect_parent_digest_data(
                adult, days_ahead=days_ahead, reference_date=ref_date
            )
            targets.append(
                {
                    "role": "parent",
                    "user": adult.user,
                    "recipient_obj": adult,
                    "email": email,
                    "data": data,
                }
            )

    # 3. Mentors
    if role in ("all", "mentor"):
        mentors = active_mentors().filter(login_enabled=True)
        for m in mentors:
            email = (
                m.personal_email or m.andrew_email or (m.user.email if m.user else None)
            )
            if email and email.strip():
                data = collect_mentor_digest_data(
                    m, days_ahead=days_ahead, reference_date=ref_date
                )
                targets.append(
                    {
                        "role": "mentor",
                        "user": m.user,
                        "recipient_obj": m,
                        "email": email.strip(),
                        "data": data,
                    }
                )

    return targets
