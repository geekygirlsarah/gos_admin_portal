import markdown as md
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import logout
from django.core.exceptions import SuspiciousFileOperation
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme

from programs.models import (
    MentorAgreement,
    MentorAgreementAcceptance,
    MentorAgreementSubmission,
)


def mentor_agreement_view(request):
    """Display all pending mentor agreements for the current user.

    GET:  Show every active agreement the user has not yet accepted.
    POST action=upload: Handle file upload for a specific agreement.
    POST action=agree: Record acceptances for all pending agreements, then
           redirect to the next URL (or dashboard).
    POST action=disagree: Log out and redirect to login.
    """
    active = MentorAgreement.get_all_active()

    # Determine which agreements are still pending for this user
    adult = getattr(request.user, "adult_profile", None)
    if adult is not None:
        accepted_ids = set(
            MentorAgreementAcceptance.objects.filter(
                adult=adult, agreement__in=active
            ).values_list("agreement_id", flat=True)
        )
        # Map agreement_id -> submission for PDF agreements
        submissions_by_agreement = {
            s.agreement_id: s
            for s in MentorAgreementSubmission.objects.filter(
                adult=adult, agreement__in=active
            )
        }
    else:
        accepted_ids = set()
        submissions_by_agreement = {}

    pending = [a for a in active if a.id not in accepted_ids]
    already_accepted = [a for a in active if a.id in accepted_ids]

    # Build pending details with content, document, and upload status
    pending_details = []
    for agreement in pending:
        content_html = None
        if agreement.content:
            content_html = md.markdown(agreement.content, extensions=["extra"])
        submission = submissions_by_agreement.get(agreement.id)
        pending_details.append(
            {
                "agreement": agreement,
                "content_html": content_html,
                "submission": submission,
                "needs_upload": bool(agreement.document),
            }
        )

    if request.method == "POST":
        action = request.POST.get("action")

        if action == "upload" and adult is not None:
            return _handle_upload(request, adult, pending, pending_details)

        if action == "agree" and adult is not None:
            return _handle_agree(
                request,
                adult,
                pending,
                pending_details,
                already_accepted,
                submissions_by_agreement,
            )

        if action == "disagree":
            logout(request)
            messages.info(
                request,
                "You must agree to the required mentor agreements to access "
                "the portal. If you have questions, please contact a Lead Mentor.",
            )
            return redirect(settings.LOGIN_URL)

    return render(
        request,
        "mentor_agreement.html",
        {
            "pending_details": pending_details,
            "already_accepted": already_accepted,
        },
    )


def _handle_agree(
    request, adult, pending, pending_details, already_accepted, submissions_by_agreement
):
    """Record acceptances for all pending agreements after verifying uploads."""
    missing = [
        a.title for a in pending if a.document and a.id not in submissions_by_agreement
    ]

    if missing:
        messages.error(
            request,
            "Please upload signed copies for: " + ", ".join(missing) + ".",
        )
        return render(
            request,
            "mentor_agreement.html",
            {
                "pending_details": pending_details,
                "already_accepted": already_accepted,
            },
        )

    ip = request.META.get("REMOTE_ADDR")
    for agreement in pending:
        MentorAgreementAcceptance.objects.get_or_create(
            adult=adult,
            agreement=agreement,
            defaults={"ip_address": ip},
        )
    next_url = request.GET.get("next", reverse("profile_dashboard"))
    if url_has_allowed_host_and_scheme(next_url, allowed_hosts={request.get_host()}):
        return redirect(next_url)
    return redirect(reverse("profile_dashboard"))


def _handle_upload(request, adult, pending, pending_details):
    """Handle file upload for a specific agreement."""
    agreement_id = request.POST.get("upload_agreement_id")
    agreement = next((a for a in pending if str(a.id) == str(agreement_id)), None)

    if agreement is None or not agreement.document:
        messages.error(request, "Invalid agreement for upload.")
        return redirect("mentor_agreement")

    # File input is named file_{{ agreement_id }}
    file_key = f"file_{agreement.id}"
    uploaded_file = request.FILES.get(file_key)

    if not uploaded_file:
        messages.error(request, "Please select a file to upload.")
        return redirect("mentor_agreement")

    submission, _created = MentorAgreementSubmission.objects.get_or_create(
        adult=adult, agreement=agreement
    )
    submission.file = uploaded_file
    try:
        submission.save()
    except SuspiciousFileOperation:
        messages.error(
            request,
            "The filename of your uploaded document is too long or contains "
            "invalid characters. Please rename the file and try again.",
        )
        return redirect("mentor_agreement")

    messages.success(request, f"Signed copy uploaded for {agreement.title}.")
    return redirect("mentor_agreement")
