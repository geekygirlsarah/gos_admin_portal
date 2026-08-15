"""Lead-mentor review pages for applications.

Provides custom views (list / detail / approve / decline / edit / delete)
gated by the ``applications.review_application`` permission. The
``LeadMentor`` group (unified in migration ``0011``) carries that permission.
"""

from __future__ import annotations

import json
import logging

from django import forms
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin, PermissionRequiredMixin
from django.core.mail import EmailMultiAlternatives, get_connection
from django.db import models
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.utils.html import strip_tags
from django.views import View
from premailer import transform

from audit.events import AuditEvent
from audit.service import log_event
from programs.models import Program

from ..models import Application
from ..services import (
    ApplicationConversionError,
    _collect_applicant_recipients,
    convert_application_to_student,
    get_primary_parent_email,
    send_application_approved_email,
    send_application_converted_email,
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


class ApplicationEmailForm(forms.Form):
    program = forms.ModelChoiceField(
        queryset=Program.objects.all(),
        required=False,
        widget=forms.Select(attrs={"class": "form-select"}),
        help_text="Select the program whose applicants you want to email. Leave blank for all programs.",
    )
    statuses = forms.MultipleChoiceField(
        required=True,
        choices=Application.Status.choices,
        widget=forms.CheckboxSelectMultiple(attrs={"class": "form-check-input"}),
        help_text="Choose one or more application statuses to email.",
    )
    subject = forms.CharField(
        max_length=255, widget=forms.TextInput(attrs={"class": "form-control"})
    )
    body = forms.CharField(
        widget=forms.Textarea(attrs={"rows": 12, "class": "form-control"}),
        help_text="Rich text is supported. Paste content or use the editor.",
    )
    test_email = forms.EmailField(
        required=False,
        widget=forms.EmailInput(attrs={"class": "form-control"}),
        help_text="Optional: send only to this address for testing.",
    )
    from_account = forms.ChoiceField(
        required=False, widget=forms.Select(attrs={"class": "form-select"})
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Build sender choices from settings
        accounts = getattr(settings, "EMAIL_SENDER_ACCOUNTS", []) or []
        choices = []
        initial_value = None
        if accounts:
            for acc in accounts:
                email = acc.get("email") or ""
                display = acc.get("display_name") or email or "Sender"
                value = acc.get("key") or email
                label = f"{display} <{email}>" if email else display
                choices.append((value, label))
            if choices:
                initial_value = choices[0][0]
        else:
            default_email = getattr(settings, "DEFAULT_FROM_EMAIL", "")
            default_name = getattr(settings, "DEFAULT_FROM_NAME", None)
            if default_name:
                label = (
                    f"Default ({default_name} <{default_email}>)"
                    if default_email
                    else f"Default ({default_name})"
                )
            else:
                label = f"Default ({default_email})" if default_email else "Default"
            choices = [("DEFAULT", label)]
            initial_value = "DEFAULT"

        self.fields["from_account"].choices = choices
        self.fields["from_account"].initial = initial_value


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
        open_only = (request.GET.get("open") or "").strip() == "1"

        valid_statuses = {c[0] for c in Application.Status.choices}
        if status and status in valid_statuses:
            qs = qs.filter(status=status)

        valid_types = {c[0] for c in Application.Type.choices}
        if applicant_type and applicant_type in valid_types:
            qs = qs.filter(applicant_type=applicant_type)

        if program_id.isdigit():
            qs = qs.filter(program_id=int(program_id))

        if open_only:
            today = timezone.now().date()
            qs = (
                qs.filter(program__active=True)
                .filter(
                    models.Q(program__applications_open__lte=today)
                    | models.Q(program__applications_open__isnull=True)
                )
                .filter(
                    models.Q(program__applications_close__gte=today)
                    | models.Q(program__applications_close__isnull=True)
                )
                .filter(
                    models.Q(program__end_date__gte=today)
                    | models.Q(program__end_date__isnull=True)
                )
            )

        # Sorting
        sort = (request.GET.get("sort") or "submitted").strip()
        direction = (request.GET.get("dir") or "desc").strip()

        sort_map = {
            "id": "application_id",
            "type": "applicant_type",
            "program": "program__name",
            "status": "status",
            "started": "created_at",
            "submitted": "submitted_at",
            "email": "email",
        }

        if sort in sort_map:
            order_by_val = sort_map[sort]
            if direction == "desc":
                if isinstance(order_by_val, str):
                    order_by_val = f"-{order_by_val}"
                else:
                    order_by_val = order_by_val.desc()
            qs = qs.order_by(order_by_val)

        # Grouping logic
        # Admin actions:
        # * Review to convert to student (all "App + Signed" statuses)
        # * Review to approve application (all "Submitted" statuses)
        # Applicant actions:
        # * Waiting on forms to be signed (all "App Approved" statuses)
        # * Waiting on Parent data (all "awaiting parent")
        # * Waiting on Student data (all "Email verified")
        # * No data yet (all "Draft" statuses)

        grouped = [
            {
                "title": "Admin actions: Review to convert to student",
                "statuses": [Application.Status.APPROVED_SIGNED],
                "apps": [],
            },
            {
                "title": "Admin actions: Review to approve application",
                "statuses": [Application.Status.SUBMITTED],
                "apps": [],
            },
            {
                "title": "Applicant actions: Waiting on forms to be signed",
                "statuses": [Application.Status.APPROVED],
                "apps": [],
            },
            {
                "title": "Applicant actions: Waiting on Parent data",
                "statuses": [Application.Status.AWAITING_PARENT],
                "apps": [],
            },
            {
                "title": "Applicant actions: Waiting on Student data",
                "statuses": [Application.Status.EMAIL_VERIFIED],
                "apps": [],
            },
            {
                "title": "Applicant actions: No data yet",
                "statuses": [Application.Status.DRAFT],
                "apps": [],
            },
            {
                "title": "Other (Converted / Declined)",
                "statuses": [Application.Status.CONVERTED, Application.Status.DECLINED],
                "apps": [],
            },
        ]

        invalid_group = {
            "title": "Closed or Ended Programs (Invalid)",
            "apps": [],
            "is_invalid": True,
        }

        # Partition applications into groups
        for app in qs:
            # Applications for closed/ended programs go into the special invalid group
            if app.program and app.program.applications_are_invalid:
                invalid_group["apps"].append(app)
                continue

            found = False
            for group in grouped:
                if app.status in group["statuses"]:
                    group["apps"].append(app)
                    found = True
                    break
            if not found:
                # Fallback group if we ever add a status forgot to categorize
                if not grouped or grouped[-1]["title"] != "Other":
                    grouped.append({"title": "Other", "statuses": [], "apps": []})
                grouped[-1]["apps"].append(app)

        # Add the invalid group at the end if it has apps
        if invalid_group["apps"]:
            grouped.append(invalid_group)

        # Remove empty groups if a filter is active
        if status or applicant_type or program_id or open_only:
            grouped = [g for g in grouped if g["apps"]]

        from programs.models import Program

        return render(
            request,
            self.template_name,
            {
                "grouped_applications": grouped,
                "status_choices": Application.Status.choices,
                "type_choices": Application.Type.choices,
                "programs": Program.objects.all().order_by("-active", "name"),
                "current_status": status,
                "current_type": applicant_type,
                "filter_program_id": program_id,
                "open_only": open_only,
                "current_sort": sort,
                "current_dir": direction,
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
            parent_email = get_primary_parent_email(application)
            if not parent_email:
                messages.error(
                    request,
                    "No parent email address found. "
                    "This application might not have reached the handoff step yet.",
                )
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
                messages.error(request, "This application has not been declined yet.")
            else:
                send_application_declined_email(
                    application, application.decline_reason, request=request
                )
                messages.success(request, "Resent decline email.")
        elif email_type == "converted":
            if application.status != Application.Status.CONVERTED:
                messages.error(request, "This application has not been converted yet.")
            else:
                send_application_converted_email(application, request=request)
                messages.success(request, "Resent conversion enrollment email.")
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
        if application.applicant_type == Application.Type.MENTOR:
            messages.success(
                request,
                f"Converted application {application.application_id} into "
                f"mentor “{student}”.",
            )
        else:
            messages.success(
                request,
                f"Converted application {application.application_id} into "
                f"student “{student}” enrolled in {application.program}.",
            )
        return redirect("application_review_detail", app_id=application.application_id)


class ApplicationEmailView(_ReviewerRequiredMixin, View):
    """Bulk email messaging for applicants by status and program."""

    template_name = "applications/review/email_form.html"

    def get(self, request):
        form = ApplicationEmailForm()
        return self._render(form)

    def post(self, request):
        form = ApplicationEmailForm(request.POST)
        if form.is_valid():
            prog = form.cleaned_data.get("program")
            statuses = form.cleaned_data["statuses"]
            subject = form.cleaned_data["subject"]
            html_body = form.cleaned_data["body"]

            # Inline CSS for better email client compatibility
            try:
                inlined_html_body = transform(html_body)
            except Exception:
                inlined_html_body = html_body
            text_body = strip_tags(inlined_html_body)
            test_email = form.cleaned_data.get("test_email")

            apps = Application.objects.filter(status__in=statuses)
            if prog:
                apps = apps.filter(program=prog)

            recipients = set()
            for app in apps:
                for addr in _collect_applicant_recipients(app):
                    recipients.add(addr)

            if not recipients and not test_email:
                messages.error(
                    request, "No recipients found for the selected criteria."
                )
                return self._render(form)

            to_send = [test_email] if test_email else sorted(recipients)

            # Determine sender account and SMTP credentials
            selected = form.cleaned_data.get("from_account")
            accounts = getattr(settings, "EMAIL_SENDER_ACCOUNTS", []) or []
            acc = None
            if accounts and selected and selected != "DEFAULT":
                # Match by key or email value
                for a in accounts:
                    key = a.get("key") or a.get("email")
                    if key == selected:
                        acc = a
                        break

            # Build SMTP connection using selected account credentials if provided
            conn_kwargs = {
                "backend": getattr(
                    settings,
                    "EMAIL_BACKEND",
                    "django.core.mail.backends.smtp.EmailBackend",
                ),
                "host": getattr(settings, "EMAIL_HOST", ""),
                "port": getattr(settings, "EMAIL_PORT", 465),
                "use_tls": getattr(settings, "EMAIL_USE_TLS", False),
                "use_ssl": getattr(settings, "EMAIL_USE_SSL", True),
                "timeout": getattr(settings, "EMAIL_TIMEOUT", 10),
            }
            if acc:
                conn_kwargs.update(
                    {
                        "username": acc.get("username") or "",
                        "password": acc.get("password") or "",
                    }
                )
                from_email = acc.get("email") or getattr(
                    settings, "DEFAULT_FROM_EMAIL", "no-reply@example.com"
                )
                # Include display_name if provided
                display_name = acc.get("display_name")
                if display_name:
                    from_email = f'"{display_name}" <{from_email}>'
            else:
                # Fall back to global credentials and default from address
                conn_kwargs.update(
                    {
                        "username": getattr(settings, "EMAIL_HOST_USER", ""),
                        "password": getattr(settings, "EMAIL_HOST_PASSWORD", ""),
                    }
                )
                from_email = getattr(
                    settings, "DEFAULT_FROM_EMAIL", "no-reply@example.com"
                )

            try:
                with get_connection(**conn_kwargs) as connection:
                    for addr in to_send:
                        msg = EmailMultiAlternatives(
                            subject=subject,
                            body=text_body,
                            from_email=from_email,
                            to=[addr],
                            connection=connection,
                        )
                        msg.attach_alternative(inlined_html_body, "text/html")
                        msg.send()

                messages.success(
                    request,
                    f"Successfully sent email to {len(to_send)} recipient(s).",
                )
                return redirect("application_review_list")
            except Exception as e:
                logger.exception("Failed to send bulk application email")
                messages.error(request, f"Failed to send email: {e}")

        return self._render(form)

    def _render(self, form):
        return render(self.request, self.template_name, {"form": form})
