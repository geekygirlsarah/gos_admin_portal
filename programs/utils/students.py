"""Student queryset helpers for filtering active (non-graduated) students."""

from __future__ import annotations

from ..models import Student


def active_students():
    """Return a queryset of students who are still active (not graduated).

    A student is considered inactive once marked ``graduated=True``. This is
    the student-level equivalent of the old ``active`` flag and is used to
    keep inactive students out of dropdowns/selection lists.
    """
    return Student.objects.filter(graduated=False)


def active_students_in_program(program):
    """Return students with an active enrollment in ``program``.

    A student is considered active in a program when their ``Enrollment`` has
    ``active=True`` and the student record isn't marked graduated. Students who
    dropped out (enrollment marked inactive) or graduated are excluded. This
    mirrors how the program detail page splits active vs. inactive students.
    """
    return Student.objects.filter(
        enrollment__program=program,
        enrollment__active=True,
        graduated=False,
    )
