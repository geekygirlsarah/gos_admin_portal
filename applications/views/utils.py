"""Shared helpers used across the application wizard views."""

from __future__ import annotations

import logging

from django.shortcuts import get_object_or_404, redirect

from programs.utils import calculate_grade

from ..models import Application
from ..services import (
    find_existing_mentor_by_email,
    find_student_by_email,
    send_otp_email,
    student_to_prefill,
)

logger = logging.getLogger(__name__)

TOTAL_STEPS = 10  # Student/parent wizard step count.
MENTOR_TOTAL_STEPS = 6  # Mentor wizard step count.


def _is_mentor(application: Application) -> bool:
    return application.applicant_type == Application.Type.MENTOR


def _mentor_progress(view_key: str) -> tuple[int, int]:
    """Return (current_step, total_steps) for a mentor wizard page."""
    mapping = {
        "step2": 1,
        "step4": 2,
        "mentor_info": 3,
        "mentor_clearance_interest": 4,
        "mentor_clearance_detail": 5,
        "mentor_confirm": 6,
        "submitted": 6,
    }
    return mapping.get(view_key, 1), MENTOR_TOTAL_STEPS


def _get_application_or_404(app_id: str) -> Application:
    return get_object_or_404(Application, application_id=(app_id or "").upper())


def _issue_and_send(application: Application, request) -> bool:
    """Generate, store and email a fresh OTP. Returns whether it succeeded."""
    try:
        code = application.issue_otp()
        send_otp_email(application, code, request=request)
        return True
    except Exception:
        logger.exception(
            "Failed to send OTP for application %s", application.application_id
        )
        return False


def _redirect_to_current_step(application: Application):
    """After resume, send the user to the step they were last on."""
    # Mentor branch: no Step 3 program, no Steps 5/6/7. Map differently.
    if _is_mentor(application):
        step = application.current_step or 1
        if application.status in (
            Application.Status.SUBMITTED,
            Application.Status.APPROVED,
            Application.Status.APPROVED_SIGNED,
            Application.Status.CONVERTED,
        ):
            return redirect("apply_submitted", app_id=application.application_id)
        if step <= 2:
            return redirect("apply_step2", app_id=application.application_id)
        if not application.email_is_verified:
            return redirect("apply_step3", app_id=application.application_id)
        data = application.data or {}
        if not data.get("mentor_info"):
            return redirect("apply_mentor_info", app_id=application.application_id)
        if not data.get("mentor_clearance_interest"):
            return redirect(
                "apply_mentor_clearance_interest",
                app_id=application.application_id,
            )
        if data.get("mentor_clearance_interest", {}).get(
            "interested"
        ) == "yes" and not data.get("mentor_clearance_detail"):
            return redirect(
                "apply_mentor_clearance_detail",
                app_id=application.application_id,
            )
        return redirect("apply_mentor_confirm", app_id=application.application_id)

    step = max(1, min(application.current_step, TOTAL_STEPS))
    name_map = {
        2: "apply_step2",
        3: "apply_step3",
        4: "apply_step4",
        5: "apply_step5",
        6: "apply_step6",
        7: "apply_step7",
        8: "apply_step8",
        9: "apply_step9",
    }
    if step in name_map:
        return redirect(name_map[step], app_id=application.application_id)
    if step >= 10:
        # Approved applicants jump straight to the signed-documents page.
        # Everyone else lands on the post-submit confirmation page.
        if application.status in (
            Application.Status.APPROVED,
            Application.Status.APPROVED_SIGNED,
        ):
            return redirect("apply_step10", app_id=application.application_id)
        return redirect("apply_submitted", app_id=application.application_id)
    return redirect("apply_start")


def _redirect_after_email_verified(application: Application):
    """Determine where to send the user immediately after email verification."""
    if _is_mentor(application):
        if find_existing_mentor_by_email(application.email):
            return redirect("apply_mentor_blocked", app_id=application.application_id)
        return redirect("apply_mentor_info", app_id=application.application_id)
    return redirect("apply_step4", app_id=application.application_id)


def _require_verified_email(application: Application):
    if not application.email_is_verified:
        return redirect("apply_step3", app_id=application.application_id)
    return None


def _is_handoff_authorized(request, application: Application) -> bool:
    """Check if the current session is authorized to access handed-off steps.

    Required when a student-initiated application has been handed off to a
    parent (status=AWAITING_PARENT).
    """
    if application.status != Application.Status.AWAITING_PARENT:
        return True
    if application.applicant_type != Application.Type.STUDENT:
        return True

    expected = application.handoff_token
    if not expected:
        # If for some reason there is no token on file but it's awaiting
        # parent, we allow it (best effort for legacy/corrupt records).
        return True

    session_token = request.session.get(f"handoff_token_{application.application_id}")
    return bool(session_token and session_token == expected)


def _auto_authorize_handoff(request, application: Application) -> None:
    """Let a logged-in parent resume an AWAITING_PARENT application.

    When the signed-in adult's email matches the handoff recipient (or a
    parent already listed on the application), grant the same session token
    that an emailed resume link would provide so the dashboard's "Resume
    application" button works without re-emailing.
    """
    if application.status != Application.Status.AWAITING_PARENT:
        return
    token = application.handoff_token
    if not token:
        return
    user = getattr(request, "user", None)
    if not user or not user.is_authenticated:
        return
    adult = getattr(user, "adult_profile", None)
    if adult is None:
        return
    adult_emails = {
        email.strip().lower()
        for email in (adult.personal_email, adult.andrew_email)
        if email
    }
    if not adult_emails:
        return
    data = application.data or {}
    parent_emails = {
        (data.get("step7_handoff") or {}).get("parent_email"),
        (data.get("step7-primaryparent") or {}).get("email"),
        (data.get("step8-secondaryparent") or {}).get("email"),
    }
    parent_emails = {e.strip().lower() for e in parent_emails if e}
    if adult_emails & parent_emails:
        request.session[f"handoff_token_{application.application_id}"] = token


def _save_step_data(application: Application, key: str, payload: dict, next_step: int):
    """Persist a step's cleaned data into ``application.data`` and bump
    ``current_step`` if needed.
    """
    data = dict(application.data or {})
    data[key] = payload
    application.data = data
    application.current_step = max(application.current_step, next_step)
    application.save(update_fields=["data", "current_step", "updated_at"])


def _sanitize_payload(cleaned_data: dict) -> dict:
    """Prepare form.cleaned_data for storage in JSONField.
    - Dates/datetimes -> ISO string
    - QuerySets (from ModelMultipleChoice) -> list of PKs
    - Model instances (from ModelChoiceField) -> PK
    """
    payload = {}
    for k, v in cleaned_data.items():
        if hasattr(v, "isoformat"):
            payload[k] = v.isoformat()
        elif hasattr(v, "values_list"):
            # ModelMultipleChoiceField returns a QuerySet
            payload[k] = list(v.values_list("pk", flat=True))
        elif hasattr(v, "pk"):
            # ModelChoiceField returns a single model instance
            payload[k] = v.pk
        else:
            payload[k] = v
    return payload


def _student_initial_for(application: Application) -> dict:
    """Build the initial dict for the StudentInfoForm based on prior step
    data, then existing-record lookup, then bare email-only defaults."""
    saved = (application.data or {}).get("step5-student") or {}
    if saved:
        # If we have graduation_year, calculate grade back
        if "graduation_year" in saved and "grade" not in saved:
            ref_date = (
                application.program.start_date
                if application and application.program
                else None
            )
            grade = calculate_grade(saved["graduation_year"], ref_date)
            if grade is not None:
                saved["grade"] = grade
        return saved
    # Try to prefill from an existing Student record.
    if application.applicant_type == Application.Type.STUDENT:
        existing = find_student_by_email(application.email)
        if existing:
            initial = student_to_prefill(existing)
            initial["personal_email"] = application.email
            # Calculate grade from graduation year based on program start date
            if existing.graduation_year:
                ref_date = (
                    application.program.start_date
                    if application and application.program
                    else None
                )
                grade = calculate_grade(existing.graduation_year, ref_date)
                if grade is not None:
                    initial["grade"] = grade
            return initial
        return {"personal_email": application.email}
    # Parent: prefill is decided by ChooseExistingStudent flow.
    return {}


def _require_mentor(application: Application):
    """Redirect non-mentor applicants back to their own flow."""
    if not _is_mentor(application):
        return _redirect_to_current_step(application)
    return None


def _require_mentor_verified(application: Application):
    """Mentor pages past OTP require a verified email + mentor type."""
    guard = _require_mentor(application)
    if guard is not None:
        return guard
    if not application.email_is_verified:
        return redirect("apply_step3", app_id=application.application_id)
    # Block existing-mentor applicants from continuing past OTP.
    if find_existing_mentor_by_email(application.email):
        return redirect("apply_mentor_blocked", app_id=application.application_id)
    return None
