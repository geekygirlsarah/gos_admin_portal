from datetime import date, time

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db import transaction
from django.db.models import Prefetch, Q
from django.db.models.functions import Coalesce
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse, reverse_lazy
from django.views.generic import CreateView, DeleteView, ListView, UpdateView, View

from outreach.forms import (
    OutreachEventForm,
    OutreachManageSignupsForm,
    OutreachShiftFormSet,
)
from outreach.models import OutreachEvent, OutreachShift, OutreachSignup
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
        return OutreachEvent.objects.filter(program=self.program).prefetch_related(
            "shifts"
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user
        role = get_user_role(user)
        context["user_role"] = role

        events = sorted(
            self.get_queryset(),
            key=lambda e: (e.start_date or date.max, e.start_time or time.max),
        )

        upcoming_events = [e for e in events if not e.is_past]
        past_events = [e for e in events if e.is_past]
        past_events.sort(key=lambda e: (e.start_date, e.start_time), reverse=True)

        context["upcoming_events"] = upcoming_events
        context["past_events"] = past_events

        if role == "Student":
            try:
                student = user.student_profile

                # Get student signups for this program, keyed by shift since a
                # student signs up for individual shifts, not whole events.
                student_signups = list(
                    OutreachSignup.objects.filter(
                        student=student, shift__event__program=self.program
                    ).select_related("shift", "shift__event")
                )

                student_signup_event_ids = {s.shift.event_id for s in student_signups}

                context["my_events"] = [
                    e for e in upcoming_events if e.id in student_signup_event_ids
                ]
                context["other_events"] = [
                    e for e in upcoming_events if e.id not in student_signup_event_ids
                ]

                context["student_signup_shift_ids"] = {
                    s.shift_id for s in student_signups
                }
                context["student_signup_roles"] = {
                    s.shift_id: s.role for s in student_signups
                }
                context["student_champion_event_ids"] = {
                    s.shift.event_id
                    for s in student_signups
                    if s.role == OutreachSignup.CHAMPION
                }

                # Add outreach stats, credited based on the specific shift
                # signed up for (not the whole event's duration).
                past_signups = [s for s in student_signups if s.shift.is_past]
                upcoming_signups = [s for s in student_signups if not s.shift.is_past]

                context["championed_count"] = sum(
                    1 for s in student_signups if s.role == OutreachSignup.CHAMPION
                )
                context["total_outreach_hours"] = sum(
                    s.shift.duration_hours for s in past_signups
                )
                context["pending_outreach_hours"] = sum(
                    s.shift.duration_hours for s in upcoming_signups
                )
            except AttributeError:
                pass

        context["can_add"] = can_user_write(user, "outreach")
        return context


class OutreachShiftFormSetMixin:
    """Adds inline ``OutreachShift`` formset handling to event create/update views.

    Requires at least one valid shift to save the event.
    """

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        if self.request.method == "POST":
            context["shift_formset"] = OutreachShiftFormSet(
                self.request.POST, instance=self.object, prefix="shifts"
            )
        else:
            context["shift_formset"] = OutreachShiftFormSet(
                instance=self.object, prefix="shifts"
            )
        return context

    def form_valid(self, form):
        context = self.get_context_data(form=form)
        shift_formset = context["shift_formset"]
        if not shift_formset.is_valid():
            return self.render_to_response(context)
        with transaction.atomic():
            response = super().form_valid(form)
            shift_formset.instance = self.object
            shift_formset.save()
        return response


class OutreachEventCreateView(
    LoginRequiredMixin,
    OutreachProgramMixin,
    DynamicWritePermissionMixin,
    OutreachShiftFormSetMixin,
    CreateView,
):
    model = OutreachEvent
    form_class = OutreachEventForm
    template_name = "outreach/event_form.html"
    section = "outreach"

    def form_valid(self, form):
        form.instance.program = self.program
        response = super().form_valid(form)
        if self.object is None:
            return response
        user = self.request.user
        role = get_user_role(user)
        if role == "Student":
            try:
                student = user.student_profile
                first_shift = self.object.first_shift
                OutreachSignup.objects.create(
                    student=student, shift=first_shift, role=OutreachSignup.CHAMPION
                )
                messages.success(
                    self.request,
                    "Event created and you have been signed up as a champion for the first shift!",
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
    LoginRequiredMixin,
    OutreachProgramMixin,
    DynamicWritePermissionMixin,
    OutreachShiftFormSetMixin,
    UpdateView,
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


class OutreachShiftSignupView(LoginRequiredMixin, OutreachProgramMixin, View):
    def post(self, request, program_id, shift_pk):
        shift = get_object_or_404(
            OutreachShift, pk=shift_pk, event__program=self.program
        )
        role = request.POST.get("role")

        if role not in [OutreachSignup.CHAMPION, OutreachSignup.HELPER]:
            messages.error(request, "Invalid role.")
            return redirect("outreach:event_list", program_id=self.program.id)

        try:
            student = request.user.student_profile
        except AttributeError:
            messages.error(request, "Only students can sign up for shifts.")
            return redirect("outreach:event_list", program_id=self.program.id)

        signup = OutreachSignup(student=student, shift=shift, role=role)
        try:
            signup.clean()
            signup.save()
            messages.success(request, f"Successfully signed up as a {role}!")
        except Exception as e:
            messages.error(request, str(e))

        return redirect("outreach:event_list", program_id=self.program.id)


class OutreachShiftCancelView(LoginRequiredMixin, OutreachProgramMixin, View):
    def post(self, request, program_id, shift_pk):
        shift = get_object_or_404(
            OutreachShift, pk=shift_pk, event__program=self.program
        )
        try:
            student = request.user.student_profile
            signup = OutreachSignup.objects.get(student=student, shift=shift)
            signup.delete()
            messages.success(request, "Signup cancelled.")
        except OutreachSignup.DoesNotExist:
            messages.error(request, "You are not signed up for this shift.")
        except AttributeError:
            messages.error(request, "Only students can cancel signups.")

        return redirect("outreach:event_list", program_id=self.program.id)


class OutreachShiftManageSignupsView(
    LoginRequiredMixin, OutreachProgramMixin, DynamicWritePermissionMixin, View
):
    section = "outreach"

    def get_object(self):
        return get_object_or_404(
            OutreachShift,
            pk=self.kwargs.get("shift_pk"),
            event__program=self.program,
        )

    def get(self, request, program_id, shift_pk):
        shift = self.get_object()
        form = OutreachManageSignupsForm(shift=shift)
        return render(
            request,
            "outreach/_manage_signups_modal_content.html",
            {
                "shift": shift,
                "form": form,
                "program": self.program,
            },
        )

    def post(self, request, program_id, shift_pk):
        shift = self.get_object()
        form = OutreachManageSignupsForm(request.POST, shift=shift)
        if form.is_valid():
            form.save()
            messages.success(
                request,
                f"Signups for {shift.event.name} on {shift.date} updated successfully.",
            )
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

        # Prefetch signups and shifts to avoid N+1
        students = students.prefetch_related(
            Prefetch(
                "outreach_signups",
                queryset=OutreachSignup.objects.filter(
                    shift__event__program=self.program
                ).select_related("shift", "shift__event"),
                to_attr="program_signups",
            )
        )

        student_stats = []
        for student in students:
            signups = student.program_signups
            past_signups = [s for s in signups if s.shift.is_past]
            championed = sum(1 for s in signups if s.role == OutreachSignup.CHAMPION)
            hours = sum(s.shift.duration_hours for s in past_signups)
            pending_hours = sum(
                s.shift.duration_hours for s in signups if not s.shift.is_past
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
