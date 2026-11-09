"""Shared attendance aggregations used by charts and mail merge."""

from django.db.models import Count, Max, Sum


def get_attendance_stats_by_student(sessions, weeks_elapsed):
    """Return chart-compatible attendance totals for each student in ``sessions``."""

    aggregates = (
        sessions.values(
            "student__id",
            "student__preferred_first_name",
            "student__legal_first_name",
            "student__last_name",
        )
        .annotate(
            total_minutes=Sum("duration_minutes"),
            session_count=Count("id"),
            days_attended=Count("check_in__date", distinct=True),
            last_attended=Max("check_in"),
        )
        .order_by("-total_minutes")
    )

    student_weeks = max(weeks_elapsed, 1)
    results = []
    for stat in aggregates:
        display_first = (
            stat["student__preferred_first_name"] or stat["student__legal_first_name"]
        )
        hours = round((stat["total_minutes"] or 0) / 60.0, 1)
        results.append(
            {
                "id": stat["student__id"],
                "name": f"{display_first} {stat['student__last_name']}",
                "total_hours": hours,
                "avg_per_week": round(hours / student_weeks, 1),
                "session_count": stat["session_count"],
                "days": stat["days_attended"],
                "last_attended": stat["last_attended"],
            }
        )
    return results
