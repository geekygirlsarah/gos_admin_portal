from datetime import timedelta
from typing import Optional, Tuple
from django.utils import timezone
from django.db import transaction

from .models import AttendanceEvent, AttendanceSession, RFIDCard


def resolve_student_by_uid(uid: str):
    try:
        card = RFIDCard.objects.select_related('student').get(uid=uid, is_active=True)
        return card.student
    except RFIDCard.DoesNotExist:
        return None


def auto_in_or_out(program, student=None, visitor_name: str = '', now=None) -> Tuple[str, Optional[AttendanceSession]]:
    """Determine whether the next event should be IN or OUT for the person and apply it.
    Returns (event_type, session).
    """
    now = now or timezone.now()
    # Find latest open session for today (or overall, policy choice)
    open_qs = AttendanceSession.objects.filter(program=program, check_out__isnull=True)
    if student:
        open_qs = open_qs.filter(student=student)
    else:
        open_qs = open_qs.filter(visitor_name=visitor_name)
    session = open_qs.order_by('-check_in').first()

    if session:
        # Close it
        session.check_out = now
        session.recompute_duration()
        session.save(update_fields=['check_out', 'duration_minutes', 'updated_at'])
        return AttendanceEvent.OUT, session
    else:
        # Open new one
        session = AttendanceSession.objects.create(
            program=program,
            student=student,
            visitor_name=visitor_name,
            check_in=now,
        )
        return AttendanceEvent.IN, session


@transaction.atomic
def record_tap(*, program, kiosk=None, rfid_uid: str = '', visitor_name: str = '', event_type: str = 'AUTO', occurred_at=None, source='kiosk', notes='') -> AttendanceEvent:
    """Create an AttendanceEvent and open/close a session as needed.
    If event_type == 'AUTO', we decide based on any open session.
    """
    # Enforce program feature toggle
    try:
        has_feature = getattr(program, 'has_feature')('attendance')
    except Exception:
        # If Program model lacks has_feature, default to allowed
        has_feature = True
    if not has_feature:
        from django.core.exceptions import PermissionDenied
        raise PermissionDenied('Attendance is not enabled for this program.')

    occurred_at = occurred_at or timezone.now()
    student = None
    if rfid_uid:
        student = resolve_student_by_uid(rfid_uid)

    # Create event first (audit trail)
    evt = AttendanceEvent.objects.create(
        program=program,
        student=student,
        visitor_name='' if student else (visitor_name or ''),
        rfid_uid=rfid_uid or '',
        kiosk=kiosk,
        event_type=event_type,
        occurred_at=occurred_at,
        source=source,
        notes=notes,
    )

    # Apply to session layer
    if event_type == AttendanceEvent.AUTO:
        decided, session = auto_in_or_out(program, student=student, visitor_name=evt.visitor_name, now=occurred_at)
        evt.event_type = decided
        evt.save(update_fields=['event_type'])
        if decided == AttendanceEvent.IN:
            session.opened_by_event = evt
            session.save(update_fields=['opened_by_event'])
        else:
            session.closed_by_event = evt
            session.save(update_fields=['closed_by_event'])
    elif event_type == AttendanceEvent.IN:
        # Open a new session, closing any dangling open one first by policy
        decided, session = auto_in_or_out(program, student=student, visitor_name=evt.visitor_name, now=occurred_at)
        if decided == AttendanceEvent.OUT:
            # If an open session existed, we closed it; now open a new one too
            decided, session = auto_in_or_out(program, student=student, visitor_name=evt.visitor_name, now=occurred_at)
        session.opened_by_event = evt
        session.save(update_fields=['opened_by_event'])
    else:  # OUT
        decided, session = auto_in_or_out(program, student=student, visitor_name=evt.visitor_name, now=occurred_at)
        # If we ended up opening a session (no prior open), immediately close it (zero duration)
        if decided == AttendanceEvent.IN:
            decided, session = auto_in_or_out(program, student=student, visitor_name=evt.visitor_name, now=occurred_at)
        session.closed_by_event = evt
        session.save(update_fields=['closed_by_event'])

    return evt
