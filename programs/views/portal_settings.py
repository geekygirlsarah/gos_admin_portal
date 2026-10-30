import os
import uuid
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.files.storage import default_storage
from django.shortcuts import redirect, render
from django.views import View

from audit.service import log_deletion
from organizations.models import Organization
from organizations.utils import (
    get_default_organization,
    get_organization_email_settings,
    update_organization_email_settings,
)
from programs.constants import TEAM_TYPES
from programs.forms import MentorAgreementForm
from programs.models import (
    BackgroundCheckType,
    Crew,
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


class PortalSettingsView(LoginRequiredMixin, LeadMentorRequiredMixin, View):
    template_name = "programs/settings.html"

    def get(self, request):
        org = getattr(request, "organization", None) or get_default_organization()
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
            uses_read = active_column is None or active_column == "view"
            uses_write = active_column is None or active_column != "view"
            grouped_permissions.append(
                {
                    "name": section_name,
                    "mentor": permissions.filter(
                        section=section_code, role="Mentor"
                    ).first(),
                    "parent": permissions.filter(
                        section=section_code, role="Parent"
                    ).first(),
                    "student": permissions.filter(
                        section=section_code, role="Student"
                    ).first(),
                    "orders_section": section_code in ORDERS_SECTIONS,
                    "uses_read": uses_read,
                    "uses_write": uses_write,
                }
            )

        teams = Team.objects.all()
        team_types = TEAM_TYPES
        crews = Crew.objects.select_related("program").all()
        subteams = SubTeam.objects.select_related("program").all()
        programs = (
            Program.objects.prefetch_related("crews", "subteams")
            .all()
            .order_by("-active", "name")
        )
        attendance_programs = [p for p in programs if p.has_feature("attendance")]

        kiosk_configs = None
        if KioskConfig:
            kiosk_configs = KioskConfig.objects.select_related("program").all()

        org = getattr(request, "organization", None) or get_default_organization()
        email_settings = get_organization_email_settings(org)

        bg_types = BackgroundCheckType.objects.for_organization(org).order_by(
            "display_order", "name"
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
            "sliding_scale_settings": SlidingScaleSettings.get_solo(),
            "pending_sliding_scale_count": SlidingScale.objects.filter(
                status=SlidingScale.STATUS_PENDING
            ).count(),
            "mentor_agreements": MentorAgreement.objects.order_by("slug", "-version"),
            "mentor_agreement_slugs": MentorAgreement.objects.values_list(
                "slug", flat=True
            ).distinct(),
            "background_check_types": bg_types,
            "organization": org,
            "email_settings": email_settings,
            "role": "LeadMentor",  # Required for base.html to show Nav correctly
            "active_tab": request.GET.get("tab", "permissions"),
            "sections": sections,
        }
        return render(request, self.template_name, context)


class PortalSlidingScaleSettingsView(LoginRequiredMixin, LeadMentorRequiredMixin, View):
    """Handles updates to the portal-wide sliding scale calculation settings."""

    def post(self, request):
        settings_obj = SlidingScaleSettings.get_solo()
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
            if org and perm.organization is None:
                perm.organization = org
            perm.save()
        messages.success(request, "Permissions updated successfully.")
        return redirect("/programs/settings/?tab=permissions")


class PortalTeamView(LoginRequiredMixin, LeadMentorRequiredMixin, View):
    """Handles add/delete/update actions for Teams from the settings page."""

    def post(self, request):
        action = request.POST.get("action")

        if action == "add_team":
            team_type = request.POST.get("team_type")
            number = request.POST.get("number")
            name = request.POST.get("name")
            color = request.POST.get("color")
            if team_type and number:
                Team.objects.create(
                    team_type=team_type, number=number, name=name, color=color
                )
                messages.success(request, f"Team {team_type} {number} added.")
            return redirect("/programs/settings/?tab=teams")

        elif action == "delete_team":
            team_id = request.POST.get("team_id")
            if team_id:
                team = Team.objects.filter(id=team_id).first()
                if team:
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
                team = Team.objects.filter(id=team_id).first()
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
        action = request.POST.get("action")

        if action == "add_crew":
            program_id = request.POST.get("program_id")
            name = request.POST.get("name")
            color = request.POST.get("color")
            if program_id and name:
                Crew.objects.create(program_id=program_id, name=name, color=color)
                messages.success(request, f"Crew {name} added.")
            return redirect("/programs/settings/?tab=crews")

        elif action == "delete_crew":
            crew_id = request.POST.get("crew_id")
            if crew_id:
                crew = Crew.objects.filter(id=crew_id).first()
                if crew:
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
                crew = Crew.objects.filter(id=crew_id).first()
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
        action = request.POST.get("action")

        if action == "add_subteam":
            program_id = request.POST.get("program_id")
            name = request.POST.get("name")
            color = request.POST.get("color")
            if program_id and name:
                SubTeam.objects.create(program_id=program_id, name=name, color=color)
                messages.success(request, f"SubTeam {name} added.")
            return redirect("/programs/settings/?tab=subteams")

        elif action == "delete_subteam":
            subteam_id = request.POST.get("subteam_id")
            if subteam_id:
                subteam = SubTeam.objects.filter(id=subteam_id).first()
                if subteam:
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
                subteam = SubTeam.objects.filter(id=subteam_id).first()
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
        action = request.POST.get("action")

        if action == "add_kiosk_config" and KioskConfig:
            label = request.POST.get("label", "").strip()
            program_id = request.POST.get("program_id")
            if label and program_id:
                KioskConfig.objects.create(
                    label=label,
                    program_id=program_id,
                )
                messages.success(request, f"Kiosk '{label}' added.")
            return redirect("/programs/settings/?tab=kiosk_configs")

        elif action == "delete_kiosk_config" and KioskConfig:
            kiosk_config_id = request.POST.get("kiosk_config_id")
            if kiosk_config_id:
                kiosk = KioskConfig.objects.filter(id=kiosk_config_id).first()
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
                kiosk = KioskConfig.objects.filter(id=kiosk_config_id).first()
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
        action = request.POST.get("action")

        if action == "add_agreement":
            form = MentorAgreementForm(request.POST, request.FILES)
            if form.is_valid():
                slug = form.cleaned_data["slug"]
                version = (
                    MentorAgreement.objects.filter(slug=slug)
                    .order_by("-version")
                    .values_list("version", flat=True)
                    .first()
                )
                version = (version or 0) + 1
                agreement = form.save(commit=False)
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
                agreement = MentorAgreement.objects.get(pk=agreement_id)
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
                        MentorAgreement.objects.filter(slug=agreement.slug)
                        .order_by("-version")
                        .values_list("version", flat=True)
                        .first()
                    )
                    new_agreement.version = (version or 0) + 1
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
                agreement = MentorAgreement.objects.get(pk=agreement_id)
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
                agreement = MentorAgreement.objects.get(pk=agreement_id)
                title = agreement.title
                from programs.models import MentorAgreementAcceptance

                versions = MentorAgreement.objects.filter(slug=agreement.slug)
                acceptance_count = MentorAgreementAcceptance.objects.filter(
                    agreement__in=versions
                ).count()
                count = versions.count()
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
                MentorAgreementAcceptance.objects.filter(
                    agreement__in=versions
                ).delete()
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
            portal_url = request.POST.get("portal_url", "").strip()
            if portal_url and not portal_url.startswith(("http://", "https://")):
                portal_url = f"https://{portal_url}"

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
                    from PIL import Image

                    img = Image.open(logo_file)
                    img.verify()
                    logo_file.seek(0)

                    ext = os.path.splitext(logo_file.name)[1].lower() or ".png"
                    filename = f"org_logos/{org.slug}_logo_{uuid.uuid4().hex[:8]}{ext}"
                    saved_path = default_storage.save(filename, logo_file)
                    raw_url = default_storage.url(saved_path)
                    logo_url = (
                        request.build_absolute_uri(raw_url) if request else raw_url
                    )
                except Exception as e:
                    messages.error(request, f"Invalid logo image file: {e}")
                    return redirect("/programs/settings/?tab=email")
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
            email_cfg["portal_url"] = portal_url
            email_cfg["contact_name"] = contact_name
            email_cfg["contact_email"] = contact_email
            email_cfg["logo_url"] = logo_url

            update_organization_email_settings(org, email_cfg)
            messages.success(
                request, "Email branding & template settings updated successfully."
            )
            return redirect("/programs/settings/?tab=email")

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
