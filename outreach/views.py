from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import Q
from django.db.models.functions import Coalesce
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from django.views.generic import CreateView, DeleteView, ListView, UpdateView, View

from outreach.forms import OutreachEventForm
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

        upcoming_events = events.filter(effective_end_date__gte=today)
        past_events = events.filter(effective_end_date__lt=today).order_by(
            "-start_date", "-start_time"
        )

        context["upcoming_events"] = upcoming_events
        context["past_events"] = past_events

        if role == "Student":
            try:
                student = user.student_profile
                context["my_events"] = upcoming_events.filter(
                    signups__student=student
                ).distinct()
                context["other_events"] = upcoming_events.exclude(
                    signups__student=student
                )
                context["student_signups"] = OutreachSignup.objects.filter(
                    student=student, event__program=self.program
                ).values_list("event_id", flat=True)
                context["student_signup_roles"] = {
                    s.event_id: s.role
                    for s in OutreachSignup.objects.filter(
                        student=student, event__program=self.program
                    )
                }
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
