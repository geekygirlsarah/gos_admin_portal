import datetime
from typing import Optional, Tuple

from django.db import IntegrityError, transaction
from django.db.models import Sum
from django.utils import timezone

from .models import AttendanceEvent, AttendanceSession, RFIDCard


def resolve_card_by_uid(uid: str) -> Optional[RFIDCard]:
    try:
        return RFIDCard.objects.select_related("student", "adult").get(
            uid=uid, is_active=True
        )
    except RFIDCard.DoesNotExist:
        # Fallback: Check if we have a card stored without leading zeros
        stripped = uid.lstrip("0")
        if stripped and stripped != uid:
            try:
                card = RFIDCard.objects.select_related("student", "adult").get(
                    uid=stripped, is_active=True
                )
                # Found it! Update to the full UID so it matches exactly next time.
                try:
                    with transaction.atomic():
                        card.uid = uid
                        card.save(update_fields=["uid"])
                except IntegrityError:
                    # If another card already has this UID (e.g. an inactive one),
                    # we can't update, but we can still return the match we found.
                    pass
                return card
            except RFIDCard.DoesNotExist:
                pass
        return None


def find_card_by_uid(uid: str) -> Optional[RFIDCard]:
    """Find a card by exact UID or stripped UID (leading zeros), regardless of active status."""
    # Try exact match first
    card = RFIDCard.objects.select_related("student", "adult").filter(uid=uid).first()
    if card:
        return card

    # Fallback: Check if we have a card stored without leading zeros
    stripped = uid.lstrip("0")
    if stripped and stripped != uid:
        card = (
            RFIDCard.objects.select_related("student", "adult")
            .filter(uid=stripped)
            .first()
        )
        if card:
            return card

    return None


def resolve_person_by_uid(uid: str):
    card = resolve_card_by_uid(uid)
    if not card:
        return None
    person = card.student or card.adult
    if person:
        # Check active status
        if hasattr(person, "active") and not person.active:
            return None
        if hasattr(person, "graduated") and person.graduated:
            return None
    return person


def resolve_student_by_uid(uid: str):
    """Legacy alias for resolve_person_by_uid."""
    person = resolve_person_by_uid(uid)
    from programs.models import Student

    return person if isinstance(person, Student) else None


def auto_in_or_out(
    program,
    student=None,
    adult=None,
    visitor_name: str = "",
    visitor_team_number=None,
    now=None,
) -> Tuple[str, Optional[AttendanceSession]]:
    """Determine whether the next event should be IN or OUT for the person and apply it.
    Returns (event_type, session).
    """
    now = now or timezone.now()
    local_now = timezone.localtime(now)
    today_start = local_now.replace(hour=0, minute=0, second=0, microsecond=0)

    # NEW: Close any stale sessions for this person first to keep the list clean
    # and provide realistic durations if they forgot to sign out.
    stale_qs = AttendanceSession.objects.filter(
        program=program, check_out__isnull=True, check_in__lt=today_start
    )
    if student:
        stale_qs = stale_qs.filter(student=student)
    elif adult:
        stale_qs = stale_qs.filter(adult=adult)
    else:
        stale_qs = stale_qs.filter(visitor_name=visitor_name)

    for stale in stale_qs:
        stale.check_out = stale.check_in + datetime.timedelta(hours=1)
        stale.recompute_duration()
        stale.save(update_fields=["check_out", "duration_minutes", "updated_at"])

    # Find latest open session for today (local time)
    open_qs = AttendanceSession.objects.filter(
        program=program, check_out__isnull=True, check_in__gte=today_start
    )
    if student:
        open_qs = open_qs.filter(student=student)
    elif adult:
        open_qs = open_qs.filter(adult=adult)
    else:
        open_qs = open_qs.filter(visitor_name=visitor_name)
    session = open_qs.order_by("-check_in").first()

    if session:
        # Close it
        session.check_out = now
        session.recompute_duration()
        session.save(update_fields=["check_out", "duration_minutes", "updated_at"])
        return AttendanceEvent.OUT, session
    else:
        # Open new one
        session = AttendanceSession.objects.create(
            program=program,
            student=student,
            adult=adult,
            visitor_name=visitor_name,
            visitor_team_number=visitor_team_number,
            check_in=now,
        )
        return AttendanceEvent.IN, session


def get_attendance_stats(program, student=None, adult=None, visitor_name=None):
    """Return a dict with total_hours and week_hours for a person in a program."""
    now = timezone.now()
    # Week starts on Monday
    start_of_week = (now - datetime.timedelta(days=now.weekday())).replace(
        hour=0, minute=0, second=0, microsecond=0
    )

    qs = AttendanceSession.objects.filter(program=program)
    if student:
        qs = qs.filter(student=student)
    elif adult:
        qs = qs.filter(adult=adult)
    elif visitor_name:
        qs = qs.filter(visitor_name=visitor_name)
    else:
        return {"total_hours": 0, "week_hours": 0}

    total_mins = qs.aggregate(total=Sum("duration_minutes"))["total"] or 0
    week_mins = (
        qs.filter(check_in__gte=start_of_week).aggregate(total=Sum("duration_minutes"))[
            "total"
        ]
        or 0
    )

    return {
        "total_hours": round(total_mins / 60.0, 1),
        "week_hours": round(week_mins / 60.0, 1),
    }


def get_student_attendance_stats(student, program):
    """Return a dict with total_hours and week_hours for a student in a program."""
    return get_attendance_stats(program, student=student)


@transaction.atomic
def record_tap(
    *,
    program,
    kiosk=None,
    rfid_uid: str = "",
    student=None,
    adult=None,
    visitor_name: str = "",
    visitor_team_number=None,
    event_type: str = "AUTO",
    occurred_at=None,
    source="kiosk",
    notes=""
) -> AttendanceEvent:
    """Create an AttendanceEvent and open/close a session as needed.
    If event_type == 'AUTO', we decide based on any open session.
    """
    # Enforce program feature toggle
    try:
        has_feature = getattr(program, "has_feature")("attendance")
    except Exception:
        # If Program model lacks has_feature, default to allowed
        has_feature = True
    if not has_feature:
        from django.core.exceptions import PermissionDenied

        raise PermissionDenied("Attendance is not enabled for this program.")

    occurred_at = occurred_at or timezone.now()
    if not (student or adult) and rfid_uid:
        person = resolve_person_by_uid(rfid_uid)
        from programs.models import Adult, Student

        student = person if isinstance(person, Student) else None
        adult = person if isinstance(person, Adult) else None

    # Create event first (audit trail)
    evt = AttendanceEvent.objects.create(
        program=program,
        student=student,
        adult=adult,
        visitor_name="" if (student or adult) else (visitor_name or ""),
        visitor_team_number=None if (student or adult) else visitor_team_number,
        rfid_uid=rfid_uid or "",
        kiosk=kiosk,
        event_type=event_type,
        occurred_at=occurred_at,
        source=source,
        notes=notes,
    )

    team_num = evt.visitor_team_number

    # Apply to session layer
    if event_type == AttendanceEvent.AUTO:
        decided, session = auto_in_or_out(
            program,
            student=student,
            adult=adult,
            visitor_name=evt.visitor_name,
            visitor_team_number=team_num,
            now=occurred_at,
        )
        evt.event_type = decided
        evt.save(update_fields=["event_type"])
        if decided == AttendanceEvent.IN:
            session.opened_by_event = evt
            session.save(update_fields=["opened_by_event"])
        else:
            session.closed_by_event = evt
            session.save(update_fields=["closed_by_event"])
    elif event_type == AttendanceEvent.IN:
        # Open a new session, closing any dangling open one first by policy
        decided, session = auto_in_or_out(
            program,
            student=student,
            adult=adult,
            visitor_name=evt.visitor_name,
            visitor_team_number=team_num,
            now=occurred_at,
        )
        if decided == AttendanceEvent.OUT:
            # If an open session existed, we closed it; now open a new one too
            decided, session = auto_in_or_out(
                program,
                student=student,
                adult=adult,
                visitor_name=evt.visitor_name,
                visitor_team_number=team_num,
                now=occurred_at,
            )
        session.opened_by_event = evt
        session.save(update_fields=["opened_by_event"])
    else:  # OUT
        decided, session = auto_in_or_out(
            program,
            student=student,
            adult=adult,
            visitor_name=evt.visitor_name,
            visitor_team_number=team_num,
            now=occurred_at,
        )
        # If we ended up opening a session (no prior open), immediately close it (zero duration)
        if decided == AttendanceEvent.IN:
            decided, session = auto_in_or_out(
                program,
                student=student,
                adult=adult,
                visitor_name=evt.visitor_name,
                visitor_team_number=team_num,
                now=occurred_at,
            )
        session.closed_by_event = evt
        session.save(update_fields=["closed_by_event"])

    return evt
