"""Step 10: post-approval signed-document download + upload."""

from __future__ import annotations

import mimetypes
import os
import time

from django.contrib import messages
from django.core.exceptions import SuspiciousFileOperation
from django.db import transaction
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.decorators import method_decorator
from django.utils.http import content_disposition_header
from django.views import View
from django.views.decorators.cache import never_cache

from programs.constants import OTP_TTL_SECONDS
from programs.models import ProgramDocument

from ..forms import DocumentSubmissionForm, OtpVerifyForm
from ..models import Application, ApplicationDocumentSubmission, OtpVerifyResult
from ..rate_limiting import check_otp_send_limit, check_otp_verify_limit
from .utils import (
    TOTAL_STEPS,
    _get_application_or_404,
    _issue_and_send,
    _redirect_to_current_step,
    _require_organization,
)


def _document_access_session_key(application):
    return f"application_document_access:{application.organization_id}:{application.application_id}"


def _document_challenge_session_key(application):
    return f"application_document_challenge:{application.organization_id}:{application.application_id}"


def _has_applicant_document_access(request, application):
    access = request.session.get(_document_access_session_key(application))
    return bool(
        isinstance(access, dict)
        and access.get("organization_id") == application.organization_id
        and access.get("email") == (application.email or "").strip().lower()
        and access.get("expires_at", 0) > int(time.time())
    )


def application_document_file_response(submission):
    if not submission.file:
        raise Http404
    filename = os.path.basename(submission.file.name)
    content_type, _ = mimetypes.guess_type(filename)
    response = FileResponse(
        submission.file.open("rb"),
        content_type=content_type or "application/octet-stream",
    )
    response["Content-Disposition"] = content_disposition_header(True, filename)
    response["X-Content-Type-Options"] = "nosniff"
    response["Cache-Control"] = "private, no-store"
    return response


@method_decorator(never_cache, name="dispatch")
class Step10DocumentsView(View):
    template_name = "applications/step10_documents.html"

    def get(self, request, app_id: str):
        organization = _require_organization(request)
        application = _get_application_or_404(app_id, organization)
        if not application.program_id:
            raise Http404
        guard = self._gate(application)
        if guard is not None:
            return guard
        if not _has_applicant_document_access(request, application):
            return redirect(
                "apply_step10_document_access", app_id=application.application_id
            )
        return self._render(request, application, DocumentSubmissionForm())

    def post(self, request, app_id: str):
        organization = _require_organization(request)
        application = _get_application_or_404(app_id, organization)
        if not application.program_id:
            raise Http404
        guard = self._gate(application)
        if guard is not None:
            return guard
        if not _has_applicant_document_access(request, application):
            return redirect(
                "apply_step10_document_access", app_id=application.application_id
            )

        # Identify which ProgramDocument this upload is for. Must belong to
        # the application's program and be active.
        doc_id = request.POST.get("document_id")
        document = (
            ProgramDocument.objects.filter(
                pk=doc_id,
                program=application.program,
                program__organization=organization,
                is_active=True,
            ).first()
            if doc_id
            else None
        )
        if document is None:
            messages.error(request, "We couldn't find that document. Please try again.")
            return redirect("apply_step10", app_id=application.application_id)

        form = DocumentSubmissionForm(request.POST, request.FILES)
        if not form.is_valid():
            return self._render(request, application, form, focus_doc_id=document.pk)

        submission, created = ApplicationDocumentSubmission.objects.get_or_create(
            application=application,
            document=document,
            defaults={"organization": organization},
        )
        if not created and submission.organization_id != organization.pk:
            messages.error(
                request,
                "This document submission has inconsistent organization ownership and cannot be changed.",
            )
            return redirect("apply_step10", app_id=application.application_id)
        submission.file = form.cleaned_data["file"]
        try:
            with transaction.atomic():
                submission.save()
        except SuspiciousFileOperation:
            messages.error(
                request,
                "The filename of your uploaded document is too long or contains invalid characters. "
                "Please rename the file to something shorter and try again.",
            )
            return self._render(request, application, form, focus_doc_id=document.pk)

        # If every required ProgramDocument now has a submission, promote
        # the application from APPROVED -> APPROVED_SIGNED so lead mentors
        # can see at a glance that the paperwork is in.
        if application.status == Application.Status.APPROVED:
            required_doc_ids = set(
                ProgramDocument.objects.filter(
                    program=application.program,
                    program__organization=organization,
                    is_active=True,
                    is_required=True,
                ).values_list("pk", flat=True)
            )
            if required_doc_ids:
                uploaded_doc_ids = set(
                    ApplicationDocumentSubmission.objects.for_organization(organization)
                    .filter(
                        application=application,
                        document_id__in=required_doc_ids,
                        document__program__organization=organization,
                    )
                    .values_list("document_id", flat=True)
                )
                if required_doc_ids.issubset(uploaded_doc_ids):
                    application.status = Application.Status.APPROVED_SIGNED
                    application.save(update_fields=["status", "updated_at"])

        messages.success(
            request,
            f"Uploaded signed copy of “{document.name}”. Thank you!",
        )
        return redirect("apply_step10", app_id=application.application_id)

    def _gate(self, application: Application):
        """Only approved applications may access Step 9."""
        if application.status not in (
            Application.Status.APPROVED,
            Application.Status.APPROVED_SIGNED,
        ):
            # Send them somewhere sensible: their current step, or the
            # post-submit confirmation page if they've already submitted.
            return _redirect_to_current_step(application)
        return None

    def _render(self, request, application, form, focus_doc_id=None):
        organization = _require_organization(request)
        documents = list(
            ProgramDocument.objects.filter(
                program=application.program,
                program__organization=organization,
                is_active=True,
            ).order_by("display_order", "name")
        )
        submissions_by_doc = {
            s.document_id: s
            for s in ApplicationDocumentSubmission.objects.for_organization(
                organization
            )
            .filter(
                application=application,
                document__program=application.program,
                document__program__organization=organization,
            )
            .select_related("document")
        }
        rows = []
        all_required_done = True
        for doc in documents:
            submission = submissions_by_doc.get(doc.pk)
            if doc.is_required and submission is None:
                all_required_done = False
            rows.append({"document": doc, "submission": submission})
        return render(
            request,
            self.template_name,
            {
                "application": application,
                "form": form,
                "rows": rows,
                "all_required_done": all_required_done,
                "focus_doc_id": focus_doc_id,
                "current_step": 10,
                "total_steps": TOTAL_STEPS,
            },
        )


@method_decorator(never_cache, name="dispatch")
class Step10DocumentDownloadView(View):
    def get(self, request, app_id: str, document_id: int):
        organization = _require_organization(request)
        application = _get_application_or_404(app_id, organization)
        if not application.program_id or application.status not in (
            Application.Status.APPROVED,
            Application.Status.APPROVED_SIGNED,
        ):
            raise Http404
        if not _has_applicant_document_access(request, application):
            return redirect(
                "apply_step10_document_access", app_id=application.application_id
            )

        document = get_object_or_404(
            ProgramDocument.objects.filter(
                program=application.program,
                program__organization=organization,
                is_active=True,
            ),
            pk=document_id,
        )
        submission = get_object_or_404(
            ApplicationDocumentSubmission.objects.for_organization(organization).filter(
                application=application,
                document=document,
                document__program__organization=organization,
            )
        )
        return application_document_file_response(submission)


@method_decorator(never_cache, name="dispatch")
class Step10DocumentAccessView(View):
    template_name = "applications/document_access.html"

    def get(self, request, app_id: str):
        organization = _require_organization(request)
        application = _get_application_or_404(app_id, organization)
        if (
            application.status
            not in (
                Application.Status.APPROVED,
                Application.Status.APPROVED_SIGNED,
            )
            or not application.email
        ):
            raise Http404
        if _has_applicant_document_access(request, application):
            return redirect("apply_step10", app_id=application.application_id)
        return self._render(request, application, OtpVerifyForm())

    def post(self, request, app_id: str):
        organization = _require_organization(request)
        application = _get_application_or_404(app_id, organization)
        if (
            application.status
            not in (
                Application.Status.APPROVED,
                Application.Status.APPROVED_SIGNED,
            )
            or not application.email
        ):
            raise Http404

        if request.POST.get("action") == "send":
            rate_limited = check_otp_send_limit(request, application.email)
            if rate_limited is not None:
                return rate_limited
            if not _issue_and_send(application, request):
                messages.error(
                    request,
                    "We couldn't send a verification code. Please try again later.",
                )
                return self._render(request, application, OtpVerifyForm())
            request.session[_document_challenge_session_key(application)] = {
                "organization_id": application.organization_id,
                "email": application.email.strip().lower(),
                "expires_at": int(time.time()) + OTP_TTL_SECONDS,
            }
            messages.info(
                request,
                "A verification code was sent to the email address on the application.",
            )
            return redirect(
                "apply_step10_document_access", app_id=application.application_id
            )

        pending = request.session.get(_document_challenge_session_key(application))
        form = OtpVerifyForm(request.POST)
        if not (
            isinstance(pending, dict)
            and pending.get("organization_id") == application.organization_id
            and pending.get("email") == application.email.strip().lower()
            and pending.get("expires_at", 0) > int(time.time())
        ):
            messages.error(request, "Please request a new verification code.")
            request.session.pop(_document_challenge_session_key(application), None)
            return self._render(request, application, form)

        rate_limited = check_otp_verify_limit(request, application.application_id)
        if rate_limited is not None:
            return rate_limited
        if not form.is_valid():
            return self._render(request, application, form)

        result = application.verify_otp(form.cleaned_data["code"])
        if result is not OtpVerifyResult.SUCCESS:
            messages.error(
                request,
                "That code is invalid or expired. Please request a new code and try again.",
            )
            return self._render(request, application, form)

        request.session.cycle_key()
        request.session[_document_access_session_key(application)] = {
            "organization_id": application.organization_id,
            "email": application.email.strip().lower(),
            "expires_at": int(time.time()) + OTP_TTL_SECONDS,
        }
        request.session.pop(_document_challenge_session_key(application), None)
        return redirect("apply_step10", app_id=application.application_id)

    def _render(self, request, application, form):
        return render(
            request,
            self.template_name,
            {
                "application": application,
                "form": form,
                "current_step": 10,
                "total_steps": TOTAL_STEPS,
            },
        )
