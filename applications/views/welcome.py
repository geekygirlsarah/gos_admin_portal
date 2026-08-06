"""Step 1: welcome, resume, and post-submit confirmation views."""

from __future__ import annotations

from django.contrib import messages
from django.shortcuts import redirect, render
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.cache import never_cache

from ..forms import ResumeApplicationForm
from ..models import Application, SiteSettings
from ..services import get_program_buckets
from .utils import (
    TOTAL_STEPS,
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
        if not application.email_is_verified:
            if _is_mentor(application):
                return redirect("apply_step3", app_id=application.application_id)
            return redirect("apply_step4", app_id=application.application_id)
        # Land them on the highest step they've reached so far (>= 5).
        application.current_step = max(application.current_step, 5)
        application.save(update_fields=["current_step", "updated_at"])
        return _redirect_to_current_step(application)


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
