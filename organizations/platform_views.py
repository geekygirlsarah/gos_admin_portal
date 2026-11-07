"""Views for Platform Administration (Superuser Dashboard, Org Health, Metrics, Impersonation, and Support)."""

import hashlib
import logging
import secrets
import uuid
from datetime import timedelta
from urllib.parse import urlsplit

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login as auth_login
from django.contrib.auth import logout as auth_logout
from django.contrib.auth.mixins import UserPassesTestMixin
from django.contrib.auth.models import User
from django.core.exceptions import PermissionDenied
from django.db import connection, transaction
from django.db.models import Count, Prefetch, Q, Sum
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from django.utils.crypto import constant_time_compare
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt
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
from organizations import domain_recovery
from organizations.domain_verification import verify_domain_txt
from organizations.models import (
    Organization,
    OrganizationDomain,
    OrganizationDomainRecovery,
    OrganizationInvitation,
    OrganizationSettings,
    SupportImpersonationHandoff,
    UserOrganizationMembership,
)
from organizations.platform_forms import (
    PlatformAuditLogFilterForm,
    PlatformMemberForm,
    PlatformOrganizationForm,
    PlatformUserSearchForm,
)
from organizations.utils import (
    get_organization_url,
    get_site_base_url,
    hostname_has_conflicting_claims,
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
    """Mixin that restricts view access strictly to active superusers on the root platform domain."""

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

    def dispatch(self, request, *args, **kwargs):
        # Platform admin endpoints must not be accessible via tenant sites / subdomains / custom domains
        if self.is_tenant_request(request):
            raise Http404("This page does not exist")
        return super().dispatch(request, *args, **kwargs)

    @classmethod
    def is_tenant_request(cls, request):
        """Return True if the request is routed through a specific team/tenant site."""
        if getattr(request, "organization", None) is None:
            return False
        meta = getattr(request, "META", {})
        if any(
            h in meta
            for h in ("HTTP_X_ORGANIZATION", "HTTP_X_ORG_SLUG", "HTTP_X_ORG_SUBDOMAIN")
        ):
            return True
        host = (
            (meta.get("HTTP_HOST") or meta.get("SERVER_NAME") or "")
            .split(":")[0]
            .strip()
            .lower()
        )
        if not host:
            try:
                host = request.get_host().split(":")[0].strip().lower()
            except Exception:
                host = ""
        root_domain = getattr(
            settings, "MOMENTUM_ROOT_DOMAIN", "momentumteam.app"
        ).lower()
        root_domains = {
            root_domain,
            f"www.{root_domain}",
            "momentumteam.app",
            "www.momentumteam.app",
            "localhost",
            "127.0.0.1",
            "testserver",
        }
        if host in root_domains:
            return False
        return True


def calculate_org_health(org):
    """Calculate health diagnostics and readiness metrics for an organization."""
    memberships = UserOrganizationMembership.objects.filter(
        organization=org, is_active=True
    ).select_related("user")

    lead_mentors_count = memberships.filter(role="LeadMentor").count()
    # Count distinct people, not membership rows: a user can hold both
    # LeadMentor and Mentor rows (migration 0003 backfills both), while
    # agreed_count/clearances_count below are people-based.
    mentors_count = (
        memberships.filter(role__in=["LeadMentor", "Mentor"])
        .values("user_id")
        .distinct()
        .count()
    )
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
                "organization_domains": OrganizationDomain.objects.for_organization(
                    org
                ),
                "domain_recoveries": (
                    OrganizationDomainRecovery.objects.for_organization(org)
                    .filter(status=OrganizationDomainRecovery.STATUS_PENDING)
                    .select_related("original_organization")
                ),
                "settings": settings,
                "member_form": PlatformMemberForm(organization=org),
            }
        )
        return context


class PlatformOrganizationDomainActionView(SuperuserRequiredMixin, View):
    """Activate or disable a verified portal hostname after operator review."""

    def post(self, request, pk, domain_pk):
        organization = get_object_or_404(Organization, pk=pk)
        with transaction.atomic():
            domain = get_object_or_404(
                OrganizationDomain.objects.for_organization(
                    organization
                ).select_for_update(),
                pk=domain_pk,
            )

            action = request.POST.get("action")
            if action == "activate":
                if domain.status == OrganizationDomain.STATUS_ACTIVE:
                    messages.info(request, f"{domain.hostname} is already active.")
                elif not organization.is_active:
                    messages.error(
                        request, "Activate the organization before its portal domain."
                    )
                elif (
                    domain.status
                    not in (
                        OrganizationDomain.STATUS_DNS_VERIFIED,
                        OrganizationDomain.STATUS_DISABLED,
                    )
                    or not domain.dns_verified_at
                ):
                    messages.error(
                        request,
                        "Verify DNS ownership before activating this domain.",
                    )
                elif request.POST.get("confirm_infrastructure_ready") not in (
                    "1",
                    "true",
                    "on",
                ):
                    messages.error(
                        request,
                        "Confirm that DNS routing, HTTPS, and trusted host admission are ready.",
                    )
                elif hostname_has_conflicting_claims(domain):
                    messages.error(
                        request,
                        "This hostname conflicts with another organization's address. Resolve the conflict before activation.",
                    )
                elif not verify_domain_txt(domain):
                    messages.error(
                        request,
                        "The DNS ownership check no longer matches. Ask the organization to restore the TXT "
                        "record and verify it again.",
                    )
                else:
                    domain.status = OrganizationDomain.STATUS_ACTIVE
                    domain.activated_at = timezone.now()
                    domain.activated_by = request.user
                    domain.save(
                        update_fields=[
                            "status",
                            "activated_at",
                            "activated_by",
                            "updated_at",
                        ]
                    )
                    log_event(
                        event=AuditEvent.ORGANIZATION_UPDATED,
                        actor=request.user,
                        resource=organization,
                        notes=f"Activated portal hostname {domain.hostname} after operator readiness confirmation.",
                    )
                    messages.success(
                        request,
                        f"Portal hostname {domain.hostname} is active.",
                    )
            elif action == "disable":
                domain.status = OrganizationDomain.STATUS_DISABLED
                domain.save(update_fields=["status", "updated_at"])
                log_event(
                    event=AuditEvent.ORGANIZATION_UPDATED,
                    actor=request.user,
                    resource=organization,
                    notes=f"Disabled portal hostname {domain.hostname}.",
                )
                messages.success(request, f"Disabled {domain.hostname}.")
            else:
                messages.error(request, "Unknown domain action.")

        return redirect("platform_admin:organization_detail", pk=organization.pk)


class PlatformOrganizationDomainRecoveryActionView(SuperuserRequiredMixin, View):
    def post(self, request, pk, recovery_pk):
        organization = get_object_or_404(Organization, pk=pk)
        if (
            not OrganizationDomainRecovery.objects.for_organization(organization)
            .filter(pk=recovery_pk)
            .exists()
        ):
            messages.error(request, "Recovery request not found.")
        elif request.POST.get("action") != "approve":
            messages.error(request, "Unknown recovery action.")
        else:
            try:
                domain_recovery.approve_recovery(request, organization, recovery_pk)
            except domain_recovery.RecoveryError as exc:
                messages.error(request, str(exc))
            else:
                messages.success(
                    request,
                    "Recovery approved. Activate the domain separately after infrastructure review.",
                )
        return redirect("platform_admin:organization_detail", pk=organization.pk)


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
                    | Q(adult_profile__org_account_id__icontains=q)
                    | Q(student_profile__org_account_id__icontains=q)
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

        return qs.prefetch_related(
            Prefetch(
                "organization_memberships",
                queryset=UserOrganizationMembership.objects.select_related(
                    "organization"
                ),
            )
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        for user in context.get("users", []):
            organizations = {}
            for membership in user.organization_memberships.all():
                if membership.is_active and membership.organization.is_active:
                    organizations[membership.organization_id] = membership.organization
            user.support_organizations = list(organizations.values())
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


SUPPORT_HANDOFF_LIFETIME = timedelta(minutes=5)


def _hash_support_secret(value):
    return hashlib.sha256(f"support-impersonation:{value}".encode("utf-8")).hexdigest()


def _canonical_tenant_hostname(organization):
    canonical_url = get_organization_url(organization)
    try:
        parsed = urlsplit(canonical_url)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.port is not None
            or parsed.path not in ("", "/")
            or parsed.query
            or parsed.fragment
        ):
            return ""
    except ValueError:
        return ""
    return parsed.hostname.rstrip(".").lower()


def _request_hostname(request):
    try:
        return (urlsplit(f"//{request.get_host()}").hostname or "").rstrip(".").lower()
    except ValueError:
        return ""


def _handoff_has_live_access(handoff):
    organization = handoff.organization
    target_user = handoff.target_user
    initiator = handoff.initiated_by
    if not organization or not target_user or not initiator:
        return False
    if (
        not organization.is_active
        or not target_user.is_active
        or target_user.is_superuser
        or not initiator.is_active
        or not initiator.is_superuser
    ):
        return False
    if _canonical_tenant_hostname(organization) != handoff.tenant_hostname:
        return False
    return UserOrganizationMembership.objects.filter(
        user=target_user, organization=organization, is_active=True
    ).exists()


def _handoff_is_authorized_for_request(handoff, request):
    if not _handoff_has_live_access(handoff):
        return False
    organization = handoff.organization
    if getattr(request, "organization", None) != organization:
        return False
    request_host = _request_hostname(request)
    if not request_host or request_host != handoff.tenant_hostname:
        return False
    return True


def _pending_support_handoff(request, lock=False):
    raw_id = request.session.get("support_handoff_id")
    challenge = request.session.get("support_handoff_challenge")
    if not raw_id or not challenge or not request.session.session_key:
        return None
    try:
        handoff_id = uuid.UUID(str(raw_id))
    except (TypeError, ValueError):
        return None

    queryset = SupportImpersonationHandoff.objects.all()
    if lock:
        queryset = queryset.select_for_update()
    else:
        queryset = queryset.select_related(
            "initiated_by", "target_user", "organization"
        )
    handoff = queryset.filter(pk=handoff_id).first()
    if not handoff:
        return None
    if (
        not handoff.redeemed_at
        or handoff.confirmed_at
        or handoff.ended_at
        or handoff.expires_at <= timezone.now()
        or not constant_time_compare(
            handoff.tenant_session_hash,
            _hash_support_secret(request.session.session_key),
        )
        or not constant_time_compare(
            handoff.tenant_challenge_hash, _hash_support_secret(challenge)
        )
        or not _handoff_is_authorized_for_request(handoff, request)
    ):
        return None

    initial_authenticated = request.session.get(
        "support_handoff_initial_authenticated", False
    )
    initial_user_id = request.session.get("support_handoff_initial_user_id")
    current_authenticated = request.user.is_authenticated
    current_user_id = request.user.pk if current_authenticated else None
    if (
        current_authenticated != initial_authenticated
        or current_user_id != initial_user_id
    ):
        return None
    return handoff


def _clear_pending_support_session(session):
    for key in (
        "support_handoff_id",
        "support_handoff_challenge",
        "support_handoff_initial_authenticated",
        "support_handoff_initial_user_id",
    ):
        session.pop(key, None)


class PlatformImpersonateUserView(SuperuserRequiredMixin, View):
    """Create a one-use handoff to impersonate a tenant member on that tenant's host."""

    def post(self, request, pk):
        target_user = get_object_or_404(User, pk=pk)
        organization_id = request.POST.get("organization_id")
        membership = (
            UserOrganizationMembership.objects.select_related("organization")
            .filter(
                user=target_user,
                organization_id=organization_id,
                organization__is_active=True,
                is_active=True,
            )
            .first()
        )
        if (
            target_user.is_superuser
            or not target_user.is_active
            or not membership
            or not request.session.session_key
        ):
            messages.error(
                request,
                "Choose an active organization membership for an active, non-admin account.",
            )
            return redirect("platform_admin:user_support")

        organization = membership.organization
        tenant_hostname = _canonical_tenant_hostname(organization)
        if not tenant_hostname:
            messages.error(
                request,
                "This organization does not currently have a safe portal address for support access.",
            )
            return redirect("platform_admin:user_support")

        handoff_token = secrets.token_urlsafe(32)
        handoff = SupportImpersonationHandoff.objects.create(
            initiated_by=request.user,
            initiator_session_hash=_hash_support_secret(request.session.session_key),
            target_user=target_user,
            organization=organization,
            tenant_hostname=tenant_hostname,
            token_hash=_hash_support_secret(handoff_token),
            expires_at=timezone.now() + SUPPORT_HANDOFF_LIFETIME,
        )
        handoff_path = reverse("organizations:support_impersonation_handoff")
        response = render(
            request,
            "platform_admin/users/impersonation_handoff.html",
            {
                "handoff": handoff,
                "handoff_id": handoff.pk,
                "handoff_token": handoff_token,
                "tenant_post_url": f"https://{tenant_hostname}{handoff_path}",
            },
        )
        response["Cache-Control"] = "no-store"
        response["Referrer-Policy"] = "no-referrer"
        return response


def _platform_pending_support_handoff(request, handoff_id, lock=False):
    if not request.session.session_key:
        return None
    queryset = SupportImpersonationHandoff.objects.all()
    if lock:
        queryset = queryset.select_for_update()
    else:
        queryset = queryset.select_related(
            "initiated_by", "target_user", "organization"
        )
    handoff = queryset.filter(pk=handoff_id).first()
    if (
        not handoff
        or handoff.initiated_by_id != request.user.pk
        or not constant_time_compare(
            handoff.initiator_session_hash,
            _hash_support_secret(request.session.session_key),
        )
        or not handoff.redeemed_at
        or handoff.platform_returned_at
        or handoff.confirmed_at
        or handoff.ended_at
        or handoff.expires_at <= timezone.now()
        or not _handoff_has_live_access(handoff)
    ):
        return None
    return handoff


class PlatformImpersonationAuthorizeView(SuperuserRequiredMixin, View):
    """Reconfirm the initiating platform session after the tenant session is bound."""

    def get(self, request, handoff_id):
        handoff = _platform_pending_support_handoff(request, handoff_id)
        if not handoff:
            return HttpResponse(status=404)
        response = render(
            request,
            "platform_admin/users/impersonation_authorize.html",
            {"handoff": handoff},
        )
        response["Cache-Control"] = "no-store"
        return response

    def post(self, request, handoff_id):
        with transaction.atomic():
            handoff = _platform_pending_support_handoff(request, handoff_id, lock=True)
            if not handoff:
                return HttpResponse(status=404)
            token = secrets.token_urlsafe(32)
            handoff.platform_token_hash = _hash_support_secret(token)
            handoff.platform_confirmed_at = timezone.now()
            handoff.save(update_fields=["platform_token_hash", "platform_confirmed_at"])

        handoff_path = reverse("organizations:support_impersonation_handoff")
        response = render(
            request,
            "platform_admin/users/impersonation_authorize.html",
            {
                "handoff": handoff,
                "return_url": f"https://{handoff.tenant_hostname}{handoff_path}",
                "handoff_token": token,
            },
        )
        response["Cache-Control"] = "no-store"
        response["Referrer-Policy"] = "no-referrer"
        return response


# The cross-host POST cannot carry a tenant CSRF cookie; this view only validates
# the capability token and renders a same-origin, CSRF-protected redemption form.
@method_decorator(csrf_exempt, name="dispatch")
class SupportImpersonationHandoffView(View):
    """Validate the cross-host token without changing the existing tenant session."""

    def post(self, request):
        stage = request.POST.get("stage", "initial")
        if stage == "initial":
            handoff = _load_unredeemed_support_handoff(request)
            redeem_url = reverse("organizations:support_impersonation_redeem")
        elif stage == "platform_return":
            handoff = _load_platform_return_handoff(request)
            redeem_url = reverse("organizations:support_impersonation_resume")
        else:
            return HttpResponse(status=404)
        if not handoff:
            return HttpResponse(status=404)

        response = render(
            request,
            "organizations/support_impersonation_continue.html",
            {
                "handoff_id": handoff.pk,
                "handoff_token": request.POST.get("handoff_token"),
                "redeem_url": redeem_url,
            },
        )
        response["Cache-Control"] = "no-store"
        response["Referrer-Policy"] = "same-origin"
        return response


def _load_unredeemed_support_handoff(request, lock=False):
    raw_id = request.POST.get("handoff_id")
    token = request.POST.get("handoff_token")
    try:
        handoff_id = uuid.UUID(str(raw_id))
    except (TypeError, ValueError):
        return None
    if not token:
        return None

    queryset = SupportImpersonationHandoff.objects.all()
    if lock:
        queryset = queryset.select_for_update()
    else:
        queryset = queryset.select_related(
            "initiated_by", "target_user", "organization"
        )
    handoff = queryset.filter(pk=handoff_id).first()
    if (
        not handoff
        or handoff.redeemed_at
        or handoff.confirmed_at
        or handoff.ended_at
        or handoff.expires_at <= timezone.now()
        or not constant_time_compare(handoff.token_hash, _hash_support_secret(token))
        or not _handoff_is_authorized_for_request(handoff, request)
    ):
        return None
    return handoff


def _load_platform_return_handoff(request):
    raw_id = request.POST.get("handoff_id")
    token = request.POST.get("handoff_token")
    try:
        handoff_id = uuid.UUID(str(raw_id))
    except (TypeError, ValueError):
        return None
    if not token:
        return None
    handoff = (
        SupportImpersonationHandoff.objects.select_related(
            "initiated_by", "target_user", "organization"
        )
        .filter(pk=handoff_id)
        .first()
    )
    if (
        not handoff
        or not handoff.redeemed_at
        or not handoff.platform_confirmed_at
        or handoff.platform_returned_at
        or handoff.confirmed_at
        or handoff.ended_at
        or handoff.expires_at <= timezone.now()
        or not constant_time_compare(
            handoff.platform_token_hash, _hash_support_secret(token)
        )
        or not _handoff_is_authorized_for_request(handoff, request)
    ):
        return None
    return handoff


class SupportImpersonationRedeemView(View):
    """Consume the handoff on the tenant origin and bind it to the browser session."""

    def post(self, request):
        platform_base_url = get_site_base_url().rstrip("/")
        if not platform_base_url:
            return HttpResponse(status=503)
        with transaction.atomic():
            handoff = _load_unredeemed_support_handoff(request, lock=True)
            if not handoff or request.session.get("impersonator_id"):
                return HttpResponse(status=404)

            previous_id = request.session.get("support_handoff_id")
            try:
                previous_id = uuid.UUID(str(previous_id)) if previous_id else None
            except (TypeError, ValueError):
                previous_id = None
            if previous_id:
                previous = (
                    SupportImpersonationHandoff.objects.select_for_update()
                    .filter(pk=previous_id)
                    .first()
                )
                if previous and not previous.confirmed_at and not previous.ended_at:
                    previous.ended_at = timezone.now()
                    previous.save(update_fields=["ended_at"])
            _clear_pending_support_session(request.session)

            if not request.session.session_key:
                request.session.create()
            challenge = secrets.token_urlsafe(32)
            request.session["support_handoff_id"] = str(handoff.pk)
            request.session["support_handoff_challenge"] = challenge
            request.session["support_handoff_initial_authenticated"] = bool(
                request.user.is_authenticated
            )
            request.session["support_handoff_initial_user_id"] = (
                request.user.pk if request.user.is_authenticated else None
            )
            request.session.save()

            handoff.redeemed_at = timezone.now()
            handoff.tenant_session_hash = _hash_support_secret(
                request.session.session_key
            )
            handoff.tenant_challenge_hash = _hash_support_secret(challenge)
            handoff.save(
                update_fields=[
                    "redeemed_at",
                    "tenant_session_hash",
                    "tenant_challenge_hash",
                ]
            )

        authorize_path = reverse(
            "platform_admin:impersonation_authorize",
            kwargs={"handoff_id": handoff.pk},
        )
        response = render(
            request,
            "organizations/support_impersonation_platform_return.html",
            {"platform_confirm_url": f"{platform_base_url}{authorize_path}"},
        )
        response["Cache-Control"] = "no-store"
        response["Referrer-Policy"] = "no-referrer"
        return response


class SupportImpersonationResumeView(View):
    """Bind the platform's return proof to the original tenant session."""

    def post(self, request):
        token = request.POST.get("handoff_token")
        with transaction.atomic():
            handoff = _pending_support_handoff(request, lock=True)
            if (
                not handoff
                or str(handoff.pk) != request.POST.get("handoff_id")
                or not token
                or not handoff.platform_confirmed_at
                or handoff.platform_returned_at
                or not constant_time_compare(
                    handoff.platform_token_hash, _hash_support_secret(token)
                )
            ):
                return HttpResponse(status=404)
            handoff.platform_returned_at = timezone.now()
            # Clear the consumed one-use token hash, not a password.
            handoff.platform_token_hash = ""  # nosec B105
            handoff.save(update_fields=["platform_returned_at", "platform_token_hash"])
        return redirect("organizations:support_impersonation_confirm")


class SupportImpersonationConfirmView(View):
    """Require same-browser confirmation before replacing any tenant login."""

    def get(self, request):
        handoff = _pending_support_handoff(request)
        if not handoff or not handoff.platform_returned_at:
            return HttpResponse(status=404)
        response = render(
            request,
            "organizations/support_impersonation_confirm.html",
            {
                "handoff": handoff,
                "existing_user": (
                    request.user if request.user.is_authenticated else None
                ),
            },
        )
        response["Cache-Control"] = "no-store"
        return response

    def post(self, request):
        with transaction.atomic():
            handoff = _pending_support_handoff(request, lock=True)
            if not handoff or not handoff.platform_returned_at:
                return HttpResponse(status=404)

            audit_entry = log_event(
                event=AuditEvent.USER_IMPERSONATED,
                request=request,
                actor=handoff.initiated_by,
                resource=handoff.target_user,
                notes=(
                    f"Support impersonation confirmed for organization "
                    f"{handoff.organization_id}; handoff {handoff.pk}."
                ),
            )
            if audit_entry is None:
                return HttpResponse(
                    "Support access could not be started because the audit event was not recorded.",
                    status=503,
                )

            auth_login(
                request,
                handoff.target_user,
                backend="django.contrib.auth.backends.ModelBackend",
            )
            request.session["impersonator_id"] = handoff.initiated_by_id
            request.session["support_impersonation_handoff_id"] = str(handoff.pk)
            request.session["active_organization_id"] = handoff.organization_id
            _clear_pending_support_session(request.session)

            handoff.confirmed_at = timezone.now()
            handoff.tenant_session_hash = _hash_support_secret(
                request.session.session_key
            )
            handoff.save(update_fields=["confirmed_at", "tenant_session_hash"])

        return redirect("profile_dashboard")


class PlatformStopImpersonationView(View):
    """End tenant impersonation without authenticating the platform user on this host."""

    def post(self, request):
        impersonator_id = request.session.get("impersonator_id")
        if not impersonator_id:
            messages.warning(request, "No active impersonation session found.")
            return redirect("home")

        impersonated_user = request.user
        try:
            handoff_id = uuid.UUID(
                str(request.session.get("support_impersonation_handoff_id", ""))
            )
        except (TypeError, ValueError):
            handoff_id = None

        if handoff_id and impersonated_user.is_authenticated:
            with transaction.atomic():
                handoff = (
                    SupportImpersonationHandoff.objects.select_for_update()
                    .filter(
                        pk=handoff_id,
                        target_user=impersonated_user,
                        confirmed_at__isnull=False,
                        ended_at__isnull=True,
                    )
                    .first()
                )
                if handoff:
                    log_event(
                        event=AuditEvent.IMPERSONATION_ENDED,
                        request=request,
                        actor=handoff.initiated_by,
                        resource=impersonated_user,
                        notes=(
                            f"Support impersonation ended for organization "
                            f"{handoff.organization_id}; handoff {handoff.pk}."
                        ),
                    )
                    handoff.ended_at = timezone.now()
                    handoff.save(update_fields=["ended_at"])
        elif impersonated_user.is_authenticated:
            legacy_superuser = User.objects.filter(
                pk=impersonator_id, is_superuser=True
            ).first()
            if legacy_superuser:
                log_event(
                    event=AuditEvent.IMPERSONATION_ENDED,
                    request=request,
                    actor=legacy_superuser,
                    resource=impersonated_user,
                    notes="Legacy support impersonation session ended.",
                )

        auth_logout(request)
        site_base_url = get_site_base_url().rstrip("/")
        dashboard_url = reverse("platform_admin:dashboard")
        return redirect(f"{site_base_url}{dashboard_url}" if site_base_url else "home")


class PlatformSwitchContextView(SuperuserRequiredMixin, View):
    """Switch active tenant organization context for the platform superuser."""

    def post(self, request, pk):
        org = get_object_or_404(Organization, pk=pk)
        request.session["active_organization_id"] = org.pk
        messages.success(
            request, f"Switched active organization context to '{org.name}'."
        )
        return redirect(request.POST.get("next") or "home")
