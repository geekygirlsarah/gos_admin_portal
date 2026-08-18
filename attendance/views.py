from datetime import timedelta
from urllib.parse import urlencode

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views import View
from django.views.decorators.http import require_http_methods

from programs.models import Adult, Program, Student
from programs.permission_views import (
    LeadMentorRequiredMixin,
    can_user_delete,
    can_user_read,
    can_user_write,
)
from programs.utils import active_students, redirect_back

from .models import AttendanceEvent, AttendanceSession, RFIDCard


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
            # Parse datetimes via Django (they arrive in ISO or input type=datetime-local format)
            from django.utils.dateparse import parse_datetime

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
            from django.utils.dateparse import parse_datetime

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
            session.delete()
            return redirect("student_attendance", pk=student.pk)

    # GET rendering
    sessions = (
        AttendanceSession.objects.filter(student=student)
        .select_related("program")
        .order_by("-check_in")
    )

    week_start, week_end = _week_bounds()
    from django.db.models import Q

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
        overall_qs = sessions.filter(check_in__gte=start_dt)
        if program:
            overall_qs = overall_qs.filter(program=program)
        for s in overall_qs:
            ci = s.check_in
            co = s.check_out or now
            if co > ci:
                overall_total_hours += (co - ci).total_seconds() / 3600.0
        # Weeks elapsed since start (at least 1)
        days = (now.date() - overall_start_date).days
        weeks_elapsed = (days // 7) + 1
        overall_avg_hours_per_week = (
            overall_total_hours / weeks_elapsed
            if weeks_elapsed > 0
            else overall_total_hours
        )
        overall_start_display = overall_start_date

    # Pass permissions to template
    from programs.permission_views import get_user_role

    role = get_user_role(request.user)

    return render(
        request,
        "students/attendance.html",
        {
            "student": student,
            "sessions": sessions[:200],
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


class AttendanceImportView(View):
    def post(self, request):
        if not can_user_write(request.user, "attendance"):
            messages.error(request, "You do not have permission to import attendance.")
            return redirect_back(request, "import_dashboard")
        file = request.FILES.get("file")
        program_id = request.POST.get("program_id")
        overwrite = request.POST.get("overwrite") == "1"
        if not program_id:
            messages.error(request, "Please select a program for this import.")
            return redirect_back(request, "import_dashboard")
        program = Program.objects.filter(id=program_id).first()
        if not program:
            messages.error(request, "Selected program was not found.")
            return redirect_back(request, "import_dashboard")
        if not program.has_feature("attendance"):
            messages.error(
                request, "Attendance is not enabled for the selected program."
            )
            return redirect_back(request, "import_dashboard")
        if not file:
            messages.error(request, "No file uploaded.")
            return redirect_back(request, "import_dashboard")

        name = file.name.lower()
        if not name.endswith(".csv"):
            messages.error(request, "Unsupported file type. Please upload a CSV file.")
            return redirect_back(request, "import_dashboard")

        import csv
        import io

        created = 0
        updated = 0
        errors = 0
        skipped = 0
        text = io.TextIOWrapper(file.file, encoding="utf-8")
        reader = csv.DictReader(text)

        from datetime import timezone as dt_timezone

        from django.utils.dateparse import parse_datetime
        from django.utils.timezone import is_naive, make_aware

        utc = dt_timezone.utc

        def parse_utc(dt_val):
            if not dt_val:
                return None
            if hasattr(dt_val, "tzinfo"):
                # Already a datetime
                dt = dt_val
            else:
                dt = parse_datetime(str(dt_val).strip())
            if not dt:
                return None
            if is_naive(dt):
                # Treat naive as local time per system settings
                return make_aware(
                    dt, timezone=timezone.get_current_timezone()
                ).astimezone(utc)
            # Ensure in UTC for storage consistency
            return dt.astimezone(utc)

        def parse_team_number(row):
            raw = (
                row.get("visitor_team_number")
                or row.get("team_number")
                or row.get("team")
                or row.get("Visitor Team Number")
                or row.get("Team Number")
                or ""
            )
            value = str(raw).strip()
            if not value:
                return None
            try:
                team_number = int(value)
            except (TypeError, ValueError):
                return None
            return team_number if team_number > 0 else None

        def find_student(first_name, last_name, rfid):
            # Priority: RFID match
            if rfid:
                from attendance.services import resolve_card_by_uid

                card = resolve_card_by_uid(str(rfid).strip())
                if card and card.student:
                    return card.student
            # Next: name match (case-insensitive)
            fn = (first_name or "").strip()
            ln = (last_name or "").strip()
            if fn and ln:
                student = Student.objects.filter(
                    first_name__iexact=fn, last_name__iexact=ln
                ).first()
                if student:
                    return student
                student = Student.objects.filter(
                    legal_first_name__iexact=fn, last_name__iexact=ln
                ).first()
                if student:
                    return student
            return None

        try:
            for row in reader:
                first = (row.get("first_name") or row.get("First Name") or "").strip()
                last = (row.get("last_name") or row.get("Last Name") or "").strip()
                rfid = (
                    row.get("rfid")
                    or row.get("rfid_uid")
                    or row.get("RFID")
                    or row.get("RFID UID")
                    or ""
                ).strip()
                t_in_raw = (
                    row.get("time_in")
                    or row.get("time_in_utc")
                    or row.get("Time In (UTC)")
                    or row.get("time in (utc)")
                    or row.get("time_in (utc)")
                    or row.get("Time In")
                )
                t_out_raw = (
                    row.get("time_out")
                    or row.get("time_out_utc")
                    or row.get("Time Out (UTC)")
                    or row.get("time out (utc)")
                    or row.get("time_out (utc)")
                    or row.get("Time Out")
                )

                check_in = parse_utc(t_in_raw)
                check_out = parse_utc(t_out_raw)
                visitor_team_number = parse_team_number(row)
                if not check_in:
                    errors += 1
                    continue

                student = find_student(first, last, rfid)
                visitor_name = ""
                if not student:
                    # If we cannot find a student, record as visitor session with provided name or RFID
                    if first or last:
                        visitor_name = (first + " " + last).strip()
                    elif rfid:
                        visitor_name = f"RFID {rfid}"
                    else:
                        visitor_name = "Unknown"

                # Idempotency: try to find existing session with same keys
                if student:
                    existing = AttendanceSession.objects.filter(
                        program=program, student=student, check_in=check_in
                    ).first()
                else:
                    existing = AttendanceSession.objects.filter(
                        program=program,
                        student__isnull=True,
                        visitor_name=visitor_name,
                        check_in=check_in,
                    ).first()

                if existing:
                    # Update checkout if new info is provided
                    fields_to_update = []
                    if (
                        overwrite
                        and check_out
                        and (not existing.check_out or existing.check_out != check_out)
                    ):
                        existing.check_out = check_out
                        existing.recompute_duration()
                        fields_to_update.extend(
                            [
                                "check_out",
                                "duration_minutes",
                                "updated_at",
                            ]
                        )

                    if (
                        overwrite
                        and not student
                        and visitor_team_number
                        and existing.visitor_team_number != visitor_team_number
                    ):
                        existing.visitor_team_number = visitor_team_number
                        fields_to_update.append("visitor_team_number")

                    if fields_to_update:
                        existing.save(update_fields=fields_to_update)
                        updated += 1
                    else:
                        skipped += 1
                    continue

                # Create linked events (optional)
                open_event = AttendanceEvent.objects.create(
                    program=program,
                    student=student,
                    visitor_name=visitor_name if not student else "",
                    visitor_team_number=visitor_team_number if not student else None,
                    rfid_uid=rfid or "",
                    kiosk=None,
                    event_type=AttendanceEvent.IN,
                    occurred_at=check_in,
                    source="import",
                    notes="Imported from CSV",
                )
                close_event = None
                if check_out:
                    close_event = AttendanceEvent.objects.create(
                        program=program,
                        student=student,
                        visitor_name=visitor_name if not student else "",
                        visitor_team_number=(
                            visitor_team_number if not student else None
                        ),
                        rfid_uid=rfid or "",
                        kiosk=None,
                        event_type=AttendanceEvent.OUT,
                        occurred_at=check_out,
                        source="import",
                        notes="Imported from CSV",
                    )

                session = AttendanceSession(
                    program=program,
                    student=student,
                    visitor_name=visitor_name if not student else "",
                    visitor_team_number=visitor_team_number if not student else None,
                    check_in=check_in,
                    check_out=check_out,
                    opened_by_event=open_event,
                    closed_by_event=close_event,
                )
                session.recompute_duration()
                session.save()
                created += 1

            if errors:
                messages.warning(
                    request,
                    f"Attendance import completed: {created} created, {updated} updated, "
                    f"{skipped} skipped, {errors} rows had errors.",
                )
            else:
                messages.success(
                    request,
                    f"Attendance import completed: {created} created, {updated} updated, {skipped} skipped.",
                )
        except Exception as e:
            messages.error(request, f"Failed to import attendance: {e}")

        return redirect_back(request, "import_dashboard")


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

    # Allow setting a custom duration in hours, defaulting to 1
    duration_hours = 1
    try:
        if "hours" in request.POST and request.POST.get("hours"):
            duration_hours = float(request.POST.get("hours"))
    except (ValueError, TypeError):
        pass

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


@login_required
def attendance_summary_view(request):
    if not can_user_read(request.user, "attendance"):
        messages.error(request, "You do not have permission to view attendance.")
        return redirect("home")

    # Basic summary: total hours per student in current week
    start, end = _week_bounds()
    sessions = AttendanceSession.objects.filter(
        check_in__gte=start, check_in__lt=end
    ).select_related("student", "program")

    # Aggregate by student/visitor
    summary = {}
    for s in sessions:
        key = s.student.full_name if s.student else (s.visitor_name or "Unknown")
        summary[key] = summary.get(key, 0) + s.duration_minutes

    sorted_summary = sorted(summary.items(), key=lambda x: x[1], reverse=True)

    return render(
        request,
        "attendance/summary.html",
        {
            "summary": [(name, mins // 60, mins % 60) for name, mins in sorted_summary],
            "start": start,
            "end": end,
        },
    )


@login_required
def rfid_management_view(request):
    if not can_user_write(request.user, "attendance"):
        messages.error(request, "You do not have permission to manage RFID cards.")
        return redirect("home")

    from django.db.models import Q

    search_query = request.GET.get("q", "").strip()
    results = []
    assigned_cards = []
    if search_query:
        # Search students
        student_qs = Student.objects.filter(
            Q(first_name__icontains=search_query)
            | Q(last_name__icontains=search_query)
            | Q(legal_first_name__icontains=search_query)
        ).prefetch_related("rfid_cards")
        for s in student_qs[:10]:
            results.append(
                {
                    "person": s,
                    "type": "student",
                    "rfid": s.rfid_cards.filter(is_active=True).first(),
                }
            )

        # Search mentors
        adult_qs = (
            Adult.objects.filter(is_mentor=True)
            .filter(
                Q(first_name__icontains=search_query)
                | Q(last_name__icontains=search_query)
            )
            .prefetch_related("rfid_cards")
        )
        for a in adult_qs[:10]:
            results.append(
                {
                    "person": a,
                    "type": "mentor",
                    "rfid": a.rfid_cards.filter(is_active=True).first(),
                }
            )
    else:
        assigned_cards = (
            RFIDCard.objects.filter(is_active=True)
            .select_related("student", "adult")
            .order_by("-assigned_at")
        )

    if request.method == "POST":
        action = request.POST.get("action")
        if action == "assign":
            person_type = request.POST.get("person_type")
            person_id = request.POST.get("person_id")
            uid = request.POST.get("uid", "").strip()

            if not uid:
                messages.error(request, "RFID UID cannot be empty.")
            else:
                try:
                    from django.db import transaction

                    from attendance.services import find_card_by_uid

                    with transaction.atomic():
                        # Find person
                        if person_type == "student":
                            person = get_object_or_404(Student, pk=person_id)
                        else:
                            person = get_object_or_404(Adult, pk=person_id)

                        # Find if this UID is already in the system
                        existing_card = find_card_by_uid(uid)

                        # Deactivate other active cards for this person
                        person_cards = person.rfid_cards.filter(is_active=True)
                        if existing_card:
                            person_cards = person_cards.exclude(pk=existing_card.pk)
                        person_cards.update(is_active=False)

                        if existing_card:
                            # Reassign existing card
                            old_owner = existing_card.student or existing_card.adult
                            existing_card.student = (
                                person if person_type == "student" else None
                            )
                            existing_card.adult = (
                                person if person_type != "student" else None
                            )
                            existing_card.is_active = True
                            existing_card.uid = uid  # Normalize to full UID
                            existing_card.assigned_at = timezone.now()
                            existing_card.save()

                            if old_owner and old_owner != person:
                                messages.success(
                                    request,
                                    f"Reassigned RFID {uid} from {old_owner} to {person}.",
                                )
                            else:
                                messages.success(
                                    request, f"Assigned RFID {uid} to {person}."
                                )
                        else:
                            # Create new card record
                            if person_type == "student":
                                RFIDCard.objects.create(uid=uid, student=person)
                            else:
                                RFIDCard.objects.create(uid=uid, adult=person)
                            messages.success(
                                request, f"Assigned RFID {uid} to {person}."
                            )
                except Exception as e:
                    messages.error(request, f"Error assigning RFID: {e}")
            return redirect(
                f"{reverse('rfid_management')}?{urlencode({'q': search_query})}"
            )

        elif action == "deactivate":
            if not can_user_delete(request.user, "attendance"):
                messages.error(
                    request, "You do not have permission to deactivate RFID cards."
                )
                return redirect(
                    f"{reverse('rfid_management')}?{urlencode({'q': search_query})}"
                )
            card_id = request.POST.get("card_id")
            card = get_object_or_404(RFIDCard, id=card_id)
            card.is_active = False
            card.save()
            messages.success(request, f"Deactivated RFID {card.uid}")
            return redirect(
                f"{reverse('rfid_management')}?{urlencode({'q': search_query})}"
            )

    return render(
        request,
        "attendance/rfid_management.html",
        {
            "results": results,
            "assigned_cards": assigned_cards,
            "q": search_query,
        },
    )


class AllAttendanceView(LoginRequiredMixin, LeadMentorRequiredMixin, View):
    def get(self, request):
        program_id = request.GET.get("program_id")
        sort = request.GET.get("sort", "check_in")
        direction = request.GET.get("dir", "desc")

        sessions = AttendanceSession.objects.select_related(
            "student", "adult", "program"
        )

        if program_id and program_id.isdigit():
            sessions = sessions.filter(program_id=program_id)

        # Sorting logic
        if sort == "person":
            from django.db.models.functions import Coalesce

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
            from django.db.models import Case, IntegerField, Value, When

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

        programs = Program.objects.filter(features__key="attendance").distinct()

        return render(
            request,
            "attendance/all_attendance.html",
            {
                "sessions": sessions[:500],
                "programs": programs,
                "students": active_students().order_by("last_name", "first_name"),
                "selected_program_id": (
                    int(program_id) if program_id and program_id.isdigit() else None
                ),
                "current_sort": sort,
                "current_dir": direction,
            },
        )

    def post(self, request):
        action = request.POST.get("action")

        if action == "add":
            from django.utils.dateparse import parse_datetime

            person_type = request.POST.get("person_type")
            program_id = request.POST.get("program_id")
            ci_raw = request.POST.get("check_in")
            co_raw = request.POST.get("check_out")

            if not program_id or not ci_raw:
                messages.error(request, "Program and check-in time are required.")
                return redirect_back(request, "all_attendance")

            ci = parse_datetime(ci_raw)
            co = parse_datetime(co_raw) if co_raw else None

            if ci and timezone.is_naive(ci):
                ci = timezone.make_aware(ci, timezone.get_current_timezone())
            if co and timezone.is_naive(co):
                co = timezone.make_aware(co, timezone.get_current_timezone())

            new_session = AttendanceSession(
                program_id=int(program_id),
                check_in=ci,
                check_out=co,
            )

            if person_type == "student":
                student_id = request.POST.get("student_id")
                if student_id and student_id.isdigit():
                    new_session.student_id = int(student_id)
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
            from django.utils.dateparse import parse_datetime

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
            session.delete()
            messages.success(request, "Attendance entry deleted.")

        return redirect_back(request, "all_attendance")


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
    import json
    from datetime import date, datetime

    tz = timezone.get_current_timezone()
    now = timezone.now()

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
    import calendar as cal_mod

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
def program_hours_view(request, program_id):
    """Mentor attendance dashboard: bar chart of hours per student in a program."""
    program = get_object_or_404(Program, pk=program_id)

    if not can_user_read(request.user, "attendance"):
        messages.error(request, "You do not have permission to view attendance.")
        return redirect("home")

    if not program.has_feature("attendance"):
        messages.error(request, "Attendance is not enabled for this program.")
        return redirect("home")

    from django.db.models import Count, Max, Sum

    sessions = AttendanceSession.objects.filter(program=program).select_related(
        "student"
    )

    # Aggregate per student
    student_stats = (
        sessions.filter(student__isnull=False)
        .values("student__id", "student__first_name", "student__last_name")
        .annotate(
            total_minutes=Sum("duration_minutes"),
            session_count=Count("id"),
            last_attended=Max("check_in"),
        )
        .order_by("-total_minutes")
    )

    import json

    chart_labels = []
    chart_data = []
    student_list = []

    for stat in student_stats:
        name = f"{stat['student__first_name']} {stat['student__last_name']}"
        hours = round((stat["total_minutes"] or 0) / 60.0, 1)
        chart_labels.append(name)
        chart_data.append(hours)
        student_list.append(
            {
                "id": stat["student__id"],
                "name": name,
                "total_hours": hours,
                "session_count": stat["session_count"],
                "last_attended": stat["last_attended"],
            }
        )

    return render(
        request,
        "attendance/mentor_dashboard.html",
        {
            "program": program,
            "chart_labels_json": json.dumps(chart_labels),
            "chart_data_json": json.dumps(chart_data),
            "student_list": student_list,
        },
    )


class VisitorManagementView(LoginRequiredMixin, LeadMentorRequiredMixin, View):
    def get(self, request):
        from django.db.models import Count

        visitors = (
            AttendanceSession.objects.filter(student__isnull=True, adult__isnull=True)
            .exclude(visitor_name="")
            .values("visitor_name")
            .annotate(session_count=Count("id"))
            .order_by("visitor_name")
        )

        return render(
            request,
            "attendance/visitor_management.html",
            {
                "visitors": visitors,
            },
        )

    def post(self, request):
        action = request.POST.get("action")
        if action == "merge":
            selected_names = request.POST.getlist("selected_names")
            target_name = request.POST.get("target_name", "").strip()

            if not target_name:
                messages.error(request, "Target name is required for merge.")
            elif not selected_names:
                messages.error(request, "Select at least one visitor name to merge.")
            else:
                # Update all sessions
                AttendanceSession.objects.filter(
                    student__isnull=True,
                    adult__isnull=True,
                    visitor_name__in=selected_names,
                ).update(visitor_name=target_name)

                # Update all events
                AttendanceEvent.objects.filter(
                    student__isnull=True,
                    adult__isnull=True,
                    visitor_name__in=selected_names,
                ).update(visitor_name=target_name)

                messages.success(
                    request,
                    f"Merged {len(selected_names)} visitor names into '{target_name}'.",
                )

        return redirect("visitor_management")
