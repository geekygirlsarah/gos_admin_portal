from datetime import date

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db import transaction
from django.db.models import Prefetch
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.views.generic import CreateView, DeleteView, ListView, UpdateView, View

from programs.models import Program, Student
from programs.permission_views import (
    LeadMentorRequiredMixin,
    can_user_delete,
    get_user_role,
    user_is_mentor_or_lead,
)
from programs.utils import active_students_in_program
from programs.views.mixins import DynamicReadPermissionMixin
from travel.forms import (
    TravelApprovalForm,
    TravelCostOptionFormSet,
    TravelDepartureFormSet,
    TravelEventForm,
    TravelParentChaperoneStaffForm,
    TravelSignupForm,
    TravelStaffSignupForm,
)
from travel.models import (
    TravelEvent,
    TravelMentorSignup,
    TravelParentChaperoneSignup,
    TravelSignup,
)
from travel.services import (
    notify_mentor_signup_cancelled,
    notify_mentor_signup_created,
    notify_parent_chaperone_signup_cancelled,
    notify_parent_chaperone_signup_created,
    notify_signup_approved,
    notify_signup_created,
    notify_signup_decision_undone,
    notify_signup_declined,
    notify_signup_withdrawn,
)


class TravelProgramMixin:
    """404 unless the program has the ``travel`` feature enabled, and inject
    the program into context/success URLs.

    Students/Parents only reach the travel pages when the program has the
    ``travel`` feature enabled; mentors and Lead Mentors may always access
    them (follows the ``orders`` pattern).
    """

    def dispatch(self, request, *args, **kwargs):
        self.program = get_object_or_404(Program, pk=kwargs.get("program_id"))
        is_mentor = user_is_mentor_or_lead(request.user)
        if not self.program.features.filter(key="travel").exists() and not is_mentor:
            raise Http404("Travel is not enabled for this program.")
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["program"] = self.program
        return context

    def get_success_url(self):
        return reverse("travel:event_list", kwargs={"program_id": self.program.id})


class TravelEventListView(
    LoginRequiredMixin, TravelProgramMixin, DynamicReadPermissionMixin, ListView
):
    model = TravelEvent
    template_name = "travel/event_list.html"
    context_object_name = "events"
    section = "travel"

    def get_queryset(self):
        signups_qs = (
            TravelSignup.objects.filter(event__program=self.program)
            .select_related("student", "departure", "approved_by", "event")
            .prefetch_related("cost_items")
            .order_by("created_at")
        )
        mentors_qs = TravelMentorSignup.objects.filter(
            event__program=self.program
        ).select_related("adult", "departure", "event")
        chaperones_qs = TravelParentChaperoneSignup.objects.filter(
            event__program=self.program
        ).select_related("adult", "departure", "event")
        return (
            TravelEvent.objects.filter(program=self.program)
            .prefetch_related(
                Prefetch("departures"),
                Prefetch("cost_options"),
                Prefetch("signups", queryset=signups_qs),
                Prefetch("mentor_signups", queryset=mentors_qs),
                Prefetch("parent_chaperones", queryset=chaperones_qs),
            )
            .order_by("start_date", "name")
        )

    def _signup_forms(self, events):
        return {
            e.id: TravelSignupForm(
                departures=e.departures.all(),
                cost_options=e.cost_options.all(),
            )
            for e in events
        }

    def _staff_forms(self, events):
        return {
            e.id: TravelStaffSignupForm(
                program=self.program,
                departures=e.departures.all(),
                cost_options=e.cost_options.all(),
            )
            for e in events
        }

    def _chaperone_context(self, adult, is_lead, upcoming_events):
        """Parent-chaperone bits: who can chaperone, what I've joined, and the
        Lead-Mentor add forms."""
        eligible_chaperone_ids = set(
            TravelParentChaperoneSignup.eligible_adults(self.program).values_list(
                "pk", flat=True
            )
        )
        if adult is not None:
            my_chaperones = list(
                TravelParentChaperoneSignup.objects.filter(
                    adult=adult, event__program=self.program
                ).select_related("event", "departure")
            )
            my_chaperone_event_ids = {s.event_id for s in my_chaperones}
        else:
            my_chaperone_event_ids = set()
        context = {
            "can_chaperone": bool(
                adult is not None and adult.pk in eligible_chaperone_ids
            ),
            "my_chaperone_event_ids": my_chaperone_event_ids,
        }
        if is_lead:
            context["chaperone_staff_forms"] = {
                e.id: TravelParentChaperoneStaffForm(program=self.program)
                for e in upcoming_events
            }
        return context

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user
        role = get_user_role(user)
        context["user_role"] = role

        events = [e for e in self.get_queryset() if e.start_date is not None]
        events.sort(key=lambda e: (e.start_date or date.max, e.name))
        upcoming_events = [e for e in events if not e.is_past]
        past_events = [e for e in events if e.is_past]
        past_events.sort(key=lambda e: (e.start_date or date.min, e.name), reverse=True)

        context["upcoming_events"] = upcoming_events
        context["past_events"] = past_events
        context["can_manage"] = False
        context["viewer_is_mentor"] = user_is_mentor_or_lead(user)

        # Lead mentors manage trips; only they add/edit/delete or mark drivers.
        is_lead = user.is_superuser or user.groups.filter(name="LeadMentor").exists()
        context["is_lead"] = is_lead
        context["can_manage"] = is_lead
        context["can_add"] = is_lead
        context["staff_signup_forms"] = (
            self._staff_forms(upcoming_events) if is_lead else {}
        )

        # ── Student view ─────────────────────────────────────────────────────
        if role == "Student":
            try:
                student = user.student_profile
            except Student.DoesNotExist:
                student = None
            if student is not None:
                signups = list(
                    TravelSignup.objects.filter(
                        student=student, event__program=self.program
                    )
                    .select_related("event", "departure", "approved_by")
                    .prefetch_related("cost_items")
                )
                signup_by_event = {s.event_id: s for s in signups}
                context["my_events"] = [
                    e for e in upcoming_events if e.id in signup_by_event
                ]
                context["other_events"] = [
                    e for e in upcoming_events if e.id not in signup_by_event
                ]
                context["signup_by_event"] = signup_by_event
                context["signup_forms"] = self._signup_forms(
                    [e for e in upcoming_events if e.id not in signup_by_event]
                )
                context["can_sign_up"] = True
            else:
                context["signup_by_event"] = {}

        # ── Parent view (approvals live here) ────────────────────────────────
        try:
            adult = user.adult_profile
        except Exception:
            adult = None
        if adult is not None and adult.students.exists():
            parent_rows = []
            for child in adult.all_students():
                signups = [
                    s
                    for s in TravelSignup.objects.filter(
                        student=child, event__program=self.program
                    )
                    .select_related("event", "departure", "approved_by")
                    .prefetch_related("cost_items")
                ]
                signup_by_event = {s.event_id: s for s in signups}
                pending = [s for s in signups if s.status == TravelSignup.INTERESTED]
                parent_rows.append(
                    {
                        "student": child,
                        "signups": signups,
                        "signup_by_event": signup_by_event,
                        "pending": pending,
                    }
                )
            context["parent_rows"] = parent_rows
            context["parent_has_pending"] = any(r["pending"] for r in parent_rows)

        # ── Mentor / lead mentor view ────────────────────────────────────────
        if user_is_mentor_or_lead(user) and adult is not None:
            mentor_signups = list(
                TravelMentorSignup.objects.filter(
                    adult=adult, event__program=self.program
                ).select_related("event", "departure")
            )
            context["my_mentor_signups"] = mentor_signups
            context["mentor_signup_event_ids"] = {s.event_id for s in mentor_signups}

        # ── Parent chaperones ─────────────────────────────────────────────────
        context.update(self._chaperone_context(adult, is_lead, upcoming_events))

        return context


class TravelInlineFormsetsMixin:
    """Handle the departures and cost-options inline formsets on one form."""

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        if self.request.method == "POST":
            context["departure_formset"] = TravelDepartureFormSet(
                self.request.POST, instance=self.object, prefix="departures"
            )
            context["cost_formset"] = TravelCostOptionFormSet(
                self.request.POST, instance=self.object, prefix="costs"
            )
        else:
            context["departure_formset"] = TravelDepartureFormSet(
                instance=self.object, prefix="departures"
            )
            context["cost_formset"] = TravelCostOptionFormSet(
                instance=self.object, prefix="costs"
            )
        return context

    def form_valid(self, form):
        context = self.get_context_data(form=form)
        departure_formset = context["departure_formset"]
        cost_formset = context["cost_formset"]
        if not departure_formset.is_valid() or not cost_formset.is_valid():
            return self.render_to_response(context)
        with transaction.atomic():
            response = super().form_valid(form)
            departure_formset.instance = self.object
            departure_formset.save()
            cost_formset.instance = self.object
            cost_formset.save()
        return response


class TravelEventCreateView(
    LoginRequiredMixin,
    TravelProgramMixin,
    LeadMentorRequiredMixin,
    TravelInlineFormsetsMixin,
    CreateView,
):
    model = TravelEvent
    form_class = TravelEventForm
    template_name = "travel/event_form.html"

    def form_valid(self, form):
        form.instance.program = self.program
        return super().form_valid(form)


class TravelEventUpdateView(
    LoginRequiredMixin,
    TravelProgramMixin,
    LeadMentorRequiredMixin,
    TravelInlineFormsetsMixin,
    UpdateView,
):
    model = TravelEvent
    form_class = TravelEventForm
    template_name = "travel/event_form.html"

    def get_queryset(self):
        return TravelEvent.objects.filter(program=self.program)


class TravelEventDeleteView(LoginRequiredMixin, TravelProgramMixin, DeleteView):
    model = TravelEvent
    template_name = "travel/event_confirm_delete.html"

    def get_queryset(self):
        return TravelEvent.objects.filter(program=self.program)

    def dispatch(self, request, *args, **kwargs):
        self.program = get_object_or_404(Program, pk=kwargs.get("program_id"))
        if not self.program.features.filter(key="travel").exists():
            raise Http404("Travel is not enabled for this program.")
        obj = self.get_object()
        if not can_user_delete(request.user, "travel", obj):
            messages.error(request, "You do not have permission to delete this trip.")
            return redirect("travel:event_list", program_id=self.program.id)
        return super(TravelProgramMixin, self).dispatch(request, *args, **kwargs)


class TravelSignupView(LoginRequiredMixin, TravelProgramMixin, View):
    """Create a student signup ('interested').

    Students sign up for themselves; Lead Mentors can add a student after a
    conversation (parents are still emailed and must approve). Trip creation
    is Lead Mentor-only, but this action is open to students.
    """

    def post(self, request, program_id, pk):
        event = get_object_or_404(TravelEvent, pk=pk, program=self.program)
        if event.is_past:
            messages.error(request, "This trip has already ended.")
            return redirect("travel:event_list", program_id=self.program.id)

        student = self._resolve_student(request)
        if student is None:
            return redirect("travel:event_list", program_id=self.program.id)

        existing = TravelSignup.objects.filter(student=student, event=event).first()
        if existing:
            if existing.status in (TravelSignup.DECLINED, TravelSignup.WITHDRAWN):
                messages.info(
                    request,
                    "This signup is no longer active; use the sign-up-again or "
                    "reconsider action instead.",
                )
            else:
                messages.info(
                    request, "This student is already signed up for this trip."
                )
            return redirect("travel:event_list", program_id=self.program.id)

        departure, cost_items = self._validated_choices(event, request.POST)
        if departure is None and event.departures.count() == 1:
            departure = event.ordered_departures[0]

        signup = TravelSignup(student=student, event=event, departure=departure)
        try:
            signup.save()
            signup.cost_items.set(cost_items)
            messages.success(
                request,
                f"{student} is signed up for {event.name}. The parents have been "
                "emailed to review and approve.",
            )
            notify_signup_created(signup)
        except Exception as exc:  # noqa: BLE001
            signup.delete()
            messages.error(request, f"Could not complete signup: {exc}")

        return redirect("travel:event_list", program_id=self.program.id)

    def _resolve_student(self, request):
        """Return the Student this request signs up. None means the request was
        rejected (an error message was already added). Lead Mentors add any
        active student by id; students sign up for themselves."""
        if get_user_role(request.user) == "LeadMentor":
            student_id = request.POST.get("student")
            if not student_id:
                return None
            student = (
                active_students_in_program(self.program).filter(pk=student_id).first()
            )
            if student is None:
                messages.error(
                    request,
                    "That student is not an active student of this program.",
                )
            return student
        try:
            student = request.user.student_profile
        except Student.DoesNotExist:
            student = None
        if student is None:
            messages.error(request, "Only students can sign up for trips.")
        return student

    def _validated_choices(self, event, post):
        form = TravelSignupForm(
            post,
            departures=event.departures.all(),
            cost_options=event.cost_options.all(),
        )
        form.is_valid()
        return (
            form.cleaned_data.get("departure"),
            list(form.cleaned_data.get("cost_items", [])),
        )


class TravelSignupRejoinView(LoginRequiredMixin, TravelProgramMixin, View):
    """A withdrawn signup can be reactivated to 'interested' by the student or
    a Lead Mentor; parents are emailed again for approval."""

    def post(self, request, program_id, pk):
        signup = get_object_or_404(TravelSignup, pk=pk, event__program=self.program)
        if not self._may_act(request.user, signup):
            messages.error(request, "You do not have permission to do that.")
            return redirect("travel:event_list", program_id=self.program.id)
        if not signup.can_be_signed_up_again:
            messages.error(request, "This signup cannot be reactivated.")
            return redirect("travel:event_list", program_id=self.program.id)
        signup.rejoin()
        signup.save()
        notify_signup_created(signup)
        messages.success(
            request, f"Reactivated {signup.student}'s signup for {signup.event.name}."
        )
        return redirect("travel:event_list", program_id=self.program.id)

    def _may_act(self, user, signup):
        role = get_user_role(user)
        if role == "LeadMentor":
            return True
        try:
            return user.student_profile.pk == signup.student_id
        except Exception:
            return False


class TravelSignupWithdrawView(LoginRequiredMixin, TravelProgramMixin, View):
    """Back out of a trip. Students back themselves out; Lead Mentors can
    withdraw on a student's behalf (e.g. they told us in person)."""

    def post(self, request, program_id, pk):
        signup = get_object_or_404(TravelSignup, pk=pk, event__program=self.program)
        if not self._may_act(request.user, signup):
            messages.error(request, "You do not have permission to do that.")
            return redirect("travel:event_list", program_id=self.program.id)
        if not signup.can_be_withdrawn:
            messages.error(
                request, "This signup can't be withdrawn in its current state."
            )
            return redirect("travel:event_list", program_id=self.program.id)
        name = signup.student
        event = signup.event
        signup.withdraw()
        signup.save()
        notify_signup_withdrawn(signup)
        messages.info(
            request, f"{name} is no longer going to {event.name} (status emails sent)."
        )
        return redirect("travel:event_list", program_id=self.program.id)

    def _may_act(self, user, signup):
        role = get_user_role(user)
        if role == "LeadMentor":
            return True
        try:
            return user.student_profile.pk == signup.student_id
        except Exception:
            return False


class _ParentSignupActionView(LoginRequiredMixin, TravelProgramMixin, View):
    """Base for approve/decline/undo: the actor must be a parent (guardian) of
    the signup's student. Lead Mentors explicitly cannot approve/decline."""

    def _parent(self, user):
        try:
            return user.adult_profile
        except Exception:
            return None

    def _may_act(self, user, signup):
        parent = self._parent(user)
        if parent is None:
            return False
        return parent.students.filter(pk=signup.student_id).exists()


class TravelSignupApproveView(_ParentSignupActionView):
    def post(self, request, program_id, pk):
        signup = get_object_or_404(TravelSignup, pk=pk, event__program=self.program)
        if not self._may_act(request.user, signup):
            messages.error(request, "Only a parent of this student can approve travel.")
            return redirect("travel:event_list", program_id=self.program.id)
        if not signup.can_be_approved:
            messages.error(request, "This signup can't be approved right now.")
            return redirect("travel:event_list", program_id=self.program.id)

        form = TravelApprovalForm(
            request.POST,
            requires_form=signup.event.requires_permission_form,
        )
        if not form.is_valid():
            messages.error(request, form.errors.as_text().strip())
            return redirect("travel:event_list", program_id=self.program.id)

        parent = self._parent(request.user)
        try:
            signup.approve(
                parent,
                permission_acknowledged=form.cleaned_data.get(
                    "permission_acknowledged", False
                ),
                acknowledged_by=parent.display_name,
            )
            signup.save()
        except Exception as exc:  # noqa: BLE001
            messages.error(request, f"Could not approve: {exc}")
            return redirect("travel:event_list", program_id=self.program.id)

        notify_signup_approved(signup)
        messages.success(
            request,
            f"Approved {signup.student} for {signup.event.name} at "
            f"${signup.approved_total}.",
        )
        return redirect("travel:event_list", program_id=self.program.id)


class TravelSignupDeclineView(_ParentSignupActionView):
    def post(self, request, program_id, pk):
        signup = get_object_or_404(TravelSignup, pk=pk, event__program=self.program)
        if not self._may_act(request.user, signup):
            messages.error(request, "Only a parent of this student can decline travel.")
            return redirect("travel:event_list", program_id=self.program.id)
        if not signup.can_be_declined:
            messages.error(request, "This signup can't be declined right now.")
            return redirect("travel:event_list", program_id=self.program.id)
        signup.decline()
        signup.save()
        notify_signup_declined(signup)
        messages.info(request, f"Declined {signup.student} for {signup.event.name}.")
        return redirect("travel:event_list", program_id=self.program.id)


class TravelSignupUndoView(_ParentSignupActionView):
    """Parent rescinds an earlier approve/decline -> back to 'interested'."""

    def post(self, request, program_id, pk):
        signup = get_object_or_404(TravelSignup, pk=pk, event__program=self.program)
        if not self._may_act(request.user, signup):
            messages.error(
                request, "Only a parent of this student can update this decision."
            )
            return redirect("travel:event_list", program_id=self.program.id)
        if not signup.can_be_undone:
            messages.error(request, "This decision can't be undone right now.")
            return redirect("travel:event_list", program_id=self.program.id)
        signup.undo_decision()
        signup.save()
        notify_signup_decision_undone(signup)
        messages.info(
            request,
            f"Decision for {signup.student} / {signup.event.name} was reset; "
            "it is awaiting approval again.",
        )
        return redirect("travel:event_list", program_id=self.program.id)


class TravelMentorSignupView(LoginRequiredMixin, TravelProgramMixin, View):
    """A mentor volunteers to go on the trip."""

    def post(self, request, program_id, pk):
        event = get_object_or_404(TravelEvent, pk=pk, program=self.program)
        if event.is_past:
            messages.error(request, "This trip has already ended.")
            return redirect("travel:event_list", program_id=self.program.id)
        if not user_is_mentor_or_lead(request.user):
            messages.error(request, "Only mentors can sign up to go on trips.")
            return redirect("travel:event_list", program_id=self.program.id)
        try:
            adult = request.user.adult_profile
        except Exception:
            messages.error(
                request,
                "We couldn't find your mentor profile to sign you up. "
                "Please contact a Lead Mentor.",
            )
            return redirect("travel:event_list", program_id=self.program.id)

        departure_id = request.POST.get("departure")
        departure = None
        if departure_id:
            departure = event.departures.filter(pk=departure_id).first()
            if departure is None:
                departure = (
                    event.ordered_departures[0]
                    if event.departures.count() == 1
                    else None
                )
        if departure is None and event.departures.count() == 1:
            departure = event.ordered_departures[0]

        signup, created = TravelMentorSignup.objects.get_or_create(
            adult=adult, event=event, defaults={"departure": departure}
        )
        if not created and signup.departure_id != (departure.id if departure else None):
            signup.departure = departure
            signup.save(update_fields=["departure"])
        if created:
            messages.success(
                request,
                f"Thanks! You're signed up to go to {event.name}.",
            )
            notify_mentor_signup_created(signup)
        else:
            messages.info(
                request,
                f"You are already signed up to go to {event.name}.",
            )
        return redirect("travel:event_list", program_id=self.program.id)


class TravelMentorCancelView(LoginRequiredMixin, TravelProgramMixin, View):
    def post(self, request, program_id, pk):
        event = get_object_or_404(TravelEvent, pk=pk, program=self.program)
        try:
            adult = request.user.adult_profile
            signup = TravelMentorSignup.objects.get(adult=adult, event=event)
        except Exception:
            messages.error(request, "You are not signed up to go on this trip.")
            return redirect("travel:event_list", program_id=self.program.id)
        signup.delete()
        notify_mentor_signup_cancelled(signup)
        messages.info(request, f"You're no longer going to {event.name}.")
        return redirect("travel:event_list", program_id=self.program.id)


class TravelMentorDriverToggleView(
    LoginRequiredMixin, TravelProgramMixin, LeadMentorRequiredMixin, View
):
    """Lead mentors mark which mentors are driving."""

    def post(self, request, program_id, pk):
        signup = get_object_or_404(
            TravelMentorSignup, pk=pk, event__program=self.program
        )
        signup.is_driver = not signup.is_driver
        signup.save(update_fields=["is_driver"])
        messages.success(
            request,
            f"{signup.adult} {'is now marked as a driver' if signup.is_driver else 'is no longer marked as a driver'}.",
        )
        return redirect("travel:event_list", program_id=self.program.id)


def _resolve_departure_for_event(event, post):
    """Pick the departure for ``post`` (field ``departure``) if it belongs to
    the event; fall back to the single departure when the trip has one."""
    departure_id = post.get("departure")
    if departure_id:
        departure = event.departures.filter(pk=departure_id).first()
        if departure is not None:
            return departure
    if event.departures.count() == 1:
        return event.ordered_departures[0]
    return None


class TravelParentChaperoneSignupView(LoginRequiredMixin, TravelProgramMixin, View):
    """A parent signs up to chaperone a trip, or a Lead Mentor adds a parent.

    Eligibility: the parent must have at least one active student in the trip's
    program. Lead Mentors pass the parent's id via the ``adult`` field; anyone
    else signs up themselves (their linked ``Adult`` is used)."""

    def post(self, request, program_id, pk):
        event = get_object_or_404(TravelEvent, pk=pk, program=self.program)
        if event.is_past:
            messages.error(request, "This trip has already ended.")
            return redirect("travel:event_list", program_id=self.program.id)

        adult = self._resolve_adult(request, event)
        if adult is None:
            return redirect("travel:event_list", program_id=self.program.id)

        departure = _resolve_departure_for_event(event, request.POST)
        signup, created = TravelParentChaperoneSignup.objects.get_or_create(
            adult=adult, event=event, defaults={"departure": departure}
        )
        if not created and signup.departure_id != (departure.id if departure else None):
            signup.departure = departure
            signup.save(update_fields=["departure"])

        if created:
            messages.success(
                request,
                f"{adult.display_name} is signed up to chaperone {event.name}.",
            )
            notify_parent_chaperone_signup_created(signup)
        else:
            messages.info(
                request,
                f"{adult.display_name} is already signed up to chaperone {event.name}.",
            )
        return redirect("travel:event_list", program_id=self.program.id)

    def _resolve_adult(self, request, event):
        """Return the Adult being signed up, or None (an error message was
        added). Lead Mentors pick any eligible parent; other users sign up as
        themselves and must be eligible."""
        role = get_user_role(request.user)
        if role == "LeadMentor":
            adult = (
                TravelParentChaperoneSignup.eligible_adults(self.program)
                .filter(pk=request.POST.get("adult"))
                .first()
            )
            if adult is None:
                messages.error(request, "That parent isn't eligible to chaperone.")
            return adult
        try:
            adult = request.user.adult_profile
        except Exception:
            adult = None
        if (
            adult is None
            or not TravelParentChaperoneSignup.eligible_adults(self.program)
            .filter(pk=adult.pk)
            .exists()
        ):
            messages.error(
                request,
                "Only parents of a student in this program can sign up to chaperone.",
            )
            return None
        return adult


class TravelParentChaperoneCancelView(LoginRequiredMixin, TravelProgramMixin, View):
    """A parent backs out of their own chaperone sign-up."""

    def post(self, request, program_id, pk):
        event = get_object_or_404(TravelEvent, pk=pk, program=self.program)
        try:
            adult = request.user.adult_profile
            signup = TravelParentChaperoneSignup.objects.get(adult=adult, event=event)
        except Exception:
            messages.error(request, "You are not signed up to chaperone this trip.")
            return redirect("travel:event_list", program_id=self.program.id)
        signup.delete()
        notify_parent_chaperone_signup_cancelled(signup)
        messages.info(
            request, f"{adult.display_name} is no longer chaperoning {event.name}."
        )
        return redirect("travel:event_list", program_id=self.program.id)


class TravelParentChaperoneRemoveView(
    LoginRequiredMixin, TravelProgramMixin, LeadMentorRequiredMixin, View
):
    """Lead Mentors remove a parent from a trip's chaperone list."""

    def post(self, request, program_id, pk):
        signup = get_object_or_404(
            TravelParentChaperoneSignup, pk=pk, event__program=self.program
        )
        name, event_name = signup.adult.display_name, signup.event.name
        signup.delete()
        notify_parent_chaperone_signup_cancelled(signup)
        messages.info(request, f"{name} is no longer chaperoning {event_name}.")
        return redirect("travel:event_list", program_id=self.program.id)
