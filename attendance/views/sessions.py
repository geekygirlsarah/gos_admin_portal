from datetime import timedelta

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import Case, IntegerField, Q, Value, When
from django.db.models.functions import Coalesce, Lower, NullIf
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime
from django.views import View
from django.views.decorators.http import require_http_methods

from audit.service import log_deletion
from programs.models import Adult, Program, Student
from programs.permission_views import (
    LeadMentorRequiredMixin,
    can_user_delete,
    can_user_read,
    can_user_write,
)
from programs.utils import active_students, redirect_back

from ..models import AttendanceSession


def _week_bounds(now=None):
    now = now or timezone.localtime()
    start = (now - timedelta(days=now.weekday())).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    end = start + timedelta(days=7)
    return start, end


@login_required
@require_http_methods(["GET", "POST"])
def student_attendance_view(request, pk):
    student = get_object_or_404(Student, pk=pk)

    if not can_user_read(request.user, "attendance", obj=student):
        messages.error(request, "You do not have permission to view attendance.")
        return redirect("home")

    # Optional program filter
    program_id = request.GET.get("program_id") or request.POST.get("program_id")
    program = Program.objects.filter(id=program_id).first() if program_id else None

    # Handle create/update/delete
    if request.method == "POST":
        action = request.POST.get("action")
        if not can_user_write(request.user, "attendance"):
            return render(
                request,
                "students/attendance.html",
                {
                    "student": student,
                    "error": "You do not have permission to modify attendance.",
                },
                status=403,
            )
        if action == "create":
            check_in = request.POST.get("check_in")
            check_out = request.POST.get("check_out") or None
            prog_id = (
                int(request.POST.get("program_id"))
                if request.POST.get("program_id")
                else None
            )
            if not prog_id:
                return render(
                    request,
                    "students/attendance.html",
                    {
                        "student": student,
                        "error": "Program is required to create a session.",
                    },
                    status=400,
                )
            prog = get_object_or_404(Program, id=prog_id)
            if not prog.has_feature("attendance"):
                return render(
                    request,
                    "students/attendance.html",
                    {
                        "student": student,
                        "error": "Attendance is not enabled for the selected program.",
                    },
                    status=400,
                )
            session = AttendanceSession(
                program=prog, student=student, check_in=check_in, check_out=check_out
            )
            ci = parse_datetime(str(check_in))
            co = parse_datetime(str(check_out)) if check_out else None
            if ci and timezone.is_naive(ci):
                ci = timezone.make_aware(ci, timezone.get_current_timezone())
            if co and timezone.is_naive(co):
                co = timezone.make_aware(co, timezone.get_current_timezone())
            session.check_in = ci or timezone.now()
            session.check_out = co
            session.recompute_duration()
            session.save()
            return redirect("student_attendance", pk=student.pk)
        elif action == "update":
            session_id = request.POST.get("session_id")
            session = get_object_or_404(
                AttendanceSession, id=session_id, student=student
            )
            ci = parse_datetime(request.POST.get("check_in"))
            co_raw = request.POST.get("check_out")
            co = parse_datetime(co_raw) if co_raw else None
            if ci and timezone.is_naive(ci):
                ci = timezone.make_aware(ci, timezone.get_current_timezone())
            if co and timezone.is_naive(co):
                co = timezone.make_aware(co, timezone.get_current_timezone())
            session.check_in = ci or session.check_in
            session.check_out = co
            session.recompute_duration()
            session.save()
            return redirect("student_attendance", pk=student.pk)
        elif action == "delete":
            if not can_user_delete(request.user, "attendance"):
                return render(
                    request,
                    "students/attendance.html",
                    {
                        "student": student,
                        "error": "You do not have permission to delete attendance records.",
                    },
                    status=403,
                )
            session_id = request.POST.get("session_id")
            session = get_object_or_404(
                AttendanceSession, id=session_id, student=student
            )
            log_deletion(
                resource=session,
                request=request,
                before={
                    "student": str(student),
                    "program": str(session.program),
                    "check_in": str(session.check_in),
                    "check_out": str(session.check_out),
                },
                notes=f"Attendance session for {student} deleted.",
            )
            session.delete()
            return redirect("student_attendance", pk=student.pk)

    # GET rendering
    sessions = (
        AttendanceSession.objects.filter(student=student)
        .select_related("program")
        .order_by("-check_in")
    )

    week_start, week_end = _week_bounds()
    week_sessions = sessions.filter(check_in__lt=week_end).filter(
        Q(check_out__isnull=True) | Q(check_out__gt=week_start)
    )
    total_hours = 0.0
    for s in week_sessions:
        ci = s.check_in if s.check_in > week_start else week_start
        co = s.check_out or timezone.now()
        if co > week_end:
            co = week_end
        if co > ci:
            total_hours += (co - ci).total_seconds() / 3600.0

    # Programs the student is/was enrolled in (attendance-enabled only for creation UI)
    enrolled_programs = Program.objects.filter(
        enrollment__student=student, features__key="attendance"
    ).distinct()

    # Overall totals since program start
    overall_start_date = None
    if program and program.start_date:
        overall_start_date = program.start_date
    else:
        # Use the earliest program start_date among enrolled programs, if any
        start_dates = [p.start_date for p in enrolled_programs if p.start_date]
        if start_dates:
            overall_start_date = min(start_dates)
        else:
            # Fallback to the student's earliest session date
            earliest_session = sessions.order_by("check_in").first()
            if earliest_session:
                overall_start_date = earliest_session.check_in.date()

    overall_total_hours = 0.0
    overall_avg_hours_per_week = 0.0
    overall_start_display = None
    if overall_start_date:
        from datetime import datetime

        tz = timezone.get_current_timezone()
        start_dt = timezone.make_aware(
            datetime.combine(overall_start_date, datetime.min.time()), tz
        )
        now = timezone.now()
        # Filter sessions since start date; if a program filter was provided, restrict to it
        all_since_start = sessions.filter(check_in__gte=start_dt)
        if program:
            all_since_start = all_since_start.filter(program=program)
        for s in all_since_start:
            ci = s.check_in if s.check_in > start_dt else start_dt
            co = s.check_out or now
            if co > ci:
                overall_total_hours += (co - ci).total_seconds() / 3600.0

        days_elapsed = (timezone.localdate() - overall_start_date).days
        weeks_elapsed = max(
            (days_elapsed // 7) + 1, 1
        )  # at least 1 week to avoid div by zero
        overall_avg_hours_per_week = overall_total_hours / weeks_elapsed
        overall_start_display = overall_start_date.strftime("%b %d, %Y")

    if program:
        sessions = sessions.filter(program=program)

    # Determine user role for permissions
    role = None
    if (
        request.user.is_superuser
        or request.user.groups.filter(name="LeadMentor").exists()
    ):
        role = "LeadMentor"
    elif request.user.groups.filter(name="Mentor").exists():
        role = "Mentor"
    elif request.user.groups.filter(name="Parent").exists():
        role = "Parent"
    elif request.user.groups.filter(name="Student").exists():
        role = "Student"

    return render(
        request,
        "students/attendance.html",
        {
            "student": student,
            "sessions": sessions,
            "week_start": week_start,
            "week_end": week_end - timedelta(seconds=1),
            "weekly_hours": round(total_hours, 2),
            "enrolled_programs": enrolled_programs,
            "selected_program": program,
            "overall_start_date": overall_start_display,
            "overall_total_hours": round(overall_total_hours, 2),
            "overall_avg_hours_per_week": round(overall_avg_hours_per_week, 2),
            "role": role,
            "can_write_attendance": can_user_write(request.user, "attendance"),
        },
    )


@login_required
def who_is_here_view(request):
    if not can_user_read(request.user, "attendance"):
        messages.error(request, "You do not have permission to view attendance.")
        return redirect("home")

    # Filter by program if provided
    program_id = request.GET.get("program_id")
    active_sessions = AttendanceSession.objects.filter(
        check_out__isnull=True
    ).select_related("student", "adult", "program")
    if program_id and program_id.isdigit():
        active_sessions = active_sessions.filter(program_id=program_id)

    now = timezone.localtime()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    today_sessions = active_sessions.filter(check_in__gte=today_start)
    stale_sessions = active_sessions.filter(check_in__lt=today_start)

    today_student_count = today_sessions.filter(student__isnull=False).count()
    today_mentor_count = today_sessions.filter(adult__isnull=False).count()
    today_visitor_count = today_sessions.filter(
        student__isnull=True, adult__isnull=True
    ).count()

    programs = Program.objects.filter(features__key="attendance").distinct()

    return render(
        request,
        "attendance/who_is_here.html",
        {
            "today_sessions": today_sessions,
            "stale_sessions": stale_sessions,
            "programs": programs,
            "selected_program_id": (
                int(program_id) if program_id and program_id.isdigit() else None
            ),
            "today_signed_in_count": today_sessions.count(),
            "stale_sessions_count": stale_sessions.count(),
            "today_student_count": today_student_count,
            "today_mentor_count": today_mentor_count,
            "today_visitor_count": today_visitor_count,
        },
    )


@login_required
@require_http_methods(["POST"])
def close_attendance_session(request, pk):
    if not can_user_write(request.user, "attendance"):
        messages.error(request, "You do not have permission to modify attendance.")
        return redirect("attendance_active")

    session = get_object_or_404(AttendanceSession, pk=pk)
    if not session.check_out:
        now = timezone.now()
        local_now = timezone.localtime(now)
        today_start = local_now.replace(hour=0, minute=0, second=0, microsecond=0)

        # If session started before today local time, it's stale
        if session.check_in < today_start:
            # Assume 1 hour duration for stale sessions to keep hours realistic
            session.check_out = session.check_in + timedelta(hours=1)
            messages.success(
                request,
                f"Closed stale session for {session.student or session.visitor_name} with 1-hour default duration.",
            )
        else:
            session.check_out = now
            messages.success(
                request,
                f"Closed session for {session.student or session.visitor_name}.",
            )

        session.recompute_duration()
        session.save(update_fields=["check_out", "duration_minutes", "updated_at"])
    else:
        messages.info(request, "Session is already closed.")

    return redirect_back(request, "attendance_active")


@login_required
@require_http_methods(["POST"])
def close_stale_attendance_sessions(request):
    if not can_user_write(request.user, "attendance"):
        messages.error(request, "You do not have permission to modify attendance.")
        return redirect("attendance_active")

    now = timezone.localtime()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    stale_sessions = AttendanceSession.objects.filter(
        check_out__isnull=True, check_in__lt=today_start
    )

    duration_hours = int(request.POST.get("duration", 1))
    count = stale_sessions.count()

    for session in stale_sessions:
        # Assume duration_hours duration for stale sessions to keep hours realistic
        session.check_out = session.check_in + timedelta(hours=duration_hours)
        session.recompute_duration()
        session.save(update_fields=["check_out", "duration_minutes", "updated_at"])

    messages.success(
        request,
        f"Closed {count} stale sessions with {duration_hours}-hour default duration.",
    )
    return redirect_back(request, "attendance_active")


def _attendance_date_bounds(params):
    """Return (start, end) datetime bounds for a date-range filter param dict.

    `start` is inclusive, `end` is exclusive. An empty range returns (None, None)
    meaning no date filtering.
    """
    range_key = params.get("range")

    if range_key == "today":
        start = timezone.localtime().replace(hour=0, minute=0, second=0, microsecond=0)
        return start, start + timedelta(days=1)

    if range_key == "yesterday":
        now = timezone.localtime()
        today = now.replace(hour=0, minute=0, second=0, microsecond=0)
        return today - timedelta(days=1), today

    if range_key == "week":
        now = timezone.localtime()
        today = now.date()
        return (
            timezone.make_aware(
                timezone.datetime.combine(
                    today - timedelta(days=6), timezone.datetime.min.time()
                )
            ),
            timezone.make_aware(
                timezone.datetime.combine(
                    today + timedelta(days=1), timezone.datetime.min.time()
                )
            ),
        )

    if range_key == "month":
        now = timezone.localtime()
        today = now.date()
        return (
            timezone.make_aware(
                timezone.datetime.combine(
                    today - timedelta(days=29), timezone.datetime.min.time()
                )
            ),
            timezone.make_aware(
                timezone.datetime.combine(
                    today + timedelta(days=1), timezone.datetime.min.time()
                )
            ),
        )

    if range_key == "custom":
        start_date = params.get("start_date")
        end_date = params.get("end_date")
        if start_date and end_date:
            start = parse_date(start_date)
            end = parse_date(end_date)
            if start and end and end >= start:
                tz = timezone.get_current_timezone()
                start_dt = timezone.make_aware(
                    timezone.datetime.combine(start, timezone.datetime.min.time()), tz
                )
                end_dt = timezone.make_aware(
                    timezone.datetime.combine(
                        end + timedelta(days=1), timezone.datetime.min.time()
                    ),
                    tz,
                )
                return start_dt, end_dt
        return None, None

    return None, None


def _filter_attendance_sessions(params):
    """Build the AttendanceSession queryset filtered by GET params.

    Returns (sessions, range_start, range_end) where range_start/range_end are
    the inclusive/exclusive datetime bounds used (None when no date filter).
    """
    sessions = AttendanceSession.objects.select_related("student", "adult", "program")

    program_id = params.get("program_id")
    if program_id and program_id.isdigit():
        sessions = sessions.filter(program_id=program_id)

    person_type = params.get("person_type")
    if person_type == "student":
        sessions = sessions.filter(student__isnull=False)
    elif person_type == "mentor":
        sessions = sessions.filter(adult__isnull=False)
    elif person_type == "visitor":
        sessions = sessions.filter(student__isnull=True, adult__isnull=True)

    status = params.get("status")
    if status == "open":
        sessions = sessions.filter(check_out__isnull=True)
    elif status == "closed":
        sessions = sessions.filter(check_out__isnull=False)

    range_start, range_end = _attendance_date_bounds(params)
    if range_start and range_end:
        sessions = sessions.filter(check_in__gte=range_start, check_in__lt=range_end)

    return sessions, range_start, range_end


def _session_person_label(session):
    if session.student_id:
        return session.student.full_name
    if session.adult_id:
        return session.adult.full_name
    return session.visitor_name or "Unknown"


def _session_person_type(session):
    if session.student_id:
        return "Student"
    if session.adult_id:
        return "Mentor"
    return "Visitor"


_ATTENDANCE_EXPORT_HEADERS = [
    "Person",
    "Type",
    "Program",
    "Check-in",
    "Check-out",
    "Duration (minutes)",
    "Visitor Team #",
]


def _attendance_export_row(s):
    return [
        _session_person_label(s),
        _session_person_type(s),
        s.program.name if s.program else "",
        (
            timezone.localtime(s.check_in).strftime("%Y-%m-%d %H:%M")
            if s.check_in
            else ""
        ),
        (
            timezone.localtime(s.check_out).strftime("%Y-%m-%d %H:%M")
            if s.check_out
            else ""
        ),
        s.duration_minutes,
        s.visitor_team_number or "",
    ]


def _export_attendance_csv(sessions):
    from programs.utils.spreadsheets import export_csv_response

    rows = (_attendance_export_row(s) for s in sessions.iterator(chunk_size=500))
    return export_csv_response(
        filename="attendance_entries.csv",
        headers=_ATTENDANCE_EXPORT_HEADERS,
        rows=rows,
    )


def _export_attendance_excel(sessions):
    from programs.utils.spreadsheets import export_xlsx_response

    rows = (_attendance_export_row(s) for s in sessions.iterator(chunk_size=500))
    return export_xlsx_response(
        filename="attendance_entries.xlsx",
        headers=_ATTENDANCE_EXPORT_HEADERS,
        rows=rows,
        sheet_title="Attendance Entries",
    )


class AllAttendanceView(LoginRequiredMixin, LeadMentorRequiredMixin, View):
    def get(self, request):
        params = request.GET
        sort = params.get("sort", "check_in")
        direction = params.get("dir", "desc")

        sessions, range_start, range_end = _filter_attendance_sessions(params)

        # Sorting logic
        if sort == "person":
            sessions = sessions.annotate(
                person_sort=Coalesce(
                    "student__last_name", "adult__last_name", "visitor_name"
                )
            )
            order_field = "person_sort"
        elif sort == "program":
            order_field = "program__name"
        elif sort == "check_out":
            order_field = "check_out"
        elif sort == "duration":
            order_field = "duration_minutes"
        elif sort == "type":
            sessions = sessions.annotate(
                type_order=Case(
                    When(student__isnull=False, then=Value(1)),
                    When(adult__isnull=False, then=Value(2)),
                    default=Value(3),
                    output_field=IntegerField(),
                )
            )
            order_field = "type_order"
        else:  # default to check_in
            order_field = "check_in"

        if direction == "asc":
            sessions = sessions.order_by(order_field, "id")
        else:
            sessions = sessions.order_by(f"-{order_field}", "-id")

        # Export (CSV or Excel) of the full filtered/sorted queryset.
        export = params.get("export")
        if export == "csv":
            return _export_attendance_csv(sessions)
        if export == "xlsx":
            return _export_attendance_excel(sessions)

        total_sessions_count = sessions.count()
        open_sessions_count = sessions.filter(check_out__isnull=True).count()

        # Distinct people represented in the filtered sessions, broken down by
        # person type. A session belongs to exactly one of the three buckets.
        unique_students = (
            sessions.filter(student__isnull=False).values("student").distinct().count()
        )
        unique_mentors = (
            sessions.filter(adult__isnull=False).values("adult").distinct().count()
        )
        unique_visitors = (
            sessions.filter(student__isnull=True, adult__isnull=True)
            .exclude(visitor_name="")
            .values("visitor_name")
            .distinct()
            .count()
        )
        unique_attendees = unique_students + unique_mentors + unique_visitors

        programs = Program.objects.filter(features__key="attendance").distinct()

        return render(
            request,
            "attendance/all_attendance.html",
            {
                "sessions": sessions[:500],
                "programs": programs,
                "students": active_students().order_by(
                    Lower(
                        Coalesce(
                            NullIf("preferred_first_name", Value("")),
                            "legal_first_name",
                        )
                    ),
                    Lower("last_name"),
                ),
                "mentors": Adult.objects.filter(is_mentor=True).order_by(
                    Lower(
                        Coalesce(
                            NullIf("preferred_first_name", Value("")),
                            "legal_first_name",
                        )
                    ),
                    Lower("last_name"),
                ),
                "selected_program_id": (
                    int(params.get("program_id"))
                    if params.get("program_id") and params.get("program_id").isdigit()
                    else None
                ),
                "selected_person_type": params.get("person_type", ""),
                "selected_status": params.get("status", ""),
                "selected_range": params.get("range", ""),
                "start_date": params.get("start_date", ""),
                "end_date": params.get("end_date", ""),
                "range_start": range_start,
                "range_end": range_end,
                "current_sort": sort,
                "current_dir": direction,
                "total_sessions_count": total_sessions_count,
                "open_sessions_count": open_sessions_count,
                "unique_attendees": unique_attendees,
                "unique_students": unique_students,
                "unique_mentors": unique_mentors,
                "unique_visitors": unique_visitors,
            },
        )

    def post(self, request):
        action = request.POST.get("action")

        if action == "add":
            person_type = request.POST.get("person_type")
            program_id = request.POST.get("program_id")
            ci_raw = request.POST.get("check_in")
            co_raw = request.POST.get("check_out")

            if not ci_raw:
                messages.error(request, "Check-in time is required.")
                return redirect_back(request, "all_attendance")
            if not program_id and person_type != "mentor":
                messages.error(request, "Program is required.")
                return redirect_back(request, "all_attendance")

            ci = parse_datetime(ci_raw)
            co = parse_datetime(co_raw) if co_raw else None

            if ci and timezone.is_naive(ci):
                ci = timezone.make_aware(ci, timezone.get_current_timezone())
            if co and timezone.is_naive(co):
                co = timezone.make_aware(co, timezone.get_current_timezone())

            new_session = AttendanceSession(
                program_id=int(program_id) if program_id else None,
                check_in=ci,
                check_out=co,
            )

            if person_type == "student":
                student_id = request.POST.get("student_id")
                if student_id and student_id.isdigit():
                    new_session.student_id = int(student_id)
            elif person_type == "mentor":
                adult_id = request.POST.get("adult_id")
                if adult_id and adult_id.isdigit():
                    new_session.adult_id = int(adult_id)
            elif person_type == "visitor":
                new_session.visitor_name = request.POST.get("visitor_name", "").strip()
                team_raw = request.POST.get("visitor_team_number")
                if team_raw and team_raw.isdigit():
                    new_session.visitor_team_number = int(team_raw)

            new_session.recompute_duration()
            new_session.save()
            messages.success(request, "Attendance entry added.")
            return redirect_back(request, "all_attendance")

        session_id = request.POST.get("session_id")
        session = get_object_or_404(AttendanceSession, id=session_id)

        if action == "update":
            ci_raw = request.POST.get("check_in")
            co_raw = request.POST.get("check_out")
            program_id = request.POST.get("program_id")
            visitor_name = request.POST.get("visitor_name")
            visitor_team_number = request.POST.get("visitor_team_number")

            ci = parse_datetime(ci_raw) if ci_raw else None
            co = parse_datetime(co_raw) if co_raw else None

            if ci and timezone.is_naive(ci):
                ci = timezone.make_aware(ci, timezone.get_current_timezone())
            if co and timezone.is_naive(co):
                co = timezone.make_aware(co, timezone.get_current_timezone())

            if ci:
                session.check_in = ci
            session.check_out = co

            if program_id and program_id.isdigit():
                session.program_id = int(program_id)

            if visitor_name is not None and not session.student and not session.adult:
                new_name = visitor_name.strip()
                session.visitor_name = new_name
                # Keep linked events in sync if they exist
                if (
                    session.opened_by_event
                    and not session.opened_by_event.student
                    and not session.opened_by_event.adult
                ):
                    session.opened_by_event.visitor_name = new_name
                    session.opened_by_event.save(update_fields=["visitor_name"])
                if (
                    session.closed_by_event
                    and not session.closed_by_event.student
                    and not session.closed_by_event.adult
                ):
                    session.closed_by_event.visitor_name = new_name
                    session.closed_by_event.save(update_fields=["visitor_name"])

            if visitor_team_number is not None:
                new_team = None
                if visitor_team_number.isdigit():
                    new_team = int(visitor_team_number)

                session.visitor_team_number = new_team
                # Keep linked events in sync if they exist
                if (
                    session.opened_by_event
                    and not session.opened_by_event.student
                    and not session.opened_by_event.adult
                ):
                    session.opened_by_event.visitor_team_number = new_team
                    session.opened_by_event.save(update_fields=["visitor_team_number"])
                if (
                    session.closed_by_event
                    and not session.closed_by_event.student
                    and not session.closed_by_event.adult
                ):
                    session.closed_by_event.visitor_team_number = new_team
                    session.closed_by_event.save(update_fields=["visitor_team_number"])

            session.recompute_duration()
            session.save()
            messages.success(request, "Attendance entry updated.")

        elif action == "delete":
            log_deletion(
                resource=session,
                request=request,
                before={
                    "student": str(session.student) if session.student else None,
                    "adult": str(session.adult) if session.adult else None,
                    "visitor_name": session.visitor_name,
                    "program": str(session.program) if session.program else None,
                    "check_in": str(session.check_in),
                    "check_out": str(session.check_out),
                },
                notes="Attendance entry deleted from All Program Entries.",
            )
            session.delete()
            messages.success(request, "Attendance entry deleted.")

        return redirect_back(request, "all_attendance")
