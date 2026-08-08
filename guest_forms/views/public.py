"""Public views for guest forms (accessible without login)."""

from __future__ import annotations

from django.shortcuts import get_object_or_404, redirect, render
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.cache import never_cache

from guest_forms.forms import GuestFormSubmissionForm
from guest_forms.models import GuestForm


@method_decorator(never_cache, name="dispatch")
class GuestFormIndexView(View):
    """Display list of all active guest forms."""

    template_name = "guest_forms/index.html"

    def get(self, request):
        forms = GuestForm.objects.filter(is_active=True).order_by(
            "display_order", "name"
        )
        ctx = {"forms": forms}
        return render(request, self.template_name, ctx)


@method_decorator(never_cache, name="dispatch")
class GuestFormDetailView(View):
    """Display a single guest form with download link and submission form."""

    template_name = "guest_forms/detail.html"

    def get(self, request, slug):
        guest_form = get_object_or_404(GuestForm, slug=slug, is_active=True)
        form = GuestFormSubmissionForm(guest_form=guest_form)

        ctx = {
            "guest_form": guest_form,
            "form": form,
        }
        return render(request, self.template_name, ctx)

    def post(self, request, slug):
        guest_form = get_object_or_404(GuestForm, slug=slug, is_active=True)
        form = GuestFormSubmissionForm(
            request.POST, request.FILES, guest_form=guest_form
        )

        if form.is_valid():
            submission = form.save(commit=False)
            submission.guest_form = guest_form
            # Get client IP
            x_forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR")
            if x_forwarded_for:
                submission.submitted_ip = x_forwarded_for.split(",")[0].strip()
            else:
                submission.submitted_ip = request.META.get("REMOTE_ADDR")
            submission.save()

            return redirect("guest_form_submitted", slug=guest_form.slug)

        ctx = {
            "guest_form": guest_form,
            "form": form,
        }
        return render(request, self.template_name, ctx)


@method_decorator(never_cache, name="dispatch")
class GuestFormSubmittedView(View):
    """Confirmation page after successful submission."""

    template_name = "guest_forms/submitted.html"

    def get(self, request, slug):
        guest_form = get_object_or_404(GuestForm, slug=slug, is_active=True)
        return render(request, self.template_name, {"guest_form": guest_form})
