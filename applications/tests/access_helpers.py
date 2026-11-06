import time

from programs.constants import OTP_TTL_SECONDS


def grant_applicant_document_access(client, application):
    """Simulate a successful email-code challenge for document workflow tests."""
    session = client.session
    session[
        f"application_document_access:{application.organization_id}:{application.application_id}"
    ] = {
        "organization_id": application.organization_id,
        "email": (application.email or "").strip().lower(),
        "expires_at": int(time.time()) + OTP_TTL_SECONDS,
    }
    session.save()
