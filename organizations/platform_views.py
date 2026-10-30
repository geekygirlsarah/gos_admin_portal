"""Views for Platform Administration (Superuser Dashboard, Org Health, Metrics, Impersonation, and Support)."""

import logging
from datetime import timedelta

from django.contrib import messages
from django.contrib.auth import login as auth_login
from django.contrib.auth.mixins import UserPassesTestMixin
from django.contrib.auth.models import User
from django.core.exceptions import PermissionDenied
from django.db import connection
from django.db.models import Count, Q, Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from django.views import View
from django.views.generic import (
    CreateView,
    DetailView,
    ListView,
    TemplateView,
    UpdateView,
)

from audit.events import AuditEvent
from audit.models import AuditLog
from audit.service import log_event
from organizations.models import (
    Organization,
    OrganizationInvitation,
    OrganizationSettings,
    UserOrganizationMembership,
)
from organizations.platform_forms import (
    PlatformAuditLogFilterForm,
    PlatformMemberForm,
    PlatformOrganizationForm,
    PlatformUserSearchForm,
)
from programs.models import (
    Adult,
    BackgroundCheck,
    Enrollment,
    MentorAgreement,
    Program,
    Student,
)

logger = logging.getLogger(__name__)


class SuperuserRequiredMixin(UserPassesTestMixin):
    """Mixin that restricts view access strictly to active superusers."""

    def test_func(self):
        return bool(
            self.request.user.is_authenticated
            and self.request.user.is_superuser
            and self.request.user.is_active
        )

    def handle_no_permission(self):
        if not self.request.user.is_authenticated:
            return super().handle_no_permission()
        raise PermissionDenied(
            "Access to the Platform Admin Dashboard is restricted to platform superusers."
        )


def calculate_org_health(org):
    """Calculate health diagnostics and readiness metrics for an organization."""
    memberships = UserOrganizationMembership.objects.filter(
        organization=org, is_active=True
    ).select_related("user")

    lead_mentors_count = memberships.filter(role="LeadMentor").count()
    mentors_count = memberships.filter(role__in=["LeadMentor", "Mentor"]).count()
    students_count = memberships.filter(role="Student").count()
    parents_count = memberships.filter(role="Parent").count()
    total_members = memberships.count()

    active_programs_count = Program.objects.filter(
        organization=org, active=True
    ).count()
    total_programs_count = Program.objects.filter(organization=org).count()

    # Mentor compliance check
    mentor_users = [
        m.user for m in memberships.filter(role__in=["LeadMentor", "Mentor"])
    ]
    mentor_adults = list(Adult.objects.filter(user__in=mentor_users))

    # Agreements compliance
    active_agreements = MentorAgreement.get_all_active()
    if not active_agreements.exists():
        agreed_count = len(mentor_adults)
    elif mentor_adults:
        agreed_count = sum(
            1 for adult in mentor_adults if adult.has_accepted_current_agreement()
        )
    else:
        agreed_count = 0

    # Clearances compliance
    clearances_count = (
        BackgroundCheck.objects.filter(adult__in=mentor_adults, cleared=True)
        .values("adult")
        .distinct()
        .count()
        if mentor_adults
        else 0
    )

    issues = []
    if lead_mentors_count == 0:
        issues.append("No active Lead Mentors assigned")
    if active_programs_count == 0:
        issues.append("No active programs configured")
    if mentors_count > 0 and agreed_count < mentors_count:
        issues.append(
            f"{mentors_count - agreed_count} mentor(s) missing accepted agreements"
        )

    status = "healthy"
    if lead_mentors_count == 0:
        status = "danger"
    elif issues:
        status = "warning"

    return {
        "org": org,
        "lead_mentors_count": lead_mentors_count,
        "mentors_count": mentors_count,
        "students_count": students_count,
        "parents_count": parents_count,
        "total_members": total_members,
        "active_programs_count": active_programs_count,
        "total_programs_count": total_programs_count,
        "agreed_mentors_count": agreed_count,
        "clearances_count": clearances_count,
        "issues": issues,
        "status": status,
    }


def perform_isolation_health_checks():
    """Scan models to verify multi-tenant isolation and detect unscoped records."""
    checks = []

    def _check_model(name, model_cls):
        try:
            unscoped = model_cls.objects.filter(organization__isnull=True).count()
            total = model_cls.objects.count()
            checks.append(
                {
                    "model": name,
                    "total": total,
                    "unscoped": unscoped,
                    "status": "danger" if unscoped > 0 else "success",
                }
            )
        except Exception as e:
            checks.append(
                {
                    "model": name,
                    "total": 0,
                    "unscoped": 0,
                    "status": "warning",
                    "error": str(e),
                }
            )

    _check_model("Programs", Program)
    _check_model("Students", Student)
    _check_model("Adults", Adult)

    try:
        from attendance.models import Attendance

        _check_model("Attendance", Attendance)
    except ImportError:
        pass

    try:
        from applications.models import Application

        _check_model("Applications", Application)
    except ImportError:
        pass

    try:
        from outreach.models import OutreachEvent

        _check_model("Outreach Events", OutreachEvent)
    except ImportError:
        pass

    try:
        from orders.models import Order

        _check_model("Purchase Orders", Order)
    except ImportError:
        pass

    try:
        from travel.models import TravelEvent

        _check_model("Travel Events", TravelEvent)
    except ImportError:
        pass

    return checks


class PlatformDashboardView(SuperuserRequiredMixin, TemplateView):
    """Executive Platform Admin Overview Dashboard."""

    template_name = "platform_admin/dashboard.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        all_orgs = list(Organization.objects.all().order_by("name"))

        org_health_data = [calculate_org_health(org) for org in all_orgs]

        total_orgs = len(all_orgs)
        active_orgs = sum(1 for o in all_orgs if o.is_active)
        total_users = User.objects.count()
        active_users = User.objects.filter(is_active=True).count()
        total_programs = Program.objects.count()
        active_programs = Program.objects.filter(active=True).count()
        total_enrollments = Enrollment.objects.filter(active=True).count()

        # Recent Platform Audit Logs
        recent_audit_logs = (
            AuditLog.objects.all().select_related("actor").order_by("-timestamp")[:10]
        )

        context.update(
            {
                "total_orgs": total_orgs,
                "active_orgs": active_orgs,
                "inactive_orgs": total_orgs - active_orgs,
                "total_users": total_users,
                "active_users": active_users,
                "total_programs": total_programs,
                "active_programs": active_programs,
                "total_enrollments": total_enrollments,
                "org_health_data": org_health_data,
                "recent_audit_logs": recent_audit_logs,
            }
        )
        return context


class PlatformOrganizationListView(SuperuserRequiredMixin, ListView):
    """List and manage all organizations across the platform."""

    model = Organization
    template_name = "platform_admin/organizations/list.html"
    context_object_name = "organizations"
    paginate_by = 20

    def get_queryset(self):
        qs = Organization.objects.all().order_by("name")
        q = self.request.GET.get("q", "").strip()
        status = self.request.GET.get("status", "")

        if q:
            qs = qs.filter(
                Q(name__icontains=q) | Q(slug__icontains=q) | Q(subdomain__icontains=q)
            )
        if status == "active":
            qs = qs.filter(is_active=True)
        elif status == "inactive":
            qs = qs.filter(is_active=False)

        return qs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        # Augment with health and member stats
        org_items = []
        for org in context["organizations"]:
            health = calculate_org_health(org)
            org_items.append({"org": org, "health": health})
        context["org_items"] = org_items
        context["q"] = self.request.GET.get("q", "")
        context["status"] = self.request.GET.get("status", "")
        return context


class PlatformOrganizationCreateView(SuperuserRequiredMixin, CreateView):
    """Create a new tenant organization with branding settings and optional initial Lead Mentor."""

    model = Organization
    form_class = PlatformOrganizationForm
    template_name = "platform_admin/organizations/form.html"

    def form_valid(self, form):
        response = super().form_valid(form)
        org = self.object

        # Log audit event
        log_event(
            event=AuditEvent.ORGANIZATION_CREATED,
            actor=self.request.user,
            resource=org,
            notes=f"Organization '{org.name}' ({org.slug}) created by superuser {self.request.user.username}.",
        )

        # Handle initial lead mentor if specified
        lead_email = form.cleaned_data.get("initial_lead_mentor_email", "").strip()
        if lead_email:
            user = User.objects.filter(email__iexact=lead_email).first()
            if user:
                UserOrganizationMembership.objects.get_or_create(
                    user=user,
                    organization=org,
                    defaults={"role": "LeadMentor", "is_active": True},
                )
                messages.success(
                    self.request,
                    f"Organization '{org.name}' created and {user.username} assigned as Lead Mentor.",
                )
            else:
                # Create an invitation for this lead mentor
                invitation, _ = OrganizationInvitation.objects.get_or_create(
                    organization=org,
                    email=lead_email,
                    defaults={
                        "role": "LeadMentor",
                        "invited_by": self.request.user,
                        "expires_at": timezone.now() + timedelta(days=14),
                    },
                )
                messages.success(
                    self.request,
                    f"Organization '{org.name}' created. Lead Mentor invitation prepared for {lead_email}.",
                )
        else:
            messages.success(
                self.request, f"Organization '{org.name}' created successfully."
            )

        return response

    def get_success_url(self):
        return reverse(
            "platform_admin:organization_detail", kwargs={"pk": self.object.pk}
        )


class PlatformOrganizationDetailView(SuperuserRequiredMixin, DetailView):
    """Deep-dive into an organization's health, members, programs, and settings."""

    model = Organization
    template_name = "platform_admin/organizations/detail.html"
    context_object_name = "organization"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        org = self.object

        health = calculate_org_health(org)
        memberships = (
            UserOrganizationMembership.objects.filter(organization=org)
            .select_related("user")
            .order_by("role", "user__username")
        )
        programs = (
            Program.objects.filter(organization=org)
            .annotate(enrollment_count=Count("enrollment"))
            .order_by("-active", "-start_date")
        )
        invitations = (
            OrganizationInvitation.objects.filter(organization=org)
            .select_related("invited_by")
            .order_by("-created_at")
        )

        settings = getattr(org, "settings", None)

        context.update(
            {
                "health": health,
                "memberships": memberships,
                "programs": programs,
                "invitations": invitations,
                "settings": settings,
                "member_form": PlatformMemberForm(organization=org),
            }
        )
        return context


class PlatformOrganizationUpdateView(SuperuserRequiredMixin, UpdateView):
    """Edit an organization and its settings."""

    model = Organization
    form_class = PlatformOrganizationForm
    template_name = "platform_admin/organizations/form.html"

    def form_valid(self, form):
        response = super().form_valid(form)
        org = self.object

        log_event(
            event=AuditEvent.ORGANIZATION_UPDATED,
            actor=self.request.user,
            resource=org,
            notes=f"Organization '{org.name}' updated by superuser {self.request.user.username}.",
        )
        messages.success(
            self.request, f"Organization '{org.name}' updated successfully."
        )
        return response

    def get_success_url(self):
        return reverse(
            "platform_admin:organization_detail", kwargs={"pk": self.object.pk}
        )


class PlatformOrganizationToggleStatusView(SuperuserRequiredMixin, View):
    """Toggle an organization active/inactive status."""

    def post(self, request, pk):
        org = get_object_or_404(Organization, pk=pk)
        org.is_active = not org.is_active
        org.save(update_fields=["is_active", "updated_at"])

        log_event(
            event=AuditEvent.ORGANIZATION_STATUS_CHANGED,
            actor=request.user,
            resource=org,
            notes=f"Organization '{org.name}' status set to {'Active' if org.is_active else 'Inactive'} by {request.user.username}.",
        )

        status_str = "activated" if org.is_active else "deactivated"
        messages.success(
            request, f"Organization '{org.name}' was successfully {status_str}."
        )
        return redirect(
            request.POST.get("next")
            or reverse("platform_admin:organization_detail", kwargs={"pk": org.pk})
        )


class PlatformOrganizationMemberAddView(SuperuserRequiredMixin, View):
    """Add a user membership to an organization."""

    def post(self, request, pk):
        org = get_object_or_404(Organization, pk=pk)
        form = PlatformMemberForm(request.POST, organization=org)
        if form.is_valid():
            user = form.cleaned_data["user_identifier"]
            role = form.cleaned_data["role"]
            is_active = form.cleaned_data["is_active"]

            membership, created = UserOrganizationMembership.objects.update_or_create(
                user=user,
                organization=org,
                defaults={
                    "role": role,
                    "is_active": is_active,
                    "invited_by": request.user,
                },
            )
            log_event(
                event=AuditEvent.ROLE_CHANGED,
                actor=request.user,
                resource=user,
                notes=f"User '{user.username}' assigned role '{role}' in '{org.name}' by superuser.",
            )
            messages.success(
                request, f"User '{user.username}' successfully added as {role}."
            )
        else:
            for field, errors in form.errors.items():
                for err in errors:
                    messages.error(request, f"{field}: {err}")
        return redirect("platform_admin:organization_detail", pk=org.pk)


class PlatformOrganizationMemberUpdateView(SuperuserRequiredMixin, View):
    """Update a member's role or status within an organization."""

    def post(self, request, pk, member_id):
        org = get_object_or_404(Organization, pk=pk)
        membership = get_object_or_404(
            UserOrganizationMembership, pk=member_id, organization=org
        )

        role = request.POST.get("role")
        is_active = (
            request.POST.get("is_active") == "on"
            or request.POST.get("is_active") == "True"
        )

        valid_roles = [r[0] for r in UserOrganizationMembership.ROLE_CHOICES]
        if role in valid_roles:
            membership.role = role
            membership.is_active = is_active
            membership.save()

            log_event(
                event=AuditEvent.ROLE_CHANGED,
                actor=request.user,
                resource=membership.user,
                notes=f"Membership for '{membership.user.username}' in '{org.name}' updated to {role} (active={is_active}).",
            )
            messages.success(
                request, f"Membership for '{membership.user.username}' updated."
            )
        return redirect("platform_admin:organization_detail", pk=org.pk)


class PlatformOrganizationMemberDeleteView(SuperuserRequiredMixin, View):
    """Remove a user membership from an organization."""

    def post(self, request, pk, member_id):
        org = get_object_or_404(Organization, pk=pk)
        membership = get_object_or_404(
            UserOrganizationMembership, pk=member_id, organization=org
        )
        user = membership.user
        membership.delete()

        log_event(
            event=AuditEvent.ROLE_CHANGED,
            actor=request.user,
            resource=user,
            notes=f"Membership for '{user.username}' in '{org.name}' removed by superuser.",
        )
        messages.success(request, f"User '{user.username}' removed from {org.name}.")
        return redirect("platform_admin:organization_detail", pk=org.pk)


class PlatformMetricsView(SuperuserRequiredMixin, TemplateView):
    """Platform-wide Usage, Growth, and Engagement Metrics."""

    template_name = "platform_admin/metrics.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        # Users breakdown
        role_breakdown = (
            UserOrganizationMembership.objects.filter(is_active=True)
            .values("role")
            .annotate(count=Count("id"))
            .order_by("-count")
        )

        # Organizations metrics
        org_metrics = []
        for org in Organization.objects.all().order_by("name"):
            prog_count = Program.objects.filter(organization=org).count()
            student_count = Student.objects.filter(organization=org).count()
            adult_count = Adult.objects.filter(organization=org).count()
            enroll_count = Enrollment.objects.filter(
                program__organization=org, active=True
            ).count()

            org_metrics.append(
                {
                    "org": org,
                    "programs_count": prog_count,
                    "students_count": student_count,
                    "adults_count": adult_count,
                    "active_enrollments": enroll_count,
                }
            )

        # Feature usage counts
        total_attendance_sessions = 0
        total_attendance_hours = 0
        try:
            from attendance.models import Attendance, AttendanceSession

            total_attendance_sessions = AttendanceSession.objects.count()
            total_attendance_hours = (
                Attendance.objects.aggregate(total=Sum("hours"))["total"] or 0
            )
        except Exception as e:
            logger.debug("Failed to calculate attendance metrics: %s", e)

        total_applications = 0
        app_status_breakdown = []
        try:
            from applications.models import Application

            total_applications = Application.objects.count()
            app_status_breakdown = (
                Application.objects.values("status")
                .annotate(count=Count("id"))
                .order_by("-count")
            )
        except Exception as e:
            logger.debug("Failed to calculate application metrics: %s", e)

        total_orders = 0
        try:
            from orders.models import Order

            total_orders = Order.objects.count()
        except Exception as e:
            logger.debug("Failed to calculate order metrics: %s", e)

        total_outreach = 0
        try:
            from outreach.models import OutreachEvent

            total_outreach = OutreachEvent.objects.count()
        except Exception as e:
            logger.debug("Failed to calculate outreach metrics: %s", e)

        context.update(
            {
                "role_breakdown": role_breakdown,
                "org_metrics": org_metrics,
                "total_attendance_sessions": total_attendance_sessions,
                "total_attendance_hours": round(float(total_attendance_hours), 1),
                "total_applications": total_applications,
                "app_status_breakdown": app_status_breakdown,
                "total_orders": total_orders,
                "total_outreach": total_outreach,
            }
        )
        return context


class PlatformHealthCheckView(SuperuserRequiredMixin, TemplateView):
    """System & Multi-Tenant Isolation Health Diagnostics."""

    template_name = "platform_admin/health.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        # Database Check
        db_ok = False
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1")
                db_ok = True
        except Exception:
            db_ok = False

        # Tenant Isolation Checks
        isolation_checks = perform_isolation_health_checks()
        has_isolation_issues = any(c["status"] == "danger" for c in isolation_checks)

        # Mentor Compliance Check
        all_mentors = list(Adult.objects.filter(is_mentor=True))
        total_mentors = len(all_mentors)
        approved_clearances = (
            BackgroundCheck.objects.filter(adult__in=all_mentors, cleared=True)
            .values("adult")
            .distinct()
            .count()
            if total_mentors
            else 0
        )
        active_agreements = MentorAgreement.get_all_active()
        if not active_agreements.exists():
            accepted_agreements = total_mentors
        elif total_mentors:
            accepted_agreements = sum(
                1 for m in all_mentors if m.has_accepted_current_agreement()
            )
        else:
            accepted_agreements = 0

        # Organization Health Summary
        all_orgs = list(Organization.objects.all())
        org_healths = [calculate_org_health(o) for o in all_orgs]
        danger_orgs = [h for h in org_healths if h["status"] == "danger"]
        warning_orgs = [h for h in org_healths if h["status"] == "warning"]

        context.update(
            {
                "db_ok": db_ok,
                "isolation_checks": isolation_checks,
                "has_isolation_issues": has_isolation_issues,
                "total_mentors": total_mentors,
                "approved_clearances": approved_clearances,
                "accepted_agreements": accepted_agreements,
                "clearance_rate": (
                    round((approved_clearances / total_mentors * 100), 1)
                    if total_mentors
                    else 100
                ),
                "agreement_rate": (
                    round((accepted_agreements / total_mentors * 100), 1)
                    if total_mentors
                    else 100
                ),
                "org_healths": org_healths,
                "danger_orgs": danger_orgs,
                "warning_orgs": warning_orgs,
            }
        )
        return context


class PlatformUserSupportView(SuperuserRequiredMixin, ListView):
    """Global User Search, Support, Account Status, and Impersonation Directory."""

    model = User
    template_name = "platform_admin/users/support.html"
    context_object_name = "users"
    paginate_by = 25

    def get_queryset(self):
        qs = User.objects.all().order_by("-date_joined")
        form = PlatformUserSearchForm(self.request.GET)
        if form.is_valid():
            q = form.cleaned_data.get("q")
            role = form.cleaned_data.get("role")
            org = form.cleaned_data.get("organization")
            status = form.cleaned_data.get("status")

            if q:
                qs = qs.filter(
                    Q(username__icontains=q)
                    | Q(email__icontains=q)
                    | Q(first_name__icontains=q)
                    | Q(last_name__icontains=q)
                    | Q(adult_profile__phone_number__icontains=q)
                    | Q(student_profile__phone_number__icontains=q)
                    | Q(adult_profile__andrew_id__icontains=q)
                    | Q(student_profile__andrew_id__icontains=q)
                ).distinct()

            if role:
                qs = qs.filter(
                    organization_memberships__role=role,
                    organization_memberships__is_active=True,
                ).distinct()

            if org:
                qs = qs.filter(organization_memberships__organization=org).distinct()

            if status == "active":
                qs = qs.filter(is_active=True)
            elif status == "inactive":
                qs = qs.filter(is_active=False)

        return qs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["search_form"] = PlatformUserSearchForm(self.request.GET)
        return context


class PlatformUserToggleStatusView(SuperuserRequiredMixin, View):
    """Toggle a user account active/inactive."""

    def post(self, request, pk):
        user = get_object_or_404(User, pk=pk)
        if user.is_superuser:
            messages.error(
                request, "Cannot deactivate another superuser via support tools."
            )
            return redirect(
                request.POST.get("next") or reverse("platform_admin:user_support")
            )

        user.is_active = not user.is_active
        user.save(update_fields=["is_active"])

        event = (
            AuditEvent.ACCOUNT_CREATED
            if user.is_active
            else AuditEvent.ACCOUNT_DEACTIVATED
        )
        log_event(
            event=event,
            actor=request.user,
            resource=user,
            notes=f"User '{user.username}' account set to {'Active' if user.is_active else 'Inactive'} by {request.user.username}.",
        )

        status_str = "activated" if user.is_active else "deactivated"
        messages.success(
            request, f"User '{user.username}' was successfully {status_str}."
        )
        return redirect(
            request.POST.get("next") or reverse("platform_admin:user_support")
        )


class PlatformAuditLogView(SuperuserRequiredMixin, ListView):
    """Cross-Tenant Audit Log Explorer."""

    model = AuditLog
    template_name = "platform_admin/audit/list.html"
    context_object_name = "logs"
    paginate_by = 50

    def get_queryset(self):
        qs = AuditLog.objects.all().select_related("actor").order_by("-timestamp")
        form = PlatformAuditLogFilterForm(self.request.GET)
        if form.is_valid():
            event = form.cleaned_data.get("event")
            search = form.cleaned_data.get("search")
            outcome = form.cleaned_data.get("outcome")
            date_from = form.cleaned_data.get("date_from")
            date_to = form.cleaned_data.get("date_to")

            if event:
                qs = qs.filter(event=event)
            if search:
                qs = qs.filter(
                    Q(actor__username__icontains=search)
                    | Q(resource_repr__icontains=search)
                    | Q(notes__icontains=search)
                    | Q(ip_address__icontains=search)
                )
            if outcome == "success":
                qs = qs.filter(outcome="success")
            elif outcome == "failure":
                qs = qs.filter(outcome="failure")
            if date_from:
                qs = qs.filter(timestamp__date__gte=date_from)
            if date_to:
                qs = qs.filter(timestamp__date__lte=date_to)

        return qs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["filter_form"] = PlatformAuditLogFilterForm(self.request.GET)
        return context


class PlatformImpersonateUserView(SuperuserRequiredMixin, View):
    """Start impersonating a regular user for testing or troubleshooting."""

    def post(self, request, pk):
        target_user = get_object_or_404(User, pk=pk)

        # Guard: Superusers cannot be impersonated to prevent privilege loops/escalation
        if target_user.is_superuser:
            messages.error(request, "Superusers cannot be impersonated.")
            return redirect(
                request.POST.get("next") or reverse("platform_admin:user_support")
            )

        if not target_user.is_active:
            messages.error(
                request,
                f"User '{target_user.username}' is inactive and cannot be impersonated.",
            )
            return redirect(
                request.POST.get("next") or reverse("platform_admin:user_support")
            )

        original_superuser = request.user
        superuser_id = original_superuser.pk

        # Log audit event before switching session
        log_event(
            event=AuditEvent.USER_IMPERSONATED,
            actor=original_superuser,
            resource=target_user,
            notes=f"Superuser '{original_superuser.username}' initiated impersonation of '{target_user.username}' ({target_user.email}).",
        )

        # Log in target user
        auth_login(
            request, target_user, backend="django.contrib.auth.backends.ModelBackend"
        )

        # Save impersonation flag and original superuser ID in session
        request.session["impersonator_id"] = superuser_id

        # Set active organization if target user has memberships
        membership = UserOrganizationMembership.objects.filter(
            user=target_user, is_active=True
        ).first()
        if membership:
            request.session["active_organization_id"] = membership.organization_id

        messages.info(
            request,
            f"You are now impersonating {target_user.get_full_name() or target_user.username} ({target_user.email}).",
        )
        return redirect("profile_dashboard")


class PlatformStopImpersonationView(View):
    """Stop active user impersonation and return to the superuser session."""

    def post(self, request):
        return self._stop_impersonation(request)

    def get(self, request):
        return self._stop_impersonation(request)

    def _stop_impersonation(self, request):
        impersonator_id = request.session.get("impersonator_id")
        if not impersonator_id:
            messages.warning(request, "No active impersonation session found.")
            return redirect("home")

        superuser = get_object_or_404(
            User, pk=impersonator_id, is_superuser=True, is_active=True
        )
        impersonated_user = request.user

        log_event(
            event=AuditEvent.IMPERSONATION_ENDED,
            actor=superuser,
            resource=impersonated_user,
            notes=f"Superuser '{superuser.username}' ended impersonation of '{impersonated_user.username}'.",
        )

        # Log superuser back in
        auth_login(
            request, superuser, backend="django.contrib.auth.backends.ModelBackend"
        )

        if "impersonator_id" in request.session:
            del request.session["impersonator_id"]

        messages.success(
            request, "Impersonation session ended. Returned to platform administrator."
        )
        return redirect("platform_admin:dashboard")


class PlatformSwitchContextView(SuperuserRequiredMixin, View):
    """Switch active tenant organization context for the platform superuser."""

    def post(self, request, pk):
        org = get_object_or_404(Organization, pk=pk)
        request.session["active_organization_id"] = org.pk
        messages.success(
            request, f"Switched active organization context to '{org.name}'."
        )
        return redirect(request.POST.get("next") or "home")
