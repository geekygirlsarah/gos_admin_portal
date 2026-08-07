"""Grade and graduation-year calculations."""

from __future__ import annotations

from datetime import date

from .imports import get_academic_year_ending


def calculate_grade(graduation_year: int, ref_date: date = None) -> int | None:
    """Return the student's current grade as an integer (0=K, 1-12) based on
    graduation year and reference date.
    - July 1 rollover (via get_academic_year_ending).
    - Returns None if the student has already graduated (calculated grade > 12).
    - Grades below 0 are clamped to 0 (Kindergarten).
    """
    if not graduation_year:
        return None
    academic_year_ending = get_academic_year_ending(ref_date)
    grade = 12 - (graduation_year - academic_year_ending)
    if grade > 12:
        return None
    return max(0, grade)


def calculate_graduation_year(grade: int | str, ref_date: date = None) -> int:
    """Return the expected graduation year based on current grade and reference date.
    - July 1 rollover (via get_academic_year_ending).
    """
    academic_year_ending = get_academic_year_ending(ref_date)
    return academic_year_ending + (12 - int(grade))


def format_grade(grade: int | str | None) -> str:
    """Return a human-readable grade label: 'K', '1st Grade', ..., '12th Grade',
    'Graduated', or '—' if grade is None.
    """
    if grade is None or grade == "":
        return "—"

    try:
        n = int(grade)
    except (ValueError, TypeError):
        return str(grade)

    if n > 12:
        return "Graduated"
    if n == 0:
        return "K"

    if 10 <= (n % 100) <= 13:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix} Grade"
