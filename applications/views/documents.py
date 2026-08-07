"""Step 10: post-approval signed-document download + upload."""

from __future__ import annotations

from django.contrib import messages
from django.shortcuts import redirect, render
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.cache import never_cache

from programs.models import ProgramDocument

from ..forms import DocumentSubmissionForm
from ..models import Application, ApplicationDocumentSubmission
from .utils import (
    TOTAL_STEPS,
    _get_application_or_404,
    _is_mentor,
    _redirect_to_current_step,
)


@method_decorator(never_cache, name="dispatch")
class Step10DocumentsView(View):
    template_name = "applications/step10_documents.html"

    def get(self, request, app_id: str):
        application = _get_application_or_404(app_id)
        guard = self._gate(application)
        if guard is not None:
            return guard
        return self._render(request, application, DocumentSubmissionForm())

    def post(self, request, app_id: str):
        application = _get_application_or_404(app_id)
        guard = self._gate(application)
        if guard is not None:
            return guard

        # Identify which ProgramDocument this upload is for. Must belong to
        # the application's program and be active.
        doc_id = request.POST.get("document_id")
        document = (
            ProgramDocument.objects.filter(
                pk=doc_id,
                program=application.program,
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

        submission, _created = ApplicationDocumentSubmission.objects.get_or_create(
            application=application, document=document
        )
        submission.file = form.cleaned_data["file"]
        submission.save()

        # If every required ProgramDocument now has a submission, promote
        # the application from APPROVED -> APPROVED_SIGNED so lead mentors
        # can see at a glance that the paperwork is in.
        if application.status == Application.Status.APPROVED:
            required_doc_ids = set(
                ProgramDocument.objects.filter(
                    program=application.program,
                    is_active=True,
                    is_required=True,
                ).values_list("pk", flat=True)
            )
            if required_doc_ids:
                uploaded_doc_ids = set(
                    ApplicationDocumentSubmission.objects.filter(
                        application=application,
                        document_id__in=required_doc_ids,
                    ).values_list("document_id", flat=True)
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
        documents = list(
            ProgramDocument.objects.filter(
                program=application.program, is_active=True
            ).order_by("display_order", "name")
        )
        submissions_by_doc = {
            s.document_id: s
            for s in ApplicationDocumentSubmission.objects.filter(
                application=application
            ).select_related("document")
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
