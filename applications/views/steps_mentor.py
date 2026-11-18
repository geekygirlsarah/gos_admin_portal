"""Mentor-specific wizard steps (after OTP verification)."""

from __future__ import annotations

from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
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
from .browser import _require_browser_access
from .utils import (
    MENTOR_TOTAL_STEPS,
    _get_application_or_404,
    _is_mentor,
    _locked_application_redirect,
    _mentor_progress,
    _redirect_to_current_step,
    logger,
)


def _require_mentor(application: Application):
    """Redirect non-mentor applicants back to their own flow."""
    if not _is_mentor(application):
        return _redirect_to_current_step(application)
    return None


def _require_mentor_verified(request, application: Application):
    """Mentor pages past OTP require a verified email + mentor type."""
    guard = _require_mentor(application)
    if guard is not None:
        return guard
    guard = _require_browser_access(request, application)
    if guard is not None:
        return guard
    if not application.email_is_verified:
        return redirect("apply_step3", app_id=application.application_id)
    # Block existing-mentor applicants from continuing past OTP.
    if find_existing_mentor_by_email(
        application.email, organization=application.organization
    ):
        return redirect("apply_mentor_blocked", app_id=application.application_id)
    return None


@method_decorator(never_cache, name="dispatch")
class MentorBlockedView(View):
    """Shown when the OTP-verified email already belongs to a mentor on file."""

    template_name = "applications/mentor_blocked.html"

    def get(self, request, app_id: str):
        application = _get_application_or_404(app_id, request.organization)
        if not _is_mentor(application):
            return _redirect_to_current_step(application)
        guard = _require_browser_access(request, application)
        if guard is not None:
            return guard
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
        application = _get_application_or_404(app_id, request.organization)
        guard = _require_mentor_verified(request, application)
        if guard is not None:
            return guard
        initial = (application.data or {}).get("mentor_info") or {}
        return self._render(request, application, MentorInfoForm(initial=initial))

    def post(self, request, app_id: str):
        application = _get_application_or_404(app_id, request.organization)
        guard = _require_mentor_verified(request, application)
        if guard is not None:
            return guard
        form = MentorInfoForm(request.POST)
        if not form.is_valid():
            return self._render(request, application, form)
        from .utils import _save_step_data

        def validate_current(current):
            locked_redirect = _locked_application_redirect(current)
            if locked_redirect is not None:
                return locked_redirect
            guard = _require_mentor_verified(request, current)
            if guard is not None:
                return guard
            if current.program_id != application.program_id:
                form.add_error(
                    None,
                    "The application changed while this form was being saved. "
                    "Please review your information and submit it again.",
                )
                return self._render(request, current, form)
            return None

        response = _save_step_data(
            application,
            "mentor_info",
            dict(form.cleaned_data),
            next_step=6,
            validate_current=validate_current,
        )
        if response is not None:
            return response
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
        application = _get_application_or_404(app_id, request.organization)
        guard = _require_mentor_verified(request, application)
        if guard is not None:
            return guard
        initial = (application.data or {}).get("mentor_clearance_interest") or {}
        return self._render(
            request, application, MentorClearanceInterestForm(initial=initial)
        )

    def post(self, request, app_id: str):
        application = _get_application_or_404(app_id, request.organization)
        guard = _require_mentor_verified(request, application)
        if guard is not None:
            return guard
        form = MentorClearanceInterestForm(request.POST)
        if not form.is_valid():
            return self._render(request, application, form)
        from .utils import _save_step_data

        initial_interest = (application.data or {}).get(
            "mentor_clearance_interest"
        ) or {}

        def validate_current(current):
            locked_redirect = _locked_application_redirect(current)
            if locked_redirect is not None:
                return locked_redirect
            guard = _require_mentor_verified(request, current)
            if guard is not None:
                return guard
            current_interest = (current.data or {}).get(
                "mentor_clearance_interest"
            ) or {}
            if current.program_id != application.program_id:
                form.add_error(
                    None,
                    "The application changed while this form was being saved. "
                    "Please review your answers and submit them again.",
                )
            elif current_interest != initial_interest:
                form.add_error(
                    None,
                    "Your clearance interest changed while this form was being "
                    "saved. Please review your answer and submit it again.",
                )
            else:
                return None
            return self._render(request, current, form)

        response = _save_step_data(
            application,
            "mentor_clearance_interest",
            dict(form.cleaned_data),
            next_step=7,
            validate_current=validate_current,
            remove_keys=(
                ("mentor_clearance_detail",)
                if form.cleaned_data["interested"] == "no"
                else ()
            ),
        )
        if response is not None:
            return response
        if form.cleaned_data["interested"] == "yes":
            return redirect(
                "apply_mentor_clearance_detail",
                app_id=application.application_id,
            )
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
        application = _get_application_or_404(app_id, request.organization)
        guard = _require_mentor_verified(request, application)
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
        application = _get_application_or_404(app_id, request.organization)
        guard = _require_mentor_verified(request, application)
        if guard is not None:
            return guard
        form = MentorClearanceDetailForm(request.POST)
        if not form.is_valid():
            return self._render(request, application, form)
        from .utils import _save_step_data

        def validate_current(current):
            locked_redirect = _locked_application_redirect(current)
            if locked_redirect is not None:
                return locked_redirect
            guard = _require_mentor_verified(request, current)
            if guard is not None:
                return guard
            current_interest = (current.data or {}).get(
                "mentor_clearance_interest"
            ) or {}
            if current_interest.get("interested") != "yes":
                return redirect(
                    "apply_mentor_clearance_interest",
                    app_id=current.application_id,
                )
            if current.program_id != application.program_id:
                form.add_error(
                    None,
                    "The application changed while this form was being saved. "
                    "Please review your answers and submit them again.",
                )
                return self._render(request, current, form)
            return None

        response = _save_step_data(
            application,
            "mentor_clearance_detail",
            dict(form.cleaned_data),
            next_step=8,
            validate_current=validate_current,
        )
        if response is not None:
            return response
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
        application = _get_application_or_404(app_id, request.organization)
        guard = _require_mentor_verified(request, application)
        if guard is not None:
            return guard
        return self._render(request, application, ConfirmSubmitForm())

    def post(self, request, app_id: str):
        application = _get_application_or_404(app_id, request.organization)
        guard = _require_mentor_verified(request, application)
        if guard is not None:
            return guard
        form = ConfirmSubmitForm(request.POST)
        if not form.is_valid():
            return self._render(request, application, form)

        using = application._state.db
        with transaction.atomic(using=using):
            current = get_object_or_404(
                Application.objects.using(using)
                .for_organization(request.organization)
                .select_for_update(),
                pk=application.pk,
            )
            locked_redirect = _locked_application_redirect(current)
            if locked_redirect is not None:
                return locked_redirect
            guard = _require_mentor_verified(request, current)
            if guard is not None:
                return guard
            if current.program_id != application.program_id:
                form.add_error(
                    None,
                    "The application changed while you were confirming. "
                    "Please review it before submitting again.",
                )
                return self._render(request, current, form)

            current.status = Application.Status.SUBMITTED
            current.submitted_at = timezone.now()
            current.current_step = max(current.current_step, 9)
            current.applicant_progress_at = timezone.now()
            current.save(
                update_fields=[
                    "status",
                    "submitted_at",
                    "current_step",
                    "applicant_progress_at",
                    "updated_at",
                ]
            )
            application = current
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
