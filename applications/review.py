"""Lead-mentor review pages for applications.

Provides custom views (list / detail / approve / decline / edit / delete)
gated by the ``applications.review_application`` permission. The default
``Lead Mentors`` group bootstrapped in migration ``0004`` carries that
permission.
"""

from __future__ import annotations

import json
import logging

from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin, PermissionRequiredMixin
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views import View

from audit.events import AuditEvent
from audit.service import log_event

from .models import Application
from .services import (
    ApplicationConversionError,
    convert_application_to_student,
    send_application_approved_email,
    send_application_declined_email,
    send_application_submitted_email,
    send_otp_email,
    send_parent_handoff_email,
)

logger = logging.getLogger(__name__)

REVIEW_PERM = "applications.review_application"


# ---------------------------------------------------------------------------
# Forms
# ---------------------------------------------------------------------------


class DeclineForm(forms.Form):
    """Captures the reason the lead mentors are declining an application.

    The reason is emailed to the applicant (along with the parent, when
    applicable) so they understand why and can follow up.
    """

    reason = forms.CharField(
        label="Reason (will be emailed to the applicant)",
        widget=forms.Textarea(
            attrs={
                "class": "form-control",
                "rows": 5,
                "placeholder": "Briefly explain why this application is being declined.",
            }
        ),
        required=False,
        help_text=(
            "Optional. If provided, this text will appear in the email "
            "sent to the applicant."
        ),
    )


class ApplicationDataEditForm(forms.Form):
    """Free-form edit of the JSON `data` blob captured by the wizard.

    Lead mentors occasionally need to fix typos on behalf of the
    applicant (e.g. a misspelled email or wrong school name). For now we
    expose the raw JSON; a per-field UI would be a much bigger change.
    """

    data_json = forms.CharField(
        label="Captured data (JSON)",
        widget=forms.Textarea(
            attrs={"class": "form-control font-monospace", "rows": 20}
        ),
        help_text=(
            "Edit the JSON captured by the wizard to fix typos or other "
            "errors. Each top-level key is a wizard step (e.g. step5-student, step6-experience). "
            "Must be valid JSON; an object is expected at the top level."
        ),
    )
    email = forms.EmailField(
        label="Primary contact email",
        required=False,
        widget=forms.EmailInput(attrs={"class": "form-control"}),
    )

    def clean_data_json(self):
        raw = self.cleaned_data["data_json"] or ""
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise forms.ValidationError(f"Invalid JSON: {exc}")
        if not isinstance(parsed, dict):
            raise forms.ValidationError(
                "Top-level JSON value must be an object (a dict)."
            )
        return parsed


# ---------------------------------------------------------------------------
# Views
# ---------------------------------------------------------------------------


class _ReviewerRequiredMixin(LoginRequiredMixin, PermissionRequiredMixin):
    """All review pages require the dedicated review permission."""

    permission_required = REVIEW_PERM
    raise_exception = False  # default behavior: redirect to login


@method_decorator(login_required, name="dispatch")
class ApplicationReviewListView(_ReviewerRequiredMixin, View):
    """List page with status + applicant-type + program filters."""

    template_name = "applications/review/list.html"

    def get(self, request):
        qs = Application.objects.select_related("program").all()
        status = (request.GET.get("status") or "").strip()
        applicant_type = (request.GET.get("type") or "").strip()
        program_id = (request.GET.get("program") or "").strip()

        valid_statuses = {c[0] for c in Application.Status.choices}
        if status and status in valid_statuses:
            qs = qs.filter(status=status)

        valid_types = {c[0] for c in Application.Type.choices}
        if applicant_type and applicant_type in valid_types:
            qs = qs.filter(applicant_type=applicant_type)

        if program_id.isdigit():
            qs = qs.filter(program_id=int(program_id))

        return render(
            request,
            self.template_name,
            {
                "applications": qs,
                "status_choices": Application.Status.choices,
                "type_choices": Application.Type.choices,
                "current_status": status,
                "current_type": applicant_type,
                "current_program": program_id,
            },
        )


class ApplicationReviewDetailView(_ReviewerRequiredMixin, View):
    """Detail page showing all collected data + action buttons."""

    template_name = "applications/review/detail.html"

    def get(self, request, app_id: str):
        application = get_object_or_404(
            Application, application_id=(app_id or "").upper()
        )
        # Build per-document status rows for the Signed Documents card.
        documents_status = []
        all_required_uploaded = True
        any_documents = False
        if application.program_id:
            from programs.models import ProgramDocument

            program_docs = ProgramDocument.objects.filter(
                program_id=application.program_id, is_active=True
            ).order_by("display_order", "name")
            submissions = {
                s.document_id: s
                for s in application.document_submissions.select_related("document")
            }
            for doc in program_docs:
                any_documents = True
                submission = submissions.get(doc.id)
                if doc.is_required and submission is None:
                    all_required_uploaded = False
                documents_status.append(
                    {
                        "document": doc,
                        "submission": submission,
                    }
                )
        # The Convert button is enabled when the application has been
        # approved and there are no required signed documents still
        # pending. APPROVED_SIGNED implies that's already true; APPROVED
        # with no required docs (or all required docs uploaded) is also
        # convertible — the auto-upgrade to APPROVED_SIGNED only fires
        # via the applicant's upload flow, so an applicant whose program
        # has no required documents would otherwise be stuck.
        can_convert = application.status == Application.Status.APPROVED_SIGNED or (
            application.status == Application.Status.APPROVED and all_required_uploaded
        )
        return render(
            request,
            self.template_name,
            {
                "application": application,
                "data": application.data or {},
                "decline_form": DeclineForm(),
                "documents_status": documents_status,
                "all_required_uploaded": all_required_uploaded,
                "any_documents": any_documents,
                "can_convert": can_convert,
            },
        )


class ApplicationApproveView(_ReviewerRequiredMixin, View):
    """POST: mark an application APPROVED and email the applicant."""

    def post(self, request, app_id: str):
        application = get_object_or_404(
            Application, application_id=(app_id or "").upper()
        )
        if application.status in (
            Application.Status.APPROVED,
            Application.Status.APPROVED_SIGNED,
            Application.Status.CONVERTED,
        ):
            messages.info(request, "Application is already approved.")
            return redirect(
                "application_review_detail", app_id=application.application_id
            )

        old_status = application.status
        application.status = Application.Status.APPROVED
        application.reviewed_at = timezone.now()
        application.reviewed_by = request.user
        application.decline_reason = ""
        # Move them past the wizard's submit step; Step 9 will surface
        # the signed-document upload page next time they resume.
        application.current_step = max(application.current_step, 9)
        application.save()

        log_event(
            request=request,
            event=AuditEvent.ADMISSION_DECISION,
            resource=application,
            before={"status": old_status},
            after={"status": Application.Status.APPROVED},
            notes=f"Application approved by {request.user}.",
        )

        try:
            send_application_approved_email(application, request=request)
        except Exception:  # pragma: no cover - defensive
            logger.exception(
                "Failed to send approval email for %s",
                application.application_id,
            )
            messages.warning(
                request,
                "Application was approved, but the confirmation email "
                "could not be sent.",
            )
        else:
            messages.success(
                request,
                f"Approved application {application.application_id}. "
                "The applicant has been emailed.",
            )
        return redirect("application_review_detail", app_id=application.application_id)


class ApplicationDeclineView(_ReviewerRequiredMixin, View):
    """POST: mark an application DECLINED and email the applicant the reason."""

    template_name = "applications/review/decline.html"

    def get(self, request, app_id: str):
        application = get_object_or_404(
            Application, application_id=(app_id or "").upper()
        )
        return render(
            request,
            self.template_name,
            {"application": application, "form": DeclineForm()},
        )

    def post(self, request, app_id: str):
        application = get_object_or_404(
            Application, application_id=(app_id or "").upper()
        )
        form = DeclineForm(request.POST)
        if not form.is_valid():
            return render(
                request,
                self.template_name,
                {"application": application, "form": form},
            )
        reason = form.cleaned_data.get("reason") or ""
        old_status = application.status
        application.status = Application.Status.DECLINED
        application.decline_reason = reason
        application.reviewed_at = timezone.now()
        application.reviewed_by = request.user
        application.save()

        log_event(
            request=request,
            event=AuditEvent.ADMISSION_DECISION,
            resource=application,
            before={"status": old_status},
            after={"status": Application.Status.DECLINED},
            notes=f"Application declined by {request.user}. Reason: {reason}",
        )

        try:
            send_application_declined_email(application, reason=reason, request=request)
        except Exception:  # pragma: no cover - defensive
            logger.exception(
                "Failed to send decline email for %s",
                application.application_id,
            )
            messages.warning(
                request,
                "Application was declined, but the notification email "
                "could not be sent.",
            )
        else:
            messages.success(
                request,
                f"Declined application {application.application_id}. "
                "The applicant has been emailed.",
            )
        return redirect("application_review_detail", app_id=application.application_id)


class ApplicationEditView(_ReviewerRequiredMixin, View):
    """Free-form edit of the captured ``data`` JSON and primary email."""

    template_name = "applications/review/edit.html"

    def _initial(self, application: Application):
        return {
            "data_json": json.dumps(application.data or {}, indent=2),
            "email": application.email,
        }

    def get(self, request, app_id: str):
        application = get_object_or_404(
            Application, application_id=(app_id or "").upper()
        )
        return render(
            request,
            self.template_name,
            {
                "application": application,
                "form": ApplicationDataEditForm(initial=self._initial(application)),
            },
        )

    def post(self, request, app_id: str):
        application = get_object_or_404(
            Application, application_id=(app_id or "").upper()
        )
        form = ApplicationDataEditForm(request.POST)
        if not form.is_valid():
            return render(
                request,
                self.template_name,
                {"application": application, "form": form},
            )
        old_data = application.data
        application.data = form.cleaned_data["data_json"]
        new_email = (form.cleaned_data.get("email") or "").strip()
        if new_email:
            application.email = new_email
        application.save()

        log_event(
            request=request,
            event=AuditEvent.CONTACT_INFO_UPDATED,
            resource=application,
            before={
                "data": old_data,
                "email": application.email if not new_email else "changed",
            },
            after={"data": application.data, "email": application.email},
            notes=f"Application data edited by {request.user}.",
        )
        messages.success(
            request,
            f"Updated application {application.application_id}.",
        )
        return redirect("application_review_detail", app_id=application.application_id)


class ApplicationDeleteView(_ReviewerRequiredMixin, View):
    """Two-step delete: GET shows confirmation, POST performs the delete."""

    template_name = "applications/review/delete.html"

    def get(self, request, app_id: str):
        application = get_object_or_404(
            Application, application_id=(app_id or "").upper()
        )
        return render(request, self.template_name, {"application": application})

    def post(self, request, app_id: str):
        application = get_object_or_404(
            Application, application_id=(app_id or "").upper()
        )
        app_str = application.application_id
        application.delete()
        messages.success(request, f"Deleted application {app_str}.")
        return redirect("application_review_list")


class ApplicationCleanupView(_ReviewerRequiredMixin, View):
    """POST: Delete all applications older than 30 days."""

    def post(self, request):
        cutoff = timezone.now() - timezone.timedelta(days=30)
        stale_apps = Application.objects.filter(created_at__lt=cutoff)
        count = stale_apps.count()
        stale_apps.delete()

        if count > 0:
            messages.success(
                request, f"Deleted {count} applications older than 30 days."
            )
        else:
            messages.info(request, "No applications older than 30 days were found.")

        return redirect("application_review_list")


class ApplicationResendEmailView(_ReviewerRequiredMixin, View):
    """POST: resend various system emails (OTP, handoff, etc.)"""

    def post(self, request, app_id: str):
        application = get_object_or_404(
            Application, application_id=(app_id or "").upper()
        )
        email_type = request.POST.get("type")

        try:
            self._handle_resend(request, application, email_type)
        except Exception:
            logger.exception(
                "Failed to resend %s email for %s",
                email_type,
                application.application_id,
            )
            messages.error(
                request, f"Failed to resend {email_type} email. Please check logs."
            )

        return redirect("application_review_detail", app_id=application.application_id)

    def _handle_resend(self, request, application: Application, email_type: str):
        if email_type == "otp":
            code = application.issue_otp()
            send_otp_email(application, code, request=request)
            messages.success(
                request, f"Resent OTP verification email to {application.email}."
            )
        elif email_type == "handoff":
            parent_email = application.email
            if not parent_email:
                messages.error(request, "No email address found for this application.")
            else:
                send_parent_handoff_email(application, parent_email, request=request)
                messages.success(
                    request, f"Resent parent handoff email to {parent_email}."
                )
        elif email_type == "submitted":
            if not application.submitted_at:
                messages.error(request, "This application has not been submitted yet.")
            else:
                send_application_submitted_email(application, request=request)
                messages.success(request, "Resent submission confirmation email.")
        elif email_type == "approved":
            if application.status not in (
                Application.Status.APPROVED,
                Application.Status.APPROVED_SIGNED,
                Application.Status.CONVERTED,
            ):
                messages.error(request, "This application has not been approved yet.")
            else:
                send_application_approved_email(application, request=request)
                messages.success(request, "Resent approval email.")
        elif email_type == "declined":
            if application.status != Application.Status.DECLINED:
                messages.error(request, "This application has not been declined.")
            else:
                send_application_declined_email(
                    application, application.decline_reason, request=request
                )
                messages.success(request, "Resent decline email.")
        else:
            messages.error(request, f"Unknown email type: {email_type}")


class ApplicationConvertView(_ReviewerRequiredMixin, View):
    """POST: convert an APPROVED_SIGNED application into a real Student.

    Creates / updates Student + Adult records from the captured wizard
    data, enrolls the new (or existing) Student in the application's
    program, and flips the application status to CONVERTED.
    """

    def post(self, request, app_id: str):
        application = get_object_or_404(
            Application, application_id=(app_id or "").upper()
        )
        if application.status == Application.Status.CONVERTED:
            messages.info(
                request,
                "This application has already been converted to a student.",
            )
            return redirect(
                "application_review_detail", app_id=application.application_id
            )
        try:
            student = convert_application_to_student(application, request=request)
        except ApplicationConversionError as exc:
            messages.error(request, str(exc))
            return redirect(
                "application_review_detail", app_id=application.application_id
            )
        except Exception:  # pragma: no cover - defensive
            logger.exception(
                "Failed to convert application %s", application.application_id
            )
            messages.error(
                request,
                "Something went wrong converting this application. "
                "Please check the data and try again.",
            )
            return redirect(
                "application_review_detail", app_id=application.application_id
            )
        messages.success(
            request,
            f"Converted application {application.application_id} into "
            f"student “{student}” enrolled in {application.program}.",
        )
        return redirect("application_review_detail", app_id=application.application_id)
