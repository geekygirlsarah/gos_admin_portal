"""Verify the current browser before allowing access to an application."""

import time

from django.contrib import messages
from django.contrib.auth.hashers import check_password, make_password
from django.db import transaction
from django.shortcuts import redirect, render
from django.utils import timezone
from django.utils.crypto import constant_time_compare, salted_hmac
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.cache import never_cache

from programs.constants import OTP_TTL_SECONDS
from programs.models import Program

from ..forms import OtpVerifyForm
from ..models import Application, generate_otp_code, normalize_step_keys
from ..rate_limiting import check_otp_send_limit, check_otp_verify_limit
from ..services import send_otp_email
from .utils import (
    _get_application_or_404,
    _is_handoff_authorized,
    _redirect_to_current_step,
    logger,
)

_RESUME_ELIGIBLE_STATUSES = (
    Application.Status.DRAFT,
    Application.Status.EMAIL_VERIFIED,
    Application.Status.AWAITING_PARENT,
)


def _pending_resume_key(application):
    return (
        f"application_pending_resume:{application.organization_id}:"
        f"{application.application_id}"
    )


def _resume_source_digest(application):
    email = (application.email or "").strip().casefold()
    value = f"{application.organization_id}:{application.application_id}:{email}"
    return salted_hmac("applications.resume_source", value).hexdigest()


def begin_pending_resume(request, source, target):
    request.session[_pending_resume_key(target)] = {
        "source_application_id": source.application_id,
        "source_email_digest": _resume_source_digest(source),
    }
    request.session.modified = True


def complete_pending_resume(request, target):
    key = _pending_resume_key(target)
    intent = request.session.get(key)
    if not isinstance(intent, dict):
        return None
    if not _has_browser_access(request, target):
        return None
    if _identity(target)["role"] == "handoff_parent" and not _is_handoff_authorized(
        request, target
    ):
        return None

    source_id = intent.get("source_application_id")
    expected_digest = intent.get("source_email_digest")
    if not source_id or source_id == target.application_id or not expected_digest:
        request.session.pop(key, None)
        messages.error(
            request,
            "We couldn't safely resume that application. Neither application was "
            "changed. Please return to your active application or start a new one.",
        )
        return redirect("apply_start")

    using = target._state.db
    organization = target.organization
    source = None
    target_current = None
    valid = False
    with transaction.atomic(using=using):
        locked_applications = list(
            Application.objects.using(using)
            .for_organization(organization)
            .filter(application_id__in=[source_id, target.application_id])
            .order_by("pk")
            .select_for_update()
        )
        by_id = {item.application_id: item for item in locked_applications}
        source = by_id.get(source_id)
        target_current = by_id.get(target.application_id)
        if source is not None and target_current is not None:
            source_email = (source.email or "").strip().casefold()
            target_email = (target_current.email or "").strip().casefold()
            current_digest = _resume_source_digest(source)
            program_ids = {
                item.program_id
                for item in (source, target_current)
                if item.program_id is not None
            }
            valid_program_ids = set(
                Program.objects.using(using)
                .for_organization(organization)
                .filter(pk__in=program_ids)
                .values_list("pk", flat=True)
            )
            programs_are_tenant_bound = all(
                item.program_id is None or item.program_id in valid_program_ids
                for item in (source, target_current)
            )
            target_is_handoff = _identity(target_current)["role"] == "handoff_parent"
            valid = (
                source.organization_id == organization.pk
                and target_current.organization_id == organization.pk
                and source.superseded_at is None
                and target_current.superseded_at is None
                and source.status in _RESUME_ELIGIBLE_STATUSES
                and target_current.status in _RESUME_ELIGIBLE_STATUSES
                and source.email_verified_at is not None
                and source_email
                and source_email == target_email
                and constant_time_compare(current_digest, expected_digest)
                and programs_are_tenant_bound
                and _has_browser_access(request, source)
                and _has_browser_access(request, target_current)
                and (
                    not target_is_handoff
                    or _is_handoff_authorized(request, target_current)
                )
            )
            if valid:
                source.superseded_at = timezone.now()
                source.save(update_fields=["superseded_at"])

    request.session.pop(key, None)
    if not valid:
        messages.error(
            request,
            "We couldn't safely resume that application. Neither application was "
            "changed. Please return to your active application or start a new one.",
        )
        if (
            source is not None
            and source.superseded_at is None
            and source.status in _RESUME_ELIGIBLE_STATUSES
            and _has_browser_access(request, source)
        ):
            return redirect("apply_duplicate_found", app_id=source.application_id)
        return redirect("apply_start")

    request.session.pop(_key(source, "browser_access"), None)
    request.session.pop(_key(source, "bootstrap"), None)
    request.session.pop(_key(source, "browser_challenge"), None)
    request.session.pop(f"handoff_token_{source.application_id}", None)
    messages.success(request, "Access verified. Resuming your existing application.")
    return _redirect_to_current_step(target_current)


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
        resume_response = complete_pending_resume(request, application)
        if resume_response is not None:
            return resume_response
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
