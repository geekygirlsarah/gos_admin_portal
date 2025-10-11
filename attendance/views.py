from datetime import timedelta
from django.contrib.auth.decorators import login_required, permission_required
from django.shortcuts import render, get_object_or_404, redirect
from django.utils import timezone
from django.views.decorators.http import require_http_methods
from django.contrib import messages
from django.views import View

from programs.models import Student, Program, Enrollment
from .models import AttendanceSession, AttendanceEvent, RFIDCard


def _week_bounds(now=None):
    now = now or timezone.now()
    start = (now - timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=7)
    return start, end


@login_required
@require_http_methods(["GET", "POST"]) 
def student_attendance_view(request, pk):
    student = get_object_or_404(Student, pk=pk)
    # Optional program filter
    program_id = request.GET.get('program_id') or request.POST.get('program_id')
    program = Program.objects.filter(id=program_id).first() if program_id else None

    # Handle create/update/delete
    if request.method == 'POST':
        action = request.POST.get('action')
        if not request.user.has_perm('programs.change_student'):
            return render(request, 'students/attendance.html', {
                'student': student,
                'error': 'You do not have permission to modify attendance.',
            }, status=403)
        if action == 'create':
            check_in = request.POST.get('check_in')
            check_out = request.POST.get('check_out') or None
            prog_id = int(request.POST.get('program_id')) if request.POST.get('program_id') else None
            if not prog_id:
                return render(request, 'students/attendance.html', {'student': student, 'error': 'Program is required to create a session.'}, status=400)
            prog = get_object_or_404(Program, id=prog_id)
            if not prog.has_feature('attendance'):
                return render(request, 'students/attendance.html', {'student': student, 'error': 'Attendance is not enabled for the selected program.'}, status=400)
            session = AttendanceSession(program=prog, student=student, check_in=check_in, check_out=check_out)
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
            return redirect('student_attendance', pk=student.pk)
        elif action == 'update':
            session_id = request.POST.get('session_id')
            session = get_object_or_404(AttendanceSession, id=session_id, student=student)
            from django.utils.dateparse import parse_datetime
            ci = parse_datetime(request.POST.get('check_in'))
            co_raw = request.POST.get('check_out')
            co = parse_datetime(co_raw) if co_raw else None
            if ci and timezone.is_naive(ci):
                ci = timezone.make_aware(ci, timezone.get_current_timezone())
            if co and timezone.is_naive(co):
                co = timezone.make_aware(co, timezone.get_current_timezone())
            session.check_in = ci or session.check_in
            session.check_out = co
            session.recompute_duration()
            session.save()
            return redirect('student_attendance', pk=student.pk)
        elif action == 'delete':
            session_id = request.POST.get('session_id')
            session = get_object_or_404(AttendanceSession, id=session_id, student=student)
            session.delete()
            return redirect('student_attendance', pk=student.pk)

    # GET rendering
    sessions = AttendanceSession.objects.filter(student=student).select_related('program').order_by('-check_in')

    week_start, week_end = _week_bounds()
    from django.db.models import Q
    week_sessions = sessions.filter(check_in__lt=week_end).filter(Q(check_out__isnull=True) | Q(check_out__gt=week_start))
    total_hours = 0.0
    for s in week_sessions:
        ci = s.check_in if s.check_in > week_start else week_start
        co = s.check_out or timezone.now()
        if co > week_end:
            co = week_end
        if co > ci:
            total_hours += (co - ci).total_seconds() / 3600.0
    weekly_avg_hours = round(total_hours / 7.0, 2)

    # Programs the student is/was enrolled in (attendance-enabled only for creation UI)
    enrolled_programs = Program.objects.filter(enrollment__student=student, features__key='attendance').distinct()

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
            earliest_session = sessions.order_by('check_in').first()
            if earliest_session:
                overall_start_date = earliest_session.check_in.date()

    overall_total_hours = 0.0
    overall_avg_hours_per_week = 0.0
    overall_start_display = None
    if overall_start_date:
        from datetime import datetime
        tz = timezone.get_current_timezone()
        start_dt = timezone.make_aware(datetime.combine(overall_start_date, datetime.min.time()), tz)
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
        overall_avg_hours_per_week = overall_total_hours / weeks_elapsed if weeks_elapsed > 0 else overall_total_hours
        overall_start_display = overall_start_date

    return render(request, 'students/attendance.html', {
        'student': student,
        'sessions': sessions[:200],
        'week_start': week_start,
        'week_end': week_end - timedelta(seconds=1),
        'weekly_hours': round(total_hours, 2),
        'weekly_avg_hours': weekly_avg_hours,
        'enrolled_programs': enrolled_programs,
        'selected_program': program,
        'overall_start_date': overall_start_display,
        'overall_total_hours': round(overall_total_hours, 2),
        'overall_avg_hours_per_week': round(overall_avg_hours_per_week, 2),
    })

class AttendanceImportView(View):
    def post(self, request):
        if not request.user.has_perm('programs.change_student'):
            messages.error(request, 'You do not have permission to import attendance.')
            return redirect('import_dashboard')
        file = request.FILES.get('file')
        program_id = request.POST.get('program_id')
        if not program_id:
            messages.error(request, 'Please select a program for this import.')
            return redirect('import_dashboard')
        program = Program.objects.filter(id=program_id).first()
        if not program:
            messages.error(request, 'Selected program was not found.')
            return redirect('import_dashboard')
        if not program.has_feature('attendance'):
            messages.error(request, 'Attendance is not enabled for the selected program.')
            return redirect('import_dashboard')
        if not file:
            messages.error(request, 'No file uploaded.')
            return redirect('import_dashboard')

        name = file.name.lower()
        if not name.endswith('.csv'):
            messages.error(request, 'Unsupported file type. Please upload a CSV file.')
            return redirect('import_dashboard')

        import csv, io
        created = 0
        updated = 0
        errors = 0
        skipped = 0
        text = io.TextIOWrapper(file.file, encoding='utf-8')
        reader = csv.DictReader(text)

        from django.utils.dateparse import parse_datetime
        from django.utils.timezone import utc, make_aware, is_naive

        def parse_utc(dt_val):
            if not dt_val:
                return None
            if hasattr(dt_val, 'tzinfo'):
                # Already a datetime
                dt = dt_val
            else:
                dt = parse_datetime(str(dt_val).strip())
            if not dt:
                return None
            if is_naive(dt):
                # Treat naive as UTC per spec
                return make_aware(dt, timezone=utc)
            # Ensure in UTC
            return dt.astimezone(utc)

        def find_student(first_name, last_name, rfid):
            # Priority: RFID match
            if rfid:
                card = RFIDCard.objects.filter(uid__iexact=str(rfid).strip(), is_active=True).select_related('student').first()
                if card:
                    return card.student
            # Next: name match (case-insensitive)
            fn = (first_name or '').strip()
            ln = (last_name or '').strip()
            if fn and ln:
                student = Student.objects.filter(first_name__iexact=fn, last_name__iexact=ln).first()
                if student:
                    return student
            return None

        try:
            for row in reader:
                first = (row.get('first_name') or row.get('First Name') or '').strip()
                last = (row.get('last_name') or row.get('Last Name') or '').strip()
                rfid = (row.get('rfid') or row.get('rfid_uid') or row.get('RFID') or row.get('RFID UID') or '').strip()
                t_in_raw = row.get('time_in') or row.get('time_in_utc') or row.get('Time In (UTC)') or row.get('time in (utc)') or row.get('time_in (utc)') or row.get('Time In')
                t_out_raw = row.get('time_out') or row.get('time_out_utc') or row.get('Time Out (UTC)') or row.get('time out (utc)') or row.get('time_out (utc)') or row.get('Time Out')

                check_in = parse_utc(t_in_raw)
                check_out = parse_utc(t_out_raw)
                if not check_in:
                    errors += 1
                    continue

                student = find_student(first, last, rfid)
                visitor_name = ''
                if not student:
                    # If we cannot find a student, record as visitor session with provided name or RFID
                    if first or last:
                        visitor_name = (first + ' ' + last).strip()
                    elif rfid:
                        visitor_name = f"RFID {rfid}"
                    else:
                        visitor_name = 'Unknown'

                # Idempotency: try to find existing session with same keys
                if student:
                    existing = AttendanceSession.objects.filter(program=program, student=student, check_in=check_in).first()
                else:
                    existing = AttendanceSession.objects.filter(program=program, student__isnull=True, visitor_name=visitor_name, check_in=check_in).first()

                if existing:
                    # Update checkout if new info is provided
                    if check_out and (not existing.check_out or existing.check_out != check_out):
                        existing.check_out = check_out
                        existing.recompute_duration()
                        existing.save(update_fields=['check_out', 'duration_minutes', 'updated_at'])
                        updated += 1
                    else:
                        skipped += 1
                    continue

                # Create linked events (optional)
                open_event = AttendanceEvent.objects.create(
                    program=program,
                    student=student,
                    visitor_name=visitor_name if not student else '',
                    rfid_uid=rfid or '',
                    kiosk=None,
                    event_type=AttendanceEvent.IN,
                    occurred_at=check_in,
                    source='import',
                    notes='Imported from CSV'
                )
                close_event = None
                if check_out:
                    close_event = AttendanceEvent.objects.create(
                        program=program,
                        student=student,
                        visitor_name=visitor_name if not student else '',
                        rfid_uid=rfid or '',
                        kiosk=None,
                        event_type=AttendanceEvent.OUT,
                        occurred_at=check_out,
                        source='import',
                        notes='Imported from CSV'
                    )

                session = AttendanceSession(
                    program=program,
                    student=student,
                    visitor_name=visitor_name if not student else '',
                    check_in=check_in,
                    check_out=check_out,
                    opened_by_event=open_event,
                    closed_by_event=close_event
                )
                session.recompute_duration()
                session.save()
                created += 1

            if errors:
                messages.warning(request, f"Attendance import completed: {created} created, {updated} updated, {skipped} skipped, {errors} rows had errors.")
            else:
                messages.success(request, f"Attendance import completed: {created} created, {updated} updated, {skipped} skipped.")
        except Exception as e:
            messages.error(request, f"Failed to import attendance: {e}")

        return redirect('import_dashboard')
