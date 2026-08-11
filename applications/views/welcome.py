"""Step 1: welcome, resume, and post-submit confirmation views."""

from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.shortcuts import redirect, render
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.cache import never_cache

from ..forms import ResumeApplicationForm
from ..models import Application, SiteSettings
from ..services import (
    PENDING_STATUSES,
    applications_for_user,
    get_program_buckets,
)
from .utils import (
    TOTAL_STEPS,
    _auto_authorize_handoff,
    _get_application_or_404,
    _is_mentor,
    _mentor_progress,
    _redirect_to_current_step,
    _require_verified_email,
)


@method_decorator(never_cache, name="dispatch")
class WelcomeView(View):
    """Step 1: welcome page. Lets the user start a new application or resume one."""

    template_name = "applications/step1_welcome.html"

    def get(self, request):
        future_programs, _, _ = get_program_buckets()
        return render(
            request,
            self.template_name,
            {
                "settings_obj": SiteSettings.load(),
                "resume_form": ResumeApplicationForm(),
                "future_programs": future_programs,
                "current_step": 1,
                "total_steps": TOTAL_STEPS,
                "application": None,
            },
        )

    def post(self, request):
        # POST on Step 1 means "start a new application".
        application = Application.objects.create()
        return redirect("apply_step2", app_id=application.application_id)


@method_decorator(never_cache, name="dispatch")
class ResumeView(View):
    """Handle the resume form on Step 1."""

    template_name = "applications/step1_welcome.html"

    def post(self, request):
        form = ResumeApplicationForm(request.POST)
        if not form.is_valid():
            future_programs, _, _ = get_program_buckets()
            return render(
                request,
                self.template_name,
                {
                    "settings_obj": SiteSettings.load(),
                    "resume_form": form,
                    "future_programs": future_programs,
                    "current_step": 1,
                    "total_steps": TOTAL_STEPS,
                    "resume_error": True,
                    "application": None,
                },
            )
        application = Application.objects.get(
            application_id=form.cleaned_data["application_id"]
        )
        messages.info(
            request,
            f"Welcome back! Resuming application {application.application_id}.",
        )
        return _redirect_to_current_step(application)


@method_decorator(never_cache, name="dispatch")
class ResumeLinkView(View):
    """Direct link from the welcome email — drop straight into the wizard."""

    def get(self, request, app_id: str, token: str = None):
        application = _get_application_or_404(app_id)
        if token and application.handoff_token and token == application.handoff_token:
            # Store the token in the session to authorize access to handed-off steps.
            request.session[f"handoff_token_{application.application_id}"] = token
        return _redirect_to_current_step(application)


@method_decorator(never_cache, name="dispatch")
class ContinueView(View):
    def get(self, request, app_id: str):
        application = _get_application_or_404(app_id)
        # Let a logged-in parent whose email matches the handoff recipient
        # resume an AWAITING_PARENT application from their dashboard.
        _auto_authorize_handoff(request, application)
        if not application.email_is_verified:
            if _is_mentor(application):
                return redirect("apply_step3", app_id=application.application_id)
            return redirect("apply_step4", app_id=application.application_id)
        # Land them on the highest step they've reached so far (>= 5).
        application.current_step = max(application.current_step, 5)
        application.save(update_fields=["current_step", "updated_at"])
        return _redirect_to_current_step(application)


@method_decorator(never_cache, name="dispatch")
class ApplicationWithdrawView(LoginRequiredMixin, View):
    """Applicant-facing two-step withdraw (permanent delete).

    Only the person tied to the application by email (see
    ``applications_for_user``) may withdraw it, and only while its status is
    still pending (not converted or declined). GET shows a confirmation
    page; POST performs the delete.
    """

    template_name = "applications/withdraw.html"

    def _get_application(self, request, app_id: str):
        application = _get_application_or_404(app_id)
        if application.status not in PENDING_STATUSES:
            messages.error(
                request,
                "That application can no longer be withdrawn.",
            )
            return None
        if application not in applications_for_user(request.user):
            messages.error(
                request,
                "You are not allowed to withdraw that application.",
            )
            return None
        return application

    def get(self, request, app_id: str):
        application = self._get_application(request, app_id)
        if application is None:
            return redirect("profile_dashboard")
        return render(request, self.template_name, {"application": application})

    def post(self, request, app_id: str):
        application = self._get_application(request, app_id)
        if application is None:
            return redirect("profile_dashboard")
        app_str = application.application_id
        application.delete()
        messages.success(request, f"Your application {app_str} was withdrawn.")
        return redirect("profile_dashboard")


@method_decorator(never_cache, name="dispatch")
class SubmittedView(View):
    template_name = "applications/submitted.html"

    def get(self, request, app_id: str):
        application = _get_application_or_404(app_id)
        # Approved student/parent applicants belong on the documents page.
        # Mentors have no Step 10 documents flow.
        if not _is_mentor(application) and application.status in (
            Application.Status.APPROVED,
            Application.Status.APPROVED_SIGNED,
        ):
            return redirect("apply_step10", app_id=application.application_id)
        if _is_mentor(application):
            current_step, total_steps = _mentor_progress("submitted")
        else:
            current_step = min(max(application.current_step, 10), TOTAL_STEPS)
            total_steps = TOTAL_STEPS
        return render(
            request,
            self.template_name,
            {
                "application": application,
                "current_step": current_step,
                "total_steps": total_steps,
                "step5_data": application.data.get("step5-student") or {},
                "step6_data": application.data.get("step6-experience") or {},
                "step7_data": application.data.get("step7-primaryparent") or {},
                "step8_data": application.data.get("step8-secondaryparent") or {},
                "step8_skipped": bool(
                    (application.data.get("step8-secondaryparent") or {}).get(
                        "_skipped"
                    )
                ),
                "mentor_info_data": application.data.get("mentor_info") or {},
            },
        )
