"""Lead mentor review views for guest form submissions."""

from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin, PermissionRequiredMixin
from django.db import transaction
from django.db.models import Count, Q
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.views import View

from audit.events import AuditEvent
from audit.service import log_event
from guest_forms.forms import GuestFormForm
from guest_forms.models import GuestForm, GuestFormSubmission
from programs.permission_views import user_is_lead_mentor


class GuestFormReviewRequiredMixin(LoginRequiredMixin, PermissionRequiredMixin):
    """Require a Lead Mentor in the request organization or a superuser."""

    raise_exception = False

    def has_permission(self):
        user = self.request.user
        organization = getattr(self.request, "organization", None)
        return user.is_superuser or (
            organization is not None and user_is_lead_mentor(user, org=organization)
        )


class GuestFormReviewListView(GuestFormReviewRequiredMixin, View):
    """List page for guest form submissions with filters."""

    template_name = "guest_forms/review/list.html"

    def get(self, request):
        org = getattr(request, "organization", None)
        qs = (
            GuestFormSubmission.objects.for_organization(org)
            .select_related("guest_form")
            .all()
        )
        if org is not None:
            qs = qs.filter(guest_form__organization=org)

        # Filters
        form_type = (request.GET.get("type") or "").strip()
        form_id = (request.GET.get("form") or "").strip()
        search = (request.GET.get("search") or "").strip()

        if form_type and form_type in {"student", "adult"}:
            qs = qs.filter(participant_type=form_type)

        if form_id.isdigit():
            qs = qs.filter(guest_form_id=int(form_id))

        if search:
            qs = qs.filter(
                Q(participant_first_name__icontains=search)
                | Q(participant_last_name__icontains=search)
                | Q(email__icontains=search)
                | Q(emergency_contact_name__icontains=search)
                | Q(guest_form__name__icontains=search)
            )

        # Sorting
        sort = (request.GET.get("sort") or "submitted_at").strip()
        direction = (request.GET.get("dir") or "desc").strip()

        sort_map = {
            "submitted_at": "submitted_at",
            "participant_name": "participant_first_name",
            "form_name": "guest_form__name",
            "email": "email",
        }
        sort_field = sort_map.get(sort, "submitted_at")
        if direction == "desc":
            sort_field = f"-{sort_field}"
        qs = qs.order_by(sort_field)

        # Get filter options
        forms = (
            GuestForm.objects.for_organization(org)
            .filter(is_active=True)
            .order_by("display_order", "name")
        )

        ctx = {
            "submissions": qs,
            "forms": forms,
            "current_type": form_type,
            "current_form": form_id,
            "current_sort": sort,
            "current_dir": direction,
            "search_query": search,
        }
        return render(request, self.template_name, ctx)


class GuestFormReviewDetailView(GuestFormReviewRequiredMixin, View):
    """Detail view for a single guest form submission."""

    template_name = "guest_forms/review/detail.html"

    def get(self, request, submission_id):
        org = getattr(request, "organization", None)
        submissions = GuestFormSubmission.objects.for_organization(org).select_related(
            "guest_form"
        )
        if org is not None:
            submissions = submissions.filter(guest_form__organization=org)
        submission = get_object_or_404(
            submissions,
            pk=submission_id,
        )
        ctx = {"submission": submission}
        return render(request, self.template_name, ctx)


class GuestFormManageListView(GuestFormReviewRequiredMixin, View):
    """List page for managing guest forms (create/edit/delete)."""

    template_name = "guest_forms/manage/list.html"

    def get(self, request):
        org = getattr(request, "organization", None)
        forms = GuestForm.objects.for_organization(org)
        if org is None:
            forms = forms.annotate(tenant_submission_count=Count("submissions"))
        else:
            forms = forms.annotate(
                tenant_submission_count=Count(
                    "submissions",
                    filter=Q(
                        submissions__organization=org,
                        submissions__guest_form__organization=org,
                    ),
                )
            )
        forms = forms.order_by("display_order", "name")
        ctx = {"forms": forms}
        return render(request, self.template_name, ctx)


class GuestFormCreateView(GuestFormReviewRequiredMixin, View):
    """Create a new guest form."""

    template_name = "guest_forms/manage/form.html"

    def get(self, request):
        form = GuestFormForm()
        ctx = {"form": form, "is_create": True}
        return render(request, self.template_name, ctx)

    def post(self, request):
        form = GuestFormForm(request.POST, request.FILES)
        if form.is_valid():
            form.save()
            messages.success(request, "Guest form created.")
            return redirect("guest_form_manage_list")
        ctx = {"form": form, "is_create": True}
        return render(request, self.template_name, ctx)


class GuestFormUpdateView(GuestFormReviewRequiredMixin, View):
    """Update an existing guest form."""

    template_name = "guest_forms/manage/form.html"

    def get(self, request, form_id):
        org = getattr(request, "organization", None)
        guest_form = get_object_or_404(
            GuestForm.objects.for_organization(org), pk=form_id
        )
        form = GuestFormForm(instance=guest_form)
        ctx = {"form": form, "is_create": False, "guest_form": guest_form}
        return render(request, self.template_name, ctx)

    def post(self, request, form_id):
        org = getattr(request, "organization", None)
        guest_form = get_object_or_404(
            GuestForm.objects.for_organization(org), pk=form_id
        )
        form = GuestFormForm(request.POST, request.FILES, instance=guest_form)
        if form.is_valid():
            form.save()
            messages.success(request, "Guest form updated.")
            return redirect("guest_form_manage_list")
        ctx = {"form": form, "is_create": False, "guest_form": guest_form}
        return render(request, self.template_name, ctx)


class GuestFormDeleteView(GuestFormReviewRequiredMixin, View):
    """Delete a guest form."""

    template_name = "guest_forms/manage/confirm_delete.html"

    def get(self, request, form_id):
        org = getattr(request, "organization", None)
        if org is None:
            raise Http404
        guest_form = get_object_or_404(
            GuestForm.objects.for_organization(org), pk=form_id
        )
        submission_count = (
            GuestFormSubmission.objects.for_organization(org)
            .filter(
                guest_form=guest_form,
                guest_form__organization=org,
            )
            .count()
        )
        has_foreign_submissions = (
            GuestFormSubmission.objects.filter(guest_form=guest_form)
            .exclude(organization=org)
            .exists()
        )
        ctx = {
            "guest_form": guest_form,
            "submission_count": submission_count,
            "deletion_blocked": has_foreign_submissions,
        }
        return render(request, self.template_name, ctx)

    @transaction.atomic
    def post(self, request, form_id):
        org = getattr(request, "organization", None)
        if org is None:
            raise Http404
        guest_form = get_object_or_404(
            GuestForm.objects.for_organization(org).select_for_update(), pk=form_id
        )
        foreign_submissions = (
            GuestFormSubmission.objects.select_for_update()
            .filter(guest_form=guest_form)
            .exclude(organization=org)
        )
        if foreign_submissions.exists():
            messages.error(
                request,
                "This form has inconsistent submissions and cannot be deleted until reviewed.",
            )
            return redirect("guest_form_manage_list")

        name = guest_form.name
        submission_count = (
            GuestFormSubmission.objects.for_organization(org)
            .filter(
                guest_form=guest_form,
                guest_form__organization=org,
            )
            .count()
        )
        log_event(
            event=AuditEvent.RECORD_DELETED,
            resource=guest_form,
            request=request,
            before={"name": name, "submission_count": submission_count},
            notes=f"Guest form '{name}' deleted.",
        )
        guest_form.delete()
        messages.success(request, f'Deleted guest form "{name}".')
        return redirect("guest_form_manage_list")
