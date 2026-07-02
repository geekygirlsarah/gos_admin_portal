"""Sliding scale application wizard views.

A separate 5-step wizard for parents to apply for the sliding scale
payment program. Steps:
  1. Enter email (ss_step1)
  2. Verify email via OTP (ss_step2)
  3. Confirm students & programs (ss_step3)
  4. Enter income information (ss_step4)
  5. Upload proof of income (ss_step5)
  Submitted confirmation page (ss_submitted)
"""

from __future__ import annotations

import logging

from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views import View
from django.views.decorators.cache import never_cache
from django.utils.decorators import method_decorator

from .forms import (
    OtpVerifyForm,
    SlidingScaleEmailForm,
    SlidingScaleIncomeForm,
    SlidingScaleUploadForm,
)
from .models import Application, SlidingScaleApplicationDocument
from .services import (
    get_students_and_programs_for_email,
    send_otp_email,
    send_sliding_scale_submitted_email,
)

logger = logging.getLogger(__name__)

SS_TOTAL_STEPS = 5


def _get_ss_application_or_404(app_id: str) -> Application:
    return get_object_or_404(
        Application,
        application_id=(app_id or "").upper(),
        applicant_type=Application.Type.SLIDING_SCALE,
    )


def _require_verified(application: Application, request):
    """Return a redirect if the email is not yet verified, else None."""
    if not application.email_is_verified:
        return redirect("sliding_scale_verify", app_id=application.application_id)
    return None


@method_decorator(never_cache, name="dispatch")
class SlidingScaleStartView(View):
    """Step 1: collect email and send OTP."""

    template_name = "applications/sliding_scale/ss_step1_email.html"

    def get(self, request):
        return render(
            request,
            self.template_name,
            {"form": SlidingScaleEmailForm(), "current_step": 1, "total_steps": SS_TOTAL_STEPS},
        )

    def post(self, request):
        form = SlidingScaleEmailForm(request.POST)
        if not form.is_valid():
            return render(
                request,
                self.template_name,
                {"form": form, "current_step": 1, "total_steps": SS_TOTAL_STEPS},
            )
        email = form.cleaned_data["email"]
        application = Application.objects.create(
            applicant_type=Application.Type.SLIDING_SCALE,
            email=email,
            status=Application.Status.DRAFT,
            current_step=2,
        )
        code = application.issue_otp()
        send_otp_email(application, code, request=request)
        return redirect("sliding_scale_verify", app_id=application.application_id)


@method_decorator(never_cache, name="dispatch")
class SlidingScaleVerifyView(View):
    """Step 2: verify OTP sent to email."""

    template_name = "applications/sliding_scale/ss_step2_verify.html"

    def get(self, request, app_id: str):
        application = _get_ss_application_or_404(app_id)
        if application.email_is_verified:
            return redirect("sliding_scale_programs", app_id=application.application_id)
        return render(
            request,
            self.template_name,
            {
                "form": OtpVerifyForm(),
                "application": application,
                "current_step": 2,
                "total_steps": SS_TOTAL_STEPS,
            },
        )

    def post(self, request, app_id: str):
        application = _get_ss_application_or_404(app_id)
        if application.email_is_verified:
            return redirect("sliding_scale_programs", app_id=application.application_id)
        form = OtpVerifyForm(request.POST)
        if form.is_valid():
            code = form.cleaned_data["code"]
            if application.verify_otp(code):
                application.current_step = 3
                application.save(update_fields=["current_step", "updated_at"])
                return redirect("sliding_scale_programs", app_id=application.application_id)
            form.add_error("code", "Invalid or expired code. Please try again.")
        return render(
            request,
            self.template_name,
            {
                "form": form,
                "application": application,
                "current_step": 2,
                "total_steps": SS_TOTAL_STEPS,
            },
        )


@method_decorator(never_cache, name="dispatch")
class SlidingScaleResendView(View):
    """Resend OTP for the sliding scale wizard."""

    def post(self, request, app_id: str):
        application = _get_ss_application_or_404(app_id)
        if not application.email_is_verified:
            code = application.issue_otp()
            send_otp_email(application, code, request=request)
            messages.success(request, "A new verification code has been sent.")
        return redirect("sliding_scale_verify", app_id=application.application_id)


@method_decorator(never_cache, name="dispatch")
class SlidingScaleProgramsView(View):
    """Step 3: show students and programs, ask parent to confirm."""

    template_name = "applications/sliding_scale/ss_step3_programs.html"

    def get(self, request, app_id: str):
        application = _get_ss_application_or_404(app_id)
        redir = _require_verified(application, request)
        if redir:
            return redir
        student_programs = get_students_and_programs_for_email(application.email)
        return render(
            request,
            self.template_name,
            {
                "application": application,
                "student_programs": student_programs,
                "current_step": 3,
                "total_steps": SS_TOTAL_STEPS,
            },
        )

    def post(self, request, app_id: str):
        application = _get_ss_application_or_404(app_id)
        redir = _require_verified(application, request)
        if redir:
            return redir
        application.current_step = 4
        application.save(update_fields=["current_step", "updated_at"])
        return redirect("sliding_scale_income", app_id=application.application_id)


@method_decorator(never_cache, name="dispatch")
class SlidingScaleIncomeView(View):
    """Step 4: enter income and family size."""

    template_name = "applications/sliding_scale/ss_step4_income.html"

    def get(self, request, app_id: str):
        application = _get_ss_application_or_404(app_id)
        redir = _require_verified(application, request)
        if redir:
            return redir
        # Pre-populate from saved data if returning
        saved = (application.data or {}).get("ss_income", {})
        form = SlidingScaleIncomeForm(initial=saved) if saved else SlidingScaleIncomeForm()
        return render(
            request,
            self.template_name,
            {
                "form": form,
                "application": application,
                "current_step": 4,
                "total_steps": SS_TOTAL_STEPS,
            },
        )

    def post(self, request, app_id: str):
        application = _get_ss_application_or_404(app_id)
        redir = _require_verified(application, request)
        if redir:
            return redir
        form = SlidingScaleIncomeForm(request.POST)
        if not form.is_valid():
            return render(
                request,
                self.template_name,
                {
                    "form": form,
                    "application": application,
                    "current_step": 4,
                    "total_steps": SS_TOTAL_STEPS,
                },
            )
        cd = form.cleaned_data
        income_data = {
            "adjusted_gross_income": str(cd["adjusted_gross_income"]) if cd.get("adjusted_gross_income") else "",
            "adjusted_monthly_income": str(cd["adjusted_monthly_income"]) if cd.get("adjusted_monthly_income") else "",
            "family_size": str(cd["family_size"]),
            "effective_date": cd["effective_date"].isoformat() if cd.get("effective_date") else "",
        }
        data = application.data or {}
        data["ss_income"] = income_data
        application.data = data
        application.current_step = 5
        application.save(update_fields=["data", "current_step", "updated_at"])
        return redirect("sliding_scale_upload", app_id=application.application_id)


@method_decorator(never_cache, name="dispatch")
class SlidingScaleUploadView(View):
    """Step 5: upload proof-of-income documents and submit."""

    template_name = "applications/sliding_scale/ss_step5_upload.html"

    def get(self, request, app_id: str):
        application = _get_ss_application_or_404(app_id)
        redir = _require_verified(application, request)
        if redir:
            return redir
        return render(
            request,
            self.template_name,
            {
                "form": SlidingScaleUploadForm(),
                "application": application,
                "current_step": 5,
                "total_steps": SS_TOTAL_STEPS,
            },
        )

    def post(self, request, app_id: str):
        application = _get_ss_application_or_404(app_id)
        redir = _require_verified(application, request)
        if redir:
            return redir
        form = SlidingScaleUploadForm(request.POST, request.FILES)
        if not form.is_valid():
            return render(
                request,
                self.template_name,
                {
                    "form": form,
                    "application": application,
                    "current_step": 5,
                    "total_steps": SS_TOTAL_STEPS,
                },
            )
        # Save primary document (encrypted)
        SlidingScaleApplicationDocument.objects.create(
            application=application,
            document_type=SlidingScaleApplicationDocument.DocumentType.PRIMARY,
            file=form.cleaned_data["primary_document"],
        )
        # Save supplemental document if provided
        if form.cleaned_data.get("supplemental_document"):
            SlidingScaleApplicationDocument.objects.create(
                application=application,
                document_type=SlidingScaleApplicationDocument.DocumentType.SUPPLEMENTAL,
                file=form.cleaned_data["supplemental_document"],
            )
        # Mark as submitted
        application.status = Application.Status.SUBMITTED
        application.submitted_at = timezone.now()
        application.current_step = 6
        application.save(update_fields=["status", "submitted_at", "current_step", "updated_at"])
        # Send emails
        send_sliding_scale_submitted_email(application, request=request)
        return redirect("sliding_scale_submitted", app_id=application.application_id)


@method_decorator(never_cache, name="dispatch")
class SlidingScaleSubmittedView(View):
    """Confirmation page shown after submission."""

    template_name = "applications/sliding_scale/ss_submitted.html"

    def get(self, request, app_id: str):
        application = _get_ss_application_or_404(app_id)
        return render(
            request,
            self.template_name,
            {"application": application},
        )
