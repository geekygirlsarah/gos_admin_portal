from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import Count
from django.shortcuts import redirect, render
from django.views import View

from audit.events import AuditEvent
from audit.service import log_event
from programs.permission_views import LeadMentorRequiredMixin

from ..models import AttendanceEvent, AttendanceSession


class VisitorManagementView(LoginRequiredMixin, LeadMentorRequiredMixin, View):
    def get(self, request):
        visitors = (
            AttendanceSession.objects.filter(student__isnull=True, adult__isnull=True)
            .exclude(visitor_name="")
            .values("visitor_name")
            .annotate(session_count=Count("id"))
            .order_by("visitor_name")
        )

        return render(
            request,
            "attendance/visitor_management.html",
            {
                "visitors": visitors,
            },
        )

    def post(self, request):
        action = request.POST.get("action")
        if action == "merge":
            selected_names = request.POST.getlist("selected_names")
            target_name = request.POST.get("target_name", "").strip()

            if not target_name:
                messages.error(request, "Target name is required for merge.")
            elif not selected_names:
                messages.error(request, "Select at least one visitor name to merge.")
            else:
                visitor_sessions = AttendanceSession.objects.filter(
                    student__isnull=True,
                    adult__isnull=True,
                    visitor_name__in=selected_names,
                )
                visitor_events = AttendanceEvent.objects.filter(
                    student__isnull=True,
                    adult__isnull=True,
                    visitor_name__in=selected_names,
                )
                sample = visitor_sessions.first() or visitor_events.first()
                if sample is not None:
                    log_event(
                        event=AuditEvent.RECORDS_MERGED,
                        resource=sample,
                        request=request,
                        before={"merged_names": sorted(set(selected_names))},
                        after={"target_name": target_name},
                        notes=(
                            f"{len(selected_names)} visitor names merged into "
                            f"'{target_name}'."
                        ),
                    )
                visitor_sessions.update(visitor_name=target_name)
                visitor_events.update(visitor_name=target_name)

                messages.success(
                    request,
                    f"Merged {len(selected_names)} visitor names into '{target_name}'.",
                )

        return redirect("visitor_management")
