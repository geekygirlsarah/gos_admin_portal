import json
from datetime import datetime, timedelta
from django.http import JsonResponse, HttpResponseBadRequest
from django.views.decorators.csrf import csrf_exempt
from django.utils.dateparse import parse_datetime
from django.utils import timezone
from django.shortcuts import get_object_or_404
from django.db.models import Q

from api.auth import require_api_key
from attendance.models import AttendanceEvent, AttendanceSession
from attendance.services import record_tap
from programs.models import Program, Student


def _hours_from_sessions(sessions, start, end):
    total = 0.0
    for s in sessions:
        ci = s.check_in if s.check_in > start else start
        co = s.check_out or timezone.now()
        if co > end:
            co = end
        if co > ci:
            total += (co - ci).total_seconds() / 3600.0
    return round(total, 2)


@csrf_exempt
@require_api_key(scope_required='write')
def attendance_tap(request):
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)
    try:
        payload = json.loads(request.body.decode('utf-8')) if request.body else {}
    except json.JSONDecodeError:
        return HttpResponseBadRequest('Invalid JSON')

    program_id = payload.get('program_id')
    rfid_uid = payload.get('rfid_uid', '')
    visitor_name = payload.get('visitor_name', '')
    event_type = payload.get('event_type', 'AUTO')
    occurred_at = payload.get('occurred_at')

    program = get_object_or_404(Program, id=program_id)
    if not program.has_feature('attendance'):
        return JsonResponse({'error': 'Attendance is not enabled for this program'}, status=400)
    dt = parse_datetime(occurred_at) if occurred_at else timezone.now()
    if dt and timezone.is_naive(dt):
        dt = timezone.make_aware(dt, timezone.get_current_timezone())

    evt = record_tap(program=program, rfid_uid=rfid_uid, visitor_name=visitor_name, event_type=event_type, occurred_at=dt, source='api')

    return JsonResponse({
        'id': evt.id,
        'event_type': evt.event_type,
        'program_id': evt.program_id,
        'student_id': evt.student_id,
        'visitor_name': evt.visitor_name,
        'occurred_at': evt.occurred_at.isoformat(),
    }, status=201)


@require_api_key(scope_required='read')
def student_weekly_hours(request, student_id):
    student = get_object_or_404(Student, id=student_id)
    program_id = request.GET.get('program_id')
    program = get_object_or_404(Program, id=program_id)

    # week bounds: Monday 00:00 to next Monday 00:00
    now = timezone.now()
    start = (now - timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=7)

    sessions = AttendanceSession.objects.filter(program=program, student=student, check_in__lt=end).filter(Q(check_out__isnull=True) | Q(check_out__gt=start))
    hours = _hours_from_sessions(sessions, start, end)

    return JsonResponse({
        'student_id': student.id,
        'program_id': program.id,
        'week_start': start.date().isoformat(),
        'week_end': (end - timedelta(seconds=1)).date().isoformat(),
        'hours': hours,
    })


@require_api_key(scope_required='read')
def program_weekly_hours(request, program_id):
    program = get_object_or_404(Program, id=program_id)
    now = timezone.now()
    start = (now - timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=7)

    sessions = AttendanceSession.objects.filter(program=program, check_in__lt=end, student__isnull=False).filter(Q(check_out__isnull=True) | Q(check_out__gt=start)).select_related('student')

    by_student = {}
    for s in sessions:
        by_student.setdefault(s.student_id, []).append(s)

    data = []
    for sid, rows in by_student.items():
        hours = _hours_from_sessions(rows, start, end)
        data.append({'student_id': sid, 'hours': hours})

    return JsonResponse({
        'program_id': program.id,
        'week_start': start.date().isoformat(),
        'week_end': (end - timedelta(seconds=1)).date().isoformat(),
        'students': sorted(data, key=lambda x: x['hours'], reverse=True),
    })
