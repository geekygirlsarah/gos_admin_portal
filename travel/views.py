import logging
from datetime import date
from pathlib import Path

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.core import signing
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Case, F, IntegerField, Prefetch, Q, Value, When
from django.db.models.functions import Coalesce, Lower, NullIf
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.utils import timezone
from django.views.generic import CreateView, DeleteView, ListView, UpdateView, View

from audit.events import AuditEvent
from audit.mixins import AuditedDeleteMixin
from audit.service import log_deletion, log_event
from programs.models import AdultStudentRelationship, Program, Student
from programs.permission_views import (
    LeadMentorRequiredMixin,
    can_user_delete,
    can_user_read,
    can_user_write,
    get_user_role,
    user_is_lead_mentor,
    user_is_mentor_or_lead,
    user_is_parent,
)
from programs.utils import active_students_in_program
from programs.views.mixins import DynamicReadPermissionMixin
from travel.forms import (
    TravelApprovalForm,
    TravelCostOptionFormSet,
    TravelDepartureFormSet,
    TravelEventForm,
    TravelMentorStaffSignupForm,
    TravelParentChaperoneStaffForm,
    TravelSignedPermissionFormUpload,
    TravelSignupDepartureForm,
    TravelSignupForm,
    TravelStaffSignupForm,
)
from travel.models import (
    TravelCostOption,
    TravelDeparture,
    TravelEvent,
    TravelMentorSignup,
    TravelParentChaperoneSignup,
    TravelReapprovalSnapshot,
    TravelReminderDelivery,
    TravelSignedPermissionForm,
    TravelSignup,
)
from travel.services import (
    build_trip_cancellation_notice,
    notify_approved_trip_changes,
    notify_mentor_signup_cancelled,
    notify_mentor_signup_created,
    notify_parent_chaperone_signup_cancelled,
    notify_parent_chaperone_signup_created,
    notify_signed_permission_uploaded,
    notify_signup_approved,
    notify_signup_choices_updated,
    notify_signup_created,
    notify_signup_decision_undone,
    notify_signup_declined,
    notify_signup_departure_changed,
    notify_signup_withdrawn,
    notify_trip_cancelled,
    trip_change_summary,
)
from travel.utils import signed_permission_form_prefetches

logger = logging.getLogger(__name__)


def _notify_signup_created_after_commit(request, signup):
    try:
        sent = notify_signup_created(signup)
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "Travel signup notification delivery failed "
            "(signup_id=%s, event_id=%s, error_type=%s).",
            signup.pk,
            signup.event_id,
            type(exc).__name__,
        )
        sent = False
    if sent is not True:
        messages.warning(
            request,
            "The signup was saved, but its email notice could not be confirmed "
            "for every recipient. Review family contact details and follow up directly.",
        )


def _notify_with_warning(request, notifier, *args, **kwargs):
    try:
        sent = notifier(*args, **kwargs)
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "Travel notification delivery failed (action=%s, error_type=%s).",
            notifier.__name__,
            type(exc).__name__,
        )
        sent = False
    if sent is not True:
        messages.warning(
            request,
            "The change was saved, but one or more email notices could not be "
            "confirmed. Review contact details and email history, then follow up directly.",
        )
    return sent


class TravelProgramMixin:
    """404 unless the program has the ``travel`` feature enabled, and inject
    the program into context/success URLs.

    Students/Parents only reach the travel pages when the program has the
    ``travel`` feature enabled; mentors and Lead Mentors may always access
    them (follows the ``orders`` pattern).
    """

    def dispatch(self, request, *args, **kwargs):
        org = getattr(request, "organization", None)
        self.program = get_object_or_404(
            Program.objects.for_organization(org), pk=kwargs.get("program_id")
        )
        is_mentor = user_is_mentor_or_lead(request.user, org=org)
        if not self.program.features.filter(key="travel").exists() and not is_mentor:
            raise Http404("Travel is not enabled for this program.")
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["program"] = self.program
        return context

    def get_success_url(self):
        return reverse("travel:event_list", kwargs={"program_id": self.program.id})


def _local_travel_signups(organization, program):
    return (
        TravelSignup.objects.for_organization(organization)
        .filter(
            organization=organization,
            event__organization=organization,
            event__program=program,
            event__program__organization=organization,
            student__organization=organization,
        )
        .filter(
            Q(departure__isnull=True)
            | Q(
                departure__organization=organization,
                departure__event_id=F("event_id"),
            )
        )
    )


def _locked_local_travel_signup(organization, program, pk):
    signup = get_object_or_404(_local_travel_signups(organization, program), pk=pk)
    event = get_object_or_404(
        TravelEvent.objects.for_organization(organization).select_for_update(),
        pk=signup.event_id,
        program=program,
        organization=organization,
    )
    return get_object_or_404(
        _local_travel_signups(organization, program)
        .select_for_update(of=("self",))
        .select_related("student", "event", "departure"),
        pk=pk,
        event=event,
    )


def _current_signed_forms(organization):
    return signed_permission_form_prefetches(organization)[0]


def _outdated_signed_forms(organization):
    return signed_permission_form_prefetches(organization)[1]


class MentorTripDetailsReadPermissionMixin:
    def dispatch(self, request, *args, **kwargs):
        org = getattr(request, "organization", None)
        if get_user_role(request.user, org=org) == "Mentor" and not can_user_read(
            request.user, "travel_details", self.program, org=org
        ):
            messages.error(
                request, "You do not have permission to access that section."
            )
            return redirect("home")
        return super().dispatch(request, *args, **kwargs)


class TravelEventListView(
    LoginRequiredMixin,
    TravelProgramMixin,
    MentorTripDetailsReadPermissionMixin,
    DynamicReadPermissionMixin,
    ListView,
):
    model = TravelEvent
    template_name = "travel/event_list.html"
    context_object_name = "events"
    section = "travel"

    def get_queryset(self):
        org = getattr(self.request, "organization", None)
        signups_qs = (
            TravelSignup.objects.for_organization(org)
            .filter(
                event__program=self.program,
                event__organization=org,
                student__organization=org,
            )
            .filter(
                Q(departure__isnull=True)
                | Q(departure__organization=org, departure__event_id=F("event_id"))
            )
            .select_related("student", "departure", "approved_by", "event")
            .prefetch_related(
                "cost_items",
                _current_signed_forms(org),
                _outdated_signed_forms(org),
                Prefetch(
                    "student__adultstudentrelationship_set",
                    queryset=AdultStudentRelationship.objects.for_organization(org)
                    .filter(
                        organization=org,
                        adult__organization=org,
                        student__organization=org,
                        adult__is_incomplete_application_profile=False,
                        student__is_incomplete_application_profile=False,
                    )
                    .select_related("adult", "student"),
                    to_attr="tenant_contact_relationships",
                ),
                Prefetch(
                    "reminder_deliveries",
                    queryset=TravelReminderDelivery.objects.for_organization(org)
                    .filter(
                        organization=org,
                        status__in=(
                            TravelReminderDelivery.Status.PENDING,
                            TravelReminderDelivery.Status.FAILED,
                            TravelReminderDelivery.Status.UNKNOWN,
                            TravelReminderDelivery.Status.NO_RECIPIENTS,
                        ),
                    )
                    .order_by("-created_at"),
                    to_attr="travel_reminder_delivery_problems",
                ),
            )
            .order_by(
                Case(
                    When(status=TravelSignup.APPROVED, then=Value(0)),
                    When(status=TravelSignup.INTERESTED, then=Value(1)),
                    When(status=TravelSignup.DECLINED, then=Value(2)),
                    When(status=TravelSignup.WITHDRAWN, then=Value(3)),
                    default=Value(4),
                    output_field=IntegerField(),
                ),
                Lower(
                    Coalesce(
                        NullIf("student__preferred_first_name", Value("")),
                        "student__legal_first_name",
                    )
                ),
                Lower("student__last_name"),
                "student__pk",
            )
        )
        departures_qs = TravelDeparture.objects.for_organization(org).filter(
            event__organization=org
        )
        cost_options_qs = TravelCostOption.objects.for_organization(org).filter(
            event__organization=org
        )
        mentors_qs = (
            TravelMentorSignup.objects.for_organization(org)
            .filter(
                event__program=self.program,
                event__organization=org,
                adult__organization=org,
            )
            .select_related("adult", "departure", "event")
            .order_by(
                Lower(
                    Coalesce(
                        NullIf("adult__preferred_first_name", Value("")),
                        "adult__legal_first_name",
                    )
                ),
                Lower("adult__last_name"),
            )
        )
        chaperones_qs = (
            TravelParentChaperoneSignup.objects.for_organization(org)
            .filter(
                event__program=self.program,
                event__organization=org,
                adult__organization=org,
            )
            .select_related("adult", "departure", "event")
            .order_by(
                Lower(
                    Coalesce(
                        NullIf("adult__preferred_first_name", Value("")),
                        "adult__legal_first_name",
                    )
                ),
                Lower("adult__last_name"),
            )
        )
        return (
            TravelEvent.objects.for_organization(
                getattr(self.request, "organization", None)
            )
            .filter(program=self.program)
            .prefetch_related(
                Prefetch("departures", queryset=departures_qs),
                Prefetch("cost_options", queryset=cost_options_qs),
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
                requires_departure=e.requires_departure,
            )
            for e in events
        }

    def _signup_edit_forms(self, signups):
        return {
            signup.pk: TravelSignupForm(
                instance=signup,
                departures=signup.event.departures.all(),
                cost_options=signup.event.cost_options.all(),
                requires_departure=signup.event.requires_departure,
            )
            for signup in signups
        }

    def _staff_forms(self, events):
        return {
            e.id: TravelStaffSignupForm(
                program=self.program,
                departures=e.departures.all(),
                cost_options=e.cost_options.all(),
                requires_departure=e.requires_departure,
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
                TravelParentChaperoneSignup.objects.for_organization(
                    getattr(self.request, "organization", None)
                )
                .filter(
                    adult=adult,
                    adult__organization=getattr(self.request, "organization", None),
                    event__organization=getattr(self.request, "organization", None),
                    event__program=self.program,
                )
                .select_related("event", "departure")
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
        org = getattr(self.request, "organization", None)
        role = get_user_role(user, org=org)
        context["user_role"] = role

        events = [e for e in self.get_queryset() if e.start_date is not None]
        for event in events:
            active_signups = []
            for signup in event.signups.all():
                signup.travel_readiness_requirements = signup.readiness_requirements
                signup.readiness_action_labels = signup.outstanding_readiness_labels
                signup.travel_ready = signup.is_ready_for_travel
                if signup.status in (TravelSignup.INTERESTED, TravelSignup.APPROVED):
                    active_signups.append(signup)
                eligible_guardians = [
                    adult
                    for adult in signup.student.all_parents
                    if adult.login_enabled
                    and adult.email_updates
                    and (adult.personal_email or "").strip()
                ]
                signup.guardian_contact_missing = not eligible_guardians
                signup.reminder_delivery_problems = getattr(
                    signup, "travel_reminder_delivery_problems", []
                )
            event.travel_active_signup_count = len(active_signups)
            event.travel_ready_signup_count = sum(
                signup.travel_ready for signup in active_signups
            )
            event.travel_outstanding_signups = [
                signup for signup in active_signups if not signup.travel_ready
            ]
        events.sort(key=lambda e: (e.start_date or date.max, e.name))
        upcoming_events = [e for e in events if not e.is_past]
        past_events = [e for e in events if e.is_past]
        past_events.sort(key=lambda e: (e.start_date or date.min, e.name), reverse=True)

        context["upcoming_events"] = upcoming_events
        context["past_events"] = past_events
        context["can_manage"] = False
        context["viewer_is_mentor"] = user_is_mentor_or_lead(user, org=org)
        context["viewer_is_parent"] = user_is_parent(user, org=org)

        # Trip details are editable by mentors when configured; trip rosters
        # and deletion remain Lead-Mentor-only.
        is_lead = user_is_lead_mentor(user, org=org)
        context["is_lead"] = is_lead
        context["can_manage"] = is_lead
        context["can_view_student_signups"] = is_lead or role == "Mentor"
        can_edit = is_lead or (
            role == "Mentor"
            and can_user_write(user, "travel_details", self.program, org=org)
        )
        context["can_edit"] = can_edit
        context["can_add"] = can_edit
        context["staff_signup_forms"] = (
            self._staff_forms(upcoming_events) if is_lead else {}
        )
        context["mentor_staff_forms"] = (
            {
                event.id: TravelMentorStaffSignupForm(
                    program=self.program,
                    event=event,
                    departures=event.departures.all(),
                    prefix=f"mentor-{event.id}",
                )
                for event in upcoming_events
            }
            if is_lead
            else {}
        )

        # ── Student view ─────────────────────────────────────────────────────
        if role == "Student":
            try:
                student = user.student_profile
            except Student.DoesNotExist:
                student = None
            if student is not None:
                org = getattr(self.request, "organization", None)
                signups = list(
                    _local_travel_signups(org, self.program)
                    .filter(student=student)
                    .select_related("student", "event", "departure", "approved_by")
                    .prefetch_related(
                        "cost_items",
                        _current_signed_forms(org),
                        _outdated_signed_forms(org),
                    )
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
                context["signup_edit_forms"] = self._signup_edit_forms(
                    [
                        signup
                        for signup in signups
                        if signup.status
                        in (TravelSignup.INTERESTED, TravelSignup.APPROVED)
                        and not signup.event.is_past
                    ]
                )
                context["can_sign_up"] = True
            else:
                context["signup_by_event"] = {}

        # ── Parent view (approvals live here) ────────────────────────────────
        try:
            adult = user.adult_profile
        except Exception:
            adult = None
        if (
            adult is not None
            and user_is_parent(user, org=org)
            and adult.organization_id == org.pk
        ):
            parent_rows = []
            local_child_ids = set(
                AdultStudentRelationship.objects.for_organization(org)
                .filter(adult=adult, student__organization=org)
                .values_list("student_id", flat=True)
            )
            children = [
                child for child in adult.all_students() if child.pk in local_child_ids
            ]
            children.sort(
                key=lambda s: (
                    (s.preferred_first_name or s.legal_first_name or "").lower(),
                    (s.last_name or "").lower(),
                )
            )
            signups_by_student = {}
            signups = (
                _local_travel_signups(org, self.program)
                .filter(student_id__in=local_child_ids)
                .select_related("student", "event", "departure", "approved_by")
                .prefetch_related(
                    "cost_items",
                    Prefetch(
                        "event__cost_options",
                        queryset=TravelCostOption.objects.for_organization(org).filter(
                            event__organization=org
                        ),
                    ),
                    _current_signed_forms(org),
                    _outdated_signed_forms(org),
                )
            )
            for signup in signups:
                signups_by_student.setdefault(signup.student_id, []).append(signup)
            for child in children:
                signups = signups_by_student.get(child.pk, [])
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
        if user_is_mentor_or_lead(user, org=org) and adult is not None:
            mentor_signups = list(
                TravelMentorSignup.objects.for_organization(org)
                .filter(
                    adult=adult,
                    adult__organization=org,
                    event__organization=org,
                    event__program=self.program,
                )
                .select_related("event", "departure")
            )
            context["my_mentor_signups"] = mentor_signups
            context["mentor_signup_event_ids"] = {s.event_id for s in mentor_signups}

        # ── Parent chaperones ─────────────────────────────────────────────────
        context.update(self._chaperone_context(adult, is_lead, upcoming_events))

        return context


class TravelInlineFormsetsMixin:
    """Handle the departures and cost-options inline formsets on one form."""

    def get_inline_formset_querysets(self):
        organization = getattr(self.request, "organization", None)
        if organization is None or self.object is None:
            return TravelDeparture.objects.none(), TravelCostOption.objects.none()
        return (
            TravelDeparture.objects.for_organization(organization).filter(
                event=self.object,
                event__organization=organization,
            ),
            TravelCostOption.objects.for_organization(organization).filter(
                event=self.object,
                event__organization=organization,
            ),
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        departure_queryset, cost_queryset = self.get_inline_formset_querysets()
        if self.request.method == "POST":
            context["departure_formset"] = TravelDepartureFormSet(
                self.request.POST,
                instance=self.object,
                prefix="departures",
                queryset=departure_queryset,
            )
            context["cost_formset"] = TravelCostOptionFormSet(
                self.request.POST,
                instance=self.object,
                prefix="costs",
                queryset=cost_queryset,
            )
        else:
            context["departure_formset"] = TravelDepartureFormSet(
                instance=self.object,
                prefix="departures",
                queryset=departure_queryset,
            )
            context["cost_formset"] = TravelCostOptionFormSet(
                instance=self.object,
                prefix="costs",
                queryset=cost_queryset,
            )
        return context

    def _update_approved_signup_charges(self, departure_changes=None):
        departure_changes = departure_changes or {}
        organization = self.object.organization
        local_cost_options = TravelCostOption.objects.for_organization(
            organization
        ).filter(
            organization=organization,
            event=self.object,
            event__organization=organization,
        )
        signups = (
            TravelSignup.objects.for_organization(organization)
            .filter(
                event=self.object,
                event__organization=organization,
                event__program=self.program,
                event__program__organization=organization,
                student__organization=organization,
                status=TravelSignup.APPROVED,
            )
            .filter(
                Q(departure__isnull=True)
                | Q(
                    departure__organization=organization,
                    departure__event_id=F("event_id"),
                )
            )
            .select_for_update(of=("self",))
            .select_related("student", "event", "departure")
            .prefetch_related(
                Prefetch("cost_items", queryset=local_cost_options),
                Prefetch("event__cost_options", queryset=local_cost_options),
            )
            .order_by("pk")
        )
        updates = []
        for signup in signups:
            if signup.approved_total is None and signup.pk not in departure_changes:
                continue
            previous_total = signup.approved_total
            new_total = signup.total_cost
            cost_changed = (
                previous_total != new_total
                if previous_total is not None
                else signup.balance_amount != new_total
            )
            previous_departure = departure_changes.get(signup.pk)
            if not cost_changed and previous_departure is None:
                continue

            before = {
                "status": signup.status,
                "approved_total": str(previous_total),
                "approved_by_id": signup.approved_by_id,
                "approved_at": (
                    signup.approved_at.isoformat() if signup.approved_at else None
                ),
                "balance_amount": (
                    str(signup.balance_amount)
                    if signup.balance_amount is not None
                    else None
                ),
                "balance_effective_date": (
                    signup.balance_effective_date.isoformat()
                    if signup.balance_effective_date
                    else None
                ),
                "departure_id": signup.departure_id,
            }
            signup.status = TravelSignup.INTERESTED
            signup.approved_by = None
            signup.approved_at = None
            signup.approved_total = None
            update_fields = [
                "status",
                "approved_by",
                "approved_at",
                "approved_total",
                "updated_at",
            ]
            after = {
                "status": signup.status,
                "approved_total": None,
                "approved_by_id": None,
                "approved_at": None,
                "balance_amount": (
                    str(signup.balance_amount)
                    if signup.balance_amount is not None
                    else None
                ),
                "departure_id": signup.departure_id,
            }
            if cost_changed:
                effective_date = timezone.localdate()
                signup.balance_amount = new_total
                signup.balance_effective_date = effective_date
                update_fields.extend(["balance_amount", "balance_effective_date"])
                after["balance_amount"] = str(new_total)
                after["balance_effective_date"] = effective_date.isoformat()
            signup.save(update_fields=update_fields)

            audit_event = (
                AuditEvent.TRAVEL_CHARGE_UPDATED
                if cost_changed
                else AuditEvent.TRAVEL_APPROVAL_REOPENED
            )
            log_event(
                event=audit_event,
                resource=signup,
                request=self.request,
                organization=organization,
                before=before,
                after=after,
                notes=(
                    "Travel fee total changed; guardian reapproval is required."
                    if cost_changed
                    else "Selected departure details changed; guardian reapproval is required."
                ),
            )
            update = {
                "signup_id": signup.pk,
                "previous_total": previous_total,
                "new_total": new_total,
                "cost_changed": cost_changed,
                "departure_changed": previous_departure is not None,
                "approval_reopened": True,
            }
            if previous_departure is not None:
                update["previous_departure"] = previous_departure
                update["current_departure"] = (
                    signup.departure.display_label
                    if signup.departure_id
                    else "No specific departure"
                )
            updates.append(update)
        return updates

    def _pending_signup_totals(self):
        organization = self.object.organization
        local_cost_options = TravelCostOption.objects.for_organization(
            organization
        ).filter(
            organization=organization,
            event=self.object,
            event__organization=organization,
        )
        signups = (
            TravelSignup.objects.for_organization(organization)
            .filter(
                organization=organization,
                event=self.object,
                event__organization=organization,
                event__program=self.program,
                event__program__organization=organization,
                student__organization=organization,
                status=TravelSignup.INTERESTED,
            )
            .filter(
                Q(departure__isnull=True)
                | Q(
                    departure__organization=organization,
                    departure__event=self.object,
                )
            )
            .select_related("event")
            .prefetch_related(
                Prefetch("cost_items", queryset=local_cost_options),
                Prefetch("event__cost_options", queryset=local_cost_options),
            )
        )
        return {signup.pk: signup.total_cost for signup in signups}

    def form_valid(self, form):
        context = self.get_context_data(form=form)
        departure_formset = context["departure_formset"]
        cost_formset = context["cost_formset"]
        if not departure_formset.is_valid() or not cost_formset.is_valid():
            return self.render_to_response(context)
        is_update = self.object is not None and self.object.pk is not None
        changes = (
            trip_change_summary(form, departure_formset, cost_formset)
            if is_update
            else []
        )
        changed_departure_ids = {
            inline_form.instance.pk
            for inline_form in departure_formset.forms
            if inline_form.instance.pk
            and inline_form.has_changed()
            and (
                any(field != "DELETE" for field in inline_form.changed_data)
                or inline_form.cleaned_data.get("DELETE", False)
            )
        }
        with transaction.atomic():
            response = super().form_valid(form)
            pending_totals_before = (
                self._pending_signup_totals() if is_update and changes else {}
            )
            departure_changes = {}
            if is_update and changed_departure_ids:
                affected_signups = (
                    TravelSignup.objects.for_organization(self.object.organization)
                    .filter(
                        organization=self.object.organization,
                        event=self.object,
                        event__organization=self.object.organization,
                        event__program__organization=self.object.organization,
                        student__organization=self.object.organization,
                        departure_id__in=changed_departure_ids,
                        status=TravelSignup.APPROVED,
                    )
                    .filter(
                        departure__organization=self.object.organization,
                        departure__event=self.object,
                    )
                    .select_for_update(of=("self",))
                    .select_related("departure")
                    .order_by("pk")
                )
                departure_changes = {
                    signup.pk: signup.departure.display_label
                    for signup in affected_signups
                }
            for formset in (departure_formset, cost_formset):
                formset.instance = self.object
                for inline_form in formset.forms:
                    if (
                        inline_form.instance._state.adding
                        and inline_form.has_changed()
                        and not inline_form.cleaned_data.get("DELETE", False)
                    ):
                        inline_form.instance.organization = self.object.organization
                formset.save()
            signup_updates = (
                self._update_approved_signup_charges(departure_changes)
                if is_update and changes
                else []
            )
            updated_signup_ids = {update["signup_id"] for update in signup_updates}
            pending_totals_after = (
                self._pending_signup_totals() if pending_totals_before else {}
            )
            for signup_id, previous_total in pending_totals_before.items():
                if signup_id in updated_signup_ids:
                    continue
                current_total = pending_totals_after.get(signup_id)
                if current_total is None or current_total == previous_total:
                    continue
                signup_updates.append(
                    {
                        "signup_id": signup_id,
                        "previous_total": previous_total,
                        "new_total": current_total,
                        "cost_changed": True,
                        "departure_changed": False,
                        "approval_reopened": False,
                    }
                )
            if is_update and changes:
                transaction.on_commit(
                    lambda event_id=self.object.pk, organization_id=self.object.organization_id, changes=changes, signup_updates=signup_updates: _notify_with_warning(
                        self.request,
                        notify_approved_trip_changes,
                        event_id,
                        organization_id,
                        changes,
                        signup_updates=signup_updates,
                    )
                )
        if is_update and changes:
            messages.info(
                self.request,
                "Trip updated. Update emails may be sent to students with pending or "
                "approved sign-ups and their eligible parents/guardians, as well as signed-up "
                "mentors and parent chaperones with email updates enabled, when "
                "email addresses are available.",
            )
        return response


class MentorTripDetailsWritePermissionMixin(UserPassesTestMixin):
    def test_func(self):
        org = getattr(self.request, "organization", None)
        role = get_user_role(self.request.user, org=org)
        if role == "LeadMentor":
            return True
        return role == "Mentor" and can_user_write(
            self.request.user, "travel_details", self.program, org=org
        )

    def handle_no_permission(self):
        if self.request.user.is_authenticated:
            messages.error(
                self.request, "You do not have permission to access that section."
            )
            return redirect("home")
        return super().handle_no_permission()


class TravelEventCreateView(
    LoginRequiredMixin,
    TravelProgramMixin,
    MentorTripDetailsWritePermissionMixin,
    TravelInlineFormsetsMixin,
    CreateView,
):
    model = TravelEvent
    form_class = TravelEventForm
    template_name = "travel/event_form.html"

    def form_valid(self, form):
        form.instance.program = self.program
        form.instance.organization = self.program.organization
        return super().form_valid(form)


class TravelEventUpdateView(
    LoginRequiredMixin,
    TravelProgramMixin,
    MentorTripDetailsWritePermissionMixin,
    TravelInlineFormsetsMixin,
    UpdateView,
):
    model = TravelEvent
    form_class = TravelEventForm
    template_name = "travel/event_form.html"

    def get_queryset(self):
        org = getattr(self.request, "organization", None)
        return TravelEvent.objects.for_organization(org).filter(program=self.program)


class TravelEventDeleteView(
    LoginRequiredMixin, TravelProgramMixin, AuditedDeleteMixin, DeleteView
):
    model = TravelEvent
    template_name = "travel/event_confirm_delete.html"

    def get_queryset(self):
        org = getattr(self.request, "organization", None)
        return TravelEvent.objects.for_organization(org).filter(program=self.program)

    def audit_snapshot(self, obj):
        return {
            "name": obj.name,
            "start_date": str(obj.start_date),
            "end_date": str(obj.end_date),
            "program_id": obj.program_id,
        }

    def post(self, request, *args, **kwargs):
        with transaction.atomic():
            event = get_object_or_404(
                self.get_queryset().select_for_update(), pk=kwargs.get("pk")
            )
            departures = list(
                TravelDeparture.objects.select_for_update().filter(event=event)
            )
            cost_options = list(
                TravelCostOption.objects.select_for_update().filter(event=event)
            )
            departure_ids = [departure.pk for departure in departures]
            cost_option_ids = [option.pk for option in cost_options]

            related_signup_ids = set(
                TravelSignup.objects.filter(departure_id__in=departure_ids).values_list(
                    "pk", flat=True
                )
            )
            if cost_option_ids:
                related_signup_ids.update(
                    TravelSignup.objects.filter(
                        cost_items__in=cost_option_ids
                    ).values_list("pk", flat=True)
                )

            signups = list(
                TravelSignup.objects.select_for_update()
                .filter(event=event)
                .select_related(
                    "organization", "student", "event__program", "departure"
                )
            )
            related_signups = list(
                TravelSignup.objects.select_for_update().filter(
                    pk__in=related_signup_ids
                )
            )
            mentor_signups = list(
                TravelMentorSignup.objects.select_for_update()
                .filter(event=event)
                .select_related(
                    "organization",
                    "adult__user",
                    "adult",
                    "event__program",
                    "departure",
                )
            )
            parent_signups = list(
                TravelParentChaperoneSignup.objects.select_for_update()
                .filter(event=event)
                .select_related(
                    "organization",
                    "adult__user",
                    "adult",
                    "event__program",
                    "departure",
                )
            )
            signed_forms = list(
                TravelSignedPermissionForm.objects.select_for_update(of=("self",))
                .filter(signup__event=event)
                .select_related("signup__student", "signup__event__program")
            )
            approval_snapshots = list(
                TravelReapprovalSnapshot.objects.select_for_update().filter(
                    signup__event=event
                )
            )
            has_foreign_records = any(
                row.organization_id != event.organization_id
                for row in [
                    *departures,
                    *cost_options,
                    *signups,
                    *related_signups,
                    *mentor_signups,
                    *parent_signups,
                    *signed_forms,
                    *approval_snapshots,
                ]
            ) or any(
                row.signup.organization_id != event.organization_id
                or row.signup.student.organization_id != event.organization_id
                or row.signup.event.program_id is None
                or row.signup.event.program.organization_id != event.organization_id
                for row in signed_forms
            )
            if has_foreign_records:
                messages.error(
                    request,
                    "This trip cannot be deleted because related records need review.",
                )
                return redirect("travel:event_list", program_id=self.program.id)

            self.object = event
            signed_files = [
                (row.file.storage, row.file.name) for row in signed_forms if row.file
            ]
            for _, name in signed_files:
                if (
                    TravelSignedPermissionForm.objects.filter(file=name)
                    .exclude(pk__in=[row.pk for row in signed_forms])
                    .exists()
                ):
                    messages.error(
                        request,
                        "This trip cannot be deleted because related records need review.",
                    )
                    return redirect("travel:event_list", program_id=self.program.id)
            cancellation_notice = build_trip_cancellation_notice(
                event,
                signups,
                mentor_signups,
                parent_signups,
            )
            transaction.on_commit(
                lambda notice=cancellation_notice: _notify_with_warning(
                    request, notify_trip_cancelled, notice
                )
            )
            return super().post(request, *args, **kwargs)

    def dispatch(self, request, *args, **kwargs):
        org = getattr(request, "organization", None)
        self.program = get_object_or_404(
            Program.objects.for_organization(org), pk=kwargs.get("program_id")
        )
        if not self.program.features.filter(key="travel").exists():
            raise Http404("Travel is not enabled for this program.")
        obj = self.get_object()
        if not can_user_delete(request.user, "travel", obj, org=org):
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
        org = getattr(request, "organization", None)
        event = get_object_or_404(
            TravelEvent.objects.for_organization(org), pk=pk, program=self.program
        )
        if event.is_past:
            messages.error(request, "This trip has already ended.")
            return redirect("travel:event_list", program_id=self.program.id)

        student = self._resolve_student(request)
        if student is None:
            return redirect("travel:event_list", program_id=self.program.id)

        existing = (
            TravelSignup.objects.for_organization(org)
            .filter(student=student, event=event)
            .first()
        )
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

        choices = self._validated_choices(event, request.POST)
        if choices is None:
            messages.error(request, "Choose valid travel options for this trip.")
            return redirect("travel:event_list", program_id=self.program.id)
        departure, cost_items = choices

        signup = TravelSignup(
            student=student, event=event, departure=departure, organization=org
        )
        try:
            with transaction.atomic():
                signup.save()
                signup.cost_items.set(cost_items)
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "Travel signup could not be saved (event_id=%s, error_type=%s).",
                event.pk,
                type(exc).__name__,
            )
            messages.error(
                request,
                "Could not complete signup. Please try again or contact a lead mentor.",
            )
        else:
            messages.success(request, f"{student} is signed up for {event.name}.")
            transaction.on_commit(
                lambda: _notify_signup_created_after_commit(request, signup)
            )

        return redirect("travel:event_list", program_id=self.program.id)

    def _resolve_student(self, request):
        """Return the Student this request signs up. None means the request was
        rejected (an error message was already added). Lead Mentors add any
        active student by id; students sign up for themselves."""
        org = getattr(request, "organization", None)
        if get_user_role(request.user, org=org) == "LeadMentor":
            student_id = request.POST.get("student")
            if not student_id:
                return None
            student = (
                active_students_in_program(self.program, organization=org)
                .filter(pk=student_id)
                .first()
            )
            if student is None:
                messages.error(
                    request,
                    "That student is not an active student of this program.",
                )
            return student
        student = (
            active_students_in_program(self.program, organization=org)
            .filter(user=request.user)
            .first()
        )
        if student is None:
            messages.error(
                request,
                "Only active students in this program can sign up for trips.",
            )
        return student

    def _validated_choices(self, event, post):
        organization = event.organization
        departures = TravelDeparture.objects.for_organization(organization).filter(
            event=event,
            event__organization=organization,
        )
        cost_options = TravelCostOption.objects.for_organization(organization).filter(
            event=event,
            event__organization=organization,
        )
        form = TravelSignupForm(
            post,
            instance=TravelSignup(event=event, organization=organization),
            departures=departures,
            cost_options=cost_options,
            requires_departure=event.requires_departure,
        )
        if not form.is_valid():
            return None
        departure = form.cleaned_data.get("departure")
        if departure is None and departures.count() == 1:
            departure = departures.first()
        return (
            departure,
            list(form.cleaned_data.get("cost_items", [])),
        )


class TravelSignupEditView(LoginRequiredMixin, TravelProgramMixin, View):
    """Let a student update their active departure and optional cost choices."""

    def post(self, request, program_id, pk):
        organization = getattr(request, "organization", None)
        if get_user_role(request.user, org=organization) != "Student":
            messages.error(request, "Only students can edit their own trip choices.")
            return redirect("travel:event_list", program_id=self.program.id)

        student = (
            active_students_in_program(self.program, organization=organization)
            .filter(user=request.user, organization=organization)
            .first()
        )
        if student is None:
            messages.error(
                request, "Only active students in this program can edit sign-ups."
            )
            return redirect("travel:event_list", program_id=self.program.id)

        signup_queryset = _local_travel_signups(organization, self.program).filter(
            student=student,
            status__in=(TravelSignup.INTERESTED, TravelSignup.APPROVED),
        )
        with transaction.atomic():
            signup = get_object_or_404(
                signup_queryset.select_for_update(of=("self",)).select_related(
                    "event", "event__program", "departure"
                ),
                pk=pk,
            )
            if signup.event.is_past:
                messages.error(request, "This trip has already ended.")
                return redirect("travel:event_list", program_id=self.program.id)
            if signup.cost_items.exclude(
                organization=organization, event=signup.event
            ).exists():
                messages.error(
                    request,
                    "This signup has cost records that require organization review.",
                )
                return redirect("travel:event_list", program_id=self.program.id)

            departures = TravelDeparture.objects.for_organization(organization).filter(
                event=signup.event,
                event__organization=organization,
            )
            cost_options = TravelCostOption.objects.for_organization(
                organization
            ).filter(event=signup.event, event__organization=organization)
            previous_departure_id = signup.departure_id
            previous_departure = signup.departure
            previous_costs = list(
                signup.cost_items.filter(
                    organization=organization, event=signup.event
                ).order_by("sort_order", "pk")
            )
            previous_total = signup.total_cost
            previous_charge = (
                signup.balance_amount
                if signup.balance_amount is not None
                else signup.approved_total
            )
            previous_total_for_notice = (
                previous_charge if previous_charge is not None else previous_total
            )
            form = TravelSignupForm(
                request.POST,
                instance=signup,
                departures=departures,
                cost_options=cost_options,
                requires_departure=signup.event.requires_departure,
            )
            if not form.is_valid():
                messages.error(request, "Choose valid travel options for this trip.")
                return redirect("travel:event_list", program_id=self.program.id)

            selected_costs = list(form.cleaned_data["cost_items"])
            previous_optional_ids = {
                option.pk for option in previous_costs if not option.is_required
            }
            selected_optional_ids = {
                option.pk for option in selected_costs if not option.is_required
            }
            selected_departure = form.cleaned_data["departure"]
            departure_changed = previous_departure_id != getattr(
                selected_departure, "pk", None
            )
            if not departure_changed and previous_optional_ids == selected_optional_ids:
                messages.info(request, "Your trip choices have not changed.")
                return redirect("travel:event_list", program_id=self.program.id)

            was_approved = signup.status == TravelSignup.APPROVED
            before = {
                "status": signup.status,
                "approved_total": (
                    str(signup.approved_total)
                    if signup.approved_total is not None
                    else None
                ),
                "approved_by_id": signup.approved_by_id,
                "approved_at": (
                    signup.approved_at.isoformat() if signup.approved_at else None
                ),
                "balance_amount": (
                    str(signup.balance_amount)
                    if signup.balance_amount is not None
                    else None
                ),
                "balance_effective_date": (
                    signup.balance_effective_date.isoformat()
                    if signup.balance_effective_date
                    else None
                ),
            }

            signup.departure = selected_departure
            update_fields = ["departure", "updated_at"]
            if was_approved:
                signup.status = TravelSignup.INTERESTED
                signup.approved_by = None
                signup.approved_at = None
                signup.approved_total = None
                update_fields.extend(
                    [
                        "status",
                        "approved_by",
                        "approved_at",
                        "approved_total",
                    ]
                )
            signup.save(update_fields=update_fields)
            signup.cost_items.set(selected_costs)
            new_total = signup.total_cost

            charge_tracked = was_approved or signup.balance_amount is not None
            charge_changed = charge_tracked and signup.balance_amount != new_total
            if charge_changed:
                effective_date = timezone.localdate()
                signup.balance_amount = new_total
                signup.balance_effective_date = effective_date
                signup.save(
                    update_fields=[
                        "balance_amount",
                        "balance_effective_date",
                        "updated_at",
                    ]
                )
                log_event(
                    event=AuditEvent.TRAVEL_CHARGE_UPDATED,
                    resource=signup,
                    request=request,
                    organization=organization,
                    before=before,
                    after={
                        "status": signup.status,
                        "approved_total": (
                            None
                            if was_approved
                            else (
                                str(signup.approved_total)
                                if signup.approved_total is not None
                                else None
                            )
                        ),
                        "approved_by_id": signup.approved_by_id,
                        "approved_at": (
                            signup.approved_at.isoformat()
                            if signup.approved_at
                            else None
                        ),
                        "balance_amount": str(new_total),
                        "balance_effective_date": effective_date.isoformat(),
                    },
                    notes="Student travel choices changed the current trip charge.",
                )

            previous_cost_labels = [option.name for option in previous_costs]
            selected_cost_labels = [option.name for option in selected_costs]
            previous_departure_label = (
                previous_departure.display_label
                if previous_departure
                else "No specific departure"
            )
            selected_departure_label = (
                selected_departure.display_label
                if selected_departure
                else "No specific departure"
            )
            transaction.on_commit(
                lambda signup_id=signup.pk, organization_id=organization.pk, previous_departure=previous_departure_label, selected_departure=selected_departure_label, previous_costs=previous_cost_labels, selected_costs=selected_cost_labels, previous_total=previous_total_for_notice, new_total=new_total: _notify_with_warning(
                    request,
                    notify_signup_choices_updated,
                    signup_id,
                    organization_id,
                    previous_departure,
                    selected_departure,
                    previous_costs,
                    selected_costs,
                    previous_total,
                    new_total,
                )
            )

        messages.success(request, "Your trip choices were updated.")
        return redirect("travel:event_list", program_id=self.program.id)


class TravelSignupDepartureView(
    LoginRequiredMixin, TravelProgramMixin, LeadMentorRequiredMixin, View
):
    """Let a Lead Mentor reassign a student's departure without changing approval."""

    def post(self, request, program_id, pk):
        organization = getattr(request, "organization", None)
        signup_queryset = _local_travel_signups(organization, self.program).filter(
            status__in=(TravelSignup.INTERESTED, TravelSignup.APPROVED)
        )
        with transaction.atomic():
            signup = get_object_or_404(
                signup_queryset.select_for_update(of=("self",)).select_related(
                    "event", "event__program", "departure"
                ),
                pk=pk,
            )
            if signup.event.is_past:
                messages.error(request, "This trip has already ended.")
                return redirect("travel:event_list", program_id=self.program.id)

            previous_departure_id = signup.departure_id
            previous_departure = signup.departure
            departures = TravelDeparture.objects.for_organization(organization).filter(
                event=signup.event,
                event__organization=organization,
            )
            form = TravelSignupDepartureForm(
                request.POST,
                instance=signup,
                departures=departures,
                requires_departure=signup.event.requires_departure,
            )
            if not form.is_valid():
                messages.error(request, "Choose a valid departure for this trip.")
                return redirect("travel:event_list", program_id=self.program.id)

            new_departure = form.cleaned_data["departure"]
            if previous_departure_id == (new_departure.pk if new_departure else None):
                messages.info(request, "The student's departure has not changed.")
                return redirect("travel:event_list", program_id=self.program.id)

            previous_label = (
                previous_departure.display_label
                if previous_departure
                else "No specific departure"
            )
            new_label = (
                new_departure.display_label
                if new_departure
                else "No specific departure"
            )
            signup.departure = new_departure
            update_fields = ["departure", "updated_at"]
            if signup.status == TravelSignup.APPROVED:
                signup.status = TravelSignup.INTERESTED
                signup.approved_by = None
                signup.approved_at = None
                signup.approved_total = None
                update_fields.extend(
                    ["status", "approved_by", "approved_at", "approved_total"]
                )
            signup.save(update_fields=update_fields)
            transaction.on_commit(
                lambda signup_id=signup.pk, organization_id=organization.pk, previous_departure=previous_label, selected_departure=new_label: _notify_with_warning(
                    request,
                    notify_signup_departure_changed,
                    signup_id,
                    organization_id,
                    previous_departure,
                    selected_departure,
                )
            )

        messages.success(
            request,
            f"{signup.student}'s departure was updated. Guardian approval is required again.",
        )
        return redirect("travel:event_list", program_id=self.program.id)


def _can_access_signed_permission(user, signup, organization):
    if user_is_lead_mentor(user, org=organization):
        return True
    if not user_is_parent(user, org=organization):
        return False
    parent = getattr(user, "adult_profile", None)
    return bool(
        parent
        and parent.is_parent
        and parent.organization_id == organization.pk
        and AdultStudentRelationship.objects.for_organization(organization)
        .filter(
            organization=organization,
            adult=parent,
            student=signup.student,
            student__organization=organization,
        )
        .exists()
    )


class TravelSignedPermissionUploadView(LoginRequiredMixin, TravelProgramMixin, View):
    def post(self, request, program_id, pk):
        organization = getattr(request, "organization", None)
        with transaction.atomic():
            signup = get_object_or_404(
                _local_travel_signups(organization, self.program).select_related(
                    "event", "student"
                ),
                pk=pk,
            )
            if not _can_access_signed_permission(request.user, signup, organization):
                raise Http404
            if not signup.can_be_withdrawn:
                raise Http404
            event = get_object_or_404(
                TravelEvent.objects.for_organization(organization).select_for_update(),
                pk=signup.event_id,
                program=self.program,
            )
            form = TravelSignedPermissionFormUpload(request.POST, request.FILES)
            if (
                not event.requires_permission_form
                or not form.is_valid()
                or form.cleaned_data["form_version"] != event.permission_form_version
            ):
                messages.error(
                    request,
                    "Choose a valid signed form for the current trip form (PDF, JPEG, or PNG, up to 10 MB).",
                )
                return redirect(self.get_success_url())
            TravelSignedPermissionForm.objects.create(
                organization=organization,
                signup=signup,
                form_version=event.permission_form_version,
                file=form.cleaned_data["file"],
                uploaded_by=request.user,
            )
            transaction.on_commit(
                lambda signup_id=signup.pk, organization_id=organization.pk: _notify_with_warning(
                    request,
                    notify_signed_permission_uploaded,
                    signup_id,
                    organization_id,
                )
            )
        messages.success(
            request,
            "Signed permission form uploaded. Approval of the current trip choices is a separate step.",
        )
        return redirect(self.get_success_url())


class TravelSignedPermissionDownloadView(LoginRequiredMixin, TravelProgramMixin, View):
    def get(self, request, program_id, pk):
        organization = getattr(request, "organization", None)
        document = get_object_or_404(
            TravelSignedPermissionForm.objects.for_organization(organization)
            .filter(
                organization=organization,
                signup__in=_local_travel_signups(organization, self.program),
            )
            .select_related("signup__student"),
            pk=pk,
        )
        if not _can_access_signed_permission(
            request.user, document.signup, organization
        ):
            raise Http404
        response = FileResponse(
            document.file.open("rb"),
            as_attachment=True,
            filename=Path(document.file.name).name,
            content_type="application/octet-stream",
        )
        response["Cache-Control"] = "private, no-store"
        response["X-Content-Type-Options"] = "nosniff"
        return response


class TravelSignupRejoinView(LoginRequiredMixin, TravelProgramMixin, View):
    """A withdrawn signup can be reactivated to 'interested' by the student or
    a Lead Mentor; parents are emailed again for approval."""

    def post(self, request, program_id, pk):
        org = getattr(request, "organization", None)
        with transaction.atomic():
            signup = _locked_local_travel_signup(org, self.program, pk)
            if not self._may_act(request.user, signup, org=org):
                messages.error(request, "You do not have permission to do that.")
                return redirect("travel:event_list", program_id=self.program.id)
            if not signup.can_be_signed_up_again:
                messages.error(request, "This signup cannot be reactivated.")
                return redirect("travel:event_list", program_id=self.program.id)
            signup.rejoin()
            signup.save()
        transaction.on_commit(
            lambda: _notify_signup_created_after_commit(request, signup)
        )
        messages.success(
            request, f"Reactivated {signup.student}'s signup for {signup.event.name}."
        )
        return redirect("travel:event_list", program_id=self.program.id)

    def _may_act(self, user, signup, org=None):
        role = get_user_role(user, org=org)
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
        org = getattr(request, "organization", None)
        with transaction.atomic():
            signup = _locked_local_travel_signup(org, self.program, pk)
            if not self._may_act(request.user, signup, org=org):
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
        _notify_with_warning(request, notify_signup_withdrawn, signup)
        messages.info(request, f"{name} is no longer going to {event.name}.")
        return redirect("travel:event_list", program_id=self.program.id)

    def _may_act(self, user, signup, org=None):
        role = get_user_role(user, org=org)
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
        organization = getattr(self.request, "organization", None)
        parent = self._parent(user)
        if (
            parent is None
            or organization is None
            or not user_is_parent(user, org=organization)
            or not parent.is_parent
            or parent.organization_id != organization.pk
        ):
            return False
        return (
            AdultStudentRelationship.objects.for_organization(organization)
            .filter(
                organization=organization,
                adult=parent,
                student=signup.student,
                student__organization=organization,
            )
            .exists()
        )


class TravelSignupApproveView(_ParentSignupActionView):
    def post(self, request, program_id, pk):
        org = getattr(request, "organization", None)
        signup = get_object_or_404(
            _local_travel_signups(org, self.program),
            pk=pk,
            event__program=self.program,
        )
        if not self._may_act(request.user, signup):
            messages.error(request, "Only a parent of this student can approve travel.")
            return redirect("travel:event_list", program_id=self.program.id)
        with transaction.atomic():
            event = get_object_or_404(
                TravelEvent.objects.for_organization(org).select_for_update(),
                pk=signup.event_id,
                program=self.program,
            )
            signup = get_object_or_404(
                _local_travel_signups(org, self.program)
                .select_for_update(of=("self",))
                .select_related("event", "departure", "student"),
                pk=pk,
                event=event,
            )
            if not self._may_act(request.user, signup):
                messages.error(
                    request, "Only a parent of this student can approve travel."
                )
                return redirect("travel:event_list", program_id=self.program.id)
            if not signup.can_be_approved:
                messages.error(request, "This signup can't be approved right now.")
                return redirect("travel:event_list", program_id=self.program.id)
            try:
                snapshot = signing.loads(
                    request.POST.get("approval_token", ""), salt="travel.approval"
                )
            except signing.BadSignature:
                snapshot = None
            if snapshot != signup.approval_snapshot:
                messages.error(
                    request,
                    "Trip details changed or the approval page expired. Review the current details before approving.",
                )
                return redirect("travel:event_list", program_id=self.program.id)

            form = TravelApprovalForm(
                request.POST,
                requires_form=signup.needs_permission_acknowledgment,
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
            except ValidationError as exc:
                messages.error(request, f"Could not approve: {exc}")
                return redirect("travel:event_list", program_id=self.program.id)

        _notify_with_warning(request, notify_signup_approved, signup)
        messages.success(
            request,
            f"Approved {signup.student} for {signup.event.name} at "
            f"${signup.approved_total}.",
        )
        return redirect("travel:event_list", program_id=self.program.id)


class TravelSignupDeclineView(_ParentSignupActionView):
    def post(self, request, program_id, pk):
        org = getattr(request, "organization", None)
        with transaction.atomic():
            signup = _locked_local_travel_signup(org, self.program, pk)
            if not self._may_act(request.user, signup):
                messages.error(
                    request, "Only a parent of this student can decline travel."
                )
                return redirect("travel:event_list", program_id=self.program.id)
            if not signup.can_be_declined:
                messages.error(request, "This signup can't be declined right now.")
                return redirect("travel:event_list", program_id=self.program.id)
            signup.decline()
            signup.save()
        _notify_with_warning(request, notify_signup_declined, signup)
        messages.info(request, f"Declined {signup.student} for {signup.event.name}.")
        return redirect("travel:event_list", program_id=self.program.id)


class TravelSignupUndoView(_ParentSignupActionView):
    """Parent rescinds an earlier approve/decline -> back to 'interested'."""

    def post(self, request, program_id, pk):
        org = getattr(request, "organization", None)
        with transaction.atomic():
            signup = _locked_local_travel_signup(org, self.program, pk)
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
        _notify_with_warning(request, notify_signup_decision_undone, signup)
        messages.info(
            request,
            f"Decision for {signup.student} / {signup.event.name} was reset; "
            "it is awaiting approval again.",
        )
        return redirect("travel:event_list", program_id=self.program.id)


class TravelMentorStaffSignupView(
    LoginRequiredMixin, TravelProgramMixin, LeadMentorRequiredMixin, View
):
    """Lead Mentors add another active mentor to a trip."""

    def post(self, request, program_id, pk):
        org = getattr(request, "organization", None)
        event = get_object_or_404(
            TravelEvent.objects.for_organization(org),
            pk=pk,
            program=self.program,
            organization=org,
        )
        if event.is_past:
            messages.error(request, "This trip has already ended.")
            return redirect("travel:event_list", program_id=self.program.id)

        departures = TravelDeparture.objects.for_organization(org).filter(
            event=event,
            event__organization=org,
        )
        form = TravelMentorStaffSignupForm(
            request.POST,
            program=self.program,
            event=event,
            departures=departures,
            prefix=f"mentor-{event.id}",
        )
        if not form.is_valid():
            messages.error(request, "Choose an active mentor for this trip.")
            return redirect("travel:event_list", program_id=self.program.id)

        adult = form.cleaned_data["adult"]
        if (
            TravelMentorSignup.objects.filter(event=event, adult=adult)
            .exclude(organization=org)
            .exists()
        ):
            messages.error(request, "An existing signup requires organization review.")
            return redirect("travel:event_list", program_id=self.program.id)

        departure = form.cleaned_data.get("departure") or _resolve_departure_for_event(
            event, request.POST
        )
        signup, created = TravelMentorSignup.objects.for_organization(
            org
        ).get_or_create(
            organization=org,
            adult=adult,
            event=event,
            defaults={"departure": departure},
        )
        if not created and signup.departure_id != (departure.id if departure else None):
            signup.departure = departure
            signup.save(update_fields=["departure"])
        if created:
            _notify_with_warning(request, notify_mentor_signup_created, signup)
            messages.success(
                request,
                f"{adult.display_name} has been added to {event.name}.",
            )
        return redirect("travel:event_list", program_id=self.program.id)


class TravelMentorSignupView(LoginRequiredMixin, TravelProgramMixin, View):
    """A mentor volunteers to go on the trip."""

    def post(self, request, program_id, pk):
        org = getattr(request, "organization", None)
        event = get_object_or_404(
            TravelEvent.objects.for_organization(org), pk=pk, program=self.program
        )
        if event.is_past:
            messages.error(request, "This trip has already ended.")
            return redirect("travel:event_list", program_id=self.program.id)
        if not user_is_mentor_or_lead(request.user, org=request.organization):
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
        if adult.organization_id != org.pk:
            messages.error(
                request,
                "Your mentor profile does not belong to this organization.",
            )
            return redirect("travel:event_list", program_id=self.program.id)
        if (
            TravelMentorSignup.objects.filter(event=event, adult=adult)
            .exclude(organization=org)
            .exists()
        ):
            messages.error(request, "An existing signup requires organization review.")
            return redirect("travel:event_list", program_id=self.program.id)

        departure = _resolve_departure_for_event(event, request.POST)

        signup, created = TravelMentorSignup.objects.for_organization(
            org
        ).get_or_create(
            organization=org,
            adult=adult,
            event=event,
            defaults={"departure": departure},
        )
        if not created and signup.departure_id != (departure.id if departure else None):
            signup.departure = departure
            signup.save(update_fields=["departure"])
        if created:
            messages.success(
                request,
                f"Thanks! You're signed up to go to {event.name}.",
            )
            _notify_with_warning(request, notify_mentor_signup_created, signup)
        else:
            messages.info(
                request,
                f"You are already signed up to go to {event.name}.",
            )
        return redirect("travel:event_list", program_id=self.program.id)


class TravelMentorCancelView(LoginRequiredMixin, TravelProgramMixin, View):
    def post(self, request, program_id, pk):
        org = getattr(request, "organization", None)
        event = get_object_or_404(
            TravelEvent.objects.for_organization(org), pk=pk, program=self.program
        )
        try:
            adult = request.user.adult_profile
            signup = TravelMentorSignup.objects.for_organization(org).get(
                adult=adult, adult__organization=org, event=event
            )
        except Exception:
            messages.error(request, "You are not signed up to go on this trip.")
            return redirect("travel:event_list", program_id=self.program.id)
        log_deletion(
            resource=signup,
            request=request,
            before={"adult": str(signup.adult), "event": event.name},
            notes=f"Mentor cancelled travel signup for '{event.name}'.",
        )
        signup.delete()
        _notify_with_warning(request, notify_mentor_signup_cancelled, signup)
        messages.info(request, f"You're no longer going to {event.name}.")
        return redirect("travel:event_list", program_id=self.program.id)


class TravelMentorDriverToggleView(
    LoginRequiredMixin, TravelProgramMixin, LeadMentorRequiredMixin, View
):
    """Lead mentors mark which mentors are driving."""

    def post(self, request, program_id, pk):
        org = getattr(request, "organization", None)
        signup = get_object_or_404(
            TravelMentorSignup.objects.for_organization(org),
            pk=pk,
            event__program=self.program,
            adult__organization=org,
            event__organization=org,
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
    organization = event.organization
    departures = TravelDeparture.objects.for_organization(organization).filter(
        event=event,
        event__organization=organization,
    )
    departure_id = post.get("departure")
    if departure_id:
        departure = departures.filter(pk=departure_id).first()
        if departure is not None:
            return departure
    if departures.count() == 1:
        return departures.first()
    return None


class TravelParentChaperoneSignupView(LoginRequiredMixin, TravelProgramMixin, View):
    """A parent signs up to chaperone a trip, or a Lead Mentor adds a parent.

    Eligibility: the parent must have at least one active student in the trip's
    program. Lead Mentors pass the parent's id via the ``adult`` field; anyone
    else signs up themselves (their linked ``Adult`` is used)."""

    def post(self, request, program_id, pk):
        org = getattr(request, "organization", None)
        event = get_object_or_404(
            TravelEvent.objects.for_organization(org), pk=pk, program=self.program
        )
        if event.is_past:
            messages.error(request, "This trip has already ended.")
            return redirect("travel:event_list", program_id=self.program.id)

        adult = self._resolve_adult(request, event)
        if adult is None:
            return redirect("travel:event_list", program_id=self.program.id)
        if adult.organization_id != org.pk:
            messages.error(request, "That parent isn't eligible to chaperone.")
            return redirect("travel:event_list", program_id=self.program.id)
        if (
            TravelParentChaperoneSignup.objects.filter(event=event, adult=adult)
            .exclude(organization=org)
            .exists()
        ):
            messages.error(request, "An existing signup requires organization review.")
            return redirect("travel:event_list", program_id=self.program.id)

        departure = _resolve_departure_for_event(event, request.POST)
        signup, created = TravelParentChaperoneSignup.objects.for_organization(
            org
        ).get_or_create(
            organization=org,
            adult=adult,
            event=event,
            defaults={"departure": departure},
        )
        if not created and signup.departure_id != (departure.id if departure else None):
            signup.departure = departure
            signup.save(update_fields=["departure"])

        if created:
            messages.success(
                request,
                f"{adult.display_name} is signed up to chaperone {event.name}.",
            )
            _notify_with_warning(
                request, notify_parent_chaperone_signup_created, signup
            )
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
        role = get_user_role(request.user, org=getattr(request, "organization", None))
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
            or adult.organization_id != self.program.organization_id
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
        org = getattr(request, "organization", None)
        event = get_object_or_404(
            TravelEvent.objects.for_organization(org), pk=pk, program=self.program
        )
        try:
            adult = request.user.adult_profile
            signup = TravelParentChaperoneSignup.objects.for_organization(org).get(
                adult=adult, adult__organization=org, event=event
            )
        except Exception:
            messages.error(request, "You are not signed up to chaperone this trip.")
            return redirect("travel:event_list", program_id=self.program.id)
        log_deletion(
            resource=signup,
            request=request,
            before={"adult": str(signup.adult), "event": event.name},
            notes=f"Parent cancelled chaperone signup for '{event.name}'.",
        )
        signup.delete()
        _notify_with_warning(request, notify_parent_chaperone_signup_cancelled, signup)
        messages.info(
            request, f"{adult.display_name} is no longer chaperoning {event.name}."
        )
        return redirect("travel:event_list", program_id=self.program.id)


class TravelParentChaperoneRemoveView(
    LoginRequiredMixin, TravelProgramMixin, LeadMentorRequiredMixin, View
):
    """Lead Mentors remove a parent from a trip's chaperone list."""

    def post(self, request, program_id, pk):
        org = getattr(request, "organization", None)
        signup = get_object_or_404(
            TravelParentChaperoneSignup.objects.for_organization(org),
            pk=pk,
            event__program=self.program,
            adult__organization=org,
            event__organization=org,
        )
        name, event_name = signup.adult.display_name, signup.event.name
        log_deletion(
            resource=signup,
            request=request,
            before={"adult": str(signup.adult), "event": event_name},
            notes=f"Lead Mentor removed {name} from chaperoning '{event_name}'.",
        )
        signup.delete()
        _notify_with_warning(request, notify_parent_chaperone_signup_cancelled, signup)
        messages.info(request, f"{name} is no longer chaperoning {event_name}.")
        return redirect("travel:event_list", program_id=self.program.id)
