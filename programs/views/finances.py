import datetime
import mimetypes
import os
from collections import defaultdict
from decimal import ROUND_HALF_DOWN, Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin, PermissionRequiredMixin
from django.core.mail import EmailMultiAlternatives
from django.db import transaction
from django.db.models import F, Value
from django.db.models.functions import Coalesce, Lower, NullIf
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone
from django.views.generic import CreateView, DeleteView, ListView, UpdateView, View
from premailer import transform

from audit.mixins import AuditedDeleteMixin
from audit.service import log_deletion

from ..forms import (
    FeeAssignmentEditForm,
    FeeForm,
    PaymentForm,
    ProgramEmailBalancesForm,
    SlidingScaleApplicationForm,
    SlidingScaleForm,
)
from ..models import (
    Adult,
    AdultStudentRelationship,
    Enrollment,
    Fee,
    Payment,
    Program,
    SlidingScale,
    SlidingScaleSettings,
    Student,
    TaxForm,
)
from ..permission_views import (
    LeadMentorRequiredMixin,
    can_user_delete,
    can_user_read,
    get_user_role,
    user_is_parent,
)
from ..utils import (
    get_academic_year_ending,
    get_student_program_balance,
    redirect_back,
)
from ..utils.balance_email import (
    get_balance_email_from_address,
    get_balance_email_settings,
)
from ..utils.balances import (
    InconsistentBalanceStatementError,
    TenantBalanceStatementCalculator,
)
from ..utils.email_async import send_messages_in_background
from ..utils.email_html import extract_inline_images, html_to_text, normalize_email_html
from ..utils.notifications import get_sender_connection
from .mixins import (
    DynamicReadPermissionMixin,
    DynamicWritePermissionMixin,
    LogFormSaveMixin,
    TenantLeadMentorPermissionRequiredMixin,
    forms_logger,
    logger,
)


def _request_organization(request):
    organization = getattr(request, "organization", None)
    if organization is None:
        raise Http404
    return organization


def _tenant_program_student(request, program_id, student_id):
    organization = _request_organization(request)
    program = get_object_or_404(
        Program.objects.for_organization(organization),
        pk=program_id,
        organization=organization,
    )
    student = get_object_or_404(
        Student.objects.for_organization(organization),
        pk=student_id,
        organization=organization,
    )
    get_object_or_404(
        Enrollment.objects.for_organization(organization).filter(
            student__organization=organization,
            program__organization=organization,
        ),
        student=student,
        program=program,
    )
    return organization, program, student


class ProgramFeeSelectView(LoginRequiredMixin, LeadMentorRequiredMixin, View):
    template_name = "programs/fee_select.html"

    def dispatch(self, request, *args, **kwargs):
        organization = _request_organization(request)
        self.program = get_object_or_404(
            Program.objects.for_organization(organization),
            pk=kwargs["pk"],
            organization=organization,
        )
        return super().dispatch(request, *args, **kwargs)

    def get(self, request, pk):
        organization = _request_organization(request)
        fees = (
            Fee.objects.for_organization(organization)
            .filter(
                program=self.program,
                program__organization=organization,
            )
            .order_by("name")
        )
        return render(
            request, self.template_name, {"program": self.program, "fees": fees}
        )


class ProgramFeeAssignmentEditView(
    LoginRequiredMixin,
    TenantLeadMentorPermissionRequiredMixin,
    DynamicWritePermissionMixin,
    View,
):
    template_name = "programs/fee_assignment_form.html"
    section = "fees"
    permission_required = "programs.change_fee"

    def dispatch(self, request, *args, **kwargs):
        organization = _request_organization(request)
        self.program = get_object_or_404(
            Program.objects.for_organization(organization),
            pk=kwargs["pk"],
            organization=organization,
        )
        self.fee = get_object_or_404(
            Fee.objects.for_organization(organization).filter(
                program__organization=organization
            ),
            pk=kwargs["fee_id"],
            program=self.program,
        )
        return super().dispatch(request, *args, **kwargs)

    def get(self, request, pk, fee_id):
        form = FeeAssignmentEditForm(
            program=self.program, fee=self.fee, organization=request.organization
        )
        return render(
            request,
            self.template_name,
            {"program": self.program, "fee": self.fee, "form": form},
        )

    def post(self, request, pk, fee_id):
        form = FeeAssignmentEditForm(
            request.POST,
            program=self.program,
            fee=self.fee,
            organization=request.organization,
        )
        if form.is_valid():
            form.save()
            messages.success(request, "Fee applicability saved.")
            return redirect(
                "program_fee_assignments", pk=self.program.pk, fee_id=self.fee.pk
            )
        return render(
            request,
            self.template_name,
            {"program": self.program, "fee": self.fee, "form": form},
        )


class ProgramFeeCreateView(
    LogFormSaveMixin,
    LoginRequiredMixin,
    TenantLeadMentorPermissionRequiredMixin,
    DynamicWritePermissionMixin,
    CreateView,
):
    permission_required = "programs.add_fee"
    model = Fee
    form_class = FeeForm
    template_name = "programs/fee_form.html"
    section = "fees"

    def dispatch(self, request, *args, **kwargs):
        organization = _request_organization(request)
        self.program = get_object_or_404(
            Program.objects.for_organization(organization),
            pk=kwargs["pk"],
            organization=organization,
        )
        return super().dispatch(request, *args, **kwargs)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["program"] = self.program
        return kwargs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)

        ctx["role"] = get_user_role(self.request.user, org=self.request.organization)
        ctx["program"] = self.program
        ctx["is_create"] = True
        return ctx

    def form_valid(self, form):
        form.instance.organization = self.program.organization
        form.instance.program = self.program
        # Save the fee and its student assignments without letting the Fee
        # post_save or FeeAssignment post_save signals email parents before
        # the assignments are in place (that would email everyone, then each
        # newly-assigned student again). Afterward, notify exactly the
        # applicable students' parents once.
        from .. import signals as _signals
        from ..models import FeeAssignment
        from ..signals import notify_fee_parents

        disconnected_added = _signals.post_save.disconnect(
            _signals.notify_parents_on_fee_added, sender=Fee
        )
        disconnected_assignment = _signals.post_save.disconnect(
            _signals.notify_parents_on_fee_assignment, sender=FeeAssignment
        )
        try:
            response = super().form_valid(form)
        finally:
            if disconnected_added:
                _signals.post_save.connect(
                    _signals.notify_parents_on_fee_added, sender=Fee
                )
            if disconnected_assignment:
                _signals.post_save.connect(
                    _signals.notify_parents_on_fee_assignment, sender=FeeAssignment
                )
        notify_fee_parents(form.instance)
        messages.success(
            self.request,
            f'Fee "{form.instance.name}" was created. Applicable parents were emailed.',
        )
        return response

    def get_success_url(self):
        return reverse("program_fee_select", kwargs={"pk": self.program.pk})


class ProgramFeeUpdateView(
    LogFormSaveMixin,
    LoginRequiredMixin,
    TenantLeadMentorPermissionRequiredMixin,
    DynamicWritePermissionMixin,
    UpdateView,
):
    permission_required = "programs.change_fee"
    model = Fee
    form_class = FeeForm
    template_name = "programs/fee_form.html"
    pk_url_kwarg = "fee_id"
    section = "fees"

    def dispatch(self, request, *args, **kwargs):
        organization = _request_organization(request)
        self.program = get_object_or_404(
            Program.objects.for_organization(organization),
            pk=kwargs["pk"],
            organization=organization,
        )
        return super().dispatch(request, *args, **kwargs)

    def get_object(self, queryset=None):
        organization = _request_organization(self.request)
        return get_object_or_404(
            Fee.objects.for_organization(organization).filter(
                program__organization=organization
            ),
            pk=self.kwargs["fee_id"],
            program=self.program,
        )

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["program"] = self.program
        return kwargs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["program"] = self.program
        ctx["is_create"] = False
        return ctx

    def form_valid(self, form):
        form.instance.organization = self.program.organization
        form.instance.program = self.program
        messages.success(self.request, "Fee updated.")
        return super().form_valid(form)

    def get_success_url(self):
        return reverse("program_fee_select", kwargs={"pk": self.program.pk})


class ProgramPaymentCreateView(
    LogFormSaveMixin,
    LoginRequiredMixin,
    TenantLeadMentorPermissionRequiredMixin,
    DynamicWritePermissionMixin,
    CreateView,
):
    permission_required = "programs.add_payment"
    model = Payment
    form_class = PaymentForm
    template_name = "programs/payment_form.html"
    section = "payments"

    def dispatch(self, request, *args, **kwargs):
        organization = _request_organization(request)
        self.program = get_object_or_404(
            Program.objects.for_organization(organization),
            pk=kwargs["pk"],
            organization=organization,
        )
        return super().dispatch(request, *args, **kwargs)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["program"] = self.program
        return kwargs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["program"] = self.program
        return ctx

    def form_valid(self, form):
        obj = form.save(commit=False)
        # Ensure program is set from the URL context
        obj.program = self.program
        obj.organization = self.program.organization
        obj.save()
        # Log creation with a concise summary and field values
        user = getattr(self.request, "user", None)
        user_repr = (
            f"{getattr(user, 'pk', 'anon')}:{getattr(user, 'username', 'anonymous')}"
            if getattr(user, "is_authenticated", False)
            else "anonymous"
        )
        forms_logger.info(
            "FormSave: Payment[%s] create by %s | student=%s | program=%s | amount=%s | paid_via=%s | paid_on=%s",
            obj.pk,
            user_repr,
            self._fmt_val(getattr(obj, "student", None)),
            self._fmt_val(getattr(obj, "program", None)),
            self._fmt_val(getattr(obj, "amount", None)),
            self._fmt_val(getattr(obj, "paid_via", None)),
            self._fmt_val(getattr(obj, "paid_on", None)),
        )
        messages.success(self.request, "Payment recorded successfully.")
        return redirect("program_detail", pk=self.program.pk)


class ProgramPaymentUpdateView(
    LogFormSaveMixin,
    LoginRequiredMixin,
    TenantLeadMentorPermissionRequiredMixin,
    DynamicWritePermissionMixin,
    UpdateView,
):
    permission_required = "programs.change_payment"
    model = Payment
    form_class = PaymentForm
    template_name = "programs/payment_form.html"
    section = "payments"

    def dispatch(self, request, *args, **kwargs):
        organization = _request_organization(request)
        self.program = get_object_or_404(
            Program.objects.for_organization(organization),
            pk=kwargs["pk"],
            organization=organization,
        )
        return super().dispatch(request, *args, **kwargs)

    def get_object(self, queryset=None):
        organization = _request_organization(self.request)
        return get_object_or_404(
            Payment.objects.for_organization(organization).filter(
                program__organization=organization,
                student__organization=organization,
            ),
            pk=self.kwargs["payment_id"],
            program=self.program,
        )

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["program"] = self.program
        return kwargs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["program"] = self.program
        ctx["is_edit"] = True
        return ctx

    def form_valid(self, form):
        try:
            before = Payment.objects.for_organization(
                _request_organization(self.request)
            ).get(pk=self.object.pk)
        except Payment.DoesNotExist:
            before = None
        obj = form.save(commit=False)
        obj.program = self.program
        obj.organization = self.program.organization
        obj.save()
        user = getattr(self.request, "user", None)
        user_repr = (
            f"{getattr(user, 'pk', 'anon')}:{getattr(user, 'username', 'anonymous')}"
            if getattr(user, "is_authenticated", False)
            else "anonymous"
        )
        for f in getattr(form, "changed_data", []) or []:
            old = getattr(before, f, None) if before is not None else None
            new = getattr(obj, f, None)
            forms_logger.info(
                "FormSave: %s[%s] %s by %s | field=%s | from=%s | to=%s",
                "Payment",
                obj.pk,
                "update",
                user_repr,
                f,
                self._fmt_val(old),
                self._fmt_val(new),
            )
        messages.success(self.request, "Payment updated successfully.")
        return redirect("program_payment_detail", pk=self.program.pk, payment_id=obj.pk)


class ProgramPaymentDeleteView(
    LoginRequiredMixin,
    TenantLeadMentorPermissionRequiredMixin,
    DynamicWritePermissionMixin,
    AuditedDeleteMixin,
    DeleteView,
):
    model = Payment
    template_name = "programs/payment_confirm_delete.html"
    permission_required = "programs.delete_payment"
    section = "payments"

    def dispatch(self, request, *args, **kwargs):
        organization = _request_organization(request)
        self.program = get_object_or_404(
            Program.objects.for_organization(organization),
            pk=kwargs["pk"],
            organization=organization,
        )
        obj = self.get_object()
        if not can_user_delete(request.user, "payments", obj, org=organization):
            messages.error(
                request, "You do not have permission to delete this payment."
            )
            return redirect(
                "program_student_balance",
                pk=self.program.pk,
                student_id=obj.student_id,
            )
        return super().dispatch(request, *args, **kwargs)

    def get_object(self, queryset=None):
        organization = _request_organization(self.request)
        return get_object_or_404(
            Payment.objects.for_organization(organization).filter(
                program__organization=organization,
                student__organization=organization,
            ),
            pk=self.kwargs["payment_id"],
            program=self.program,
        )

    def audit_snapshot(self, obj):
        return {
            "payment_id": obj.pk,
            "student_id": obj.student_id,
            "student_name": str(obj.student),
            "program_id": obj.program_id,
            "program_name": str(obj.program),
            "amount": str(obj.amount),
            "paid_via": obj.paid_via,
            "paid_on": str(obj.paid_on),
            "check_number": obj.check_number,
            "notes": obj.notes,
        }

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["program"] = self.program
        ctx["payment"] = self.object
        ctx["student"] = self.object.student
        return ctx

    def form_valid(self, form):
        payment = self.get_object()
        student = payment.student
        amount = payment.amount
        self.student_id = payment.student_id
        response = super().form_valid(form)
        messages.success(
            self.request,
            f"Payment of ${amount:.2f} for {student} was deleted.",
        )
        return response

    def get_success_url(self):
        if hasattr(self, "student_id") and self.student_id:
            return reverse(
                "program_student_balance",
                kwargs={"pk": self.program.pk, "student_id": self.student_id},
            )
        return reverse("program_detail", kwargs={"pk": self.program.pk})


class ProgramPaymentDetailView(LoginRequiredMixin, DynamicReadPermissionMixin, View):
    section = "payments"

    def get_object(self):
        return get_object_or_404(Payment, pk=self.kwargs["payment_id"])

    def get(self, request, pk, payment_id):
        program = get_object_or_404(Program, pk=pk)
        payment = get_object_or_404(Payment, pk=payment_id)
        # Ensure payment belongs to this program
        if payment.program_id != program.id:
            messages.error(request, "Payment does not belong to this program.")
            return redirect("program_detail", pk=program.pk)
        student = payment.student
        # Ensure enrollment
        if not Enrollment.objects.filter(student=student, program=program).exists():
            messages.error(request, f"{student} is not enrolled in {program}.")
            return redirect("program_detail", pk=program.pk)
        return render(
            request,
            "programs/payment_detail.html",
            {
                "program": program,
                "student": student,
                "payment": payment,
            },
        )


class ProgramPaymentPrintView(LoginRequiredMixin, DynamicReadPermissionMixin, View):
    section = "payments"

    def get_object(self):
        return get_object_or_404(Payment, pk=self.kwargs["payment_id"])

    def get(self, request, pk, payment_id):
        program = get_object_or_404(Program, pk=pk)
        payment = get_object_or_404(Payment, pk=payment_id)
        if payment.program_id != program.id:
            messages.error(request, "Payment does not belong to this program.")
            return redirect("program_detail", pk=program.pk)
        student = payment.student
        if not Enrollment.objects.filter(student=student, program=program).exists():
            messages.error(request, f"{student} is not enrolled in {program}.")
            return redirect("program_detail", pk=program.pk)
        return render(
            request,
            "programs/payment_print.html",
            {
                "program": program,
                "student": student,
                "payment": payment,
            },
        )


class SlidingScaleCreateView(
    LogFormSaveMixin,
    LoginRequiredMixin,
    DynamicWritePermissionMixin,
    CreateView,
):
    model = SlidingScale
    form_class = SlidingScaleForm
    template_name = "programs/sliding_scale_form.html"
    section = "sliding_scale"

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["organization"] = getattr(self.request, "organization", None)
        return kwargs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["settings_obj"] = SlidingScaleSettings.get_solo(self.request.organization)
        return ctx

    def form_valid(self, form):
        # The sliding scale is no longer tied to a single program — it applies
        # across all of the student's programs — so we simply create the record.
        obj = form.save(commit=False)
        organization = getattr(self.request, "organization", None)
        if organization is None:
            raise Http404
        obj.organization = organization
        if (
            not obj.reviewed_by
            and getattr(self.request, "user", None)
            and self.request.user.is_authenticated
        ):
            obj.reviewed_by = self.request.user
            if not obj.reviewed_at:
                obj.reviewed_at = timezone.now()
        obj.save()
        # Log creation
        user = getattr(self.request, "user", None)
        user_repr = (
            f"{getattr(user, 'pk', 'anon')}:{getattr(user, 'username', 'anonymous')}"
            if getattr(user, "is_authenticated", False)
            else "anonymous"
        )
        forms_logger.info(
            "FormSave: SlidingScale[%s] create by %s | student=%s | percent=%s",
            obj.pk,
            user_repr,
            self._fmt_val(getattr(obj, "student", None)),
            self._fmt_val(getattr(obj, "percent", None)),
        )
        messages.success(self.request, "Sliding scale saved successfully.")
        return redirect("sliding_scale_review_list")


class SlidingScaleUpdateView(
    LogFormSaveMixin,
    LoginRequiredMixin,
    DynamicWritePermissionMixin,
    UpdateView,
):
    model = SlidingScale
    form_class = SlidingScaleForm
    template_name = "programs/sliding_scale_form.html"
    section = "sliding_scale"

    def get_object(self, queryset=None):
        organization = getattr(self.request, "organization", None)
        if organization is None:
            raise Http404
        return get_object_or_404(
            SlidingScale.objects.for_organization(organization).filter(
                student__organization=organization
            ),
            pk=self.kwargs["sliding_id"],
        )

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["organization"] = getattr(self.request, "organization", None)
        return kwargs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["settings_obj"] = SlidingScaleSettings.get_solo(self.request.organization)
        return ctx

    def form_valid(self, form):
        obj = form.save(commit=False)
        if (
            not obj.reviewed_by
            and getattr(self.request, "user", None)
            and self.request.user.is_authenticated
        ):
            obj.reviewed_by = self.request.user
            if not obj.reviewed_at:
                obj.reviewed_at = timezone.now()
        # Capture old values for changed fields before saving
        try:
            before = SlidingScale.objects.get(pk=obj.pk)
        except SlidingScale.DoesNotExist:
            before = None
        obj.save()
        # Log update with field-level changes when possible
        user = getattr(self.request, "user", None)
        user_repr = (
            f"{getattr(user, 'pk', 'anon')}:{getattr(user, 'username', 'anonymous')}"
            if getattr(user, "is_authenticated", False)
            else "anonymous"
        )
        for f in getattr(form, "changed_data", []) or []:
            old = getattr(before, f, None) if before is not None else None
            new = getattr(obj, f, None)
            forms_logger.info(
                "FormSave: %s[%s] %s by %s | field=%s | from=%s | to=%s",
                "SlidingScale",
                obj.pk,
                "update",
                user_repr,
                f,
                self._fmt_val(old),
                self._fmt_val(new),
            )
        messages.success(self.request, "Sliding scale updated successfully.")
        return redirect("sliding_scale_review_list")


class SlidingScaleTaxFormDeleteView(LoginRequiredMixin, LeadMentorRequiredMixin, View):

    def test_func(self):
        if getattr(self.request, "organization", None) is None:
            return False
        return super().test_func()

    def post(self, request, sliding_id, form_id):
        organization = _request_organization(request)
        tax_form = get_object_or_404(
            TaxForm.objects.for_organization(organization).filter(
                sliding_scale__organization=organization,
                sliding_scale__student__organization=organization,
            ),
            pk=form_id,
            sliding_scale_id=sliding_id,
        )
        log_deletion(
            resource=tax_form,
            request=request,
            before={
                "sliding_scale_id": sliding_id,
                "file_name": os.path.basename(tax_form.file.name or ""),
            },
            notes="Uploaded tax form deleted.",
        )
        tax_form.file.delete(save=False)
        tax_form.delete()
        messages.success(request, "Tax form deleted.")
        return redirect("sliding_scale_edit", sliding_id=sliding_id)


class SlidingScaleTaxFormViewView(LoginRequiredMixin, LeadMentorRequiredMixin, View):
    """Stream the *decrypted* contents of an uploaded tax form so a Lead
    Mentor can view it (e.g. a PDF or image rendered inline in the browser)
    or download it, without ever exposing the encrypted bytes on disk."""

    def test_func(self):
        if getattr(self.request, "organization", None) is None:
            return False
        return super().test_func()

    def get(self, request, sliding_id, form_id):
        organization = _request_organization(request)
        tax_form = get_object_or_404(
            TaxForm.objects.for_organization(organization).filter(
                sliding_scale__organization=organization,
                sliding_scale__student__organization=organization,
            ),
            pk=form_id,
            sliding_scale_id=sliding_id,
        )
        filename = os.path.basename(tax_form.file.name)
        content_type, _ = mimetypes.guess_type(filename)
        content_type = content_type or "application/octet-stream"
        disposition = "attachment" if request.GET.get("download") else "inline"
        response = FileResponse(
            tax_form.file.open("rb"),
            content_type=content_type,
        )
        response["Content-Disposition"] = f'{disposition}; filename="{filename}"'
        return response


class SlidingScaleReviewListView(LoginRequiredMixin, LeadMentorRequiredMixin, ListView):
    """Lead Mentor queue of sliding scale applications awaiting review."""

    model = SlidingScale
    template_name = "programs/sliding_scale_review_list.html"
    context_object_name = "applications"

    def get_queryset(self):
        organization = getattr(self.request, "organization", None)
        if organization is None:
            return SlidingScale.objects.none()
        return (
            SlidingScale.objects.for_organization(organization)
            .filter(
                organization_id=organization.pk,
                student__organization_id=organization.pk,
                status=SlidingScale.STATUS_PENDING,
            )
            .select_related("student", "applied_by")
            .order_by("created_at")
        )

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        today = timezone.localdate()
        organization = getattr(self.request, "organization", None)

        if organization is None:
            decided_qs = SlidingScale.objects.none()
        else:
            decided_qs = (
                SlidingScale.objects.for_organization(organization)
                .filter(
                    organization_id=organization.pk,
                    student__organization_id=organization.pk,
                )
                .exclude(status=SlidingScale.STATUS_PENDING)
                .select_related("student", "applied_by", "reviewed_by")
                .order_by(
                    F("date").desc(nulls_last=True),
                    "-created_at",
                )
            )

        active_applications = []
        past_applications = []

        for app in decided_qs:
            if app.status == SlidingScale.STATUS_APPROVED and (
                app.expiration_date is None or app.expiration_date >= today
            ):
                active_applications.append(app)
            else:
                past_applications.append(app)

        # Group past applications by school year (July–June), newest first
        past_grouped = {}
        for app in past_applications:
            dt = (
                app.expiration_date
                or app.date
                or (app.created_at.date() if app.created_at else today)
            )
            ending = get_academic_year_ending(dt)
            label = f"{ending - 1}-{ending}"
            past_grouped.setdefault(label, []).append(app)

        past_by_year = sorted(past_grouped.items(), key=lambda kv: kv[0], reverse=True)

        student_active_counts = {}
        for app in active_applications:
            if app.student_id:
                student_active_counts[app.student_id] = (
                    student_active_counts.get(app.student_id, 0) + 1
                )
        students_with_multiple_active = {
            s_id for s_id, count in student_active_counts.items() if count > 1
        }
        students_with_active = set(student_active_counts.keys())

        ctx["active_applications"] = active_applications
        ctx["past_applications_by_year"] = past_by_year
        ctx["decided_applications"] = active_applications
        ctx["students_with_multiple_active"] = students_with_multiple_active
        ctx["students_with_active"] = students_with_active
        return ctx


class SlidingScaleReviewDecideView(LoginRequiredMixin, LeadMentorRequiredMixin, View):
    """Approve or decline a pending sliding scale application."""

    template_name = "programs/sliding_scale_review_detail.html"

    def get_object(self):
        organization = getattr(self.request, "organization", None)
        if organization is None:
            raise Http404
        return get_object_or_404(
            SlidingScale.objects.for_organization(organization).filter(
                status=SlidingScale.STATUS_PENDING,
                student__organization=organization,
            ),
            pk=self.kwargs["pk"],
        )

    def get(self, request, pk):
        application = self.get_object()
        settings_obj = SlidingScaleSettings.get_solo(request.organization)
        suggested_percent = settings_obj.compute_discount_percent(
            application.family_size, application.adjusted_gross_income
        )
        existing_approved_sliding_scales = (
            SlidingScale.objects.for_organization(request.organization)
            .filter(
                student=application.student,
                student__organization=request.organization,
                status=SlidingScale.STATUS_APPROVED,
            )
            .exclude(pk=application.pk)
            .order_by(
                F("date").desc(nulls_last=True),
                "-created_at",
            )
        )
        return render(
            request,
            self.template_name,
            {
                "application": application,
                "suggested_percent": suggested_percent,
                "settings_obj": settings_obj,
                "existing_approved_sliding_scales": existing_approved_sliding_scales,
            },
        )

    def post(self, request, pk):
        application = self.get_object()
        action = request.POST.get("action")

        if action == "approve":
            percent = request.POST.get("percent")
            date_val = request.POST.get("date") or None
            expiration_date = request.POST.get("expiration_date") or None
            try:
                application.percent = Decimal(percent)
            except (TypeError, InvalidOperation):
                messages.error(request, "Please enter a valid discount percent.")
                return redirect("sliding_scale_review_decide", pk=pk)
            if application.percent < 0 or application.percent > 100:
                messages.error(request, "Percent must be between 0 and 100.")
                return redirect("sliding_scale_review_decide", pk=pk)
            application.date = date_val or application.date or datetime.date.today()
            application.expiration_date = (
                expiration_date
                if expiration_date is not None
                else application.expiration_date
            )
            application.status = SlidingScale.STATUS_APPROVED
            application.reviewed_by = request.user
            application.reviewed_at = timezone.now()
            application.save()
            messages.success(
                request, f"Sliding scale approved for {application.student}."
            )
        elif action == "decline":
            reason = (request.POST.get("decline_reason") or "").strip()
            if not reason:
                messages.error(
                    request, "Please provide a reason for declining this application."
                )
                return redirect("sliding_scale_review_decide", pk=pk)
            application.status = SlidingScale.STATUS_DECLINED
            application.decline_reason = reason
            application.reviewed_by = request.user
            application.reviewed_at = timezone.now()
            application.save()
            messages.success(
                request,
                f"Sliding scale application declined for {application.student}.",
            )
        else:
            messages.error(request, "Unknown action.")

        return redirect("sliding_scale_review_list")


class SlidingScaleApplyView(LoginRequiredMixin, View):
    """Parent-facing sliding scale application, reached from the Payments page.

    Only a Parent (not the Student, and not a Mentor) may apply, and only for
    their own linked student(s). The application applies across all of the
    student's programs, not just one.
    """

    template_name = "programs/sliding_scale_apply.html"

    def _get_student_for_parent(self, request, student_id):
        organization = getattr(request, "organization", None)
        if organization is None or not user_is_parent(request.user, org=organization):
            messages.error(request, "Only a parent can apply for the sliding scale.")
            return None, redirect("parent_payments")

        student = get_object_or_404(
            Student.objects.for_organization(organization).filter(
                organization_id=organization.pk
            ),
            pk=student_id,
        )
        try:
            adult = request.user.adult_profile
            if (
                not adult.is_parent
                or adult.organization_id != organization.pk
                or not can_user_read(
                    request.user, "student_info", student, org=organization
                )
            ):
                raise Adult.DoesNotExist
        except (Adult.DoesNotExist, AttributeError):
            messages.error(
                request, "You do not have permission to apply for this student."
            )
            return None, redirect("parent_payments")

        return student, None

    def get(self, request, student_id):
        student, error_redirect = self._get_student_for_parent(request, student_id)
        if error_redirect:
            return error_redirect

        existing_pending = (
            SlidingScale.objects.for_organization(request.organization)
            .filter(
                organization_id=request.organization.pk,
                student__organization_id=request.organization.pk,
                student=student,
                status=SlidingScale.STATUS_PENDING,
            )
            .exists()
        )
        if existing_pending:
            messages.info(
                request,
                f"{student} already has a sliding scale application pending review.",
            )
            return redirect("parent_payments")

        form = SlidingScaleApplicationForm()
        settings_obj = SlidingScaleSettings.get_solo(request.organization)
        return render(
            request,
            self.template_name,
            {"student": student, "form": form, "settings_obj": settings_obj},
        )

    def post(self, request, student_id):
        student, error_redirect = self._get_student_for_parent(request, student_id)
        if error_redirect:
            return error_redirect

        form = SlidingScaleApplicationForm(request.POST, request.FILES)
        if not form.is_valid():
            settings_obj = SlidingScaleSettings.get_solo(request.organization)
            return render(
                request,
                self.template_name,
                {"student": student, "form": form, "settings_obj": settings_obj},
            )

        adult = request.user.adult_profile
        application = SlidingScale.objects.create(
            organization=request.organization,
            student=student,
            family_size=form.cleaned_data["family_size"],
            adjusted_gross_income=form.cleaned_data["adjusted_gross_income"],
            status=SlidingScale.STATUS_PENDING,
            applied_by=adult,
            notes=form.cleaned_data.get("notes") or "",
        )

        documents = form.cleaned_data.get("documents")
        if documents:
            files = documents if isinstance(documents, list) else [documents]
            for f in files:
                TaxForm.objects.create(
                    organization=request.organization,
                    sliding_scale=application,
                    file=f,
                )

        messages.success(
            request,
            f"Sliding scale application submitted for {student}. A Lead Mentor will review it soon.",
        )
        return redirect("parent_payments")


class SlidingScaleWithdrawView(LoginRequiredMixin, View):
    """Allows a Parent to withdraw their own pending sliding scale application
    (e.g. to correct a mistake and reapply)."""

    def post(self, request, pk):
        organization = getattr(request, "organization", None)
        if organization is None or not user_is_parent(request.user, org=organization):
            messages.error(
                request, "Only a parent can withdraw a sliding scale application."
            )
            return redirect("parent_payments")

        with transaction.atomic():
            application = get_object_or_404(
                SlidingScale.objects.for_organization(organization)
                .select_for_update()
                .filter(
                    organization_id=organization.pk,
                    student__organization_id=organization.pk,
                    status=SlidingScale.STATUS_PENDING,
                ),
                pk=pk,
            )
            student = application.student

            try:
                adult = request.user.adult_profile
                if (
                    not adult.is_parent
                    or adult.organization_id != organization.pk
                    or not can_user_read(
                        request.user, "student_info", student, org=organization
                    )
                ):
                    raise Adult.DoesNotExist
            except (Adult.DoesNotExist, AttributeError):
                messages.error(
                    request,
                    "You do not have permission to withdraw this application.",
                )
                return redirect("parent_payments")

            tax_forms = TaxForm.objects.select_for_update().filter(
                sliding_scale=application
            )
            if tax_forms.exclude(organization_id=organization.pk).exists():
                messages.error(
                    request,
                    "This application cannot be withdrawn until its related documents are reviewed.",
                )
                return redirect("parent_payments")

            for tax_form in tax_forms.filter(organization_id=organization.pk):
                tax_form.file.delete(save=False)
                tax_form.delete()
            log_deletion(
                resource=application,
                request=request,
                before={
                    "student": str(student),
                    "status": application.status,
                    "percent": application.percent,
                },
                notes=f"Sliding scale application for {student} withdrawn by parent.",
            )
            application.delete()

        messages.success(
            request,
            f"Sliding scale application for {student} has been withdrawn. You may submit a new one at any time.",
        )
        return redirect("parent_payments")


class ProgramStudentBalanceView(LoginRequiredMixin, DynamicReadPermissionMixin, View):
    section = "payments"

    def get(self, request, pk, student_id):
        organization, program, student = _tenant_program_student(
            request, pk, student_id
        )

        # Object level check for Parents (including parents who also mentor)
        if user_is_parent(request.user, org=organization):
            try:
                adult = request.user.adult_profile
                has_local_relationship = (
                    adult.organization_id == organization.pk
                    and AdultStudentRelationship.objects.for_organization(organization)
                    .filter(
                        adult=adult,
                        student=student,
                        adult__organization=organization,
                        student__organization=organization,
                    )
                    .exists()
                )
                if not has_local_relationship:
                    messages.error(
                        request,
                        "You do not have permission to view this balance sheet.",
                    )
                    return redirect("home")
            except Exception:
                messages.error(
                    request, "You do not have permission to view this balance sheet."
                )
                return redirect("home")
        can_view_sliding = can_user_read(
            request.user, "sliding_scale", org=organization
        )
        calculator = TenantBalanceStatementCalculator(
            organization, [student], [program]
        )
        try:
            balance_data = calculator.get_balance_data(
                student, program, can_view_sliding=can_view_sliding
            )
        except InconsistentBalanceStatementError as exc:
            raise Http404("Balance data is unavailable for review.") from exc

        return render(
            request,
            "programs/balance_sheet.html",
            {
                "program": program,
                "student": student,
                "entries": balance_data["entries"],
                "total_fees": balance_data["total_fees"],
                "total_sliding": balance_data["total_sliding"],
                "total_payments": balance_data["total_payments"],
                "balance": balance_data["balance"],
                "sliding_scale": balance_data["sliding_scale"],
                "all_overlapping_sliding_scales": balance_data[
                    "all_overlapping_sliding_scales"
                ],
                "overlapping_sliding_count": balance_data["overlapping_sliding_count"],
            },
        )


class ProgramStudentBalancePrintView(
    LoginRequiredMixin, DynamicReadPermissionMixin, View
):
    section = "payments"

    def get_object(self):
        organization = _request_organization(self.request)
        return get_object_or_404(
            Student.objects.for_organization(organization),
            pk=self.kwargs["student_id"],
            organization=organization,
        )

    def get(self, request, pk, student_id):
        organization, program, student = _tenant_program_student(
            request, pk, student_id
        )

        # Object level check for Parents (including parents who also mentor)
        if user_is_parent(request.user, org=organization):
            try:
                adult = request.user.adult_profile
                has_local_relationship = (
                    adult.organization_id == organization.pk
                    and AdultStudentRelationship.objects.for_organization(organization)
                    .filter(
                        adult=adult,
                        student=student,
                        adult__organization=organization,
                        student__organization=organization,
                    )
                    .exists()
                )
                if not has_local_relationship:
                    messages.error(
                        request,
                        "You do not have permission to view this balance sheet.",
                    )
                    return redirect("home")
            except Exception:
                messages.error(
                    request, "You do not have permission to view this balance sheet."
                )
                return redirect("home")
        can_view_sliding = can_user_read(
            request.user, "sliding_scale", org=organization
        )
        calculator = TenantBalanceStatementCalculator(
            organization, [student], [program]
        )
        try:
            balance_data = calculator.get_balance_data(
                student, program, can_view_sliding=can_view_sliding
            )
        except InconsistentBalanceStatementError as exc:
            raise Http404("Balance data is unavailable for review.") from exc

        return render(
            request,
            "programs/balance_sheet_print.html",
            {
                "program": program,
                "student": student,
                "entries": balance_data["entries"],
                "total_fees": balance_data["total_fees"],
                "total_sliding": balance_data["total_sliding"],
                "total_payments": balance_data["total_payments"],
                "balance": balance_data["balance"],
                "sliding_scale": balance_data["sliding_scale"],
                "all_overlapping_sliding_scales": balance_data[
                    "all_overlapping_sliding_scales"
                ],
                "overlapping_sliding_count": balance_data["overlapping_sliding_count"],
            },
        )


class ProgramEmailBalancesView(LoginRequiredMixin, LeadMentorRequiredMixin, View):
    template_name = "programs/email_balances_form.html"

    def dispatch(self, request, *args, **kwargs):
        if getattr(request, "organization", None) is None:
            raise Http404
        return super().dispatch(request, *args, **kwargs)

    def get(self, request, pk):
        org = request.organization
        program = get_object_or_404(Program.objects.for_organization(org), pk=pk)
        form = ProgramEmailBalancesForm(
            program=program,
            organization=org,
            can_view_sliding=can_user_read(request.user, "sliding_scale", org=org),
        )
        return self._render(request, form, program)

    def post(self, request, pk):
        org = request.organization
        program = get_object_or_404(Program.objects.for_organization(org), pk=pk)
        can_view_sliding = can_user_read(request.user, "sliding_scale", org=org)
        form = ProgramEmailBalancesForm(
            request.POST,
            program=program,
            organization=org,
            can_view_sliding=can_view_sliding,
        )
        if not form.is_valid():
            return self._render(request, form, program)

        subject = form.cleaned_data["subject"]
        default_message = form.cleaned_data.get("default_message") or ""
        recipient_filter = form.cleaned_data.get("recipient_filter")
        selected_student = form.cleaned_data.get("student")
        test_email = form.cleaned_data.get("test_email")

        # Build sender connection (reuse logic from get_sender_connection)
        selected = form.cleaned_data.get("from_account")
        org_settings = get_balance_email_settings(org)
        connection, from_email, reply_to = get_sender_connection(
            selected, organization=org
        )
        if selected == "DEFAULT":
            from_email = get_balance_email_from_address(org_settings)

        students = list(form.statement_calculator.students.values())
        if recipient_filter == "individual" and selected_student:
            students = [
                student for student in students if student.pk == selected_student.pk
            ]

        relationships = defaultdict(list)
        for relationship in (
            AdultStudentRelationship.objects.for_organization(org)
            .filter(
                student_id__in=[student.pk for student in students],
                student__organization=org,
                adult__organization=org,
                adult__email_updates=True,
                adult__login_enabled=True,
            )
            .select_related("adult")
            .order_by("adult__last_name", "adult__legal_first_name", "pk")
        ):
            relationships[relationship.student_id].append(relationship)

        unavailable = False
        targets = []
        for s in students:
            try:
                balance_data = form.statement_calculator.get_balance_data(
                    s, program, can_view_sliding=can_view_sliding
                )
            except InconsistentBalanceStatementError:
                unavailable = True
                continue
            entries = balance_data["entries"]
            total_fees = balance_data["total_fees"]
            total_sliding = balance_data["total_sliding"]
            total_payments = balance_data["total_payments"]
            balance = balance_data["balance"]
            sliding = balance_data["sliding_scale"]

            # Apply recipient filters
            if recipient_filter == "non_zero" and balance == 0:
                continue
            if recipient_filter == "positive" and balance <= 0:
                continue

            # Preserve primary/secondary ordering among valid local relationships.
            emails = []
            student_relationships = relationships[s.pk]
            by_id = {
                relationship.pk: relationship for relationship in student_relationships
            }
            ordered_relationships = [
                by_id[relationship_id]
                for relationship_id in (
                    s.primary_contact_relationship_id,
                    s.secondary_contact_relationship_id,
                )
                if relationship_id in by_id
            ] + student_relationships
            for relationship in ordered_relationships:
                adult = relationship.adult
                email = adult.personal_email or adult.org_email
                if email:
                    emails.append(email)
            # Deduplicate while preserving order
            seen = set()
            deduped = []
            for e in emails:
                if e and e not in seen:
                    deduped.append(e)
                    seen.add(e)
            if not deduped:
                continue
            targets.append(
                {
                    "student": s,
                    "emails": deduped,
                    "entries": entries,
                    "total_fees": total_fees,
                    "total_sliding": total_sliding,
                    "total_payments": total_payments,
                    "balance": balance,
                    "sliding_scale": sliding,
                }
            )

        if unavailable:
            messages.error(
                request,
                "Some balance statements are unavailable because their records need review.",
            )
        if not targets and not test_email:
            messages.error(request, "No recipients found to email.")
            return self._render(request, form, program)

        # Prepare sending: if test, pick first student's content or a generic minimal body
        to_send = []
        if test_email:
            sample = targets[0] if targets else None
            if sample is None:
                messages.error(
                    request, "No sample data available to send a test email."
                )
                return self._render(request, form, program)
            to_send.append((test_email, sample))
        else:
            for t in targets:
                # send one email to combined recipients per student
                to_send.append((t["emails"], t))

        message_html = normalize_email_html(default_message or "")
        message_images = extract_inline_images(default_message or "")

        messages_to_send = []
        for dest, data in to_send:
            # Render balance sheet HTML
            ctx = {
                "program": program,
                "student": data["student"],
                "entries": data["entries"],
                "total_fees": data["total_fees"],
                "total_sliding": data["total_sliding"],
                "total_payments": data["total_payments"],
                "balance": data["balance"],
                "sliding_scale": data["sliding_scale"],
                "organization": org,
                "org_email_settings": org_settings,
            }
            # Include optional rich-text message inside the template so styles apply correctly
            ctx["message_html"] = message_html
            balance_html = render_to_string(
                "programs/balance_sheet_email.html", ctx, request=None
            )
            full_html = balance_html
            try:
                inlined_html = transform(full_html)
            except Exception:
                inlined_html = full_html
            text_body = html_to_text(inlined_html)

            # Ensure dest is a list of flat email strings
            if isinstance(dest, str):
                to_list = [dest]
            else:
                to_list = list(dest)
            # Normalize: strip and drop empties/None
            to_list = [str(e).strip() for e in to_list if e and str(e).strip()]
            if not to_list:
                logger.warning(
                    "ProgramEmailBalances: no valid recipient emails; skipping statement."
                )
                continue

            email = EmailMultiAlternatives(
                subject=subject,
                body=text_body,
                from_email=from_email,
                to=to_list,
                reply_to=[reply_to] if reply_to else None,
                connection=connection,
            )
            email.attach_alternative(inlined_html, "text/html")
            for inline_img in message_images:
                email.attach(inline_img.to_mime())
            from programs.utils.notifications import _mark_delivery_tracking

            _mark_delivery_tracking(
                email,
                org,
                enabled=not test_email,
                history_category="balance_statement",
                history_initiated_by=request.user,
                retain_history_body=True,
            )
            messages_to_send.append(email)

        try:
            send_messages_in_background(messages_to_send, connection=connection)
        except Exception as e:
            logger.error(
                "ProgramEmailBalances: background dispatch failed | error=%s",
                type(e).__name__,
            )

        if test_email:
            messages.success(request, f"Test email sent to {test_email}.")
        else:
            messages.success(
                request, f"Balance emails queued/sent for {len(to_send)} student(s)."
            )
        return redirect("program_dues_owed", pk=program.pk)

    def _render(self, request, form, program):
        return render(
            request,
            self.template_name,
            {
                "program": program,
                "form": form,
            },
        )


class ProgramDuesOwedView(LoginRequiredMixin, LeadMentorRequiredMixin, View):
    """
    Lists all students enrolled in a specific program with their dues owed and current balance,
    using the same balance computation as the per-program balance sheet.
    """

    template_name = "programs/dues_owed.html"
    section = "programs"

    def _program_balance_data_for_student(self, student, program, calculator):
        organization = _request_organization(self.request)
        can_view_sliding = can_user_read(
            self.request.user, "sliding_scale", org=organization
        )
        return calculator.get_balance_data(
            student, program, can_view_sliding=can_view_sliding
        )

    def get(self, request, pk):
        organization = _request_organization(request)
        program = get_object_or_404(
            Program.objects.for_organization(organization),
            pk=pk,
            organization=organization,
        )
        has_mismatched_enrollments = (
            Enrollment.objects.filter(program=program)
            .exclude(
                organization=organization,
                student__organization=organization,
            )
            .exists()
        )
        # Fetch all enrollments for this program.
        enrollments = (
            Enrollment.objects.for_organization(organization)
            .filter(
                program=program,
                student__organization=organization,
                program__organization=organization,
            )
            .select_related("student", "student__school")
            .order_by(
                Lower(
                    Coalesce(
                        NullIf("student__preferred_first_name", Value("")),
                        "student__legal_first_name",
                    )
                ),
                Lower("student__last_name"),
            )
        )
        calculator = TenantBalanceStatementCalculator(
            organization, [enrollment.student for enrollment in enrollments], [program]
        )

        active_rows = []
        inactive_rows = []
        total_dues = Decimal("0.00")
        total_balance = Decimal("0.00")
        unavailable_count = int(has_mismatched_enrollments)
        filter_owed = request.GET.get("filter") == "owed"
        for e in enrollments:
            s = e.student
            try:
                balance_data = self._program_balance_data_for_student(
                    s, program, calculator
                )
            except InconsistentBalanceStatementError:
                unavailable_count += 1
                row = {"student": s, "balance_unavailable": True}
                if not e.active or s.graduated:
                    inactive_rows.append(row)
                else:
                    active_rows.append(row)
                continue
            dues = balance_data["total_fees"] - balance_data["total_sliding"]
            balance = balance_data["balance"]
            if filter_owed and balance <= 0:
                continue

            row = {
                "student": s,
                "dues": dues,
                "dues_owed": dues,
                "balance": balance,
                "amount_owed": balance,
            }

            # A student is inactive if their enrollment is marked inactive,
            # or if the student record itself is marked graduated.
            if not e.active or s.graduated:
                inactive_rows.append(row)
            else:
                active_rows.append(row)
                total_dues += dues
                total_balance += balance

        return render(
            request,
            self.template_name,
            {
                "program": program,
                "active_rows": active_rows,
                "inactive_rows": inactive_rows,
                "rows": active_rows + inactive_rows,
                "total_dues": total_dues,
                "total_balance": total_balance,
                "grand_total": total_balance,
                "unavailable_count": unavailable_count,
                "totals_unavailable": unavailable_count > 0,
                "filter_owed": filter_owed,
            },
        )
