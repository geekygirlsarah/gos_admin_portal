"""Verify the current browser before allowing access to an application."""

import time

from django.contrib import messages
from django.contrib.auth.hashers import check_password, make_password
from django.shortcuts import redirect, render
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.cache import never_cache

from programs.constants import OTP_TTL_SECONDS

from ..forms import OtpVerifyForm
from ..models import Application, generate_otp_code, normalize_step_keys
from ..rate_limiting import check_otp_send_limit, check_otp_verify_limit
from ..services import send_otp_email
from .utils import _get_application_or_404, _redirect_to_current_step, logger


def _identity(application):
    data = normalize_step_keys(application.data or {})
    handoff = data.get("step7_handoff") or {}
    is_parent_handoff = application.applicant_type == Application.Type.STUDENT and (
        application.status == Application.Status.AWAITING_PARENT or handoff
    )
    recipient = (
        handoff.get("parent_email", "")
        if is_parent_handoff
        else application.email or ""
    )
    return {
        "organization_id": application.organization_id,
        "application_id": application.application_id,
        "applicant_type": application.applicant_type,
        "application_email": (application.email or "").strip().lower(),
        "recipient": recipient.strip().lower(),
        "role": "handoff_parent" if is_parent_handoff else "applicant",
        "handoff_token": application.handoff_token if is_parent_handoff else "",
    }


def _key(application, kind):
    return (
        f"application_{kind}:{application.organization_id}:{application.application_id}"
    )


def _has_browser_access(request, application):
    identity = _identity(application)
    grant = request.session.get(_key(application, "browser_access"))
    return bool(
        identity["recipient"]
        and isinstance(grant, dict)
        and grant.get("identity") == identity
        and grant.get("expires_at", 0) > time.time()
    )


def _grant_browser_access(request, application):
    request.session.cycle_key()
    request.session[_key(application, "browser_access")] = {
        "identity": _identity(application),
        "expires_at": int(time.time()) + 12 * 60 * 60,
    }
    request.session.pop(_key(application, "bootstrap"), None)
    if _identity(application)["role"] == "handoff_parent" and application.handoff_token:
        request.session[f"handoff_token_{application.application_id}"] = (
            application.handoff_token
        )


def _start_bootstrap(request, application):
    request.session[_key(application, "bootstrap")] = True


def _has_bootstrap(request, application):
    return bool(request.session.get(_key(application, "bootstrap")))


def _require_browser_access(request, application, allow_bootstrap=False):
    if _has_browser_access(request, application) or (
        allow_bootstrap and _has_bootstrap(request, application)
    ):
        return None
    return redirect("apply_browser_verification", app_id=application.application_id)


@method_decorator(never_cache, name="dispatch")
class BrowserVerificationView(View):
    template_name = "applications/browser_verification.html"

    def get(self, request, app_id):
        application = _get_application_or_404(app_id, request.organization)
        if _has_browser_access(request, application):
            return _redirect_to_current_step(application)
        return self._render(request, application, OtpVerifyForm())

    def post(self, request, app_id):
        application = _get_application_or_404(app_id, request.organization)
        identity = _identity(application)
        recipient = identity["recipient"]
        if not recipient:
            return self._render(request, application, OtpVerifyForm())
        key = _key(application, "browser_challenge")
        if request.POST.get("action") == "send":
            limited = check_otp_send_limit(request, recipient)
            if limited is not None:
                return limited
            request.session.pop(key, None)
            code = generate_otp_code()
            try:
                send_otp_email(application, code, request=request, recipient=recipient)
            except Exception:
                logger.exception(
                    "Failed to send browser verification code for %s", app_id
                )
                messages.error(
                    request, "We couldn't send a code. Please try again later."
                )
                return self._render(request, application, OtpVerifyForm())
            request.session[key] = {
                "identity": identity,
                "code_hash": make_password(code),
                "attempts": 0,
                "expires_at": int(time.time()) + OTP_TTL_SECONDS,
            }
            messages.info(request, "A verification code has been emailed.")
            return redirect("apply_browser_verification", app_id=app_id)
        limited = check_otp_verify_limit(request, application.application_id)
        if limited is not None:
            return limited
        form = OtpVerifyForm(request.POST)
        pending = request.session.get(key)
        if not (
            isinstance(pending, dict)
            and pending.get("identity") == identity
            and pending.get("expires_at", 0) > time.time()
            and pending.get("code_hash")
        ):
            request.session.pop(key, None)
            messages.error(request, "Please request a new verification code.")
            return self._render(request, application, form)
        if not form.is_valid():
            return self._render(request, application, form)
        pending["attempts"] += 1
        if pending["attempts"] > 10:
            request.session.pop(key, None)
            messages.error(request, "Too many attempts. Please request a new code.")
            return self._render(request, application, form)
        if not check_password(form.cleaned_data["code"], pending["code_hash"]):
            request.session[key] = pending
            messages.error(request, "That code didn't match. Please try again.")
            return self._render(request, application, form)
        request.session.pop(key, None)
        if identity["role"] == "applicant" and not application.email_is_verified:
            application.email_verified_at = timezone.now()
            if application.status == Application.Status.DRAFT:
                application.status = Application.Status.EMAIL_VERIFIED
            application.save(
                update_fields=["email_verified_at", "status", "updated_at"]
            )
        _grant_browser_access(request, application)
        return _redirect_to_current_step(application)

    def _render(self, request, application, form):
        return render(
            request,
            self.template_name,
            {
                "application": application,
                "form": form,
                "missing_email": not _identity(application)["recipient"],
            },
            status=400 if not _identity(application)["recipient"] else 200,
        )
