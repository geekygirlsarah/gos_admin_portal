"""Shared test helpers for the calendar_feeds app."""

from datetime import timedelta

from django.contrib.auth.models import Group, User
from django.utils import timezone

from calendar_feeds.models import CalendarEvent, CalendarFeed, CalendarFeedACL
from programs.models import Adult, Enrollment, Program, Student


def make_lead_mentor_user(username="lead", password="password123"):  # nosec B107
    user = User.objects.create_user(username=username, password=password)  # nosec B106
    group, _ = Group.objects.get_or_create(name="LeadMentor")
    user.groups.add(group)
    return user


def make_mentor_user(username="mentor", password="password123"):  # nosec B107
    user = User.objects.create_user(username=username, password=password)  # nosec B106
    Adult.objects.create(
        user=user,
        legal_first_name="Mentor",
        last_name="User",
        is_mentor=True,
        mentor_active=True,
    )
    return user


def make_parent_user(username="parent", password="password123"):  # nosec B107
    user = User.objects.create_user(username=username, password=password)  # nosec B106
    Adult.objects.create(
        user=user,
        legal_first_name="Parent",
        last_name="User",
        is_parent=True,
    )
    return user


def make_student_user(username="student", password="password123"):  # nosec B107
    user = User.objects.create_user(username=username, password=password)  # nosec B106
    student = Student.objects.create(
        user=user,
        legal_first_name="Test",
        last_name="Student",
        graduation_year=timezone.now().year + 1,
    )
    return user, student


def make_program(name="Test Program", *, features=None):
    program = Program.objects.create(name=name, active=True)
    if features:
        for key, display in features:
            program.features.get_or_create(key=key, defaults={"name": display})
    return program


def make_enrollment(student, program, active=True):
    return Enrollment.objects.create(
        student=student,
        program=program,
        active=active,
        start_date=timezone.localdate(),
        end_date=timezone.localdate() + timedelta(days=180),
    )


def make_feed(
    *,
    name="Test Feed",
    slug=None,
    visibility=CalendarFeed.VISIBILITY_MEMBERS,
    source_type=CalendarFeed.SOURCE_MANUAL,
    program=None,
    acls=None,
    **kwargs,
):
    """Create a CalendarFeed (and optional ACLs).

    ``acls`` is a dict mapping role -> (can_read, can_write), e.g.
    ``{"Mentor": (True, True), "Student": (True, False)}``.
    """
    feed = CalendarFeed.objects.create(
        name=name,
        slug=slug,
        visibility=visibility,
        source_type=source_type,
        program=program,
        **kwargs,
    )
    if acls:
        for role, (can_read, can_write) in acls.items():
            CalendarFeedACL.objects.create(
                feed=feed, role=role, can_read=can_read, can_write=can_write
            )
    return feed


def make_event(
    feed,
    *,
    title="Test Event",
    start_datetime=None,
    end_datetime=None,
    all_day=False,
    rrule_ical="",
    **kwargs,
):
    if start_datetime is None:
        start_datetime = timezone.make_aware(
            timezone.datetime.now() + timedelta(days=3),
            timezone.UTC,
        )
    if end_datetime is None:
        end_datetime = start_datetime + timedelta(hours=1)
    return CalendarEvent.objects.create(
        feed=feed,
        title=title,
        start_datetime=start_datetime,
        end_datetime=end_datetime,
        all_day=all_day,
        rrule_ical=rrule_ical,
        **kwargs,
    )
