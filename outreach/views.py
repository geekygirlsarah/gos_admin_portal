from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import Prefetch, Q
from django.db.models.functions import Coalesce
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from django.views.generic import CreateView, DeleteView, ListView, UpdateView, View

from outreach.forms import OutreachEventForm, OutreachManageSignupsForm
from outreach.models import OutreachEvent, OutreachSignup
from programs.models import Program
from programs.permission_views import can_user_delete, can_user_write, get_user_role
from programs.views.mixins import (
    DynamicReadPermissionMixin,
    DynamicWritePermissionMixin,
)


class OutreachProgramMixin:
    def dispatch(self, request, *args, **kwargs):
        self.program = get_object_or_404(Program, pk=kwargs.get("program_id"))
        if not self.program.features.filter(key="outreach").exists():
            raise Http404("Outreach is not enabled for this program.")
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["program"] = self.program
        return context

    def get_success_url(self):
        return reverse("outreach:event_list", kwargs={"program_id": self.program.id})


class OutreachEventListView(
    LoginRequiredMixin, OutreachProgramMixin, DynamicReadPermissionMixin, ListView
):
    model = OutreachEvent
    template_name = "outreach/event_list.html"
    context_object_name = "events"
    section = "outreach"

    def get_queryset(self):
        return OutreachEvent.objects.filter(program=self.program)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user
        role = get_user_role(user)
        context["user_role"] = role

        today = timezone.now().date()
        events = (
            self.get_queryset()
            .annotate(effective_end_date=Coalesce("end_date", "start_date"))
            .order_by("start_date", "start_time")
        )

        upcoming_events = [e for e in events if not e.is_past]
        past_events = [e for e in events if e.is_past]
        past_events.sort(key=lambda e: (e.start_date, e.start_time), reverse=True)

        context["upcoming_events"] = upcoming_events
        context["past_events"] = past_events

        if role == "Student":
            try:
                student = user.student_profile

                # Get student signups for this program
                student_signups = list(
                    OutreachSignup.objects.filter(
                        student=student, event__program=self.program
                    ).select_related("event")
                )

                student_signup_ids = {s.event_id for s in student_signups}

                context["my_events"] = [
                    e for e in upcoming_events if e.id in student_signup_ids
                ]
                context["other_events"] = [
                    e for e in upcoming_events if e.id not in student_signup_ids
                ]

                context["student_signups"] = list(student_signup_ids)
                context["student_signup_roles"] = {
                    s.event_id: s.role for s in student_signups
                }

                # Add outreach stats
                past_signups = [s for s in student_signups if s.event.is_past]
                upcoming_signups = [s for s in student_signups if not s.event.is_past]

                context["championed_count"] = sum(
                    1 for s in student_signups if s.role == OutreachSignup.CHAMPION
                )
                context["total_outreach_hours"] = sum(
                    s.event.duration_hours for s in past_signups
                )
                context["pending_outreach_hours"] = sum(
                    s.event.duration_hours for s in upcoming_signups
                )
            except AttributeError:
                pass

        context["can_add"] = can_user_write(user, "outreach")
        return context


class OutreachEventCreateView(
    LoginRequiredMixin, OutreachProgramMixin, DynamicWritePermissionMixin, CreateView
):
    model = OutreachEvent
    form_class = OutreachEventForm
    template_name = "outreach/event_form.html"
    section = "outreach"

    def form_valid(self, form):
        form.instance.program = self.program
        response = super().form_valid(form)
        user = self.request.user
        role = get_user_role(user)
        if role == "Student":
            try:
                student = user.student_profile
                OutreachSignup.objects.create(
                    student=student, event=self.object, role=OutreachSignup.CHAMPION
                )
                messages.success(
                    self.request,
                    "Event created and you have been signed up as a champion!",
                )
            except AttributeError:
                messages.warning(
                    self.request,
                    "Event created, but we couldn't find your student profile to sign you up as a champion.",
                )
        else:
            messages.success(self.request, "Event created successfully.")
        return response


class OutreachEventUpdateView(
    LoginRequiredMixin, OutreachProgramMixin, DynamicWritePermissionMixin, UpdateView
):
    model = OutreachEvent
    form_class = OutreachEventForm
    template_name = "outreach/event_form.html"
    section = "outreach"

    def get_queryset(self):
        return OutreachEvent.objects.filter(program=self.program)


class OutreachEventDeleteView(LoginRequiredMixin, OutreachProgramMixin, DeleteView):
    model = OutreachEvent
    template_name = "outreach/event_confirm_delete.html"

    def get_queryset(self):
        return OutreachEvent.objects.filter(program=self.program)

    def dispatch(self, request, *args, **kwargs):
        # Initialize program via mixin logic first
        self.program = get_object_or_404(Program, pk=kwargs.get("program_id"))
        if not self.program.features.filter(key="outreach").exists():
            raise Http404("Outreach is not enabled for this program.")

        obj = self.get_object()
        if not can_user_delete(request.user, "outreach", obj):
            messages.error(request, "You do not have permission to delete this event.")
            return redirect("outreach:event_list", program_id=self.program.id)
        return super(OutreachProgramMixin, self).dispatch(request, *args, **kwargs)


class OutreachEventSignupView(LoginRequiredMixin, OutreachProgramMixin, View):
    def post(self, request, program_id, pk):
        event = get_object_or_404(OutreachEvent, pk=pk, program=self.program)
        role = request.POST.get("role")

        if role not in [OutreachSignup.CHAMPION, OutreachSignup.HELPER]:
            messages.error(request, "Invalid role.")
            return redirect("outreach:event_list", program_id=self.program.id)

        try:
            student = request.user.student_profile
        except AttributeError:
            messages.error(request, "Only students can sign up for events.")
            return redirect("outreach:event_list", program_id=self.program.id)

        signup = OutreachSignup(student=student, event=event, role=role)
        try:
            signup.clean()
            signup.save()
            messages.success(request, f"Successfully signed up as a {role}!")
        except Exception as e:
            messages.error(request, str(e))

        return redirect("outreach:event_list", program_id=self.program.id)


class OutreachEventCancelView(LoginRequiredMixin, OutreachProgramMixin, View):
    def post(self, request, program_id, pk):
        event = get_object_or_404(OutreachEvent, pk=pk, program=self.program)
        try:
            student = request.user.student_profile
            signup = OutreachSignup.objects.get(student=student, event=event)
            signup.delete()
            messages.success(request, "Signup cancelled.")
        except OutreachSignup.DoesNotExist:
            messages.error(request, "You are not signed up for this event.")
        except AttributeError:
            messages.error(request, "Only students can cancel signups.")

        return redirect("outreach:event_list", program_id=self.program.id)


class OutreachEventManageSignupsView(
    LoginRequiredMixin, OutreachProgramMixin, DynamicWritePermissionMixin, View
):
    section = "outreach"

    def get_object(self):
        return get_object_or_404(
            OutreachEvent, pk=self.kwargs.get("pk"), program=self.program
        )

    def get(self, request, program_id, pk):
        event = self.get_object()
        form = OutreachManageSignupsForm(event=event)
        return render(
            request,
            "outreach/_manage_signups_modal_content.html",
            {
                "event": event,
                "form": form,
                "program": self.program,
            },
        )

    def post(self, request, program_id, pk):
        event = self.get_object()
        form = OutreachManageSignupsForm(request.POST, event=event)
        if form.is_valid():
            form.save()
            messages.success(request, f"Signups for {event.name} updated successfully.")
        else:
            for field, errors in form.errors.items():
                for error in errors:
                    messages.error(
                        request,
                        (
                            f"{field.capitalize()}: {error}"
                            if field != "__all__"
                            else error
                        ),
                    )

        return redirect("outreach:event_list", program_id=self.program.id)


class OutreachStudentStatsView(
    LoginRequiredMixin, OutreachProgramMixin, DynamicReadPermissionMixin, View
):
    section = "outreach"

    def get(self, request, program_id):
        from programs.utils import active_students_in_program

        students = (
            active_students_in_program(self.program)
            .annotate(display_first_name=Coalesce("first_name", "legal_first_name"))
            .order_by("display_first_name", "last_name")
        )

        # Prefetch signups and events to avoid N+1
        students = students.prefetch_related(
            Prefetch(
                "outreach_signups",
                queryset=OutreachSignup.objects.filter(
                    event__program=self.program
                ).select_related("event"),
                to_attr="program_signups",
            )
        )

        student_stats = []
        for student in students:
            signups = student.program_signups
            past_signups = [s for s in signups if s.event.is_past]
            championed = sum(1 for s in signups if s.role == OutreachSignup.CHAMPION)
            hours = sum(s.event.duration_hours for s in past_signups)
            pending_hours = sum(
                s.event.duration_hours for s in signups if not s.event.is_past
            )
            student_stats.append(
                {
                    "name": student.full_name,
                    "championed": championed,
                    "hours": hours,
                    "pending_hours": pending_hours,
                }
            )

        return render(
            request,
            "outreach/_student_stats_modal_content.html",
            {
                "student_stats": student_stats,
                "program": self.program,
            },
        )
