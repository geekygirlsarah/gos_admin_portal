import calendar as cal_mod
import json
from datetime import date, datetime, timedelta
from urllib.parse import urlencode

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Count, Max, Sum
from django.http import HttpResponseRedirect
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from programs.models import Program, Student
from programs.permission_views import can_user_read
from programs.utils import active_students, active_students_in_program

from ..models import AttendanceSession


@login_required
def student_hours_view(request, pk):
    """Student/parent attendance hours visualization with line chart and calendar."""
    student = get_object_or_404(Student, pk=pk)

    if not can_user_read(request.user, "attendance", obj=student):
        messages.error(request, "You do not have permission to view attendance.")
        return redirect("home")

    # Attendance-enabled programs the student is enrolled in (active + past)
    enrolled_programs = Program.objects.filter(
        enrollment__student=student, features__key="attendance"
    ).distinct()

    # Program filter
    program_id = request.GET.get("program_id")
    selected_program = None
    if program_id and program_id.isdigit():
        selected_program = enrolled_programs.filter(id=int(program_id)).first()

    sessions = AttendanceSession.objects.filter(student=student).select_related(
        "program"
    )
    if selected_program:
        sessions = sessions.filter(program=selected_program)

    # --- Line chart: cumulative hours per week ---
    tz = timezone.get_current_timezone()
    now = timezone.localtime()

    chart_labels = []
    chart_data = []

    overall_start_date = None
    if selected_program and selected_program.start_date:
        overall_start_date = selected_program.start_date
    else:
        start_dates = [p.start_date for p in enrolled_programs if p.start_date]
        if start_dates:
            overall_start_date = min(start_dates)
        else:
            earliest = sessions.order_by("check_in").first()
            if earliest:
                overall_start_date = earliest.check_in.date()

    if overall_start_date:
        start_dt = timezone.make_aware(
            datetime.combine(overall_start_date, datetime.min.time()), tz
        )

        # Build week boundaries from start to now
        week_start = overall_start_date
        cumulative = 0.0
        while week_start <= now.date():
            week_end = week_start + timedelta(days=7)
            week_start_dt = timezone.make_aware(
                datetime.combine(week_start, datetime.min.time()), tz
            )
            week_end_dt = timezone.make_aware(
                datetime.combine(week_end, datetime.min.time()), tz
            )
            # Sum hours for sessions overlapping this week
            week_hours = 0.0
            for s in sessions.filter(check_in__lt=week_end_dt).exclude(
                check_out__isnull=False, check_out__lt=week_start_dt
            ):
                ci = s.check_in if s.check_in > week_start_dt else week_start_dt
                co = s.check_out or now
                if co > week_end_dt:
                    co = week_end_dt
                if co > ci:
                    week_hours += (co - ci).total_seconds() / 3600.0
            cumulative += week_hours
            chart_labels.append(week_start.strftime("%b %d"))
            chart_data.append(round(cumulative, 2))
            week_start = week_end

    # --- Stats ---
    total_hours = 0.0
    if overall_start_date:
        start_dt = timezone.make_aware(
            datetime.combine(overall_start_date, datetime.min.time()), tz
        )
        for s in sessions.filter(check_in__gte=start_dt):
            co = s.check_out or now
            if co > s.check_in:
                total_hours += (co - s.check_in).total_seconds() / 3600.0
        days = (now.date() - overall_start_date).days
        weeks_elapsed = max((days // 7) + 1, 1)
    else:
        weeks_elapsed = 1

    avg_hours_per_week = total_hours / weeks_elapsed if weeks_elapsed > 0 else 0

    # --- Calendar: hours per day for the current month ---
    cal_month = int(request.GET.get("cal_month", now.month))
    cal_year = int(request.GET.get("cal_year", now.year))

    cal = cal_mod.Calendar(firstweekday=6)  # Sunday first
    month_days = cal.monthdayscalendar(cal_year, cal_month)
    month_names = [
        "",
        "January",
        "February",
        "March",
        "April",
        "May",
        "June",
        "July",
        "August",
        "September",
        "October",
        "November",
        "December",
    ]

    # Aggregate sessions by day for this month (store per-session detail)
    month_start = timezone.make_aware(datetime(cal_year, cal_month, 1, 0, 0, 0), tz)
    last_day = cal_mod.monthrange(cal_year, cal_month)[1]
    month_end = timezone.make_aware(
        datetime(cal_year, cal_month, last_day, 23, 59, 59), tz
    )
    day_sessions = {}
    for s in sessions.filter(check_in__lt=month_end).exclude(
        check_out__isnull=False, check_out__lt=month_start
    ):
        ci = s.check_in if s.check_in > month_start else month_start
        co = s.check_out or now
        if co > month_end:
            co = month_end
        if co > ci:
            session_date = timezone.localtime(ci).date()
            total_secs = (co - ci).total_seconds()
            hours = int(total_secs // 3600)
            minutes = int((total_secs % 3600) // 60)
            local_ci = timezone.localtime(ci)
            local_co = timezone.localtime(co)
            entry = {
                "check_in": local_ci.strftime("%I:%M %p").lstrip("0"),
                "check_out": local_co.strftime("%I:%M %p").lstrip("0"),
                "total_hrs": round(total_secs / 3600.0, 1),
                "hours_part": hours,
                "minutes_part": minutes,
            }
            day_sessions.setdefault(session_date, []).append(entry)

    # Build calendar data with per-session detail
    calendar_data = []
    for week in month_days:
        week_row = []
        for day in week:
            if day == 0:
                week_row.append(None)
            else:
                d = date(cal_year, cal_month, day)
                sessions_list = day_sessions.get(d, [])
                total = round(sum(s["total_hrs"] for s in sessions_list), 1)
                week_row.append({"day": day, "hours": total, "sessions": sessions_list})
        calendar_data.append(week_row)

    # Today highlight
    today = now.date()
    cal_today = (
        today.day if cal_year == today.year and cal_month == today.month else None
    )

    # Determine prev/next month
    if cal_month == 1:
        prev_month, prev_year = 12, cal_year - 1
    else:
        prev_month, prev_year = cal_month - 1, cal_year
    if cal_month == 12:
        next_month, next_year = 1, cal_year + 1
    else:
        next_month, next_year = cal_month + 1, cal_year

    # Clamp calendar navigation: earliest is the overall start date, latest is today
    cal_earliest = overall_start_date
    cal_latest = today
    can_go_prev = True
    can_go_next = True
    if cal_earliest:
        if (prev_year, prev_month) < (cal_earliest.year, cal_earliest.month):
            can_go_prev = False
    if (next_year, next_month) > (cal_latest.year, cal_latest.month):
        can_go_next = False

    return render(
        request,
        "attendance/hours_visualization.html",
        {
            "student": student,
            "enrolled_programs": enrolled_programs,
            "selected_program": selected_program,
            "chart_labels_json": json.dumps(chart_labels),
            "chart_data_json": json.dumps(chart_data),
            "overall_start_date": overall_start_date,
            "total_hours": round(total_hours, 1),
            "avg_hours_per_week": round(avg_hours_per_week, 1),
            "weeks_elapsed": weeks_elapsed,
            "calendar_data": calendar_data,
            "cal_month": cal_month,
            "cal_year": cal_year,
            "cal_month_name": month_names[cal_month],
            "cal_today": cal_today,
            "prev_month": prev_month,
            "prev_year": prev_year,
            "next_month": next_month,
            "next_year": next_year,
            "can_go_prev": can_go_prev,
            "can_go_next": can_go_next,
            "day_names": ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"],
        },
    )


@login_required
def attendance_hours_chart_view(request):
    """Mentor attendance hours bar chart with configurable average-lines."""
    if not can_user_read(request.user, "attendance"):
        messages.error(request, "You do not have permission to view attendance.")
        return redirect("home")

    programs = Program.objects.filter(
        features__key="attendance", active=True
    ).distinct()

    program_id = request.GET.get("program_id")
    selected_program = None
    if program_id and program_id.isdigit():
        selected_program = programs.filter(id=int(program_id)).first()

    # --- Date range ---
    now = timezone.localtime()

    date_from_str = request.GET.get("date_from", "")
    date_to_str = request.GET.get("date_to", "")
    days_of_week = request.GET.getlist("days_of_week")

    # When a program is selected and the user hasn't supplied explicit dates,
    # default to the program's date window (clamped to today if end is future).
    dates_explicitly_set = "date_from" in request.GET or "date_to" in request.GET
    if selected_program and not dates_explicitly_set:
        if selected_program.start_date:
            date_from_str = selected_program.start_date.isoformat()
        if selected_program.end_date and selected_program.end_date < now.date():
            date_to_str = selected_program.end_date.isoformat()
        else:
            date_to_str = now.date().isoformat()

    date_from = None
    date_to = None
    if date_from_str:
        try:
            date_from = datetime.strptime(date_from_str, "%Y-%m-%d").date()
        except ValueError:
            pass
    if date_to_str:
        try:
            date_to = datetime.strptime(date_to_str, "%Y-%m-%d").date()
        except ValueError:
            pass

    # --- Average lines ---
    default_avg_lines = [
        {"value": 3, "color": "#dc3545"},
        {"value": 6, "color": "#fd7e14"},
        {"value": 9, "color": "#198754"},
    ]
    avg_lines_json_str = request.GET.get("avg_lines", "")
    avg_lines = default_avg_lines
    if avg_lines_json_str:
        try:
            parsed = json.loads(avg_lines_json_str)
            if isinstance(parsed, list) and parsed:
                avg_lines = [
                    {
                        "value": float(item.get("value", 3)),
                        "color": str(item.get("color", "#dc3545")),
                    }
                    for item in parsed
                ]
        except (json.JSONDecodeError, ValueError, TypeError):
            pass

    # --- Sessions query ---
    include_unlogged = request.GET.get("include_unlogged") in (
        "1",
        "true",
        "True",
        "on",
        "yes",
    )
    sessions = AttendanceSession.objects.filter(student__isnull=False)
    if selected_program:
        sessions = sessions.filter(
            program=selected_program,
            student__in=active_students_in_program(selected_program),
        )
    else:
        sessions = sessions.filter(student__in=active_students())
    if date_from:
        sessions = sessions.filter(
            check_in__date__gte=date_from,
        )
    if date_to:
        sessions = sessions.filter(
            check_in__date__lte=date_to,
        )
    if days_of_week:
        sessions = sessions.filter(check_in__week_day__in=days_of_week)
    sessions = sessions.select_related("student")

    # --- Compute overall start for weeks_elapsed ---
    # When the user explicitly picks dates, use those; fall back to program
    # start_date, then to the earliest session.
    if dates_explicitly_set and date_from:
        overall_start_date = date_from
    elif selected_program and selected_program.start_date:
        overall_start_date = selected_program.start_date
    elif date_from:
        overall_start_date = date_from
    else:
        earliest = sessions.order_by("check_in").first()
        overall_start_date = earliest.check_in.date() if earliest else None

    if overall_start_date:
        end_date = date_to or now.date()
        days = (end_date - overall_start_date).days
        weeks_elapsed = max((days + 6) // 7, 1)
    else:
        weeks_elapsed = 1

    # --- Aggregate per student ---
    student_stats = (
        sessions.values(
            "student__id",
            "student__preferred_first_name",
            "student__legal_first_name",
            "student__last_name",
        )
        .annotate(
            total_minutes=Sum("duration_minutes"),
            session_count=Count("id"),
            days_attended=Count("check_in__date", distinct=True),
            last_attended=Max("check_in"),
        )
        .order_by("-total_minutes")
    )

    chart_labels = []
    chart_data = []
    student_list = []
    total_hours_all = 0.0

    for stat in student_stats:
        display_first = (
            stat["student__preferred_first_name"] or stat["student__legal_first_name"]
        )
        name = f"{display_first} {stat['student__last_name']}"
        hours = round((stat["total_minutes"] or 0) / 60.0, 1)
        total_hours_all += hours
        chart_labels.append(name)
        chart_data.append(hours)
        student_weeks = max(weeks_elapsed, 1)
        student_list.append(
            {
                "id": stat["student__id"],
                "name": name,
                "total_hours": hours,
                "avg_per_week": round(hours / student_weeks, 1),
                "session_count": stat["session_count"],
                "days": stat["days_attended"],
                "last_attended": stat["last_attended"],
            }
        )

    if include_unlogged:
        logged_student_ids = {stat["student__id"] for stat in student_stats}
        students_scope = (
            active_students_in_program(selected_program)
            if selected_program
            else active_students()
        )
        unlogged_students = students_scope.exclude(id__in=logged_student_ids).order_by(
            "preferred_first_name", "legal_first_name", "last_name"
        )
        for student in unlogged_students:
            name = student.display_name
            chart_labels.append(name)
            chart_data.append(0.0)
            student_list.append(
                {
                    "id": student.id,
                    "name": name,
                    "total_hours": 0.0,
                    "avg_per_week": 0.0,
                    "session_count": 0,
                    "days": 0,
                    "last_attended": None,
                }
            )

    total_hours_all = round(total_hours_all, 1)
    student_count = len(student_list)
    avg_hours_per_week = (
        round(total_hours_all / weeks_elapsed, 1) if weeks_elapsed > 0 else 0
    )

    # Per-line exceeded counts
    exceeded_counts = []
    for line in avg_lines:
        count = sum(1 for s in student_list if s["avg_per_week"] >= line["value"])
        exceeded_counts.append(
            {"label": line["value"], "color": line["color"], "count": count}
        )

    # Sort toggle
    sort_by = request.GET.get("sort", "hours")
    if sort_by == "alpha":
        student_list.sort(key=lambda s: s["name"].lower())
        paired = sorted(zip(chart_labels, chart_data), key=lambda p: p[0].lower())
    else:
        student_list.sort(key=lambda s: (-s["total_hours"], s["name"].lower()))
        paired = sorted(
            zip(chart_labels, chart_data), key=lambda p: (-p[1], p[0].lower())
        )
    chart_labels = [p[0] for p in paired]
    chart_data = [p[1] for p in paired]

    # Build annotation lines: Y position = avg_hrs_per_week * weeks_elapsed
    annotation_lines = []
    for line in avg_lines:
        y_pos = round(line["value"] * weeks_elapsed, 1)
        line_value = line["value"]
        label_value = int(line_value) if line_value == int(line_value) else line_value
        total_value = int(y_pos) if y_pos == int(y_pos) else y_pos
        annotation_lines.append(
            {
                "y": y_pos,
                "color": line["color"],
                "label": f"{label_value} hrs/wk = {total_value} hrs",
            }
        )

    # Axis orientation: names along the left (horizontal bars, tall) is the
    # default so every student is visible and comparable; pass axis=vertical
    # for names along the bottom (vertical bars, wide). Server-driven so the
    # PNG and sort links stay consistent with what's displayed.
    is_horizontal = request.GET.get("axis") != "vertical"

    # Build clean sort URLs
    base_params = {}
    if program_id:
        base_params["program_id"] = program_id
    if date_from_str:
        base_params["date_from"] = date_from_str
    if date_to_str:
        base_params["date_to"] = date_to_str
    if avg_lines_json_str:
        base_params["avg_lines"] = avg_lines_json_str
    if include_unlogged:
        base_params["include_unlogged"] = "1"
    if days_of_week:
        base_params["days_of_week"] = days_of_week
    base_params["axis"] = "horizontal" if is_horizontal else "vertical"
    sort_hours_url = f"?{urlencode(base_params, doseq=True)}&sort=hours"
    sort_alpha_url = f"?{urlencode(base_params, doseq=True)}&sort=alpha"

    swap_params = {k: v for k, v in base_params.items() if k != "axis"}
    if sort_by:
        swap_params["sort"] = sort_by
    swap_axis_url = (
        f"{urlencode(swap_params, doseq=True)}"
        f"&axis={'vertical' if is_horizontal else 'horizontal'}"
    )
    if swap_axis_url:
        swap_axis_url = f"?{swap_axis_url}"

    # Chart sizing. The tall (names-left) layout always fits horizontally. The
    # wide (names-bottom) layout is adaptive to the container by default; its
    # per-student minimum width is only applied client-side by the expand
    # button so the wrapper can scroll when a program is large.
    if is_horizontal:
        chart_height_px = max(350, student_count * 40 + 80)
        chart_min_width_px = None
    else:
        chart_height_px = max(350, min(700, (student_count + 1) * 16))
        chart_min_width_px = student_count * 150

    return render(
        request,
        "attendance/hours_chart.html",
        {
            "programs": programs,
            "selected_program": selected_program,
            "date_from": date_from_str,
            "date_to": date_to_str,
            "avg_lines": avg_lines,
            "avg_lines_json": json.dumps(avg_lines),
            "annotation_lines_json": json.dumps(annotation_lines),
            "chart_labels_json": json.dumps(chart_labels),
            "chart_data_json": json.dumps(chart_data),
            "student_list": student_list,
            "student_count": student_count,
            "avg_hours_per_week": avg_hours_per_week,
            "exceeded_counts": exceeded_counts,
            "weeks_elapsed": weeks_elapsed,
            "sort_by": sort_by,
            "sort_hours_url": sort_hours_url,
            "sort_alpha_url": sort_alpha_url,
            "is_horizontal": is_horizontal,
            "swap_axis_url": swap_axis_url,
            "chart_height_px": chart_height_px,
            "chart_min_width_px": chart_min_width_px,
            "overall_start_date": overall_start_date,
            "include_unlogged": include_unlogged,
            "days_of_week": days_of_week,
        },
    )


@login_required
def program_hours_view(request, program_id):
    """Redirect to the shared Hours Chart page with the program pre-selected."""
    url = f"{reverse('attendance_hours_chart')}?{urlencode({'program_id': program_id})}"
    return HttpResponseRedirect(url)
