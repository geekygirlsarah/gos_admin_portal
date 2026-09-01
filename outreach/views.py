from datetime import date, time

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db import transaction
from django.db.models import Prefetch, Q
from django.db.models.functions import Coalesce
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from django.views.generic import CreateView, DeleteView, ListView, UpdateView, View

from outreach.forms import (
    OutreachEventForm,
    OutreachManageSignupsForm,
    OutreachSetTimesForm,
    OutreachShiftFormSet,
)
from outreach.models import (
    OutreachEvent,
    OutreachMentorSignup,
    OutreachShift,
    OutreachSignup,
)
from outreach.utils import (
    can_operate_checkin,
    can_view_checkin,
    compute_outreach_stats,
)
from programs.models import Program
from programs.permission_views import (
    can_user_delete,
    can_user_write,
    get_user_role,
    user_is_mentor,
)
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
            Prefetch(
                "shifts",
                queryset=OutreachShift.objects.prefetch_related(
                    "signups__student", "mentor_signups__adult"
                ),
            )
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
                context.update(compute_outreach_stats(student_signups))
            except AttributeError:
                pass

        context["can_add"] = can_user_write(user, "outreach")

        # Shifts the viewer may operate the check-in/out station for.
        can_checkin_shift_ids = set()
        for event in events:
            for shift in event.ordered_shifts:
                if can_operate_checkin(user, shift):
                    can_checkin_shift_ids.add(shift.pk)
        context["can_checkin_shift_ids"] = can_checkin_shift_ids

        # Mentor support signups: any mentor (or lead mentor) may volunteer
        # for a shift; there is no capacity limit.
        context["viewer_is_mentor"] = user_is_mentor(user) or role == "LeadMentor"
        context["mentor_signup_shift_ids"] = set()
        try:
            context["mentor_signup_shift_ids"] = set(
                OutreachMentorSignup.objects.filter(
                    adult=user.adult_profile,
                    shift__event__program=self.program,
                ).values_list("shift_id", flat=True)
            )
        except AttributeError:
            pass

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
        if shift.is_past:
            messages.error(request, "This shift has ended. You can no longer sign up.")
            return redirect("outreach:event_list", program_id=self.program.id)
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
        if shift.is_past:
            messages.error(request, "This shift has ended. You can no longer cancel.")
            return redirect("outreach:event_list", program_id=self.program.id)
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


class OutreachShiftMentorSignupView(LoginRequiredMixin, OutreachProgramMixin, View):
    """Let a mentor volunteer to support a specific shift.

    There is no capacity limit for mentor support signups.
    """

    def post(self, request, program_id, shift_pk):
        shift = get_object_or_404(
            OutreachShift, pk=shift_pk, event__program=self.program
        )
        if shift.is_past:
            messages.error(request, "This shift has ended. You can no longer sign up.")
            return redirect("outreach:event_list", program_id=self.program.id)
        role = get_user_role(request.user)
        if not (user_is_mentor(request.user) or role == "LeadMentor"):
            messages.error(
                request, "Only mentors can sign up to support outreach shifts."
            )
            return redirect("outreach:event_list", program_id=self.program.id)

        try:
            adult = request.user.adult_profile
        except AttributeError:
            messages.error(
                request,
                "We couldn't find your mentor profile to sign you up. "
                "Please contact a Lead Mentor.",
            )
            return redirect("outreach:event_list", program_id=self.program.id)

        signup, created = OutreachMentorSignup.objects.get_or_create(
            adult=adult, shift=shift
        )
        if created:
            messages.success(
                request,
                f"Thanks! You are signed up to support {shift.event.name} on {shift.date}.",
            )
        else:
            messages.info(
                request,
                f"You are already signed up to support {shift.event.name} on {shift.date}.",
            )

        return redirect("outreach:event_list", program_id=self.program.id)


class OutreachShiftMentorCancelView(LoginRequiredMixin, OutreachProgramMixin, View):
    def post(self, request, program_id, shift_pk):
        shift = get_object_or_404(
            OutreachShift, pk=shift_pk, event__program=self.program
        )
        if shift.is_past:
            messages.error(request, "This shift has ended. You can no longer cancel.")
            return redirect("outreach:event_list", program_id=self.program.id)
        try:
            signup = OutreachMentorSignup.objects.get(
                adult=request.user.adult_profile, shift=shift
            )
            signup.delete()
            messages.success(request, "Support signup cancelled.")
        except OutreachMentorSignup.DoesNotExist:
            messages.error(request, "You are not signed up to support this shift.")
        except AttributeError:
            messages.error(request, "Only mentors can cancel support signups.")

        return redirect("outreach:event_list", program_id=self.program.id)


class OutreachShiftManageSignupsView(
    LoginRequiredMixin, OutreachProgramMixin, DynamicWritePermissionMixin, View
):
    section = "outreach"

    def dispatch(self, request, *args, **kwargs):
        # A shift that has ended becomes view-only for everyone except
        # mentors/lead mentors (so rosters and attendance can still be
        # corrected after the fact). Resolve the program/shift directly here
        # because the parent mixin hasn't populated ``self.program`` yet.
        program = get_object_or_404(Program, pk=kwargs.get("program_id"))
        shift = get_object_or_404(
            OutreachShift,
            pk=kwargs.get("shift_pk"),
            event__program=program,
        )
        if shift.is_past and not (
            user_is_mentor(request.user) or get_user_role(request.user) == "LeadMentor"
        ):
            messages.error(
                request, "This shift has ended and can no longer be changed."
            )
            return redirect("outreach:event_list", program_id=program.id)
        return super().dispatch(request, *args, **kwargs)

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
            .annotate(
                display_first_name=Coalesce("preferred_first_name", "legal_first_name")
            )
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
            stats = compute_outreach_stats(signups)
            student_stats.append(
                {
                    "name": student.full_name,
                    "championed": stats["championed_count"],
                    "hours": stats["total_outreach_hours"],
                    "pending_hours": stats["pending_outreach_hours"],
                    "unconfirmed_count": stats["unconfirmed_count"],
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


class OutreachShiftCheckInView(LoginRequiredMixin, OutreachProgramMixin, View):
    """Phone-first check-in/out station for a single outreach shift.

    Mentors/lead mentors always have access; a shift's champion may operate
    it until every signup has been stamped in and out, or the grace window
    after the shift ends (see ``can_operate_checkin``). Walk-up students are
    added as helpers and bypass the helper capacity limit.
    """

    template_name = "outreach/_checkin.html"

    def _get_shift(self):
        return get_object_or_404(
            OutreachShift, pk=self.kwargs.get("shift_pk"), event__program=self.program
        )

    def get(self, request, program_id, shift_pk):
        shift = self._get_shift()
        if not can_view_checkin(request.user, shift):
            messages.error(request, "You can't view this shift's check-in page.")
            return redirect("outreach:event_list", program_id=self.program.id)

        from programs.utils import active_students_in_program

        signups = shift.signups.select_related("student").order_by(
            "student__legal_first_name", "student__last_name"
        )
        active_students = (
            active_students_in_program(self.program)
            .annotate(
                display_first_name=Coalesce("preferred_first_name", "legal_first_name")
            )
            .order_by("display_first_name", "last_name")
        )

        return render(
            request,
            self.template_name,
            {
                "shift": shift,
                "program": self.program,
                "signups": signups,
                "signed_up_student_ids": set(
                    signups.values_list("student_id", flat=True)
                ),
                "active_students": active_students,
                "can_operate": can_operate_checkin(request.user, shift),
            },
        )

    def post(self, request, program_id, shift_pk):
        from programs.utils import active_students_in_program

        shift = self._get_shift()
        if not can_operate_checkin(request.user, shift):
            messages.error(
                request, "This shift's check-in is locked. Ask a mentor for help."
            )
            return redirect(
                "outreach:shift_check_in",
                program_id=program_id,
                shift_pk=shift_pk,
            )

        action = request.POST.get("action")
        student_id = request.POST.get("student_id")
        now = timezone.now()

        if action in ("check_in", "check_out"):
            signup = (
                OutreachSignup.objects.filter(shift=shift, student_id=student_id)
                .select_related("student")
                .first()
            )
            if signup is None:
                messages.error(request, "That student isn't signed up for this shift.")
            elif action == "check_in":
                signup.checked_in_at = now
                signup.save(update_fields=["checked_in_at"])
            else:
                signup.checked_out_at = now
                signup.save(update_fields=["checked_out_at"])
        elif action == "check_in_all":
            updated = shift.signups.filter(checked_in_at__isnull=True).update(
                checked_in_at=now
            )
            if updated:
                messages.success(request, f"Checked in all {updated} student(s).")
        elif action == "check_out_all":
            updated = shift.signups.filter(checked_out_at__isnull=True).update(
                checked_out_at=now
            )
            if updated:
                messages.success(request, f"Checked out all {updated} student(s).")
        elif action == "walk_up":
            student = (
                active_students_in_program(self.program).filter(pk=student_id).first()
            )
            if student is None:
                messages.error(request, "That's not a valid student for this program.")
            else:
                signup, created = OutreachSignup.objects.get_or_create(
                    shift=shift,
                    student=student,
                    defaults={
                        "role": OutreachSignup.HELPER,
                        "checked_in_at": now,
                    },
                )
                if not created and signup.checked_in_at is None:
                    signup.checked_in_at = now
                    signup.save(update_fields=["checked_in_at"])
                if created:
                    messages.success(request, f"{student.display_name} checked in.")
        elif action == "set_times":
            signup = (
                OutreachSignup.objects.filter(shift=shift, student_id=student_id)
                .select_related("student")
                .first()
            )
            if signup is None:
                messages.error(request, "That student isn't signed up for this shift.")
            else:
                form = OutreachSetTimesForm(request.POST)
                if form.is_valid():
                    cleaned = form.cleaned_data
                    signup.checked_in_at = cleaned["checked_in_at"]
                    signup.checked_out_at = cleaned["checked_out_at"]
                    signup.save(update_fields=["checked_in_at", "checked_out_at"])
                    messages.success(
                        request, f"Updated times for {signup.student.display_name}."
                    )
                else:
                    for errors in form.errors.values():
                        for error in errors:
                            messages.error(
                                request, f"{signup.student.display_name}: {error}"
                            )
        else:
            messages.error(request, "Unknown action.")

        return redirect(
            "outreach:shift_check_in",
            program_id=program_id,
            shift_pk=shift_pk,
        )
