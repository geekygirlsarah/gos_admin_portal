import os
import uuid
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import ValidationError
from django.core.files.storage import default_storage
from django.db import IntegrityError, transaction
from django.db.models import Prefetch
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views import View

from audit.events import AuditEvent
from audit.service import log_deletion, log_event
from organizations import domain_recovery
from organizations.domain_forms import OrganizationDomainRequestForm
from organizations.domain_verification import verify_domain_txt
from organizations.models import Organization, OrganizationDomain, OrganizationSettings
from organizations.utils import (
    get_default_organization,
    get_organization_email_settings,
    get_organization_momentum_url,
    get_organization_payment_settings,
    update_organization_email_settings,
    update_organization_payment_settings,
)
from programs.constants import TEAM_TYPES
from programs.forms import MentorAgreementForm, PaymentInstructionsForm
from programs.models import (
    BackgroundCheckType,
    Crew,
    Enrollment,
    MentorAgreement,
    Program,
    RolePermission,
    SlidingScale,
    SlidingScaleSettings,
    SubTeam,
    Team,
)
from programs.permission_views import (
    ORDERS_SECTIONS,
    LeadMentorRequiredMixin,
)

try:
    from attendance.models import KioskConfig
except ImportError:
    KioskConfig = None


def _stored_notification_sender(organization):
    data = (
        OrganizationSettings.objects.filter(organization=organization)
        .values_list("data", flat=True)
        .first()
    )
    email_settings = data.get("email_settings") if isinstance(data, dict) else None
    value = (
        email_settings.get("notification_from_email")
        if isinstance(email_settings, dict)
        else None
    )
    return value if isinstance(value, str) else ""


def _request_organization(request):
    organization = getattr(request, "organization", None)
    if organization is None:
        raise Http404
    return organization


def _has_foreign_enrollments(field_name, resource, organization, program=None):
    ownership = {
        "organization": organization,
        "student__organization": organization,
        "program__organization": organization,
    }
    if program is not None:
        ownership["program"] = program
    return (
        Enrollment.objects.filter(**{field_name: resource})
        .exclude(**ownership)
        .exists()
    )


class PortalSettingsView(LoginRequiredMixin, LeadMentorRequiredMixin, View):
    template_name = "programs/settings.html"

    def get(self, request):
        org = _request_organization(request)
        agreements = MentorAgreement.objects.for_organization(org)
        sections = RolePermission.SECTION_CHOICES
        roles = RolePermission.ROLE_CHOICES

        # Ensure all combinations exist for this organization
        for role_code, role_name in roles:
            for section_code, section_name in sections:
                # Orders content is scoped to students/mentors by default, so the
                # auto-created rows must not inherit the model default (all
                # roles enabled); a Lead Mentor can flip these in the UI later.
                defaults = {}
                if section_code in ORDERS_SECTIONS:
                    orders_ok = role_code in ("Mentor", "Student")
                    defaults = {
                        "can_read": orders_ok,
                        "can_write": orders_ok,
                    }
                elif section_code == "mail_merge":
                    defaults = {"can_read": False, "can_write": False}
                RolePermission.objects.get_or_create(
                    organization=org,
                    role=role_code,
                    section=section_code,
                    defaults=defaults,
                )

        permissions = RolePermission.objects.for_organization(org)

        # Group permissions by section for the new table layout
        grouped_permissions = []
        for section_code, section_name in sections:
            active_column = ORDERS_SECTIONS.get(section_code)
            is_mail_merge = section_code == "mail_merge"
            uses_read = (
                is_mail_merge or active_column is None or active_column == "view"
            )
            uses_write = not is_mail_merge and (
                active_column is None or active_column != "view"
            )
            grouped_permissions.append(
                {
                    "name": section_name,
                    "mentor": permissions.filter(
                        section=section_code, role="Mentor"
                    ).first(),
                    "parent": (
                        None
                        if is_mail_merge
                        else permissions.filter(
                            section=section_code, role="Parent"
                        ).first()
                    ),
                    "student": (
                        None
                        if is_mail_merge
                        else permissions.filter(
                            section=section_code, role="Student"
                        ).first()
                    ),
                    "orders_section": section_code in ORDERS_SECTIONS,
                    "uses_read": uses_read,
                    "uses_write": uses_write,
                }
            )

        teams = Team.objects.for_organization(org)
        team_types = TEAM_TYPES
        crews = (
            Crew.objects.for_organization(org)
            .filter(program__organization=org)
            .select_related("program")
        )
        subteams = (
            SubTeam.objects.for_organization(org)
            .filter(program__organization=org)
            .select_related("program")
        )
        programs = (
            Program.objects.for_organization(org)
            .prefetch_related(
                Prefetch(
                    "crews",
                    queryset=Crew.objects.for_organization(org).filter(
                        program__organization=org
                    ),
                ),
                Prefetch(
                    "subteams",
                    queryset=SubTeam.objects.for_organization(org).filter(
                        program__organization=org
                    ),
                ),
            )
            .order_by("-active", "name")
        )
        attendance_programs = [p for p in programs if p.has_feature("attendance")]

        kiosk_configs = None
        if KioskConfig:
            kiosk_configs = (
                KioskConfig.objects.for_organization(org)
                .filter(program__organization=org)
                .select_related("program")
            )

        email_settings = get_organization_email_settings(org)

        bg_types = BackgroundCheckType.objects.for_organization(org).order_by(
            "display_order", "name"
        )

        from django.contrib.auth import get_user_model

        from organizations.models import (
            OrganizationInvitation,
            UserOrganizationMembership,
        )

        User = get_user_model()

        lead_mentor_memberships = (
            UserOrganizationMembership.objects.filter(
                organization=org, role="LeadMentor", is_active=True
            )
            .select_related(
                "user", "user__adult_profile", "user__student_profile", "invited_by"
            )
            .order_by(
                "user__first_name", "user__last_name", "user__email", "user__username"
            )
        )
        lead_mentor_user_ids = set(
            lead_mentor_memberships.values_list("user_id", flat=True)
        )

        eligible_lead_mentor_users = (
            User.objects.filter(is_active=True)
            .exclude(id__in=lead_mentor_user_ids)
            .select_related("adult_profile", "student_profile")
            .order_by("first_name", "last_name", "email", "username")
        )

        pending_lead_mentor_invitations = (
            OrganizationInvitation.objects.filter(
                organization=org,
                role="LeadMentor",
                status=OrganizationInvitation.STATUS_PENDING,
            )
            .select_related("invited_by")
            .order_by("-created_at")
        )

        organization_domains = (
            OrganizationDomain.objects.for_organization(org).order_by(
                "-created_at", "hostname"
            )
            if org
            else OrganizationDomain.objects.none()
        )

        context = {
            "grouped_permissions": grouped_permissions,
            "teams": teams,
            "team_types": team_types,
            "crews": crews,
            "subteams": subteams,
            "programs": programs,
            "attendance_programs": attendance_programs,
            "kiosk_configs": kiosk_configs,
            "sliding_scale_settings": SlidingScaleSettings.get_solo(
                request.organization
            ),
            "pending_sliding_scale_count": SlidingScale.objects.filter(
                status=SlidingScale.STATUS_PENDING
            ).count(),
            "mentor_agreements": agreements.order_by("slug", "-version"),
            "mentor_agreement_slugs": agreements.values_list(
                "slug", flat=True
            ).distinct(),
            "background_check_types": bg_types,
            "lead_mentor_memberships": lead_mentor_memberships,
            "eligible_lead_mentor_users": eligible_lead_mentor_users,
            "pending_lead_mentor_invitations": pending_lead_mentor_invitations,
            "organization": org,
            "organization_domains": organization_domains,
            "momentum_portal_url": get_organization_momentum_url(org),
            "email_settings": email_settings,
            "notification_from_override": _stored_notification_sender(org),
            "payment_settings": get_organization_payment_settings(org),
            "role": "LeadMentor",  # Required for base.html to show Nav correctly
            "active_tab": request.GET.get("tab", "permissions"),
            "sections": sections,
        }
        return render(request, self.template_name, context)


class PortalPaymentInstructionsView(LoginRequiredMixin, LeadMentorRequiredMixin, View):
    """Update payment guidance for the resolved organization."""

    def post(self, request):
        organization = _request_organization(request)
        form = PaymentInstructionsForm(request.POST)
        if not form.is_valid():
            messages.error(
                request,
                "Enter valid payment instructions and an HTTP(S) online payment URL.",
            )
            return redirect("/programs/settings/?tab=payment_instructions")

        before = get_organization_payment_settings(organization)
        update_organization_payment_settings(
            organization,
            instructions=form.cleaned_data["payment_instructions"],
            online_payment_url=form.cleaned_data["online_payment_url"],
        )
        after = get_organization_payment_settings(organization)
        log_event(
            request=request,
            event=AuditEvent.ORGANIZATION_UPDATED,
            resource=organization,
            before={
                "payment_instructions_configured": bool(before["instructions"]),
                "online_payment_configured": bool(before["online_payment_url"]),
            },
            after={
                "payment_instructions_configured": bool(after["instructions"]),
                "online_payment_configured": bool(after["online_payment_url"]),
            },
            notes="Updated parent payment instructions.",
        )
        messages.success(request, "Payment instructions updated successfully.")
        return redirect("/programs/settings/?tab=payment_instructions")


class PortalSlidingScaleSettingsView(LoginRequiredMixin, LeadMentorRequiredMixin, View):
    """Handles updates to the portal-wide sliding scale calculation settings."""

    def post(self, request):
        settings_obj = SlidingScaleSettings.get_solo(request.organization)
        fields = [
            "base_amount",
            "additional_member_amount",
            "low_multiplier",
            "high_multiplier",
        ]
        try:
            for field in fields:
                value = request.POST.get(field)
                setattr(settings_obj, field, Decimal(value))
        except (TypeError, InvalidOperation):
            messages.error(request, "Please enter valid numbers for all fields.")
            return redirect("/programs/settings/?tab=sliding_scale_settings")

        settings_obj.save()
        messages.success(request, "Sliding scale settings updated successfully.")
        return redirect("/programs/settings/?tab=sliding_scale_settings")


class PortalPermissionsUpdateView(LoginRequiredMixin, LeadMentorRequiredMixin, View):
    """Handles the 'update_permissions' action from the settings page."""

    def post(self, request):
        org = getattr(request, "organization", None) or get_default_organization()
        permissions = RolePermission.objects.for_organization(org)
        for perm in permissions:
            perm_key = f"perm_{perm.id}"
            if perm_key in request.POST:
                val = request.POST.get(perm_key)
                if val == "write":
                    perm.can_read = True
                    perm.can_write = True
                elif val == "read":
                    perm.can_read = True
                    perm.can_write = False
                else:  # "none"
                    perm.can_read = False
                    perm.can_write = False
            else:
                read_key = f"read_{perm.id}"
                write_key = f"write_{perm.id}"
                perm.can_read = read_key in request.POST
                perm.can_write = write_key in request.POST
            if perm.section == "mail_merge":
                perm.can_write = False
            if org and perm.organization is None:
                perm.organization = org
            perm.save()
        messages.success(request, "Permissions updated successfully.")
        return redirect("/programs/settings/?tab=permissions")


class PortalTeamView(LoginRequiredMixin, LeadMentorRequiredMixin, View):
    """Handles add/delete/update actions for Teams from the settings page."""

    def post(self, request):
        organization = _request_organization(request)
        action = request.POST.get("action")

        if action == "add_team":
            team_type = request.POST.get("team_type")
            number = request.POST.get("number")
            name = request.POST.get("name")
            color = request.POST.get("color")
            if team_type and number:
                Team.objects.create(
                    organization=organization,
                    team_type=team_type,
                    number=number,
                    name=name,
                    color=color,
                )
                messages.success(request, f"Team {team_type} {number} added.")
            return redirect("/programs/settings/?tab=teams")

        elif action == "delete_team":
            team_id = request.POST.get("team_id")
            if team_id:
                team = (
                    Team.objects.for_organization(organization)
                    .filter(id=team_id)
                    .first()
                )
                if team:
                    if _has_foreign_enrollments("team", team, organization):
                        messages.error(
                            request,
                            "This team cannot be deleted while it is linked to records owned by another organization.",
                        )
                        return redirect("/programs/settings/?tab=teams")
                    log_deletion(
                        resource=team,
                        request=request,
                        before={
                            "team_type": team.team_type,
                            "number": team.number,
                            "name": team.name,
                            "color": team.color,
                        },
                        notes="Team deleted from Portal Settings.",
                    )
                    team.delete()
                messages.success(request, "Team deleted.")
            return redirect("/programs/settings/?tab=teams")

        elif action == "update_team":
            team_id = request.POST.get("team_id")
            team_type = request.POST.get("team_type")
            number = request.POST.get("number")
            name = request.POST.get("name")
            color = request.POST.get("color")
            if team_id:
                team = (
                    Team.objects.for_organization(organization)
                    .filter(id=team_id)
                    .first()
                )
                if team:
                    team.team_type = team_type
                    team.number = number
                    team.name = name
                    team.color = color
                    team.save()
                    messages.success(request, "Team updated.")
            return redirect("/programs/settings/?tab=teams")

        return redirect("/programs/settings/?tab=teams")


class PortalCrewView(LoginRequiredMixin, LeadMentorRequiredMixin, View):
    """Handles add/delete/update actions for Crews from the settings page."""

    def post(self, request):
        organization = _request_organization(request)
        action = request.POST.get("action")

        if action == "add_crew":
            program_id = request.POST.get("program_id")
            name = request.POST.get("name")
            color = request.POST.get("color")
            if program_id and name:
                program = (
                    Program.objects.for_organization(organization)
                    .filter(pk=program_id)
                    .first()
                )
                if program is not None:
                    Crew.objects.create(
                        organization=organization,
                        program=program,
                        name=name,
                        color=color,
                    )
                    messages.success(request, f"Crew {name} added.")
            return redirect("/programs/settings/?tab=crews")

        elif action == "delete_crew":
            crew_id = request.POST.get("crew_id")
            if crew_id:
                crew = (
                    Crew.objects.for_organization(organization)
                    .filter(program__organization=organization, id=crew_id)
                    .first()
                )
                if crew:
                    if _has_foreign_enrollments(
                        "crew", crew, organization, program=crew.program
                    ):
                        messages.error(
                            request,
                            "This crew cannot be deleted while it is linked to records owned by another organization.",
                        )
                        return redirect("/programs/settings/?tab=crews")
                    log_deletion(
                        resource=crew,
                        request=request,
                        before={
                            "name": crew.name,
                            "color": crew.color,
                            "program_id": crew.program_id,
                        },
                        notes="Crew deleted from Portal Settings.",
                    )
                    crew.delete()
                messages.success(request, "Crew deleted.")
            return redirect("/programs/settings/?tab=crews")

        elif action == "update_crew":
            crew_id = request.POST.get("crew_id")
            name = request.POST.get("name")
            color = request.POST.get("color")
            if crew_id:
                crew = (
                    Crew.objects.for_organization(organization)
                    .filter(program__organization=organization, id=crew_id)
                    .first()
                )
                if crew:
                    crew.name = name
                    crew.color = color
                    crew.save()
                    messages.success(request, "Crew updated.")
            return redirect("/programs/settings/?tab=crews")

        return redirect("/programs/settings/?tab=crews")


class PortalSubteamView(LoginRequiredMixin, LeadMentorRequiredMixin, View):
    """Handles add/delete/update actions for SubTeams from the settings page."""

    def post(self, request):
        organization = _request_organization(request)
        action = request.POST.get("action")

        if action == "add_subteam":
            program_id = request.POST.get("program_id")
            name = request.POST.get("name")
            color = request.POST.get("color")
            if program_id and name:
                program = (
                    Program.objects.for_organization(organization)
                    .filter(pk=program_id)
                    .first()
                )
                if program is not None:
                    SubTeam.objects.create(
                        organization=organization,
                        program=program,
                        name=name,
                        color=color,
                    )
                    messages.success(request, f"SubTeam {name} added.")
            return redirect("/programs/settings/?tab=subteams")

        elif action == "delete_subteam":
            subteam_id = request.POST.get("subteam_id")
            if subteam_id:
                subteam = (
                    SubTeam.objects.for_organization(organization)
                    .filter(program__organization=organization, id=subteam_id)
                    .first()
                )
                if subteam:
                    if _has_foreign_enrollments(
                        "subteam", subteam, organization, program=subteam.program
                    ):
                        messages.error(
                            request,
                            "This subteam cannot be deleted while it is linked to records owned by another organization.",
                        )
                        return redirect("/programs/settings/?tab=subteams")
                    log_deletion(
                        resource=subteam,
                        request=request,
                        before={
                            "name": subteam.name,
                            "color": subteam.color,
                            "program_id": subteam.program_id,
                        },
                        notes="SubTeam deleted from Portal Settings.",
                    )
                    subteam.delete()
                messages.success(request, "SubTeam deleted.")
            return redirect("/programs/settings/?tab=subteams")

        elif action == "update_subteam":
            subteam_id = request.POST.get("subteam_id")
            name = request.POST.get("name")
            color = request.POST.get("color")
            if subteam_id:
                subteam = (
                    SubTeam.objects.for_organization(organization)
                    .filter(program__organization=organization, id=subteam_id)
                    .first()
                )
                if subteam:
                    subteam.name = name
                    subteam.color = color
                    subteam.save()
                    messages.success(request, "SubTeam updated.")
            return redirect("/programs/settings/?tab=subteams")

        return redirect("portal_settings")


class PortalKioskView(LoginRequiredMixin, LeadMentorRequiredMixin, View):
    """Handles add/delete/toggle actions for Kiosks from the settings page."""

    def post(self, request):
        organization = _request_organization(request)
        action = request.POST.get("action")

        if action == "add_kiosk_config" and KioskConfig:
            label = request.POST.get("label", "").strip()
            program_id = request.POST.get("program_id")
            if label and program_id:
                program = (
                    Program.objects.for_organization(organization)
                    .filter(pk=program_id)
                    .first()
                )
                if program is not None:
                    KioskConfig.objects.create(
                        organization=organization,
                        label=label,
                        program=program,
                    )
                    messages.success(request, f"Kiosk '{label}' added.")
            return redirect("/programs/settings/?tab=kiosk_configs")

        elif action == "delete_kiosk_config" and KioskConfig:
            kiosk_config_id = request.POST.get("kiosk_config_id")
            if kiosk_config_id:
                kiosk = (
                    KioskConfig.objects.for_organization(organization)
                    .filter(program__organization=organization, id=kiosk_config_id)
                    .first()
                )
                if kiosk:
                    log_deletion(
                        resource=kiosk,
                        request=request,
                        before={
                            "label": kiosk.label,
                            "program_id": kiosk.program_id,
                            "is_active": kiosk.is_active,
                        },
                        notes="Kiosk configuration deleted from Portal Settings.",
                    )
                    kiosk.delete()
                messages.success(request, "Kiosk configuration deleted.")
            return redirect("/programs/settings/?tab=kiosk_configs")

        elif action == "toggle_kiosk_config" and KioskConfig:
            kiosk_config_id = request.POST.get("kiosk_config_id")
            if kiosk_config_id:
                kiosk = (
                    KioskConfig.objects.for_organization(organization)
                    .filter(program__organization=organization, id=kiosk_config_id)
                    .first()
                )
                if kiosk:
                    kiosk.is_active = not kiosk.is_active
                    kiosk.save(update_fields=["is_active"])
                    state = "activated" if kiosk.is_active else "deactivated"
                    messages.success(request, f"Kiosk '{kiosk.label}' {state}.")
            return redirect("/programs/settings/?tab=kiosk_configs")

        return redirect("/programs/settings/?tab=kiosk_configs")


class PortalAgreementView(LoginRequiredMixin, LeadMentorRequiredMixin, View):
    """Handles create / update / toggle / delete actions for Mentor Agreements."""

    def post(self, request):
        org = getattr(request, "organization", None)
        if org is None:
            raise Http404
        agreements = MentorAgreement.objects.for_organization(org)
        action = request.POST.get("action")

        if action == "add_agreement":
            form = MentorAgreementForm(request.POST, request.FILES)
            if form.is_valid():
                slug = form.cleaned_data["slug"]
                version = (
                    agreements.filter(slug=slug)
                    .order_by("-version")
                    .values_list("version", flat=True)
                    .first()
                )
                version = (version or 0) + 1
                agreement = form.save(commit=False)
                agreement.organization = org
                agreement.version = version
                if not agreement.effective_date:
                    agreement.effective_date = date.today()
                agreement.save()
                messages.success(
                    request,
                    f"Agreement '{agreement.title}' created (version {version}).",
                )
            else:
                messages.error(request, "Please correct the errors below.")
            return redirect("/programs/settings/?tab=agreements")

        elif action == "update_agreement":
            agreement_id = request.POST.get("agreement_id")
            try:
                agreement = agreements.get(pk=agreement_id)
            except MentorAgreement.DoesNotExist:
                messages.error(request, "Agreement not found.")
                return redirect("/programs/settings/?tab=agreements")

            old_content = agreement.content
            old_doc = agreement.document
            form = MentorAgreementForm(request.POST, request.FILES, instance=agreement)
            if form.is_valid():
                new_agreement = form.save(commit=False)
                content_changed = new_agreement.content != old_content
                doc_changed = (
                    "document" in form.changed_data
                    and new_agreement.document
                    and new_agreement.document != old_doc
                )
                if content_changed or doc_changed:
                    # Create new version
                    version = (
                        agreements.filter(slug=agreement.slug)
                        .order_by("-version")
                        .values_list("version", flat=True)
                        .first()
                    )
                    new_agreement.version = (version or 0) + 1
                    new_agreement.organization = org
                    new_agreement.pk = None
                    new_agreement.created_at = None
                    new_agreement.updated_at = None
                    if not new_agreement.effective_date:
                        new_agreement.effective_date = date.today()
                    new_agreement.save()
                    messages.success(
                        request,
                        f"Agreement '{new_agreement.title}' updated as version {new_agreement.version}.",
                    )
                else:
                    # No content change — just update metadata (title, is_active, etc.)
                    form.save()
                    messages.success(
                        request,
                        f"Agreement '{agreement.title}' metadata updated.",
                    )
            else:
                messages.error(request, "Please correct the errors below.")
            return redirect("/programs/settings/?tab=agreements")

        elif action == "toggle_agreement":
            agreement_id = request.POST.get("agreement_id")
            try:
                agreement = agreements.get(pk=agreement_id)
                agreement.is_active = not agreement.is_active
                agreement.save(update_fields=["is_active"])
                state = "activated" if agreement.is_active else "deactivated"
                messages.success(request, f"Agreement '{agreement.title}' {state}.")
            except MentorAgreement.DoesNotExist:
                messages.error(request, "Agreement not found.")
            return redirect("/programs/settings/?tab=agreements")

        elif action == "delete_agreement":
            agreement_id = request.POST.get("agreement_id")
            try:
                agreement = agreements.get(pk=agreement_id)
                title = agreement.title
                from programs.models import (
                    MentorAgreementAcceptance,
                    MentorAgreementSubmission,
                )

                with transaction.atomic():
                    version_ids = list(
                        agreements.select_for_update()
                        .filter(slug=agreement.slug)
                        .values_list("pk", flat=True)
                    )
                    if agreement.pk not in version_ids:
                        raise MentorAgreement.DoesNotExist
                    versions = agreements.filter(pk__in=version_ids)
                    acceptances = MentorAgreementAcceptance.objects.filter(
                        agreement__in=versions
                    )
                    submissions = MentorAgreementSubmission.objects.filter(
                        agreement__in=versions
                    )
                    list(acceptances.select_for_update().values_list("pk", flat=True))
                    list(submissions.select_for_update().values_list("pk", flat=True))
                    if any(
                        records.exclude(
                            organization=org, adult__organization=org
                        ).exists()
                        for records in (acceptances, submissions)
                    ):
                        messages.error(
                            request,
                            "Agreement has inconsistent records and cannot be deleted. "
                            "Contact a platform administrator for review.",
                        )
                        return redirect("/programs/settings/?tab=agreements")
                    acceptance_count = acceptances.count()
                    count = len(version_ids)
                    log_deletion(
                        resource=agreement,
                        request=request,
                        before={
                            "title": title,
                            "slug": agreement.slug,
                            "version_count": count,
                            "acceptance_count": acceptance_count,
                        },
                        notes=(
                            f"Mentor agreement '{title}' and {count} version(s) deleted "
                            f"({acceptance_count} acceptance record(s) removed)."
                        ),
                    )
                    acceptances.delete()
                    versions.delete()
                messages.success(
                    request,
                    f"Agreement '{title}' and {count} version(s) deleted.",
                )
            except MentorAgreement.DoesNotExist:
                messages.error(request, "Agreement not found.")
            return redirect("/programs/settings/?tab=agreements")

        return redirect("/programs/settings/?tab=agreements")


class PortalEmailSettingsView(LoginRequiredMixin, LeadMentorRequiredMixin, View):
    """Handles updating organization email settings and sender identities from Portal Settings."""

    def post(self, request):
        org = getattr(request, "organization", None) or get_default_organization()
        if not org:
            org = Organization.objects.filter(is_active=True).first()
            if not org:
                org, _ = Organization.objects.get_or_create(
                    slug="gos", defaults={"name": "Girls of Steel"}
                )

        action = request.POST.get("action")
        email_cfg = get_organization_email_settings(org)
        email_cfg["notification_from_email"] = _stored_notification_sender(org)

        if action == "update_branding":
            primary_color = (
                request.POST.get("primary_color", "").strip()
                or email_cfg.get("primary_color")
                or "#4a154b"
            )
            secondary_color = (
                request.POST.get("secondary_color", "").strip()
                or email_cfg.get("secondary_color")
                or "#f8f9fa"
            )
            header_text_color = (
                request.POST.get("header_text_color", "").strip()
                or email_cfg.get("header_text_color")
                or "#ffffff"
            )
            organization_name = (
                request.POST.get("organization_name", "").strip()
                or email_cfg.get("organization_name")
                or org.name
            )
            address_line1 = request.POST.get("address_line1", "").strip()
            address_line2 = request.POST.get("address_line2", "").strip()
            footer_text = request.POST.get("footer_text", "").strip()
            contact_name = request.POST.get("contact_name", "").strip()
            contact_email = request.POST.get("contact_email", "").strip()

            logo_url = request.POST.get("logo_url", "").strip()
            remove_logo = request.POST.get("remove_logo") in (
                "1",
                "true",
                "on",
                True,
            )

            if remove_logo:
                logo_url = ""
            elif "logo_file" in request.FILES and request.FILES["logo_file"]:
                logo_file = request.FILES["logo_file"]
                try:
                    from django.utils.text import slugify
                    from PIL import Image

                    # Open and verify image with Pillow
                    img = Image.open(logo_file)
                    img.verify()
                    logo_file.seek(0)

                    # Strictly validate image format against allowlist
                    fmt = (getattr(img, "format", "") or "").upper()
                    allowed_formats = {
                        "PNG": ".png",
                        "JPEG": ".jpg",
                        "GIF": ".gif",
                        "WEBP": ".webp",
                    }
                    if fmt not in allowed_formats:
                        raise ValueError(
                            f"Unsupported image format: {fmt or 'unknown'}"
                        )

                    ext = allowed_formats[fmt]
                    safe_slug = slugify(org.slug) or "org"
                    filename = f"org_logos/{safe_slug}_logo_{uuid.uuid4().hex[:8]}{ext}"
                    saved_path = default_storage.save(filename, logo_file)
                    raw_url = default_storage.url(saved_path)
                    logo_url = (
                        request.build_absolute_uri(raw_url) if request else raw_url
                    )
                except Exception as e:
                    messages.error(request, f"Invalid logo image file: {e}")
                    return redirect("/programs/settings/?tab=branding")
            elif not logo_url and "logo_url" not in request.POST:
                logo_url = email_cfg.get("logo_url", "")
            elif logo_url and logo_url.startswith("/") and request:
                logo_url = request.build_absolute_uri(logo_url)

            email_cfg["primary_color"] = primary_color
            email_cfg["secondary_color"] = secondary_color
            email_cfg["header_text_color"] = header_text_color
            email_cfg["organization_name"] = organization_name
            email_cfg["address_line1"] = address_line1
            email_cfg["address_line2"] = address_line2
            email_cfg["footer_text"] = footer_text
            email_cfg["contact_name"] = contact_name
            email_cfg["contact_email"] = contact_email
            email_cfg["logo_url"] = logo_url

            update_organization_email_settings(org, email_cfg)
            messages.success(
                request, "Email branding & template settings updated successfully."
            )
            return redirect("/programs/settings/?tab=branding")

        elif action == "update_email_settings":
            sender_display_name = request.POST.get("sender_display_name", "").strip()
            reply_to_email = request.POST.get("reply_to_email", "").strip()
            notification_from_email = request.POST.get(
                "notification_from_email", ""
            ).strip()

            custom_domain_enabled = request.POST.get("custom_domain_enabled") in (
                "on",
                "true",
                "1",
                True,
            )
            custom_domain_domain = request.POST.get("custom_domain_domain", "").strip()
            custom_domain_from_email = request.POST.get(
                "custom_domain_from_email", ""
            ).strip()

            email_cfg["sender_display_name"] = sender_display_name
            email_cfg["reply_to_email"] = reply_to_email
            email_cfg["notification_from_email"] = notification_from_email
            email_cfg["custom_domain"] = {
                "enabled": custom_domain_enabled,
                "domain": custom_domain_domain,
                "from_email": custom_domain_from_email,
            }

            update_organization_email_settings(org, email_cfg)
            messages.success(request, "Email settings updated successfully.")
            return redirect("/programs/settings/?tab=email")

        elif action == "add_sender_account":
            key = request.POST.get("key", "").strip().lower()
            display_name = request.POST.get("display_name", "").strip()
            email = request.POST.get("email", "").strip()
            reply_to = request.POST.get("reply_to", "").strip() or email

            if not key or not email:
                messages.error(
                    request, "Key and email are required for sender identities."
                )
                return redirect("/programs/settings/?tab=email")

            accounts = list(email_cfg.get("sender_accounts", []))
            if any(a.get("key") == key for a in accounts):
                messages.error(
                    request, f"Sender identity with key '{key}' already exists."
                )
                return redirect("/programs/settings/?tab=email")

            accounts.append(
                {
                    "key": key,
                    "display_name": display_name,
                    "email": email,
                    "reply_to": reply_to,
                }
            )
            email_cfg["sender_accounts"] = accounts
            update_organization_email_settings(org, email_cfg)
            messages.success(request, f"Sender identity '{display_name or key}' added.")
            return redirect("/programs/settings/?tab=email")

        elif action == "update_sender_account":
            key = request.POST.get("key", "").strip().lower()
            display_name = request.POST.get("display_name", "").strip()
            email = request.POST.get("email", "").strip()
            reply_to = request.POST.get("reply_to", "").strip() or email

            accounts = list(email_cfg.get("sender_accounts", []))
            updated = False
            for acc in accounts:
                if acc.get("key") == key:
                    acc["display_name"] = display_name
                    acc["email"] = email
                    acc["reply_to"] = reply_to
                    updated = True
                    break

            if updated:
                email_cfg["sender_accounts"] = accounts
                update_organization_email_settings(org, email_cfg)
                messages.success(request, f"Sender identity '{key}' updated.")
            else:
                messages.error(request, f"Sender identity '{key}' not found.")
            return redirect("/programs/settings/?tab=email")

        elif action == "delete_sender_account":
            key = request.POST.get("key", "").strip().lower()
            accounts = list(email_cfg.get("sender_accounts", []))
            initial_count = len(accounts)
            accounts = [a for a in accounts if a.get("key") != key]
            if len(accounts) < initial_count:
                email_cfg["sender_accounts"] = accounts
                update_organization_email_settings(org, email_cfg)
                messages.success(request, f"Sender identity '{key}' deleted.")
            return redirect("/programs/settings/?tab=email")

        elif action == "send_test_email":
            test_recipient = request.POST.get("test_recipient", "").strip()
            if not test_recipient:
                messages.error(request, "Please enter a valid test recipient email.")
                return redirect("/programs/settings/?tab=email")

            from programs.utils.notifications import send_templated_notification

            try:
                sent = send_templated_notification(
                    template_name="emails/generic_notification.html",
                    context={
                        "title": "Portal Email Configuration Test",
                        "heading": "Email Configuration Test",
                        "paragraphs": [
                            (
                                "This is a test notification confirming that organization "
                                "email branding and sender identities are functioning properly."
                            ),
                            (
                                "If you are seeing this, the notification template renders "
                                "correctly with your organization settings."
                            ),
                        ],
                        "call_to_action_url": email_cfg.get("portal_url", ""),
                        "call_to_action_text": "Visit Admin Portal",
                    },
                    subject="[Test] Admin Portal Email Settings Verification",
                    recipient_list=[test_recipient],
                    organization=org,
                    track_delivery=False,
                )
                if sent:
                    messages.success(
                        request,
                        f"Test email sent successfully to {test_recipient}.",
                    )
                else:
                    messages.error(
                        request,
                        "Failed to dispatch test email. Please check delivery logs.",
                    )
            except Exception as exc:
                messages.error(request, f"Error sending test email: {exc}")

            return redirect("/programs/settings/?tab=email")

        return redirect("/programs/settings/?tab=email")


class PortalOrgAccountSettingsView(LoginRequiredMixin, LeadMentorRequiredMixin, View):
    """Handles updating organization account / institutional email settings from Portal Settings."""

    def post(self, request):
        from organizations.utils import (
            get_default_organization,
            update_organization_settings,
        )

        org = getattr(request, "organization", None)
        if not org and request.user.is_authenticated:
            membership = getattr(request.user, "organization_memberships", None)
            if membership:
                active_mem = membership.filter(is_active=True).first()
                if active_mem:
                    org = active_mem.organization
        if not org:
            org = get_default_organization()
        if not org:
            messages.error(request, "No active organization found.")
            return redirect("/programs/settings/?tab=org_accounts")

        org_account_enabled = request.POST.get("org_account_enabled") == "on"
        org_account_name = (
            request.POST.get("org_account_name", "").strip() or "Andrew ID"
        )
        org_email_domain = (
            request.POST.get("org_email_domain", "").strip().lower().lstrip("@")
        )
        force_mentor_org_email = request.POST.get("force_mentor_org_email") == "on"
        force_student_org_email = request.POST.get("force_student_org_email") == "on"
        force_parent_org_email = request.POST.get("force_parent_org_email") == "on"
        force_alumni_org_email = request.POST.get("force_alumni_org_email") == "on"

        new_settings = {
            "org_account_enabled": org_account_enabled,
            "org_account_name": org_account_name,
            "org_email_domain": org_email_domain,
            "force_mentor_org_email": force_mentor_org_email,
            "force_student_org_email": force_student_org_email,
            "force_parent_org_email": force_parent_org_email,
            "force_alumni_org_email": force_alumni_org_email,
        }

        update_organization_settings(org, new_settings)
        messages.success(request, "Organization account settings updated successfully.")
        return redirect("/programs/settings/?tab=org_accounts")


def _parse_cutoff_date(post_data, min_age):
    """Parses cutoff month and day (or full date) from POST data if min_age is configured."""
    if min_age is None:
        return None
    cutoff_month_raw = post_data.get("student_min_age_cutoff_month")
    cutoff_day_raw = post_data.get("student_min_age_cutoff_day")
    if cutoff_month_raw and cutoff_day_raw:
        try:
            m = max(1, min(12, int(cutoff_month_raw)))
            d = max(1, min(31, int(cutoff_day_raw)))
            return date(2000, m, d)
        except (ValueError, TypeError):
            return date(2000, 9, 1)
    cutoff_date_raw = (post_data.get("student_min_age_cutoff_date") or "").strip()
    if cutoff_date_raw:
        try:
            return datetime.strptime(cutoff_date_raw, "%Y-%m-%d").date()
        except ValueError:
            return date(2000, 9, 1)
    return date(2000, 9, 1)


class PortalBackgroundCheckTypeView(LoginRequiredMixin, LeadMentorRequiredMixin, View):
    """Handles add / update / toggle / delete actions for BackgroundCheckTypes."""

    def post(self, request):
        action = request.POST.get("action")
        org = getattr(request, "organization", None) or get_default_organization()

        if action == "add_type":
            name = request.POST.get("name", "").strip()
            if not name:
                messages.error(request, "Name is required.")
                return redirect("/programs/settings/?tab=background_checks")

            description = request.POST.get("description", "").strip()
            try:
                validity_years = int(request.POST.get("validity_years", 5))
            except (ValueError, TypeError):
                validity_years = 5

            applies_to_students = request.POST.get("applies_to_students") in (
                "on",
                "true",
                "1",
                True,
            )
            applies_to_mentors = request.POST.get("applies_to_mentors") in (
                "on",
                "true",
                "1",
                True,
            )
            applies_to_lead_mentors = request.POST.get("applies_to_lead_mentors") in (
                "on",
                "true",
                "1",
                True,
            )
            applies_to_parents = request.POST.get("applies_to_parents") in (
                "on",
                "true",
                "1",
                True,
            )
            applies_to_alumni = request.POST.get("applies_to_alumni") in (
                "on",
                "true",
                "1",
                True,
            )

            student_min_age_by_date_raw = (
                request.POST.get("student_min_age_by_date")
                or request.POST.get("student_min_age")
                or ""
            ).strip()
            student_min_age_by_date = (
                int(student_min_age_by_date_raw)
                if student_min_age_by_date_raw.isdigit()
                else None
            )

            student_min_age_cutoff_date = _parse_cutoff_date(
                request.POST, student_min_age_by_date
            )

            student_min_age_during_program_raw = request.POST.get(
                "student_min_age_during_program", ""
            ).strip()
            student_min_age_during_program = (
                int(student_min_age_during_program_raw)
                if student_min_age_during_program_raw.isdigit()
                else None
            )

            try:
                display_order = int(request.POST.get("display_order", 0))
            except (ValueError, TypeError):
                display_order = 0

            is_active = request.POST.get("is_active", "on") in (
                "on",
                "true",
                "1",
                True,
            )

            bct = BackgroundCheckType.objects.create(
                organization=org,
                name=name,
                description=description,
                validity_years=validity_years,
                applies_to_students=applies_to_students,
                applies_to_mentors=applies_to_mentors,
                applies_to_lead_mentors=applies_to_lead_mentors,
                applies_to_parents=applies_to_parents,
                applies_to_alumni=applies_to_alumni,
                student_min_age_by_date=student_min_age_by_date,
                student_min_age_cutoff_date=student_min_age_cutoff_date,
                student_min_age_during_program=student_min_age_during_program,
                display_order=display_order,
                is_active=is_active,
            )
            messages.success(request, f"Background check type '{bct.name}' created.")
            return redirect("/programs/settings/?tab=background_checks")

        elif action == "update_type":
            type_id = request.POST.get("type_id")
            bct = BackgroundCheckType.objects.filter(id=type_id).first()
            if not bct:
                messages.error(request, "Background check type not found.")
                return redirect("/programs/settings/?tab=background_checks")

            name = request.POST.get("name", "").strip()
            if not name:
                messages.error(request, "Name is required.")
                return redirect("/programs/settings/?tab=background_checks")

            bct.name = name
            bct.description = request.POST.get("description", "").strip()
            try:
                bct.validity_years = int(request.POST.get("validity_years", 5))
            except (ValueError, TypeError):
                pass

            bct.applies_to_students = request.POST.get("applies_to_students") in (
                "on",
                "true",
                "1",
                True,
            )
            bct.applies_to_mentors = request.POST.get("applies_to_mentors") in (
                "on",
                "true",
                "1",
                True,
            )
            bct.applies_to_lead_mentors = request.POST.get(
                "applies_to_lead_mentors"
            ) in ("on", "true", "1", True)
            bct.applies_to_parents = request.POST.get("applies_to_parents") in (
                "on",
                "true",
                "1",
                True,
            )
            bct.applies_to_alumni = request.POST.get("applies_to_alumni") in (
                "on",
                "true",
                "1",
                True,
            )

            student_min_age_by_date_raw = (
                request.POST.get("student_min_age_by_date")
                or request.POST.get("student_min_age")
                or ""
            ).strip()
            bct.student_min_age_by_date = (
                int(student_min_age_by_date_raw)
                if student_min_age_by_date_raw.isdigit()
                else None
            )

            bct.student_min_age_cutoff_date = _parse_cutoff_date(
                request.POST, bct.student_min_age_by_date
            )

            student_min_age_during_program_raw = request.POST.get(
                "student_min_age_during_program", ""
            ).strip()
            bct.student_min_age_during_program = (
                int(student_min_age_during_program_raw)
                if student_min_age_during_program_raw.isdigit()
                else None
            )

            try:
                bct.display_order = int(request.POST.get("display_order", 0))
            except (ValueError, TypeError):
                pass

            if "is_active" in request.POST:
                bct.is_active = request.POST.get("is_active") in (
                    "on",
                    "true",
                    "1",
                    True,
                )

            bct.save()
            messages.success(request, f"Background check type '{bct.name}' updated.")
            return redirect("/programs/settings/?tab=background_checks")

        elif action == "toggle_active":
            type_id = request.POST.get("type_id")
            bct = BackgroundCheckType.objects.filter(id=type_id).first()
            if bct:
                bct.is_active = not bct.is_active
                bct.save(update_fields=["is_active", "updated_at"])
                state = "activated" if bct.is_active else "deactivated"
                messages.success(request, f"Clearance type '{bct.name}' {state}.")
            return redirect("/programs/settings/?tab=background_checks")

        elif action == "delete_type":
            type_id = request.POST.get("type_id")
            bct = BackgroundCheckType.objects.filter(id=type_id).first()
            if bct:
                log_deletion(
                    resource=bct,
                    request=request,
                    before={
                        "name": bct.name,
                        "validity_years": bct.validity_years,
                        "organization_id": bct.organization_id,
                    },
                    notes="Background check type deleted from Portal Settings.",
                )
                bct.delete()
                messages.success(request, "Background check type deleted.")
            return redirect("/programs/settings/?tab=background_checks")

        return redirect("/programs/settings/?tab=background_checks")


class PortalLeadMentorView(LoginRequiredMixin, LeadMentorRequiredMixin, View):
    """Handles add/remove actions for Organization Lead Mentors from Portal Settings."""

    def post(self, request):
        from django.contrib.auth import get_user_model

        from audit.events import AuditEvent
        from audit.service import log_event
        from organizations.models import (
            OrganizationInvitation,
            UserOrganizationMembership,
        )
        from organizations.services import send_invitation_email

        User = get_user_model()
        org = getattr(request, "organization", None) or get_default_organization()
        action = request.POST.get("action")

        def report_failed_invitation(invitation, change):
            log_event(
                request=request,
                event=AuditEvent.ROLE_CHANGED,
                resource=invitation,
                notes=(
                    f"{change} Lead Mentor invitation to {invitation.email} for "
                    f"organization '{org.name}'; email not sent."
                ),
            )
            messages.error(
                request,
                f"The invitation for {invitation.email} was saved but could not be sent. "
                "Check the portal address and email settings, then retry the invitation.",
            )

        if action == "add_lead_mentor":
            user_id = request.POST.get("user_id")
            if not user_id:
                messages.error(
                    request, "Please select a user to assign as Lead Mentor."
                )
                return redirect("/programs/settings/?tab=lead_mentors")

            target_user = User.objects.filter(id=user_id, is_active=True).first()
            if not target_user:
                messages.error(request, "Selected user was not found or is inactive.")
                return redirect("/programs/settings/?tab=lead_mentors")

            target_name = (
                (
                    getattr(target_user, "adult_profile", None)
                    and target_user.adult_profile.display_name
                )
                or (
                    getattr(target_user, "student_profile", None)
                    and target_user.student_profile.display_name
                )
                or target_user.get_full_name()
                or target_user.email
                or target_user.username
            )

            if not target_user.email or "@" not in target_user.email:
                messages.error(
                    request,
                    f"Selected user '{target_name}' does not have a valid email address configured.",
                )
                return redirect("/programs/settings/?tab=lead_mentors")

            email = target_user.email.strip().lower()

            # Check if this user is already an active Lead Mentor in this organization
            active_lead = UserOrganizationMembership.objects.filter(
                organization=org,
                role="LeadMentor",
                is_active=True,
                user=target_user,
            ).first()
            if active_lead:
                messages.error(
                    request,
                    f"A user with email '{email}' is already an active Lead Mentor for {org.name}.",
                )
                return redirect("/programs/settings/?tab=lead_mentors")

            # Check if there is already a pending invitation for this email
            existing_invite = OrganizationInvitation.objects.filter(
                organization=org,
                role="LeadMentor",
                email__iexact=email,
                status=OrganizationInvitation.STATUS_PENDING,
            ).first()
            if existing_invite:
                existing_invite.expires_at = timezone.now() + timedelta(days=14)
                existing_invite.invited_by = request.user
                existing_invite.save(update_fields=["expires_at", "invited_by"])
                if not send_invitation_email(existing_invite, request=request):
                    report_failed_invitation(existing_invite, "Refreshed")
                    return redirect("/programs/settings/?tab=lead_mentors")
                log_event(
                    request=request,
                    event=AuditEvent.ROLE_CHANGED,
                    resource=existing_invite,
                    notes=f"Refreshed and resent Lead Mentor invitation to {target_name} ({email}) for organization '{org.name}'.",
                )
                messages.success(
                    request,
                    f"Lead Mentor invitation sent to {target_name} ({email}). Their Lead Mentor access will be enabled once they accept the invitation (valid for 14 days).",
                )
                return redirect("/programs/settings/?tab=lead_mentors")

            invitation = OrganizationInvitation.objects.create(
                organization=org,
                email=email,
                role="LeadMentor",
                invited_by=request.user,
                expires_at=timezone.now() + timedelta(days=14),
            )
            if not send_invitation_email(invitation, request=request):
                report_failed_invitation(invitation, "Created")
                return redirect("/programs/settings/?tab=lead_mentors")
            log_event(
                request=request,
                event=AuditEvent.ROLE_CHANGED,
                resource=invitation,
                notes=f"Sent Lead Mentor invitation to {target_name} ({email}) for organization '{org.name}'.",
            )
            messages.success(
                request,
                f"Lead Mentor invitation sent to {target_name} ({email}). Their Lead Mentor access will be enabled once they accept the invitation (valid for 14 days).",
            )
            return redirect("/programs/settings/?tab=lead_mentors")

        elif action == "remove_lead_mentor":
            user_id = request.POST.get("user_id")
            if not user_id:
                messages.error(request, "Target user not specified.")
                return redirect("/programs/settings/?tab=lead_mentors")

            target_user = User.objects.filter(id=user_id).first()
            if not target_user:
                messages.error(request, "User not found.")
                return redirect("/programs/settings/?tab=lead_mentors")

            # Lockout protection: Cannot remove yourself if you're the sole active Lead Mentor and not superuser
            if target_user.id == request.user.id and not request.user.is_superuser:
                active_count = UserOrganizationMembership.objects.filter(
                    organization=org,
                    role="LeadMentor",
                    is_active=True,
                ).count()
                if active_count <= 1:
                    messages.error(
                        request,
                        "Cannot remove the only Lead Mentor. Assign another Lead Mentor first to prevent "
                        "organization lockout.",
                    )
                    return redirect("/programs/settings/?tab=lead_mentors")

            membership = UserOrganizationMembership.objects.filter(
                user=target_user,
                organization=org,
                role="LeadMentor",
                is_active=True,
            ).first()

            if membership:
                membership.is_active = False
                membership.save(update_fields=["is_active", "updated_at"])

                log_event(
                    request=request,
                    event=AuditEvent.ROLE_CHANGED,
                    resource=target_user,
                    notes=f"Removed Lead Mentor role for user {target_user} in organization '{org.name}'.",
                )
                target_name = (
                    (
                        getattr(target_user, "adult_profile", None)
                        and target_user.adult_profile.display_name
                    )
                    or (
                        getattr(target_user, "student_profile", None)
                        and target_user.student_profile.display_name
                    )
                    or target_user.get_full_name()
                    or target_user.email
                    or target_user.username
                )
                messages.success(
                    request,
                    f"Removed Lead Mentor role from {target_name}.",
                )
            else:
                messages.warning(request, "User is not an active Lead Mentor.")

            return redirect("/programs/settings/?tab=lead_mentors")

        elif action == "invite_lead_mentor":
            email = (request.POST.get("email") or "").strip().lower()
            if not email or "@" not in email:
                messages.error(request, "Please enter a valid email address.")
                return redirect("/programs/settings/?tab=lead_mentors")

            # Check if this user is already an active Lead Mentor in this organization
            active_lead = UserOrganizationMembership.objects.filter(
                organization=org,
                role="LeadMentor",
                is_active=True,
                user__email__iexact=email,
            ).first()
            if active_lead:
                messages.error(
                    request,
                    f"A user with email '{email}' is already an active Lead Mentor for {org.name}.",
                )
                return redirect("/programs/settings/?tab=lead_mentors")

            # Check if there is already a pending invitation for this email
            existing_invite = OrganizationInvitation.objects.filter(
                organization=org,
                role="LeadMentor",
                email__iexact=email,
                status=OrganizationInvitation.STATUS_PENDING,
            ).first()
            if existing_invite:
                existing_invite.expires_at = timezone.now() + timedelta(days=14)
                existing_invite.invited_by = request.user
                existing_invite.save(update_fields=["expires_at", "invited_by"])
                if not send_invitation_email(existing_invite, request=request):
                    report_failed_invitation(existing_invite, "Refreshed")
                    return redirect("/programs/settings/?tab=lead_mentors")
                log_event(
                    request=request,
                    event=AuditEvent.ROLE_CHANGED,
                    resource=existing_invite,
                    notes=f"Refreshed and resent Lead Mentor invitation to {email} for organization '{org.name}'.",
                )
                messages.success(
                    request,
                    f"Updated and resent Lead Mentor invitation to {email}. Valid for 14 days.",
                )
                return redirect("/programs/settings/?tab=lead_mentors")

            invitation = OrganizationInvitation.objects.create(
                organization=org,
                email=email,
                role="LeadMentor",
                invited_by=request.user,
                expires_at=timezone.now() + timedelta(days=14),
            )
            if not send_invitation_email(invitation, request=request):
                report_failed_invitation(invitation, "Created")
                return redirect("/programs/settings/?tab=lead_mentors")
            log_event(
                request=request,
                event=AuditEvent.ROLE_CHANGED,
                resource=invitation,
                notes=f"Sent Lead Mentor invitation to {email} for organization '{org.name}'.",
            )
            messages.success(
                request,
                f"Successfully sent Lead Mentor invitation to {email}. The invitation will expire in 14 days.",
            )
            return redirect("/programs/settings/?tab=lead_mentors")

        elif action == "resend_invitation":
            invitation_id = request.POST.get("invitation_id")
            if not invitation_id:
                messages.error(request, "Invitation ID not specified.")
                return redirect("/programs/settings/?tab=lead_mentors")

            invitation = OrganizationInvitation.objects.filter(
                id=invitation_id,
                organization=org,
                role="LeadMentor",
                status=OrganizationInvitation.STATUS_PENDING,
            ).first()
            if not invitation:
                messages.error(request, "Pending invitation not found.")
                return redirect("/programs/settings/?tab=lead_mentors")

            invitation.expires_at = timezone.now() + timedelta(days=14)
            invitation.save(update_fields=["expires_at"])
            if not send_invitation_email(invitation, request=request):
                report_failed_invitation(invitation, "Refreshed")
                return redirect("/programs/settings/?tab=lead_mentors")
            messages.success(
                request,
                f"Resent Lead Mentor invitation to {invitation.email}.",
            )
            return redirect("/programs/settings/?tab=lead_mentors")

        elif action == "revoke_invitation":
            invitation_id = request.POST.get("invitation_id")
            if not invitation_id:
                messages.error(request, "Invitation ID not specified.")
                return redirect("/programs/settings/?tab=lead_mentors")

            invitation = OrganizationInvitation.objects.filter(
                id=invitation_id,
                organization=org,
                role="LeadMentor",
                status=OrganizationInvitation.STATUS_PENDING,
            ).first()
            if not invitation:
                messages.error(request, "Pending invitation not found.")
                return redirect("/programs/settings/?tab=lead_mentors")

            invitation.status = OrganizationInvitation.STATUS_DECLINED
            invitation.save(update_fields=["status"])
            log_event(
                request=request,
                event=AuditEvent.ROLE_CHANGED,
                resource=invitation,
                notes=f"Revoked Lead Mentor invitation for {invitation.email} in organization '{org.name}'.",
            )
            messages.success(
                request,
                f"Revoked Lead Mentor invitation for {invitation.email}.",
            )
            return redirect("/programs/settings/?tab=lead_mentors")

        messages.error(request, "Unknown action.")
        return redirect("/programs/settings/?tab=lead_mentors")


class PortalDomainView(LoginRequiredMixin, LeadMentorRequiredMixin, View):
    """Manage custom portal-domain requests for the current organization."""

    redirect_url = "/programs/settings/?tab=portal_address"

    def post(self, request):
        organization = getattr(request, "organization", None)
        if organization is None:
            raise Http404

        action = request.POST.get("action")
        if action in ("request_recovery", "verify_recovery", "revoke_recovery"):
            try:
                if action == "request_recovery":
                    domain_recovery.request_recovery(
                        request, organization, request.POST.get("hostname", "")
                    )
                    message = (
                        "Recovery requested. Publish the separate TXT challenge below."
                    )
                elif action == "verify_recovery":
                    domain_recovery.verify_recovery(
                        request, organization, request.POST.get("recovery_id")
                    )
                    message = (
                        "DNS proof verified. Momentum support must review the recovery."
                    )
                else:
                    domain_recovery.revoke_recovery(
                        request, organization, request.POST.get("recovery_id")
                    )
                    message = "Recovery request revoked."
            except (domain_recovery.RecoveryError, ValidationError) as exc:
                messages.error(request, str(exc))
            else:
                messages.success(request, message)
            return redirect(self.redirect_url)

        if action == "request":
            form = OrganizationDomainRequestForm(
                request.POST, organization=organization
            )
            if not form.is_valid():
                messages.error(
                    request,
                    "; ".join(form.errors.get("hostname", ["Enter a valid hostname."])),
                )
                return redirect(self.redirect_url)

            domain = OrganizationDomain(
                organization=organization,
                hostname=form.cleaned_data["hostname"],
                created_by=request.user,
            )
            try:
                domain.save()
            except IntegrityError:
                messages.error(
                    request,
                    "That hostname is already registered. Contact Momentum support if you believe this is an error.",
                )
                return redirect(self.redirect_url)

            log_event(
                request=request,
                event=AuditEvent.ORGANIZATION_UPDATED,
                resource=organization,
                notes=f"Requested portal hostname {domain.hostname}.",
            )
            messages.success(
                request,
                f"Added {domain.hostname}. Add the TXT record shown below, then check it here. "
                "New addresses need Momentum support to complete setup and activate them; existing "
                "legacy addresses can continue working during the transition.",
            )
            return redirect(self.redirect_url)

        if action == "verify":
            domain = get_object_or_404(
                OrganizationDomain.objects.for_organization(organization),
                pk=request.POST.get("domain_id"),
            )
            if domain.status == OrganizationDomain.STATUS_ACTIVE:
                messages.info(request, "This domain is already active.")
                return redirect(self.redirect_url)

            if not verify_domain_txt(domain):
                messages.error(
                    request,
                    "We could not find the matching TXT record. Check the record name and value, allow time "
                    "for DNS to update, then try again.",
                )
                return redirect(self.redirect_url)

            with transaction.atomic():
                current = get_object_or_404(
                    OrganizationDomain.objects.for_organization(
                        organization
                    ).select_for_update(),
                    pk=domain.pk,
                )
                if (
                    current.hostname != domain.hostname
                    or current.verification_token != domain.verification_token
                ):
                    messages.error(
                        request, "The domain changed during verification. Try again."
                    )
                    return redirect(self.redirect_url)
                if current.status == OrganizationDomain.STATUS_ACTIVE:
                    messages.info(request, "This domain is already active.")
                    return redirect(self.redirect_url)

                current.dns_verified_at = timezone.now()
                if current.status != OrganizationDomain.STATUS_DISABLED:
                    current.status = OrganizationDomain.STATUS_DNS_VERIFIED
                current.save(update_fields=["dns_verified_at", "status", "updated_at"])
                log_event(
                    request=request,
                    event=AuditEvent.ORGANIZATION_UPDATED,
                    resource=organization,
                    notes=f"Verified DNS ownership for portal hostname {current.hostname}.",
                )

            if current.status == OrganizationDomain.STATUS_DISABLED:
                messages.success(
                    request,
                    f"DNS ownership for {current.hostname} is verified. The domain remains disabled "
                    "until a Momentum administrator activates it.",
                )
            else:
                messages.success(
                    request,
                    f"DNS ownership for {current.hostname} is verified. Registry activation still requires "
                    "Momentum support to confirm HTTPS and host routing; existing legacy routes may remain available.",
                )
            return redirect(self.redirect_url)

        messages.error(request, "Unknown domain action.")
        return redirect(self.redirect_url)
