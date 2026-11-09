"""Views for toSend webhook ingestion and Lead Mentor email delivery issues management."""

from __future__ import annotations

import json
import logging

from django.contrib import messages
from django.db.models import Q
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.utils import timezone
from django.views import View
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from django.views.generic import ListView

from programs.models import EmailDeliveryEvent
from programs.permission_views import LeadMentorRequiredMixin
from programs.utils.tosend_webhooks import (
    process_tosend_event,
    quarantine_malformed_tosend_payload,
    verify_tosend_signature,
)
from programs.utils.urls import get_safe_url

logger = logging.getLogger(__name__)


@csrf_exempt
@require_POST
def tosend_webhook_view(request: HttpRequest) -> HttpResponse:
    """Public webhook endpoint to receive delivery events (bounces, complaints) from toSend."""
    sig_header = request.headers.get("X-ToSend-Signature") or request.META.get(
        "HTTP_X_TOSEND_SIGNATURE"
    )
    ts_header = request.headers.get("X-ToSend-Timestamp") or request.META.get(
        "HTTP_X_TOSEND_TIMESTAMP"
    )

    if not verify_tosend_signature(request.body, sig_header, ts_header):
        return JsonResponse({"error": "Invalid signature"}, status=401)

    try:
        data = json.loads(request.body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        quarantine_malformed_tosend_payload(request.body)
        logger.warning("Malformed JSON in signed toSend webhook request.")
        return JsonResponse({"status": "quarantined", "processed": 1}, status=200)

    events_to_process = []
    if isinstance(data, list):
        events_to_process = data
    elif isinstance(data, dict):
        if isinstance(data.get("events"), list):
            events_to_process = data["events"]
        elif isinstance(data.get("data"), list):
            events_to_process = data["data"]
        else:
            events_to_process = [data]
    else:
        events_to_process = [data]

    processed_count = 0
    for item in events_to_process:
        quarantine_record = process_tosend_event(item)
        if quarantine_record:
            processed_count += 1

    return JsonResponse({"status": "ok", "processed": processed_count})


class EmailDeliveryIssueListView(LeadMentorRequiredMixin, ListView):
    """Lead Mentor dashboard view for tracking and resolving email delivery failures."""

    model = EmailDeliveryEvent
    template_name = "programs/email_issues_list.html"
    context_object_name = "issues"
    paginate_by = 50

    def get_queryset(self):
        organization = getattr(self.request, "organization", None)
        if organization is None:
            return EmailDeliveryEvent.objects.none()
        qs = EmailDeliveryEvent.objects.verified_for_organization(
            organization
        ).select_related("student", "adult", "application", "resolved_by")

        status_filter = self.request.GET.get("status", "unresolved")
        if status_filter == "unresolved":
            qs = qs.filter(resolved=False)
        elif status_filter == "resolved":
            qs = qs.filter(resolved=True)

        type_filter = self.request.GET.get("type", "all")
        if type_filter in ("bounced", "complaint", "unsubscribed"):
            qs = qs.filter(event_type=type_filter)

        search = self.request.GET.get("q", "").strip()
        if search:
            qs = qs.filter(
                Q(recipient_email__icontains=search)
                | Q(reason__icontains=search)
                | Q(message_id__icontains=search)
                | Q(student__preferred_first_name__icontains=search)
                | Q(student__legal_first_name__icontains=search)
                | Q(student__last_name__icontains=search)
                | Q(adult__preferred_first_name__icontains=search)
                | Q(adult__legal_first_name__icontains=search)
                | Q(adult__last_name__icontains=search)
            )

        return qs.order_by("-created_at")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["status_filter"] = self.request.GET.get("status", "unresolved")
        context["type_filter"] = self.request.GET.get("type", "all")
        context["search_query"] = self.request.GET.get("q", "")
        organization = getattr(self.request, "organization", None)
        context["unresolved_count"] = (
            EmailDeliveryEvent.objects.verified_for_organization(organization)
            .filter(resolved=False)
            .count()
        )
        return context


class EmailDeliveryIssueResolveView(LeadMentorRequiredMixin, View):
    """POST endpoint for Lead Mentors to mark an email delivery issue as resolved."""

    def post(self, request: HttpRequest, pk: int) -> HttpResponse:
        organization = getattr(request, "organization", None)
        event = get_object_or_404(
            EmailDeliveryEvent.objects.verified_for_organization(organization), pk=pk
        )
        event.resolved = True
        event.resolved_at = timezone.now()
        event.resolved_by = request.user if request.user.is_authenticated else None
        event.save(update_fields=["resolved", "resolved_at", "resolved_by"])

        messages.success(
            request,
            f"Delivery issue for {event.recipient_email} marked as resolved.",
        )
        next_url = request.POST.get("next") or request.GET.get("next")
        safe_url = get_safe_url(request, next_url)
        return redirect(safe_url or "email_delivery_issues_list")
