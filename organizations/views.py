from django.contrib import messages
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.views import View

from audit.events import AuditEvent
from audit.service import log_event

from .models import OrganizationInvitation


class AcceptInvitationView(View):
    """View allowing a user to review and accept an organization invitation."""

    template_name = "organizations/accept_invitation.html"

    def get_invitation(self, token):
        invitation = get_object_or_404(OrganizationInvitation, token=token)
        if not invitation.is_valid():
            raise Http404("This invitation is invalid or has expired.")
        return invitation

    def get(self, request, token):
        invitation = self.get_invitation(token)
        if not request.user.is_authenticated:
            # If not logged in, redirect to login with next param
            login_url = f"/accounts/login/?next={request.path}"
            return redirect(login_url)

        email_matches = invitation.matches_user(request.user)

        return render(
            request,
            self.template_name,
            {
                "invitation": invitation,
                "organization": invitation.organization,
                "email_matches": email_matches,
            },
        )

    def post(self, request, token):
        invitation = self.get_invitation(token)
        if not request.user.is_authenticated:
            return redirect(f"/accounts/login/?next={request.path}")

        if not invitation.matches_user(request.user):
            user_display = (
                request.user.email
                or getattr(
                    getattr(request.user, "adult_profile", None),
                    "personal_email",
                    None,
                )
                or request.user.username
            )
            messages.error(
                request,
                f"This invitation was sent to {invitation.email}, but you are currently signed in as {user_display}. "
                "Please sign in with the invited email address to accept.",
            )
            return render(
                request,
                self.template_name,
                {
                    "invitation": invitation,
                    "organization": invitation.organization,
                    "email_matches": False,
                },
            )

        try:
            invitation.accept(request.user)
            log_event(
                request=request,
                event=AuditEvent.ROLE_CHANGED,
                resource=request.user,
                notes=f"User {request.user} accepted invitation for role {invitation.role} in {invitation.organization.name}.",
            )
            messages.success(
                request,
                f"You have joined {invitation.organization.name} as {invitation.get_role_display()}!",
            )
            request.session["active_organization_id"] = invitation.organization_id
            return redirect("profile_dashboard")
        except ValueError as e:
            messages.error(request, str(e))
            return redirect("home")
