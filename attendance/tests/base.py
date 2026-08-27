from datetime import timedelta

from django.contrib.auth.models import Group, User
from django.test import Client
from django.utils import timezone

from programs.models import (
    Adult,
    Enrollment,
    Program,
    ProgramFeature,
    RolePermission,
    Student,
)


def make_program(name="Test Program", active=True, start_date=None, end_date=None):
    if start_date is None:
        start_date = timezone.now().date()
    if end_date is None:
        end_date = start_date + timedelta(days=365)
    program = Program.objects.create(
        name=name, active=active, start_date=start_date, end_date=end_date
    )
    feat, _ = ProgramFeature.objects.get_or_create(
        key="attendance", defaults={"name": "Attendance"}
    )
    program.features.add(feat)
    return program


def make_lead_mentor_user(username="lead_mentor", password="password123"):  # nosec B107
    user = User.objects.create_user(username=username, password=password)
    group, _ = Group.objects.get_or_create(name="LeadMentor")
    user.groups.add(group)
    return user


def make_mentor_user(username="mentor", password="password123"):  # nosec B107
    user = User.objects.create_user(username=username, password=password)
    Adult.objects.create(
        user=user, legal_first_name="Mentor", last_name="User", is_mentor=True
    )
    RolePermission.objects.update_or_create(
        role="Mentor",
        section="attendance",
        defaults={"can_read": True, "can_write": True},
    )
    return user


def make_parent_user(username="parent", password="password123"):  # nosec B107
    user = User.objects.create_user(username=username, password=password)
    Adult.objects.create(
        user=user, legal_first_name="Parent", last_name="User", is_parent=True
    )
    return user


def make_student(preferred_first_name="Test", last_name="Student", **kwargs):
    return Student.objects.create(
        preferred_first_name=preferred_first_name, last_name=last_name, **kwargs
    )


def make_adult(legal_first_name="Adult", last_name="User", **kwargs):
    return Adult.objects.create(
        legal_first_name=legal_first_name, last_name=last_name, **kwargs
    )


def make_client():
    return Client()
