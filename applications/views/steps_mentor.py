"""Mentor-specific wizard steps (after OTP verification)."""

from __future__ import annotations

from django.shortcuts import redirect, render
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.cache import never_cache

from ..forms import (
    ConfirmSubmitForm,
    MentorClearanceDetailForm,
    MentorClearanceInterestForm,
    MentorInfoForm,
)
from ..models import Application
from ..services import (
    find_existing_mentor_by_email,
    send_application_submitted_email,
    send_lead_notification_email,
)
from .utils import (
    MENTOR_TOTAL_STEPS,
    _get_application_or_404,
    _is_mentor,
    _mentor_progress,
    _redirect_to_current_step,
    logger,
)


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


@method_decorator(never_cache, name="dispatch")
class MentorBlockedView(View):
    """Shown when the OTP-verified email already belongs to a mentor on file."""

    template_name = "applications/mentor_blocked.html"

    def get(self, request, app_id: str):
        application = _get_application_or_404(app_id)
        if not _is_mentor(application):
            return _redirect_to_current_step(application)
        return render(
            request,
            self.template_name,
            {
                "application": application,
                "current_step": _mentor_progress("step4")[0],
                "total_steps": MENTOR_TOTAL_STEPS,
            },
        )


@method_decorator(never_cache, name="dispatch")
class MentorInfoView(View):
    template_name = "applications/mentor_info.html"

    def get(self, request, app_id: str):
        application = _get_application_or_404(app_id)
        guard = _require_mentor_verified(application)
        if guard is not None:
            return guard
        initial = (application.data or {}).get("mentor_info") or {}
        return self._render(request, application, MentorInfoForm(initial=initial))

    def post(self, request, app_id: str):
        application = _get_application_or_404(app_id)
        guard = _require_mentor_verified(application)
        if guard is not None:
            return guard
        form = MentorInfoForm(request.POST)
        if not form.is_valid():
            return self._render(request, application, form)
        from .utils import _save_step_data

        _save_step_data(
            application, "mentor_info", dict(form.cleaned_data), next_step=6
        )
        return redirect(
            "apply_mentor_clearance_interest", app_id=application.application_id
        )

    def _render(self, request, application, form):
        current_step, total_steps = _mentor_progress("mentor_info")
        return render(
            request,
            self.template_name,
            {
                "application": application,
                "form": form,
                "current_step": current_step,
                "total_steps": total_steps,
            },
        )


@method_decorator(never_cache, name="dispatch")
class MentorClearanceInterestView(View):
    template_name = "applications/mentor_clearance_interest.html"

    def get(self, request, app_id: str):
        application = _get_application_or_404(app_id)
        guard = _require_mentor_verified(application)
        if guard is not None:
            return guard
        initial = (application.data or {}).get("mentor_clearance_interest") or {}
        return self._render(
            request, application, MentorClearanceInterestForm(initial=initial)
        )

    def post(self, request, app_id: str):
        application = _get_application_or_404(app_id)
        guard = _require_mentor_verified(application)
        if guard is not None:
            return guard
        form = MentorClearanceInterestForm(request.POST)
        if not form.is_valid():
            return self._render(request, application, form)
        from .utils import _save_step_data

        _save_step_data(
            application,
            "mentor_clearance_interest",
            dict(form.cleaned_data),
            next_step=7,
        )
        if form.cleaned_data["interested"] == "yes":
            return redirect(
                "apply_mentor_clearance_detail",
                app_id=application.application_id,
            )
        # Not interested → clear any prior detail and jump to confirm.
        data = dict(application.data or {})
        data.pop("mentor_clearance_detail", None)
        application.data = data
        application.save(update_fields=["data", "updated_at"])
        return redirect("apply_mentor_confirm", app_id=application.application_id)

    def _render(self, request, application, form):
        current_step, total_steps = _mentor_progress("mentor_clearance_interest")
        return render(
            request,
            self.template_name,
            {
                "application": application,
                "form": form,
                "current_step": current_step,
                "total_steps": total_steps,
            },
        )


@method_decorator(never_cache, name="dispatch")
class MentorClearanceDetailView(View):
    template_name = "applications/mentor_clearance_detail.html"

    def get(self, request, app_id: str):
        application = _get_application_or_404(app_id)
        guard = _require_mentor_verified(application)
        if guard is not None:
            return guard
        # Don't show this page if they said "no" on the interest step.
        interest = (application.data or {}).get("mentor_clearance_interest") or {}
        if interest.get("interested") != "yes":
            return redirect(
                "apply_mentor_clearance_interest",
                app_id=application.application_id,
            )
        initial = (application.data or {}).get("mentor_clearance_detail") or {}
        return self._render(
            request, application, MentorClearanceDetailForm(initial=initial)
        )

    def post(self, request, app_id: str):
        application = _get_application_or_404(app_id)
        guard = _require_mentor_verified(application)
        if guard is not None:
            return guard
        form = MentorClearanceDetailForm(request.POST)
        if not form.is_valid():
            return self._render(request, application, form)
        from .utils import _save_step_data

        _save_step_data(
            application,
            "mentor_clearance_detail",
            dict(form.cleaned_data),
            next_step=8,
        )
        return redirect("apply_mentor_confirm", app_id=application.application_id)

    def _render(self, request, application, form):
        current_step, total_steps = _mentor_progress("mentor_clearance_detail")
        return render(
            request,
            self.template_name,
            {
                "application": application,
                "form": form,
                "current_step": current_step,
                "total_steps": total_steps,
            },
        )


@method_decorator(never_cache, name="dispatch")
class MentorConfirmView(View):
    template_name = "applications/mentor_confirm.html"

    def get(self, request, app_id: str):
        application = _get_application_or_404(app_id)
        guard = _require_mentor_verified(application)
        if guard is not None:
            return guard
        return self._render(request, application, ConfirmSubmitForm())

    def post(self, request, app_id: str):
        application = _get_application_or_404(app_id)
        guard = _require_mentor_verified(application)
        if guard is not None:
            return guard
        form = ConfirmSubmitForm(request.POST)
        if not form.is_valid():
            return self._render(request, application, form)
        application.status = Application.Status.SUBMITTED
        application.submitted_at = timezone.now()
        application.current_step = max(application.current_step, 9)
        application.save(
            update_fields=["status", "submitted_at", "current_step", "updated_at"]
        )
        from .utils import logger

        try:
            send_application_submitted_email(application, request=request)
        except Exception:
            logger.exception(
                "Failed to send mentor submitted email for %s",
                application.application_id,
            )
        try:
            send_lead_notification_email(application, request=request)
        except Exception:
            logger.exception(
                "Failed to send lead notification for mentor %s",
                application.application_id,
            )
        return redirect("apply_submitted", app_id=application.application_id)

    def _render(self, request, application, form):
        data = application.data or {}
        current_step, total_steps = _mentor_progress("mentor_confirm")
        return render(
            request,
            self.template_name,
            {
                "application": application,
                "form": form,
                "mentor_info": data.get("mentor_info") or {},
                "clearance_interest": data.get("mentor_clearance_interest") or {},
                "clearance_detail": data.get("mentor_clearance_detail") or {},
                "current_step": current_step,
                "total_steps": total_steps,
            },
        )
