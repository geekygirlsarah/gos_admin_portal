import datetime
import mimetypes
import os
from decimal import ROUND_HALF_DOWN, Decimal, InvalidOperation

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.mixins import (
    LoginRequiredMixin,
    PermissionRequiredMixin,
)
from django.core.mail import EmailMultiAlternatives, get_connection
from django.db.models import Value
from django.db.models.functions import Coalesce, Lower, NullIf
from django.http import FileResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone
from django.utils.html import strip_tags
from django.views.generic import CreateView, ListView, UpdateView, View
from premailer import transform

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
    can_user_read,
    get_user_role,
    user_is_parent,
)
from ..utils import (
    get_student_balance_data,
    get_student_program_balance,
    redirect_back,
)
from .mixins import (
    DynamicReadPermissionMixin,
    DynamicWritePermissionMixin,
    LogFormSaveMixin,
    forms_logger,
    logger,
)


class ProgramFeeSelectView(LoginRequiredMixin, LeadMentorRequiredMixin, View):
    template_name = "programs/fee_select.html"

    def dispatch(self, request, *args, **kwargs):
        self.program = get_object_or_404(Program, pk=kwargs["pk"])
        return super().dispatch(request, *args, **kwargs)

    def get(self, request, pk):
        fees = Fee.objects.filter(program=self.program).order_by("name")
        return render(
            request, self.template_name, {"program": self.program, "fees": fees}
        )


class ProgramFeeAssignmentEditView(
    LoginRequiredMixin, DynamicWritePermissionMixin, View
):
    template_name = "programs/fee_assignment_form.html"
    section = "fees"

    def dispatch(self, request, *args, **kwargs):
        self.program = get_object_or_404(Program, pk=kwargs["pk"])
        self.fee = get_object_or_404(Fee, pk=kwargs["fee_id"], program=self.program)
        return super().dispatch(request, *args, **kwargs)

    def get(self, request, pk, fee_id):
        form = FeeAssignmentEditForm(program=self.program, fee=self.fee)
        return render(
            request,
            self.template_name,
            {"program": self.program, "fee": self.fee, "form": form},
        )

    def post(self, request, pk, fee_id):
        form = FeeAssignmentEditForm(request.POST, program=self.program, fee=self.fee)
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
    PermissionRequiredMixin,
    DynamicWritePermissionMixin,
    CreateView,
):
    permission_required = "programs.add_fee"
    model = Fee
    form_class = FeeForm
    template_name = "programs/fee_form.html"
    section = "fees"

    def dispatch(self, request, *args, **kwargs):
        self.program = get_object_or_404(Program, pk=kwargs["pk"])
        return super().dispatch(request, *args, **kwargs)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["program"] = self.program
        return kwargs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)

        ctx["role"] = get_user_role(self.request.user)
        ctx["program"] = self.program
        ctx["is_create"] = True
        return ctx

    def form_valid(self, form):
        messages.success(self.request, "Fee created.")
        return super().form_valid(form)

    def get_success_url(self):
        return reverse(
            "program_fee_assignments",
            kwargs={"pk": self.program.pk, "fee_id": self.object.pk},
        )


class ProgramFeeUpdateView(
    LogFormSaveMixin,
    LoginRequiredMixin,
    PermissionRequiredMixin,
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
        self.program = get_object_or_404(Program, pk=kwargs["pk"])
        return super().dispatch(request, *args, **kwargs)

    def get_object(self, queryset=None):
        return get_object_or_404(Fee, pk=self.kwargs["fee_id"], program=self.program)

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
        messages.success(self.request, "Fee updated.")
        return super().form_valid(form)

    def get_success_url(self):
        return reverse(
            "program_fee_assignments",
            kwargs={"pk": self.program.pk, "fee_id": self.object.pk},
        )


class ProgramPaymentCreateView(
    LogFormSaveMixin,
    LoginRequiredMixin,
    DynamicWritePermissionMixin,
    CreateView,
):
    model = Payment
    form_class = PaymentForm
    template_name = "programs/payment_form.html"
    section = "payments"

    def dispatch(self, request, *args, **kwargs):
        self.program = get_object_or_404(Program, pk=kwargs["pk"])
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

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["settings_obj"] = SlidingScaleSettings.get_solo()
        return ctx

    def form_valid(self, form):
        # The sliding scale is no longer tied to a single program — it applies
        # across all of the student's programs — so we simply create the record.
        obj = form.save(commit=False)
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
        return get_object_or_404(SlidingScale, pk=self.kwargs["sliding_id"])

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["settings_obj"] = SlidingScaleSettings.get_solo()
        return ctx

    def form_valid(self, form):
        obj = form.save(commit=False)
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


class SlidingScaleTaxFormDeleteView(
    LoginRequiredMixin, DynamicWritePermissionMixin, View
):
    section = "sliding_scale"
    permission_required = "programs.change_slidingscale"

    def test_func(self):
        # Allow users with change_slidingscale permission in addition to LeadMentors
        if self.request.user.has_perm("programs.change_slidingscale"):
            return True
        return super().test_func()

    def post(self, request, sliding_id, form_id):
        tax_form = get_object_or_404(
            TaxForm,
            pk=form_id,
            sliding_scale_id=sliding_id,
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
        # Allow users with change_slidingscale permission in addition to LeadMentors
        if self.request.user.has_perm("programs.change_slidingscale"):
            return True
        return super().test_func()

    def get(self, request, sliding_id, form_id):
        tax_form = get_object_or_404(TaxForm, pk=form_id, sliding_scale_id=sliding_id)
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
        return (
            SlidingScale.objects.filter(status=SlidingScale.STATUS_PENDING)
            .select_related("student", "applied_by")
            .order_by("created_at")
        )

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["decided_applications"] = (
            SlidingScale.objects.exclude(status=SlidingScale.STATUS_PENDING)
            .select_related("student", "applied_by", "reviewed_by")
            .order_by("-reviewed_at", "-updated_at")[:25]
        )
        return ctx


class SlidingScaleReviewDecideView(LoginRequiredMixin, LeadMentorRequiredMixin, View):
    """Approve or decline a pending sliding scale application."""

    template_name = "programs/sliding_scale_review_detail.html"

    def get_object(self):
        return get_object_or_404(SlidingScale, pk=self.kwargs["pk"])

    def get(self, request, pk):
        application = self.get_object()
        settings_obj = SlidingScaleSettings.get_solo()
        suggested_percent = settings_obj.compute_discount_percent(
            application.family_size, application.adjusted_gross_income
        )
        return render(
            request,
            self.template_name,
            {
                "application": application,
                "suggested_percent": suggested_percent,
                "settings_obj": settings_obj,
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
            application.date = date_val or datetime.date.today()
            application.expiration_date = expiration_date
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
        if not user_is_parent(request.user):
            messages.error(request, "Only a parent can apply for the sliding scale.")
            return None, redirect("parent_payments")

        student = get_object_or_404(Student, pk=student_id)
        try:
            adult = request.user.adult_profile
            if not adult.is_parent or student not in adult.students.all():
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

        existing_pending = SlidingScale.objects.filter(
            student=student, status=SlidingScale.STATUS_PENDING
        ).exists()
        if existing_pending:
            messages.info(
                request,
                f"{student} already has a sliding scale application pending review.",
            )
            return redirect("parent_payments")

        form = SlidingScaleApplicationForm()
        settings_obj = SlidingScaleSettings.get_solo()
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
            settings_obj = SlidingScaleSettings.get_solo()
            return render(
                request,
                self.template_name,
                {"student": student, "form": form, "settings_obj": settings_obj},
            )

        adult = request.user.adult_profile
        application = SlidingScale.objects.create(
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
                TaxForm.objects.create(sliding_scale=application, file=f)

        messages.success(
            request,
            f"Sliding scale application submitted for {student}. A Lead Mentor will review it soon.",
        )
        return redirect("parent_payments")


class SlidingScaleWithdrawView(LoginRequiredMixin, View):
    """Allows a Parent to withdraw their own pending sliding scale application
    (e.g. to correct a mistake and reapply)."""

    def post(self, request, pk):
        application = get_object_or_404(
            SlidingScale, pk=pk, status=SlidingScale.STATUS_PENDING
        )
        student = application.student

        if not user_is_parent(request.user):
            messages.error(
                request, "Only a parent can withdraw a sliding scale application."
            )
            return redirect("parent_payments")

        try:
            adult = request.user.adult_profile
            if not adult.is_parent or student not in adult.students.all():
                raise Adult.DoesNotExist
        except (Adult.DoesNotExist, AttributeError):
            messages.error(
                request,
                "You do not have permission to withdraw this application.",
            )
            return redirect("parent_payments")

        for tax_form in application.tax_forms.all():
            tax_form.file.delete(save=False)
            tax_form.delete()
        application.delete()

        messages.success(
            request,
            f"Sliding scale application for {student} has been withdrawn. You may submit a new one at any time.",
        )
        return redirect("parent_payments")


class ProgramStudentBalanceView(LoginRequiredMixin, DynamicReadPermissionMixin, View):
    section = "payments"

    def get(self, request, pk, student_id):
        program = get_object_or_404(Program, pk=pk)
        student = get_object_or_404(Student, pk=student_id)

        # Object level check for Parents (including parents who also mentor)
        if user_is_parent(request.user):
            try:
                adult = request.user.adult_profile
                if student not in adult.students.all():
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
        # Ensure enrollment
        if not Enrollment.objects.filter(student=student, program=program).exists():
            messages.error(request, f"{student} is not enrolled in {program}.")
            return redirect("program_detail", pk=program.pk)

        can_view_sliding = can_user_read(request.user, "sliding_scale")
        balance_data = get_student_balance_data(
            student, program, can_view_sliding=can_view_sliding
        )

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
            },
        )


class ProgramStudentBalancePrintView(
    LoginRequiredMixin, DynamicReadPermissionMixin, View
):
    section = "payments"

    def get_object(self):
        return get_object_or_404(Student, pk=self.kwargs["student_id"])

    def get(self, request, pk, student_id):
        program = get_object_or_404(Program, pk=pk)
        student = get_object_or_404(Student, pk=student_id)

        # Object level check for Parents (including parents who also mentor)
        if user_is_parent(request.user):
            try:
                adult = request.user.adult_profile
                if student not in adult.students.all():
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
        # Ensure enrollment
        if not Enrollment.objects.filter(student=student, program=program).exists():
            messages.error(request, f"{student} is not enrolled in {program}.")
            return redirect("program_detail", pk=program.pk)

        can_view_sliding = can_user_read(request.user, "sliding_scale")
        balance_data = get_student_balance_data(
            student, program, can_view_sliding=can_view_sliding
        )

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
            },
        )


class ProgramEmailBalancesView(LoginRequiredMixin, LeadMentorRequiredMixin, View):
    template_name = "programs/email_balances_form.html"

    def get(self, request, pk):
        program = get_object_or_404(Program, pk=pk)
        form = ProgramEmailBalancesForm(program=program)
        return self._render(request, form, program)

    def post(self, request, pk):
        program = get_object_or_404(Program, pk=pk)
        form = ProgramEmailBalancesForm(request.POST, program=program)
        if not form.is_valid():
            return self._render(request, form, program)

        subject = form.cleaned_data["subject"]
        default_message = form.cleaned_data.get("default_message") or ""
        recipient_filter = form.cleaned_data.get("recipient_filter")
        selected_student = form.cleaned_data.get("student")
        test_email = form.cleaned_data.get("test_email")

        # Build sender connection (reuse logic from ProgramEmailView)
        selected = form.cleaned_data.get("from_account")
        accounts = getattr(settings, "EMAIL_SENDER_ACCOUNTS", []) or []
        acc = None
        if accounts and selected and selected != "DEFAULT":
            for a in accounts:
                key = a.get("key") or a.get("email")
                if key == selected:
                    acc = a
                    break
        conn_kwargs = {
            "backend": getattr(
                settings, "EMAIL_BACKEND", "django.core.mail.backends.smtp.EmailBackend"
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
            # Include display_name name if provided
            display_name = acc.get("display_name")
            if display_name:
                from_email = f'"{display_name}" <{from_email}>'
        else:
            conn_kwargs.update(
                {
                    "username": getattr(settings, "EMAIL_HOST_USER", ""),
                    "password": getattr(settings, "EMAIL_HOST_PASSWORD", ""),
                }
            )
            from_email = getattr(settings, "DEFAULT_FROM_EMAIL", "no-reply@example.com")
            # Include sender name from settings if available
            sender_name = getattr(settings, "DEFAULT_FROM_NAME", None)
            if sender_name:
                from_email = f'"{sender_name}" <{from_email}>'
        connection = get_connection(**conn_kwargs)

        # Collect students enrolled in program
        students_qs = Student.objects.filter(
            enrollment__program=program
        ).select_related("school")
        if recipient_filter == "individual" and selected_student:
            students_qs = students_qs.filter(pk=selected_student.pk)

        students = students_qs.order_by(
            Lower(Coalesce(NullIf("first_name", Value("")), "legal_first_name")),
            Lower("last_name"),
        )

        can_view_sliding = can_user_read(self.request.user, "sliding_scale")

        # Build list of targets with non-empty recipient emails
        targets = []
        for s in students:
            balance_data = get_student_balance_data(
                s, program, can_view_sliding=can_view_sliding
            )
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

            # Gather recipient emails: only parents/guardians who opted in for updates
            emails = []
            for adult in s.all_parents:
                # Only include parents/guardians who have opted into email updates and are active
                if getattr(adult, "email_updates", False) and getattr(
                    adult, "login_enabled", True
                ):
                    email = adult.personal_email or adult.andrew_email
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

        sent_total = 0
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
            }
            # Include optional rich-text message inside the template so styles apply correctly
            ctx["message_html"] = default_message or ""
            balance_html = render_to_string(
                "programs/balance_sheet_email.html", ctx, request=None
            )
            full_html = balance_html
            try:
                inlined_html = transform(full_html)
            except Exception:
                inlined_html = full_html
            text_body = strip_tags(inlined_html)

            # Ensure dest is a list of flat email strings
            if isinstance(dest, str):
                to_list = [dest]
            else:
                to_list = list(dest)
            # Normalize: strip and drop empties/None
            to_list = [str(e).strip() for e in to_list if e and str(e).strip()]
            if not to_list:
                logger.warning(
                    "ProgramEmailBalances: no valid recipient emails for %s; skipping",
                    data["student"],
                )
                continue

            # Place all adult emails in To; only archive address in BCC
            to_addr = to_list
            bcc = ["swithee@andrew.cmu.edu"]

            email = EmailMultiAlternatives(
                subject=subject,
                body=text_body,
                from_email=from_email,
                to=to_addr,
                bcc=bcc,
                connection=connection,
            )
            email.attach_alternative(inlined_html, "text/html")
            try:
                sent = email.send(fail_silently=False)
                sent_total += sent
            except Exception as e:
                logger.error(
                    "ProgramEmailBalances: send failed for %s | error=%s",
                    data["student"],
                    e,
                    exc_info=True,
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
    Lists all students enrolled in a specific program and the total amount each currently owes
    for that program, using the same balance computation as the per-program balance sheet.
    """

    template_name = "programs/dues_owed.html"
    section = "programs"

    def _program_balance_for_student(self, student, program):
        can_view_sliding = can_user_read(self.request.user, "sliding_scale")
        return get_student_program_balance(
            student,
            program,
            can_view_sliding=can_view_sliding,
        )

    def get(self, request, pk):
        program = get_object_or_404(Program, pk=pk)
        # Fetch all enrollments for this program.
        enrollments = (
            Enrollment.objects.filter(program=program)
            .select_related("student", "student__school")
            .order_by(
                Lower(
                    Coalesce(
                        NullIf("student__first_name", Value("")),
                        "student__legal_first_name",
                    )
                ),
                Lower("student__last_name"),
            )
        )

        active_rows = []
        inactive_rows = []
        grand_total = 0
        filter_owed = request.GET.get("filter") == "owed"
        for e in enrollments:
            s = e.student
            balance_sum = self._program_balance_for_student(s, program)
            if filter_owed and balance_sum <= 0:
                continue

            row = {
                "student": s,
                "amount_owed": balance_sum,
            }

            # A student is inactive if their enrollment is marked inactive,
            # or if the student record itself is marked graduated.
            if not e.active or s.graduated:
                inactive_rows.append(row)
            else:
                active_rows.append(row)

            grand_total += balance_sum

        return render(
            request,
            self.template_name,
            {
                "program": program,
                "active_rows": active_rows,
                "inactive_rows": inactive_rows,
                "rows": active_rows + inactive_rows,
                "grand_total": grand_total,
                "filter_owed": filter_owed,
            },
        )
