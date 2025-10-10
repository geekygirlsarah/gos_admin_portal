from datetime import timedelta
from django.contrib.auth.decorators import login_required, permission_required
from django.shortcuts import render, get_object_or_404, redirect
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from programs.models import Student, Program, Enrollment
from .models import AttendanceSession


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
