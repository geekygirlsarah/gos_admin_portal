"""Utility functions for programs: images, alumni, imports, grades, URLs,
balances, notifications, and student querysets.

This package re-exports all public names so existing ``from programs.utils
import X`` imports continue to work after the single-file ``utils.py`` was
split into submodules.
"""

from __future__ import annotations

from .adults import active_adults, active_alumni, active_mentors, active_parents
from .alumni import convert_student_to_alumni, find_matching_alumni_adult
from .balances import (
    compute_sliding_discount_rounded,
    get_active_sliding_scale,
    get_student_balance_data,
    get_student_program_balance,
    program_overlaps_sliding_window,
)
from .colors import get_contrast_color
from .geocoding import normalize_address, resolve_address_points
from .grades import calculate_grade, calculate_graduation_year, format_grade
from .images import normalize_image_field
from .imports import (
    get_academic_year_ending,
    row_raw,
    row_val,
    row_val_bool,
    row_val_date,
)
from .notifications import (
    LEAD_MENTOR_EMAIL,
    generate_otp,
    get_lead_mentor_notification_email,
    send_otp_email,
    send_templated_notification,
)
from .students import active_students, active_students_in_program
from .urls import (
    generate_signed_parent_url,
    get_safe_url,
    redirect_back,
    verify_signed_parent_token,
)

__all__ = [
    # images
    "normalize_image_field",
    # alumni
    "convert_student_to_alumni",
    "find_matching_alumni_adult",
    # adults
    "active_adults",
    "active_mentors",
    "active_parents",
    "active_alumni",
    # imports (csv/xlsx helpers + academic year)
    "get_academic_year_ending",
    "row_raw",
    "row_val",
    "row_val_bool",
    "row_val_date",
    # grades
    "calculate_grade",
    "calculate_graduation_year",
    "format_grade",
    # geocoding
    "normalize_address",
    "resolve_address_points",
    # colors
    "get_contrast_color",
    # urls
    "generate_signed_parent_url",
    "get_safe_url",
    "redirect_back",
    "verify_signed_parent_token",
    # balances
    "compute_sliding_discount_rounded",
    "get_active_sliding_scale",
    "get_student_balance_data",
    "get_student_program_balance",
    "program_overlaps_sliding_window",
    # notifications
    "LEAD_MENTOR_EMAIL",
    "generate_otp",
    "get_lead_mentor_notification_email",
    "send_otp_email",
    "send_templated_notification",
    # students
    "active_students",
    "active_students_in_program",
]
