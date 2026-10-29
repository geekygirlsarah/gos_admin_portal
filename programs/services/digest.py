"""Weekly digest service for students, parents, and mentors."""

import datetime
import logging
from decimal import Decimal
from typing import Any, Dict, List, Optional

from django.db.models import Sum
from django.utils import timezone

from attendance.models import AttendanceSession
from outreach.models import (
    OutreachEvent,
    OutreachMentorSignup,
    OutreachShift,
    OutreachSignup,
)
from outreach.utils import compute_outreach_stats
from programs.models import (
    Adult,
    Enrollment,
    Program,
    Student,
)
from programs.utils import (
    active_mentors,
    active_students,
    get_student_balance_data,
)

logger = logging.getLogger(__name__)


def get_upcoming_calendar_events(
    user=None,
    role: str = "Student",
    days_ahead: int = 7,
    reference_date: Optional[datetime.date] = None,
) -> List[Dict[str, Any]]:
    """Retrieve upcoming events from manual/custom calendar feeds visible to the given user or role."""
    ref_date = reference_date or timezone.localdate()
    end_date = ref_date + datetime.timedelta(days=days_ahead)
    events = []

    try:
        from calendar_feeds.models import CalendarFeed

        feeds = CalendarFeed.objects.filter(is_active=True).exclude(
            source_type__in=[
                CalendarFeed.SOURCE_OUTREACH,
                CalendarFeed.SOURCE_TRAVEL,
            ]
        )

        for feed in feeds:
            can_read = False
            if user and user.is_authenticated:
                can_read = feed.user_can_read(user)
            else:
                if feed.is_public:
                    can_read = True
                elif feed.visibility == CalendarFeed.VISIBILITY_MEMBERS:
                    if not feed.acls.exists():
                        can_read = True
                    else:
                        can_read = feed.acls.filter(role=role, can_read=True).exists()

            if can_read:
                feed_events = feed.get_events_for_date_range(ref_date, end_date)
                events.extend(feed_events)

        events.sort(key=lambda e: (e["date"], e["start_time"] or datetime.time.min))
    except Exception:
        events = []

    return events[:10]


def compute_attendance_stats(
    person_obj,
    programs: List[Program],
    is_student: bool = True,
    days_ahead: int = 7,
    reference_date: Optional[datetime.date] = None,
) -> Dict[str, Any]:
    """Compute overall and per-program attendance hours, average weekly hours, and recent days logged."""
    ref_date = reference_date or timezone.localdate()
    total_hours = 0.0
    total_recent_hours = 0.0
    program_attendance = []

    for prog in programs:
        if prog.has_feature("attendance"):
            qs = AttendanceSession.objects.filter(program=prog)
            if is_student:
                qs = qs.filter(student=person_obj)
            else:
                qs = qs.filter(adult=person_obj)

            total_mins = qs.aggregate(total=Sum("duration_minutes"))["total"] or 0
            prog_total_hours = round(total_mins / 60.0, 1)

            # Weeks elapsed matching hours chart logic
            if prog.start_date:
                overall_start_date = prog.start_date
            else:
                earliest = qs.order_by("check_in").first()
                overall_start_date = earliest.check_in.date() if earliest else None

            if overall_start_date:
                days = (ref_date - overall_start_date).days
                weeks_elapsed = max((days + 6) // 7, 1)
            else:
                weeks_elapsed = 1

            prog_avg_per_week = (
                round(prog_total_hours / weeks_elapsed, 1) if weeks_elapsed > 0 else 0.0
            )

            # Recent sessions in past days_ahead
            recent_start = ref_date - datetime.timedelta(days=days_ahead)
            recent_qs = qs.filter(
                check_in__date__gte=recent_start, check_in__date__lte=ref_date
            )
            prog_recent_days = recent_qs.values("check_in__date").distinct().count()
            recent_mins = (
                recent_qs.aggregate(total=Sum("duration_minutes"))["total"] or 0
            )
            prog_recent_hours = round(recent_mins / 60.0, 1)

            total_hours += prog_total_hours
            total_recent_hours += prog_recent_hours

            program_attendance.append(
                {
                    "program": prog,
                    "total_hours": prog_total_hours,
                    "avg_hours_per_week": prog_avg_per_week,
                    "recent_days": prog_recent_days,
                    "recent_hours": prog_recent_hours,
                    "week_hours": prog_recent_hours,
                }
            )

    recent_start = ref_date - datetime.timedelta(days=days_ahead)
    all_qs = AttendanceSession.objects.filter(program__in=programs)
    if is_student:
        all_qs = all_qs.filter(student=person_obj)
    else:
        all_qs = all_qs.filter(adult=person_obj)
    recent_all_qs = all_qs.filter(
        check_in__date__gte=recent_start, check_in__date__lte=ref_date
    )
    total_recent_days = recent_all_qs.values("check_in__date").distinct().count()

    overall_avg_per_week = round(
        sum(bp["avg_hours_per_week"] for bp in program_attendance), 1
    )

    return {
        "total_hours": round(total_hours, 1),
        "avg_hours_per_week": overall_avg_per_week,
        "recent_days": total_recent_days,
        "recent_hours": round(total_recent_hours, 1),
        "week_hours": round(total_recent_hours, 1),
        "by_program": program_attendance,
    }


def collect_student_digest_data(
    student: Student,
    days_ahead: int = 7,
    reference_date: Optional[datetime.date] = None,
) -> Dict[str, Any]:
    """Collect upcoming commitments, open outreach opportunities, attendance, calendar events, and action items for a student."""
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

    open_opportunities = open_opportunities[:5]

    # 3. Outreach total stats
    all_signups = list(
        OutreachSignup.objects.filter(student=student).select_related(
            "shift", "shift__event"
        )
    )
    outreach_stats = compute_outreach_stats(all_signups)
    completed_events_count = len(
        {s.shift.event_id for s in all_signups if s.shift.is_past and s.is_finalized}
    )
    outreach_summary = {
        "total_hours": round(outreach_stats["total_outreach_hours"], 1),
        "completed_events": completed_events_count,
        "championed_count": outreach_stats["championed_count"],
        "pending_hours": round(outreach_stats["pending_outreach_hours"], 1),
    }

    # 4. Attendance snapshot with weekly averages and recent days
    attendance_stats = compute_attendance_stats(
        student,
        programs=active_programs,
        is_student=True,
        days_ahead=days_ahead,
        reference_date=ref_date,
    )

    # 5. Travel highlights (if travel app installed & feature active)
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

    # 6. Upcoming calendar events from active feeds
    calendar_events = get_upcoming_calendar_events(
        user=student.user,
        role="Student",
        days_ahead=days_ahead,
        reference_date=ref_date,
    )

    # 7. Action items & Reminders (Dues, Required documents, Background checks)
    total_balance = Decimal("0.00")
    program_balances = []
    for prog in active_programs:
        if prog.has_feature("finances"):
            try:
                b_data = get_student_balance_data(student, prog, can_view_sliding=True)
                bal = b_data.get("balance", Decimal("0.00"))
                if bal > 0:
                    total_balance += bal
                    program_balances.append({"program": prog, "balance": float(bal)})
            except Exception as e:
                logger.warning(
                    "Error calculating balance for student %s in program %s: %s",
                    student.pk,
                    prog.pk,
                    e,
                )

    missing_docs = []
    if active_programs:
        try:
            from programs.models import ProgramDocument

            missing_docs = list(
                ProgramDocument.objects.filter(program__in=active_programs)
                .exclude(student_submissions__student=student)
                .select_related("program")
            )
        except Exception:
            missing_docs = []

    needs_bg = False
    if (
        active_programs
        and student.date_of_birth
        and student.date_of_birth != datetime.date(1900, 1, 1)
    ):
        try:
            needs_bg = student.needs_background_check()
        except Exception:
            needs_bg = False

    action_items = {
        "total_balance_due": float(total_balance),
        "program_balances": program_balances,
        "missing_documents": missing_docs,
        "needs_background_check": needs_bg,
        "has_action_items": bool(total_balance > 0 or missing_docs or needs_bg),
    }

    has_content = bool(
        confirmed_shifts
        or open_opportunities
        or attendance_stats["total_hours"] > 0
        or outreach_summary["total_hours"] > 0
        or travel_highlights
        or calendar_events
        or action_items["has_action_items"]
    )

    return {
        "student": student,
        "days_ahead": days_ahead,
        "reference_date": ref_date,
        "end_date": end_date,
        "confirmed_shifts": confirmed_shifts,
        "open_opportunities": open_opportunities,
        "outreach_summary": outreach_summary,
        "attendance_stats": attendance_stats,
        "travel_highlights": travel_highlights,
        "calendar_events": calendar_events,
        "action_items": action_items,
        "has_content": has_content,
    }


def collect_parent_digest_data(
    adult: Adult,
    days_ahead: int = 7,
    reference_date: Optional[datetime.date] = None,
) -> Dict[str, Any]:
    """Collect commitments, updates, and calendar events for all active enrolled students of a parent."""
    ref_date = reference_date or timezone.localdate()
    end_date = ref_date + datetime.timedelta(days=days_ahead)

    linked_students = [s for s in adult.all_students() if not s.graduated]
    students_data = []

    for s in linked_students:
        s_data = collect_student_digest_data(
            s, days_ahead=days_ahead, reference_date=ref_date
        )
        if s_data["has_content"]:
            students_data.append(s_data)

    calendar_events = get_upcoming_calendar_events(
        user=adult.user,
        role="Parent",
        days_ahead=days_ahead,
        reference_date=ref_date,
    )

    chaperone_signups = []
    try:
        from travel.models import TravelParentChaperoneSignup

        chaperones = list(
            TravelParentChaperoneSignup.objects.filter(
                adult=adult,
                event__end_date__gte=ref_date,
                event__start_date__lte=end_date + datetime.timedelta(days=14),
            ).select_related("event")
        )
        for cs in chaperones:
            chaperone_signups.append(
                {
                    "event_name": cs.event.name,
                    "start_date": cs.event.start_date,
                    "end_date": cs.event.end_date,
                }
            )
    except Exception:
        chaperone_signups = []

    has_content = (
        any(sd["has_content"] for sd in students_data)
        or bool(calendar_events)
        or bool(chaperone_signups)
    )

    return {
        "adult": adult,
        "days_ahead": days_ahead,
        "reference_date": ref_date,
        "end_date": end_date,
        "students_data": students_data,
        "calendar_events": calendar_events,
        "chaperone_signups": chaperone_signups,
        "has_content": has_content,
    }


def collect_mentor_digest_data(
    adult: Adult,
    days_ahead: int = 7,
    reference_date: Optional[datetime.date] = None,
) -> Dict[str, Any]:
    """Collect mentor commitments, shifts needing support, attendance stats, calendar events, and action items."""
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
    active_progs = list(Program.objects.filter(active=True))
    attendance_stats = compute_attendance_stats(
        adult,
        programs=active_progs,
        is_student=False,
        days_ahead=days_ahead,
        reference_date=ref_date,
    )

    # 4. Travel commitments
    travel_highlights = []
    try:
        from travel.models import TravelMentorSignup

        travel_signups = list(
            TravelMentorSignup.objects.filter(
                adult=adult,
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
                    "is_driver": ts.is_driver,
                }
            )
    except Exception:
        travel_highlights = []

    # 5. Upcoming calendar events from active feeds
    calendar_events = get_upcoming_calendar_events(
        user=adult.user,
        role="Mentor",
        days_ahead=days_ahead,
        reference_date=ref_date,
    )

    # 6. Action items (Pending Agreements, Clearances)
    pending_agreements = []
    try:
        from programs.models import MentorAgreement

        if not adult.has_accepted_current_agreement():
            pending_agreements = list(
                MentorAgreement.objects.filter(is_active=True).exclude(
                    acceptances__adult=adult
                )
            )
    except Exception:
        pending_agreements = []

    needs_bg = False
    try:
        if hasattr(adult, "needs_background_check"):
            needs_bg = adult.needs_background_check()
    except Exception:
        needs_bg = False

    action_items = {
        "has_pending_agreements": bool(pending_agreements),
        "pending_agreements": pending_agreements,
        "needs_background_check": needs_bg,
        "has_action_items": bool(pending_agreements or needs_bg),
    }

    has_content = bool(
        confirmed_shifts
        or needs_support_shifts
        or attendance_stats["total_hours"] > 0
        or travel_highlights
        or calendar_events
        or action_items["has_action_items"]
    )

    return {
        "adult": adult,
        "days_ahead": days_ahead,
        "reference_date": ref_date,
        "end_date": end_date,
        "confirmed_shifts": confirmed_shifts,
        "needs_support_shifts": needs_support_shifts,
        "attendance_stats": attendance_stats,
        "travel_highlights": travel_highlights,
        "calendar_events": calendar_events,
        "action_items": action_items,
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

    # 2. Parents (strictly opted in via email_updates=True)
    if role in ("all", "parent"):
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
